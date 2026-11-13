"""
所有的Agent请求最先发到这里，通过ReAct进行统一调度
具体功能：
1. 调用tools，查询记忆模块，并写入记忆
2. 调用LangGraph中的Node，完成任务执行
3. 设定一个最大迭代轮次，超过之后，就只接关停
"""
import inspect
import json

from langgraph.graph import StateGraph, END
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

from backend.agents.agent.get_llm import get_llm
from backend.agents.agent.tools import GraphState
from backend.agents.tools import TOOLS, TOOL_MAP, get_tool_prompt
from backend.agents.skills import get_skill_list_prompt, match_triggers
from backend.middleware.logging import get_logger

logger = get_logger(__name__)

# ----------------------
# 0. 预算（budget）：一次请求内每个工具最多调几次、整轮最多跑几轮
# ----------------------

# 每个工具在一次请求内的最大调用次数。
#
# 为什么要有这个上限：模型对某个工具不满意时会反复调同一个（最典型的是
# question_set_tool —— 生成结果不合预期就再出一道）。没有上限时，一次请求
# 可能把同一个工具跑满 5 轮，白烧 token 还把用户等在那儿。
#
# 超限怎么办：不静默丢弃，也不报错中断。返回一条 observation 告诉模型
# 「这个工具已经用完预算了，别再调它」，让模型换个思路作答。模型下一轮
# 通常就会改用别的工具或直接给答案。
DEFAULT_MAX_TOOL_CALLS = 3

# 逐工具的次数上限。key 是工具名，value 是该工具的最大调用次数。
# 不在这个表里的工具用 DEFAULT_MAX_TOOL_CALLS。
#
# 例：question_set_tool 生成变式题，一次请求里出 3 道通常够用户挑了；
# 想更保守就调小，想让它多试几次就调大。
TOOL_MAX_CALLS: dict[str, int] = {
    "question_set_tool": DEFAULT_MAX_TOOL_CALLS,
}

# 一次请求内 ReAct 循环的最大轮次。
#
# 每轮 = 一次思考 + 最多一次工具调用。超了就收尾：模型还没给出答案时，
# 工具执行节点会拿到一个「已达轮次上限」的 observation，让它用现有信息作答。
#
# 从 5 提到 8：加了每工具次数上限之后，单个工具占不满全部预算，
# 轮次可以放宽一些，让模型有机会「这个工具不行，换一个」。
MAX_REACT_ROUNDS = 8


def get_max_tool_calls(tool_name: str) -> int:
    """某个工具在一次请求内的最大调用次数。未单独配置的用默认值。"""
    return TOOL_MAX_CALLS.get(tool_name, DEFAULT_MAX_TOOL_CALLS)


# 内部 observation 的前缀：这些是「系统告诉模型的情况」，不是工具的真实结果。
# 轮次用尽时要给用户一个最终回答，优先用真正的工具结果，别把系统的解释当答案。
_INTERNAL_OBSERVATION_PREFIXES = ("【工具预算】", "【工具执行】")


def format_tool_budget(counts: dict[str, int] | None, current_round: int) -> str:
    """
    把「每个工具还能调几次、还剩几轮」写进 system prompt。

    为什么要告诉模型：光在执行节点拦，模型是事后才知道「这个工具不能再用了」。
    提前说清楚，模型才能自己规划——比如第一次出的题不满意时，改调另一个工具
    或换个问法，而不是把同一个工具调到超限才被拦。
    """
    counts = counts or {}
    lines = [
        f"本次请求内每个工具的调用次数有限：默认上限 {DEFAULT_MAX_TOOL_CALLS} 次，"
        f"用完的工具不能再调用。"
    ]

    # 单独配过上限的工具要单独说明。只说「默认上限 N 次」，模型会以为
    # 所有工具都是同一个数——真给某个工具配了更大的额度时，它就该多调几次，
    # 而不是按默认值收手。
    overrides = {
        name: limit for name, limit in TOOL_MAX_CALLS.items()
        if limit != DEFAULT_MAX_TOOL_CALLS
    }
    if overrides:
        detail = "、".join(f"{name} 最多 {limit} 次" for name, limit in sorted(overrides.items()))
        lines.append(f"其中额度与默认不同的工具：{detail}。")

    used_lines: list[str] = []
    for name in sorted(counts):
        used = counts[name]
        limit = get_max_tool_calls(name)
        remaining = limit - used
        if remaining > 0:
            used_lines.append(f"- {name}：已用 {used}/{limit} 次，还可调用 {remaining} 次")
        else:
            used_lines.append(f"- {name}：已用满 {used}/{limit} 次，不要再调用它")
    if used_lines:
        lines.append("已用过的工具：")
        lines.extend(used_lines)

    remaining_rounds = MAX_REACT_ROUNDS - current_round
    lines.append(
        f"ReAct 循环最多 {MAX_REACT_ROUNDS} 轮，当前是第 {current_round} 轮，还剩 {remaining_rounds} 轮。"
    )
    if remaining_rounds <= 0:
        lines.append("这是最后一轮，请直接给出最终回答，不要再调用工具。")
    return "\n".join(lines)


