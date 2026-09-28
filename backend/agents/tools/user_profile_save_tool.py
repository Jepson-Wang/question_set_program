from typing import Type, Optional

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from backend.agents.memory.long_term_memory import LongTermMemory
from backend.agents.memory.profile_candidates import ProfileCandidate
from backend.agents.memory.short_term_memory import get_short_term_memory
from backend.dao.user_profile_mapper import get_user_profile_mapper
from backend.middleware.logging import get_logger
from backend.schemas.request.ltm_request import LTMRequest

logger = get_logger(__name__)

# grade / subject 是事实陈述（「我初二」），notes 是自由观察，三者都不存在
# 「一次性诉求 vs 稳定偏好」的歧义，第一次出现就落库。
_DIRECT_FIELDS = ("grade", "subject", "notes")

# weak_points / preferences 是偏好，逐个子键走候选池做频次确认
_PREFERENCE_FIELDS = ("weak_points", "preferences")

_candidate_store = ProfileCandidate()


class UserProfileSaveInput(BaseModel):
    grade: Optional[str] = Field(default=None, description="用户年级，例如 '七年级'")
    subject: Optional[str] = Field(default=None, description="主修学科，例如 '数学'")
    weak_points: Optional[dict] = Field(
        default=None,
        description="薄弱知识点，JSON 对象。键必须取自 profile_schema Skill 的词表",
    )
    preferences: Optional[dict] = Field(
        default=None,
        description="长期偏好，JSON 对象。键必须取自 profile_schema Skill 的词表",
    )
    notes: Optional[list] = Field(
        default=None,
        description=(
            "放不进上述任何结构化键的自由观察，每条一句话。"
            "追加写入，不参与频次确认，立即生效"
        ),
    )


class UserProfileSaveTool(BaseTool):
    name: str = "user_profile_save_tool"
    description: str = (
        "保存当前用户的长期画像。年级、学科和 notes 立即生效；"
        "薄弱知识点和长期偏好需要在不同轮次中出现两次才会写入长期画像，"
        "以区分一次性诉求和稳定偏好。"
        "weak_points 和 preferences 的键必须取自 profile_schema Skill 的词表——"
        "自造键名会导致频次永远累积不到阈值，偏好静默失效；"
        "确实没有合适的键时，请写进 notes 而不是新造一个键。"
        "用户明确提到个人学情信息时调用"
    )
    args_schema: Type[BaseModel] = UserProfileSaveInput

    def _run(self, *args, **kwargs):
        raise NotImplementedError("UserProfileSaveTool 仅支持异步调用，请使用 _arun")

    async def _arun(
        self,
        user_id: Optional[int] = None,
        grade: Optional[str] = None,
        subject: Optional[str] = None,
        weak_points: Optional[dict] = None,
        preferences: Optional[dict] = None,
        notes: Optional[list] = None,
    ) -> str:
        if user_id is None:
            return "【用户画像】保存失败：缺少 user_id"

        incoming = {
            "grade": grade, "subject": subject, "notes": notes,
            "weak_points": weak_points, "preferences": preferences,
        }
        try:
            mapper = await get_user_profile_mapper()
            stm = await get_short_term_memory()
            ltm = LongTermMemory(mapper, stm)

            to_write: dict = {
                field: incoming[field]
                for field in _DIRECT_FIELDS
                if incoming[field]
            }

            # 偏好类逐个子键过候选池，只有晋升的才进写库集合
            pending: list[str] = []   # 已记录、还没到阈值
            unrecorded: list[str] = []  # 候选池不可用，这次压根没记上
            for field in _PREFERENCE_FIELDS:
                payload = incoming[field]
                if not payload:
                    continue
                promoted: dict = {}
                for sub_key, value in payload.items():
                    times, is_promoted = await _candidate_store.submit_one_candidate(
                        user_id, field, sub_key, value
                    )
                    if is_promoted:
                        promoted[sub_key] = value
                    elif times < 0:
                        # Redis 不可用时 submit_one_candidate 返回 -1，不能当成
                        # 正常计数汇报出去，否则模型会看到「题目风格（-1/2）」
                        unrecorded.append(sub_key)
                    else:
                        # 带上这次提交的值：同键冲突以新值为准，所以池子里存的就是它。
                        # 模型在同一轮对话里能从自己上一次的 observation 看到措辞，
                        # 下次更容易用同样的说法——但这只是辅助，真正保证「值相同」
                        # 的是词表的值域约束（见 Task 10）
                        pending.append(
                            f"{sub_key}={value}（{times}/{_candidate_store.times_to_submit}）"
                        )
                if promoted:
                    to_write[field] = promoted

            if to_write:
                await ltm.add_or_update(LTMRequest(user_id=user_id, **to_write))

            # 如实汇报三种状态。含糊其辞的话，Agent 会以为候选偏好已经生效，
            # 转头就告诉用户「已经记住了」——下次用户发现没生效，会认为系统在撒谎
            parts = []
            if to_write:
                parts.append(f"已写入长期画像：{'、'.join(to_write)}")
            if pending:
                parts.append(f"已记录候选偏好，再次提到时写入：{'、'.join(pending)}")
            if unrecorded:
                parts.append(
                    f"以下偏好本次未能记录（候选池暂时不可用），请提醒用户稍后再说一次："
                    f"{'、'.join(unrecorded)}"
                )
            if not parts:
                return "【用户画像】本次没有可保存的内容"
            return "【用户画像】" + "；".join(parts)
        except Exception as e:
            # 工具的契约是「返回字符串」，不能把异常抛回 ReAct 图里让整轮对话失败。
            # 但必须落日志：只返回字符串的话，开发者在日志里什么都看不到
            logger.error("user_profile_save_tool 保存用户 %s 的画像失败: %s",
                         user_id, e, exc_info=True)
            return f"【用户画像】保存失败：{str(e)}"
