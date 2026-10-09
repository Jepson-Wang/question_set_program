from typing import Type, Optional

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import exc as sa_exc

from backend.agents.memory.long_term_memory import LongTermMemory
from backend.agents.memory.profile_candidates import ProfileCandidate
from backend.agents.memory.short_term_memory import get_short_term_memory
from backend.dao.exceptions import ProfileNotFoundError
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

# 与 user_profile.grade / subject 的 String(32) 一致
_SHORT_TEXT_MAX = 32

# 落库失败时，换了数据库连接或等锁释放后重试有可能成功的错误
_TRANSIENT_DB_ERRORS = (sa_exc.OperationalError, sa_exc.TimeoutError, ProfileNotFoundError)


class UserProfileSaveInput(BaseModel):
    grade: Optional[str] = Field(
        default=None, max_length=_SHORT_TEXT_MAX, description="用户年级，例如 '七年级'"
    )
    subject: Optional[str] = Field(
        default=None, max_length=_SHORT_TEXT_MAX, description="主修学科，例如 '数学'"
    )
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

        # 必须在进候选池之前校验：tool_exec_node 直接调 _arun，绕过了 LangChain 对
        # args_schema 的校验。放到落库时让数据库报 DataError 就晚了——偏好已经计过一次数，
        # 模型修正后重新调用会再计一次，用户只说了一次的偏好就这样被凑够了阈值
        try:
            UserProfileSaveInput(**incoming)
        except ValidationError as e:
            # 逐个字段给出 pydantic 的原因，不能一律提示「超长」：
            # 模型把 weak_points 传成字符串时，那样的提示会让它去改错地方
            problems = "；".join(f"{err['loc'][0]}：{err['msg']}" for err in e.errors())
            return f"【用户画像】保存失败，参数不合法（{problems}），本次什么都没有记录，修正后重新调用即可"

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
                # 先落库、后清候选：落库抛了异常就保留候选，交给用户下次提到时再晋升一次。
                # 拿不准有没有写进去也按失败算——多晋升一次由 merge_json_fields 的幂等消化，
                # 而先清候选再落库失败，偏好就永久丢了
                try:
                    await ltm.add_or_update(LTMRequest(user_id=user_id, **to_write))
                except sa_exc.DataError:
                    # 入口已经校验过长度，走到这里说明有校验没覆盖到的约束，重试也会一直失败
                    logger.error("user_profile_save_tool 用户 %s 的画像数据不符合数据库约束",
                                 user_id, exc_info=True)
                    return _write_failed_report(
                        to_write, "数据不符合数据库约束，重试也会失败，请不要原样重新调用"
                    )
                except _TRANSIENT_DB_ERRORS:
                    logger.error("user_profile_save_tool 用户 %s 的画像落库失败（瞬时错误）",
                                 user_id, exc_info=True)
                    return _write_failed_report(to_write, "数据库暂时不可用")
                else:
                    # 只清理这次真正晋升的子键。还在计数的候选不在 to_write 里，必须留着
                    to_delete = [
                        (field, sub_key)
                        for field in _PREFERENCE_FIELDS
                        for sub_key in (to_write.get(field) or {})
                    ]
                    if to_delete:
                        await _candidate_store.delete_field_by_given(user_id, to_delete)

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
            # 不回显 str(e)：SQLAlchemy 的异常文本带着整条 SQL 和参数，会进模型上下文
            return "【用户画像】保存失败：系统内部错误，本次没有保存"


def _write_failed_report(to_write: dict, reason: str) -> str:
    """
    落库失败时的汇报。两类字段的后果不一样，必须分开说：
    偏好的候选还在，下次提到时会再试；直写字段没有候选池兜底，这次就是丢了。
    """
    parts = [f"【用户画像】写入长期画像失败（{reason}）"]
    kept = [field for field in _PREFERENCE_FIELDS if field in to_write]
    lost = [field for field in _DIRECT_FIELDS if field in to_write]
    if kept:
        parts.append(f"{'、'.join(kept)} 的候选已保留，用户下次提到时会再次尝试写入")
    if lost:
        parts.append(f"{'、'.join(lost)} 本次没有保存，请提醒用户稍后再说一次")
    return "；".join(parts)