def _observation_tool_name(message) -> str:
    """
    从 observation 的 tool_call_id 里取回工具名。

    tool_exec_node 写消息时用的是 f"{工具名}_{轮次}"，所以去掉最后一段
    "_轮次" 就是工具名。这里靠它把「剧本内容」和「给用户看的内容」分开。
    """
    tool_call_id = getattr(message, "tool_call_id", "") or ""
    if not isinstance(tool_call_id, str) or "_" not in tool_call_id:
        return ""
    return tool_call_id.rsplit("_", 1)[0]


def _final_answer_from_observations(messages) -> str:
    """
    轮次用尽、模型却还想调工具时，用已经拿到的观察拼出最终回答。

    模型在最后一轮请求的工具不会再执行了。与其让它返回空答案（用户看到一片
    空白），不如把已经拿到的结果交给用户。优先用真正的工具结果，跳过
    「工具预算」「工具执行」这类系统提示——用户要的是题目，不是系统的解释。

    一条观察都没有、或全是内部提示时，返回面向用户的说明。
    绝不能把「请不要再调用它，改用其他工具」这种写给模型的指令甩给用户。
    """
    if not messages:
        return "本次已达到最大处理轮次，没有拿到更多结果。"

    real = []
    for m in messages:
        content = getattr(m, "content", "")
        if not isinstance(content, str) or not content:
            continue
        if content.startswith(_INTERNAL_OBSERVATION_PREFIXES):
            continue
        # load_skill_tool 返回的是给模型看的剧本，不是给用户看的内容。
        # 拿它当最终回答，用户会收到一大段 Markdown 指令。
        if _observation_tool_name(m) == "load_skill_tool":
            continue
        real.append(content)

    if real:
        return real[-1]

    # 到这里说明拿到的全是「工具报错 / 缺参 / 预算用完」这类内部提示。
    # 它们是写给模型看的指令，原样发给用户等于在念后台日志，
    # 所以换成一句用户看得懂的说明。
    tool_hint = _observation_tool_name(messages[-1])
    if tool_hint and tool_hint != "load_skill_tool":
        return (
            f"本次没有得到有效结果：工具 {tool_hint} 调用出错或调用次数已达上限。"
            f"请换一种问法，或补充更具体的信息后再试一次。"
        )
    return "本次没有得到有效结果。请换一种问法，或补充更具体的信息后再试一次。"


def format_profile(profile) -> str:
    """
    把长期画像压成几行文本，供注入 system prompt；没有可用信息时返回空串。
    在 API 层调用一次，结果放进 GraphState，避免每轮 ReAct 都查一次库。

    - weak_points 的值是掌握程度（薄弱/一般/已掌握），必须连值一起列，
      只列键名会把「已掌握」的知识点误当成薄弱点。键值都取自词表，总量有上界。
    - notes 是自由文本、最多 50 条，不注入，只在 user_profile_query_tool 主动查询时返回。
    """
    if profile is None:
        return ""
    if hasattr(profile, "model_dump"):
        data = profile.model_dump()
    else:
        data = {key: getattr(profile, key, None)
                for key in ("grade", "subject", "weak_points", "preferences")}

    grade = data.get("grade") or ""
    subject = data.get("subject") or ""
    weak = data.get("weak_points") or {}
    prefs = data.get("preferences") or {}
    # 画像行存在但什么都没填时，「年级：未知 | 学科：未知」只会白占每轮的 token
    if not (grade or subject or weak or prefs):
        return ""

    lines = [f"年级：{grade or '未知'} | 学科：{subject or '未知'}"]
    if weak:
        lines.append("知识点掌握：" + "、".join(f"{k}={v}" for k, v in weak.items()))
    if prefs:
        lines.append("长期偏好：" + "、".join(f"{k}={v}" for k, v in prefs.items()))
    return "\n".join(lines)


def build_react_system_prompt(
    hint_skills: list[str] | None = None,
    profile_text: str = "",
    tool_budget: str = "",
) -> str:
    """每轮动态构建 ReAct 系统提示词。

    - `get_skill_list_prompt()` 每次重新扫目录 + 走 mtime 缓存，新增/删除 SKILL.md 下一轮即生效。
    - 若 `hint_skills` 非空，追加「命中提示」段，作为 LLM 语义漏触发的兜底。
    - 若 `profile_text` 非空，追加「学生画像」段：它是稳定背景，必须放 system 而不是 user_input，
      否则模型会把「薄弱点：函数」当成本次诉求。
    - 若 `tool_budget` 非空，追加「调用预算」段：告诉模型每个工具还能调几次、还剩几轮，
      让它自己规划而不是把同一个工具调到超限才被拦。
    """
    prompt = f"""# 角色
你是 ReAct 决策 Agent：基于 `user_input` 和 `messages` 上下文，循环「思考→行动」，决定调用工具或直接回答。

# 可用工具 (Tools)
只能从下列工具中选择，禁止编造不存在的工具：

{get_tool_prompt()}

# 可用 Skill（能力包，按需加载）
Skill 是存放在文件中的程序化剧本，不是可执行工具。当用户诉求命中某个 Skill 的触发词时，先用 `load_skill_tool` 加载对应 Skill 的剧本，再根据剧本指引决定下一步调哪个业务工具或直接作答。

{get_skill_list_prompt()}

# 规则
1. 业务问题必须通过工具获取数据，不得凭空编造。
2. 每轮只输出一次「思考+行动」，不要模拟工具返回；真实 Observation 由系统写入 `messages`。
3. 每次只调用一个 tool。
4. 决策必须结合 `messages` 中前序工具的 `content`，而非只看 `user_input`。
5. 命中 Skill 触发词时，优先用 `load_skill_tool` 加载剧本，再进入业务工具调用。
6. 若工具和自身能力均无法解答，`final_result` 填："我不能解答用户的问题"。

# 输出格式
只返回一段合法 JSON，禁止 Markdown 代码块或额外解释。

- 需调工具：`action`=工具名，`action_args`=参数，`final_result`=""
- 信息已充足：`action`=''，`action_args`={{}}，`final_result`=通俗易懂的最终回答

结构：
{{
    "thought": "分析上下文是否充足，确定下一步",
    "action": "工具名 或 null",
    "action_args": {{"参数名": "参数值"}},
    "final_result": "最终回答"
}}
"""
    if hint_skills:
        names = "、".join(hint_skills)
        prompt += (
            f"\n# 命中提示\n"
            f"系统检测到 `user_input` 字面命中以下 Skill 触发词：{names}。"
            f"强烈建议本轮优先调用 `load_skill_tool` 加载对应 Skill，再决定后续动作；"
            f"若 `messages` 中已有该 Skill 的剧本，则直接依剧本行事，无需重复加载。\n"
        )
    if profile_text:
        prompt += (
            f"\n# 学生画像\n"
            f"以下是该学生的稳定背景信息，用于让回答贴合其学情；"
            f"它不是本次的提问内容，不要直接复述。\n{profile_text}\n"
        )
    if tool_budget:
        prompt += (
            f"\n# 调用预算\n"
            f"{tool_budget}\n"
        )
    return prompt


def _normalize_action(value) -> str:
    """
    把 LLM 可能给出的 null / "null" / None / 空白统一成空字符串。
    否则 `None != ''` 恒为真，should_continue 会误判成"还要调工具"，
    一路空转到轮次上限才结束。
    """
    if value is None:
        return ''
    action = str(value).strip()
    if action.lower() in ('null', 'none', 'nil'):
        return ''
    return action


def _extract_json_object(content: str) -> str | None:
    """截取最外层 JSON 对象，兼容模型输出的 Markdown 围栏和前后缀说明。"""
    start = content.find('{')
    end = content.rfind('}')
    if start == -1 or end == -1 or end < start:
        return None
    return content[start:end + 1]


def _normalize_tool_args(raw) -> dict:
    """
    原生 tool_calls 的 args 正常已经是 dict（langchain 解析过一遍）。
    仍要兜一层：接口实现的差异会让它偶尔是 JSON 字符串或 None，
    直接 **kwargs 展开会抛 TypeError，整轮请求 500。
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("工具参数不是合法 JSON，按空参数处理：%r", raw[:200])
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_native_tool_call(message) -> tuple[str, dict] | None:
    """
    取原生 tool_calls 里的第一个调用，返回 (工具名, 参数)；没有就返回 None。

    为什么必须有这条路径：react_think_node 调的是 bind_tools(TOOLS)，
    这等于告诉模型「可以用接口层的 function calling」。模型真这么用的时候，
    content 是空的，决策全在 tool_calls 里。以前只读 content，
    一次正确的决策会被当成「模型没说话」，直接落进兜底回答。

    只取第一个：图是「一次思考对应一次行动」的循环，提示词里也写了
    每次只调一个工具。模型一次给多个时，多出来的记日志，不并发执行。
    """
    tool_calls = getattr(message, "tool_calls", None) or []
    if not tool_calls:
        return None

    call = tool_calls[0]
    if isinstance(call, dict):
        name = call.get("name")
        args = call.get("args")
    else:
        name = getattr(call, "name", None)
        args = getattr(call, "args", None)
    if not name:
        return None
    return str(name), _normalize_tool_args(args)


def _thought_from_content(content) -> str:
    """
    从正文里尽量取一句思考过程。原生 tool_calls 那一路 content 常常是空的，
    但也可能夹着推理文字或我们这套 JSON，取出来当 thought 用。
    """
    if not isinstance(content, str) or not content.strip():
        return ""
    raw = _extract_json_object(content)
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return str(parsed.get("thought") or "")
        # 不是我们这套 JSON：正文当普通说明处理
        return content.strip()
    return content.strip()


def _parse_react_message(message) -> dict:
    """
    解析一次 LLM 响应，统一成 {thought, action, action_args, final_result}。

    两种表达方式都认：
    1. 原生 tool_calls（接口层的 function calling）：决策在 message.tool_calls 里
    2. 正文里的 JSON：{thought, action, action_args, final_result}

    两者都有时以 tool_calls 为准：它是结构化的决策，参数也经过 schema 约束，
    比正文里的自由 JSON 更可信。正文里的 thought 仍然会取出来。
    """
    native = _parse_native_tool_call(message)
    if native is not None:
        name, args = native
        content = getattr(message, "content", "")
        return {
            "thought": _thought_from_content(content) or f"模型请求调用工具 {name}",
            "action": name,
            "action_args": args,
            "final_result": "",
        }
    return _parse_react_response(getattr(message, "content", ""))


def _parse_react_response(content) -> dict:
    """
    解析 LLM 的 ReAct 输出。任何异常都降级成"一次终止回答"，
    而不是让 json.loads 抛异常把整张图打断。
    返回的 dict 保证 4 个键齐全。
    """
    fallback = {
        'thought': '模型输出解析失败',
        'action': '',
        'action_args': {},
        'final_result': "我不能解答用户的问题",
    }

    if not isinstance(content, str) or not content.strip():
        logger.error("LLM 未返回文本内容：%r", content)
        return fallback

    raw = _extract_json_object(content)
    if raw is None:
        logger.error("LLM 输出中找不到 JSON 对象：%s", content[:300])
        return fallback

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("LLM 输出 JSON 解析失败：%s | 原文：%s", e, content[:300])
        return fallback

    if not isinstance(parsed, dict):
        logger.error("LLM 输出不是 JSON 对象：%s", content[:300])
        return fallback

    action_args = parsed.get('action_args') or {}
    if not isinstance(action_args, dict):
        logger.warning("action_args 不是对象，已忽略：%r", action_args)
        action_args = {}

    return {
        'thought': parsed.get('thought') or '',
        'action': _normalize_action(parsed.get('action')),
        'action_args': action_args,
        'final_result': parsed.get('final_result') or '',
    }


async def react_think_node(state: GraphState) -> dict:
    """LLM思考：是否调用Tool、调用哪个"""
    hint_skills = match_triggers(state['user_input'])
    next_round = state['round'] + 1
    # 把预算告诉模型，让它自己规划：还剩几轮、每个工具还能调几次。
    tool_budget = format_tool_budget(state.get('tool_call_counts'), next_round)
    system_text = build_react_system_prompt(
        hint_skills, state.get('profile_text', ''), tool_budget
    )

    llm_input = {
        "user_input": state['user_input'],
        "messages": state['messages'],
        "user_id": state['user_id'],
        "session_id": state['session_id'],
    }
    llm = get_llm().bind_tools(TOOLS)
    response_message = await llm.ainvoke([
        SystemMessage(content=system_text),
        HumanMessage(content=json.dumps(llm_input, ensure_ascii=False, default=str)),
    ])
    response = _parse_react_message(response_message)

    # 模型走了原生 function calling：这一步的决策是有效的，不该被当成解析失败
    if response["action"]:
        invalid = getattr(response_message, "invalid_tool_calls", None) or []
        if invalid:
            logger.warning("模型返回了 %s 个无法解析的 tool_calls：%s", len(invalid), invalid)

    # 既没选工具又没给答案时强制收尾，避免向用户返回空串
    if not response['action'] and not response['final_result']:
        logger.warning("模型既未选择工具也未给出最终回答，强制结束本轮")
        response['final_result'] = "我不能解答用户的问题"

    # 最后一轮：模型还想调工具，但已经没有下一轮给它调了。
    # 与其让图在 END 处带着空的 final_result 结束（用户看到一片空白），
    # 不如把已经拿到的观察整理成最终回答。
    if response['action'] and next_round >= MAX_REACT_ROUNDS:
        logger.info(
            "已达轮次上限 %s，模型仍请求调用 %s，改用已有观察作答",
            MAX_REACT_ROUNDS, response['action'],
        )
        response['action'] = ''
        response['action_args'] = {}
        # 模型这一轮同时给了答案的话，以它的为准 —— 那是它自己写的，
        # 比我们从观察里拼出来的更贴题。观察拼出来的只当兜底。
        if not response['final_result']:
            response['final_result'] = _final_answer_from_observations(state.get('messages'))

    round = next_round
    logger.info("轮次: %s", round)
    logger.debug("命中触发词 Skill: %s", hint_skills)
    logger.debug("输入: %s", llm_input)
    logger.debug("思考结果: %s", response["thought"])
    logger.info("调用tool: %s", response["action"])
    logger.debug("调用tool参数: %s", response["action_args"])
    logger.debug("messages: %s", state['messages'])
    logger.info("最终回答final_result: %s", response["final_result"])

    return {
        'thought': response["thought"],
        'action': response["action"],
        'action_args': response["action_args"],
        'round': round,
        'final_result': response["final_result"],
    }

# ----------------------
# 6. Tool 执行节点（手脚）
# ----------------------
def _tool_accepted_params(tool) -> tuple[set[str] | None, set[str]]:
    """
    工具 _arun 真正接受的参数名，以及其中必填的那些。

    直接看 _arun 的签名，而不是 args_schema：args_schema 是给模型看的，
    不含运行时注入的 user_id/session_id，而 _arun 收它们。
    签名比 schema 宽，比「直接把 **args 展开」准。

    返回 (accepted, required)。accepted 为 None 表示「判断不了」——
    签名读不出来，或者工具是 **kwargs 兜底的。这两种情况下不筛参数，
    由执行节点后面的 try/except 兜底。
    """
    try:
        params = inspect.signature(tool._arun).parameters
    except (TypeError, ValueError):
        return None, set()

    accepted: set[str] = set()
    required: set[str] = set()
    takes_var_kwargs = False
    for name, param in params.items():
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            takes_var_kwargs = True
            continue
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            continue
        accepted.add(name)
        if param.default is inspect.Parameter.empty:
            required.add(name)

    if takes_var_kwargs:
        # **kwargs 兜底的工具不筛参数
        return None, set()
    return accepted, required


async def tool_exec_node(state: GraphState) -> dict:
    """执行LLM选择的Tool"""
    func_name = state['action']
    # 复制一份再改：state['action_args'] 不该被这里就地改写
    args = dict(state.get('action_args') or {})

    # 每个工具在本次请求内已被调用的次数。计数在这里改，不在思考节点改：
    # 只有真正走到执行节点的调用才算数，模型「想调」但被路由规则拦下的不算。
    counts = dict(state.get('tool_call_counts') or {})
    used = counts.get(func_name, 0)
    limit = get_max_tool_calls(func_name)

    tool = TOOL_MAP.get(func_name)
    if tool is None:
        res = f"未知工具：{func_name}"
    elif used >= limit:
        # 这个工具的预算用完了。不执行、不报错，把「用完了」当成 observation
        # 告诉模型，让它换个思路（换工具或直接作答）而不是继续调。
        logger.info(
            "工具 %s 本次请求已调用 %s 次，达到上限 %s 次，不再执行",
            func_name, used, limit,
        )
        res = (
            f"【工具预算】{func_name} 在本次请求中已经调用 {used} 次，达到上限 {limit} 次，"
            f"不再执行。请不要再调用它，改用其他工具，或者基于已有信息直接给出最终回答。"
        )
    else:
        # 身份先注入，再做参数检查：注入的参数本身也在 _arun 签名里，
        # 顺序反了会把「工具需要 user_id」误判成「模型漏传了 user_id」。
        # 注入是覆盖写，模型自己填的 user_id 也一律以 state 为准。
        if func_name == "query_memory_tool":
            args['user_id'] = state['user_id']
            args['session_id'] = state['session_id']

        if func_name in (
            "user_profile_save_tool",
            "user_profile_query_tool",
            "user_profile_delete_tool",
        ):
            args['user_id'] = state['user_id']

        accepted, required = _tool_accepted_params(tool)

        # 模型多传的参数：丢掉，不报错。LangChain 走 args_schema 校验时也是这个行为
        # （pydantic 默认忽略多余字段），这里保持一致，免得为了一个无关键白费一轮。
        # accepted 为 None 表示判断不了签名，这时一个参数都不筛。
        if accepted is not None:
            extra = [k for k in args if k not in accepted]
            if extra:
                logger.info("工具 %s 收到多余参数 %s，已忽略", func_name, extra)
                for key in extra:
                    args.pop(key, None)

        # 必填参数缺失：工具跑不起来，把问题交回给模型让它下一轮改
        missing = sorted(k for k in required if k not in args)
        if missing:
            res = (
                f"【工具执行】调用 {func_name} 缺少必填参数：{'、'.join(missing)}。"
                f"请补齐这些参数后重新调用。"
            )
        else:
            # 图节点本身在事件循环中运行，直接 await 异步实现，避免 _run 的 sync/async 桥接
            try:
                res = await tool._arun(**args)
            except Exception as e:
                # 工具的契约是「永远返回字符串」。这里兜住没在自己 try 里的异常
                # （common_tool 就没有），不让它一路冒出图把整轮对话打成 500。
                # 异常原文不发给模型：SQLAlchemy 的报错带着整条 SQL 和参数。
                logger.error("工具 %s 执行失败: %s", func_name, e, exc_info=True)
                res = f"【工具执行】{func_name} 执行出错，本次没有拿到结果。请换个方式再试一次。"

        # 真正执行了才计数。参数校验没过、工具报错，也算「调过了」——
        # 模型如果一直用错参数重试，那正是要限制的场景。
        counts[func_name] = used + 1

    messages = state['messages']
    messages.append(
        ToolMessage(content=res,
                    tool_call_id=f"{func_name}_{state['round']}"))

    return {
        'messages': messages,
        'tool_call_counts': counts,
    }

# ----------------------
# 7. 路由决策：是否继续ReAct循环
# ----------------------
def should_continue(state: GraphState) -> str:
    """
    自主决策：
    - 还有工具要调用且未超上限 → 回到执行节点
    - 没有 → 结束，回答用户

    轮次上限是 MAX_REACT_ROUNDS。轮次用尽时不再调工具，由 react_think_node
    在最后一轮把已有观察整理成最终回答（见 _final_answer_from_observations）。
    """
    logger.debug("正在执行should_continue_node")
    # 必须用真值判断：action 为 None 时 `None != ''` 恒真，会一直空转到轮次上限
    if state['action'] and state['round'] < MAX_REACT_ROUNDS:
        return "execute_tool"
    return END

# ----------------------
# 8. 构建 LangGraph 图
# ----------------------
workflow = StateGraph(GraphState) #type:ignore

# 添加节点

workflow.add_node("react_think", react_think_node) #type:ignore
workflow.add_node("execute_tool", tool_exec_node) #type:ignore

# 入口
workflow.set_entry_point("react_think")

# 条件边：自主决定下一步！
workflow.add_conditional_edges(
    "react_think",
    should_continue
)

# 执行完工具 → 回到思考
workflow.add_edge("execute_tool", "react_think")

# 编译
def get_app():
    app = workflow.compile()
    return app
