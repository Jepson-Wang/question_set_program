# 三层记忆架构改造 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把短期记忆从「JSON 字符串 RMW」换成「Redis LIST + Lua 原子淘汰 + 后台归档」，消除并发丢消息与摘要卡顿；同时打通长期画像与向量层的读取链路，并清掉事件循环里的阻塞点。

**Architecture:** 短期窗口用 Redis LIST 存储，「入队 + 超限淘汰」在一次 Lua EVAL 里原子完成，被淘汰的条目通过 `RPOPLPUSH` 原子搬进 pending 队列后由后台 `asyncio.Task` 做 LLM 精炼并写入 Chroma——因为被归档的条目已经物理移出窗口，读路径看不到它，所以不需要任何锁。画像走「事实类直写、偏好类候选池二次命中才晋升」，向量层只在生题/问答工具执行前定点检索注入。

**Tech Stack:** Python 3.14.3 / FastAPI 0.135.1 / LangGraph 1.0.10 / langchain-core 1.2.17 / redis 7.3.0 / SQLAlchemy 2.0.48 / llama-index-core 0.14.18 / chromadb 1.5.5 / pytest 9.0.2

**Spec:** `docs/superpowers/specs/2026-09-08-memory-architecture-design.md`

## Global Constraints

- 部署形态为**单机单进程**，互斥一律用进程内机制，禁止引入 Redis 分布式锁。
- 所有 I/O（DB、Redis、LLM、向量库）保持 async；新增代码不得在事件循环里做同步阻塞调用。
- Lua 脚本内使用 `RPOPLPUSH` 而非 `LMOVE`，以兼容 Redis 6.2 以下版本。
- 短期窗口 key：`stm:{user_id}:{session_id}`；待归档队列 key：`stm:pending:{user_id}:{session_id}`。
- 窗口 TTL `86400` 秒；pending 队列 TTL `604800` 秒；画像候选池 TTL `604800` 秒。
- `max_memory_size` 默认 `10`；画像候选池晋升阈值 `promote_threshold` 默认 `2`。
- 向量检索相似度阈值 `min_score` 默认 `0.3`，`top_k` 默认 `3`。
- 向量库专属线程池 `max_workers=4`，`thread_name_prefix="vec"`。
- 不写数据迁移脚本：短期记忆换 key 前缀，旧数据靠 24h TTL 自然过期。
- 提交信息用中文，遵循 `feat:` / `fix:` / `refactor:` / `test:` 前缀。

## 任务顺序与依赖

三个阶段有严格顺序：

- **阶段一（Task 1-4，对应设计文档第三节）** 阻塞点清理。独立、低风险、先落地缩小后续 diff。
- **阶段二（Task 5-7，对应设计文档第二节）** 短期记忆 LIST 化。**必须在阶段三之前**，因为它改变了 `ShortTermMemory` 的接口。
- **阶段三（Task 8-13，对应设计文档第一节）** 三层接入策略。依赖阶段二的新接口。

Task 0 是测试基建，必须最先做。

## 文件结构

**新建**

| 文件 | 职责 |
|---|---|
| `backend/tests/conftest.py` | pytest fixture：测试用 Redis 客户端（db 15）、自动清库 |
| `backend/pytest.ini` | pytest 配置，开启 asyncio 自动模式 |
| `backend/core/executors.py` | 集中管理进程级线程池，提供获取与关停入口 |
| `backend/agents/memory/profile_candidates.py` | 画像候选池：偏好频次统计与晋升判定 |
| `backend/agents/skills/profile_schema/SKILL.md` | 画像维度词表（热加载，改词表不用改代码） |
| `backend/tests/test_short_term_memory.py` | 短期记忆 LIST 化与原子淘汰的测试 |
| `backend/tests/test_memory_manager.py` | 后台归档、ack、启动恢复、关停的测试 |
| `backend/tests/test_profile_candidates.py` | 候选池与画像写入判定的测试 |
| `backend/tests/test_vector_retrieve.py` | 向量检索阈值过滤与降级的测试 |
| `backend/tests/test_react_agent_async.py` | ReAct 节点异步化与画像注入的测试 |
| `backend/tests/test_tools_sync_disabled.py` | 同步 `_run` 路径已禁用的测试 |
| `backend/tests/test_profile_merge.py` | JSON 字段合并与 notes 追加的测试 |
| `backend/tests/test_profile_schema_skill.py` | 词表加载与词表外键分流的测试 |
| `backend/tests/test_recall_injection.py` | 向量召回格式化与工具注入的测试 |

**修改**

| 文件 | 改动 |
|---|---|
| `backend/agents/memory/short_term_memory.py` | 全面重写为 Redis LIST + Lua 脚本 |
| `backend/agents/memory/memory_manager.py` | 归档转后台任务，新增 ack / 启动恢复 / 关停 |
| `backend/agents/memory/vector_store_manager.py` | 新增 `retrieve()`，改用专属线程池 |
| `backend/agents/agent/react_agent.py` | 节点改 async；system prompt 增加画像段 |
| `backend/agents/agent/get_llm.py` | 显式 `load_dotenv` |
| `backend/agents/agent/tools.py` | `GraphState` 新增 `profile_text` 字段 |
| `backend/agents/agent/common_agent.py` | 删除同步 `common_tool` |
| `backend/agents/agent/extract_agent.py` | 删除同步 `extract_tool` |
| `backend/agents/agent/question_set_agent.py` | 删除同步 `question_set_tool`；新增向量注入 |
| `backend/agents/tools/common_tool.py` | `_run` 禁用；`_arun` 前置向量检索 |
| `backend/agents/tools/extract_knowledge_tool.py` | `_run` 禁用 |
| `backend/agents/tools/question_set_tool.py` | `_run` 禁用；`_arun` 前置向量检索 |
| `backend/agents/tools/user_profile_save_tool.py` | 接入候选池；按词表分流，词表外的键转存 `notes` |
| `backend/agents/tools/user_profile_query_tool.py` | 返回结果补上 `notes` |
| `backend/agents/skills/skill_runner.py` | 新增 `load_vocab`，读取 SKILL.md 里的词表 |
| `backend/model/user_profile.py` | 新增 `notes` JSON 列 |
| `backend/schemas/request/ltm_request.py` | 新增 `notes` 字段 |
| `backend/schemas/response/user_profile_response.py` | 新增 `notes` 字段 |
| `backend/agents/memory/long_term_memory.py` | 新建画像时填充 `notes` |
| `backend/dao/user_profile_mapper.py` | JSON 字段改合并写入；`notes` 追加去重限长 |
| `backend/core/hooks.py` | 启动恢复 pending；关停等待归档并关闭线程池 |
| `backend/api/user_api/agent_api.py` | 填充 `profile_text`；两个端点同步改动 |
| `backend/requirements.txt` | 转为 UTF-8；新增 `pytest-asyncio` |

---

## Task 0: 测试基建

**Files:**
- Create: `backend/pytest.ini`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_smoke.py`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Consumes: 无
- Produces: pytest fixture `redis_test_client`（`redis.asyncio.Redis`，指向 db 15，每个测试前后自动 `flushdb`）；fixture `stm_factory`（`Callable[..., ShortTermMemory]`，Task 5 起使用）

- [ ] **Step 1: 把 requirements.txt 转成 UTF-8 并加入测试依赖**

现在这个文件是 UTF-16（PowerShell `pip freeze >` 的产物），`grep` 和部分 `pip` 版本读不动。

```bash
cd backend
python -c "
import pathlib
p = pathlib.Path('requirements.txt')
lines = [l.strip() for l in p.read_text(encoding='utf-16').splitlines() if l.strip()]
if not any(l.lower().startswith('pytest-asyncio') for l in lines):
    lines.append('pytest-asyncio')
p.write_text('\n'.join(sorted(lines, key=str.lower)) + '\n', encoding='utf-8')
print('ok, lines =', len(lines))
"
pip install pytest-asyncio
```

装完后把实际版本回填进 requirements.txt（不要凭记忆写版本号）：

```bash
pip show pytest-asyncio | grep -i version
```

- [ ] **Step 2: 写 pytest 配置**

创建 `backend/pytest.ini`：

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
python_files = test_*.py
filterwarnings =
    ignore::UserWarning:pydantic.*
```

`asyncio_mode = auto` 让 `async def test_*` 不用逐个加 `@pytest.mark.asyncio` 装饰器。

- [ ] **Step 3: 写 conftest.py**

测试打真实 Redis 的 db 15（不是 fakeredis）。原因：本计划的核心是一段 Lua 脚本，fakeredis 的 Lua 支持依赖 `lupa` 且覆盖不全，用真实 Redis 才能验证正确性。Redis 连不上时整个测试文件跳过，而不是报错。

创建 `backend/tests/conftest.py`：

```python
import os

import pytest
import pytest_asyncio
import redis.asyncio as aioredis

TEST_DB = 15


def _test_redis_url() -> str:
    host = os.getenv("REDIS_HOST", "localhost")
    port = os.getenv("REDIS_PORT", "6379")
    password = os.getenv("REDIS_PASSWORD")
    username = os.getenv("REDIS_USERNAME")
    if password and username:
        return f"redis://{username}:{password}@{host}:{port}/{TEST_DB}"
    if password:
        return f"redis://:{password}@{host}:{port}/{TEST_DB}"
    return f"redis://{host}:{port}/{TEST_DB}"


@pytest_asyncio.fixture
async def redis_test_client():
    """指向 db 15 的独立客户端；每个测试前后各清一次库。"""
    client = aioredis.from_url(
        _test_redis_url(), encoding="utf-8", decode_responses=True
    )
    try:
        await client.ping()
    except Exception as e:
        await client.aclose()
        pytest.skip(f"测试需要可用的 Redis（db {TEST_DB}）：{e}")

    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()
```

注意：`load_dotenv` 由被测模块自己负责（Task 1 会保证这点），conftest 不重复加载。

- [ ] **Step 4: 写冒烟测试确认基建可用**

创建 `backend/tests/test_smoke.py`：

```python
async def test_redis_fixture_is_isolated(redis_test_client):
    await redis_test_client.set("k", "v")
    assert await redis_test_client.get("k") == "v"


async def test_asyncio_auto_mode_works():
    import asyncio
    await asyncio.sleep(0)
    assert True
```

- [ ] **Step 5: 运行测试**

Run: `cd backend && python -m pytest tests/test_smoke.py -v`
Expected: 2 passed（Redis 不可用时第一个 skip、第二个 pass）

- [ ] **Step 6: 提交**

```bash
git add backend/pytest.ini backend/tests/conftest.py backend/tests/test_smoke.py backend/requirements.txt
git commit -m "test: 搭建 pytest 异步测试基建并修正 requirements 编码"
```

---

## Task 1: get_llm 显式加载环境变量

**Files:**
- Modify: `backend/agents/agent/get_llm.py:1-20`
- Create: `backend/tests/test_get_llm_env.py`

**Interfaces:**
- Consumes: 无
- Produces: 模块级变量 `api_key`、`base_url`、`model`、`embedding_model` 在模块导入后必然有值（不依赖其他模块先调 `load_dotenv`）

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/test_get_llm_env.py`：

```python
import importlib


def test_get_llm_loads_env_by_itself(monkeypatch):
    """清掉环境变量后重载模块，值应当由 get_llm 自己从 .env 读回来。"""
    for key in ("API_KEY", "API_URL", "MODEL_NAME", "EMBEDDING_MODEL"):
        monkeypatch.delenv(key, raising=False)

    import backend.agents.agent.get_llm as m
    importlib.reload(m)

    assert m.api_key, "get_llm 必须自己 load_dotenv，不能依赖导入顺序"
    assert m.base_url, "base_url 同样必须自己加载"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_get_llm_env.py -v`
Expected: FAIL，`assert m.api_key` 为 None

- [ ] **Step 3: 实现**

在 `backend/agents/agent/get_llm.py` 顶部，`os.getenv` 调用**之前**插入：

```python
from pathlib import Path

from dotenv import load_dotenv

# 显式指定 .env 绝对路径：不依赖当前工作目录，也不依赖其他模块先完成加载
load_dotenv(Path(__file__).resolve().parents[2] / ".env")
```

`parents[2]` 从 `backend/agents/agent/get_llm.py` 上溯到 `backend/`。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_get_llm_env.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/agents/agent/get_llm.py backend/tests/test_get_llm_env.py
git commit -m "fix: get_llm 显式加载 .env，消除模块导入顺序依赖"
```

---

## Task 2: react_think_node 改为异步

**Files:**
- Modify: `backend/agents/agent/react_agent.py`（`react_think_node` 定义与 LLM 调用处）
- Create: `backend/tests/test_react_agent_async.py`

**Interfaces:**
- Consumes: `_parse_react_response(content) -> dict`（已存在）
- Produces: `async def react_think_node(state: GraphState) -> dict`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/test_react_agent_async.py`：

```python
import inspect
from types import SimpleNamespace

from backend.agents.agent import react_agent


def _state(user_input="解方程 2x+9=5x-3"):
    return {
        "user_input": user_input,
        "user_id": 1,
        "session_id": 1,
        "thought": "",
        "action": "",
        "action_args": {},
        "messages": [],
        "round": 0,
        "final_result": "",
    }


class _FakeLLM:
    """记录被调用的是同步还是异步入口。"""

    def __init__(self, content: str, recorder: dict):
        self._content = content
        self._recorder = recorder

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self._recorder["sync"] = True
        return SimpleNamespace(content=self._content)

    async def ainvoke(self, messages):
        self._recorder["async"] = True
        return SimpleNamespace(content=self._content)


def test_react_think_node_is_coroutine_function():
    assert inspect.iscoroutinefunction(react_agent.react_think_node), (
        "react_think_node 必须是 async，否则 LangGraph 会把它丢进默认线程池，"
        "和向量库操作抢同一个池"
    )


async def test_react_think_node_uses_ainvoke(monkeypatch):
    recorder = {}
    content = '{"thought":"够了","action":"","action_args":{},"final_result":"答案是 x=4"}'
    monkeypatch.setattr(
        react_agent, "get_llm", lambda *a, **kw: _FakeLLM(content, recorder)
    )

    out = await react_agent.react_think_node(_state())

    assert recorder == {"async": True}, "不允许走同步 invoke"
    assert out["final_result"] == "答案是 x=4"
    assert out["action"] == ""
    assert out["round"] == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_react_agent_async.py -v`
Expected: FAIL，`test_react_think_node_is_coroutine_function` 报断言失败

- [ ] **Step 3: 实现**

在 `backend/agents/agent/react_agent.py` 中，把函数签名和 LLM 调用改掉：

```python
async def react_think_node(state: GraphState) -> dict:
    """LLM思考：是否调用Tool、调用哪个"""
    hint_skills = match_triggers(state['user_input'])
    system_text = build_react_system_prompt(hint_skills)

    llm_input = {
        "user_input": state['user_input'],
        "messages": state['messages'],
        "user_id": state['user_id'],
        "session_id": state['session_id'],
    }
    llm = get_llm().bind_tools(TOOLS)
    # LLM 调用是纯 IO，直接 await，不占用线程池
    response_message = await llm.ainvoke([
        SystemMessage(content=system_text),
        HumanMessage(content=json.dumps(llm_input, ensure_ascii=False, default=str)),
    ])
    response = _parse_react_response(response_message.content)
```

函数体其余部分不变。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_react_agent_async.py -v`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add backend/agents/agent/react_agent.py backend/tests/test_react_agent_async.py
git commit -m "refactor: react_think_node 改为异步，LLM 调用不再占用线程池"
```

---

## Task 3: 向量库使用专属有界线程池

**Files:**
- Create: `backend/core/executors.py`
- Modify: `backend/agents/memory/vector_store_manager.py`（三处 `run_in_executor(None, ...)`）
- Create: `backend/tests/test_executors.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `get_vector_executor() -> concurrent.futures.ThreadPoolExecutor`
  - `shutdown_executors(wait: bool = True) -> None`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/test_executors.py`：

```python
import asyncio
import threading

from backend.core import executors


def test_vector_executor_is_bounded_and_named():
    ex = executors.get_vector_executor()
    assert ex.max_workers == 4, "必须有界，避免 embedding 风暴打满线程"
    assert executors.get_vector_executor() is ex, "必须复用同一个池"


async def test_vector_executor_threads_are_prefixed():
    """跑在向量池里的任务，线程名必须能一眼认出来，便于排查阻塞。"""
    loop = asyncio.get_running_loop()
    name = await loop.run_in_executor(
        executors.get_vector_executor(), lambda: threading.current_thread().name
    )
    assert name.startswith("vec"), f"线程名应以 vec 开头，实际为 {name}"


def test_shutdown_is_idempotent():
    executors.get_vector_executor()
    executors.shutdown_executors()
    executors.shutdown_executors()  # 第二次不应抛异常
    # 关停后再取应当拿到一个可用的新池
    assert executors.get_vector_executor().max_workers == 4
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_executors.py -v`
Expected: FAIL，`ModuleNotFoundError: backend.core.executors`

- [ ] **Step 3: 实现 executors 模块**

创建 `backend/core/executors.py`：

```python
"""
进程级线程池的集中管理。

为什么不用 run_in_executor(None, ...)：
默认线程池是全局共享资源，LangGraph 的同步节点、asyncio.to_thread 都在用它，
大小只有 min(32, cpu+4)。一次 embedding 风暴会把它占满，饿死其他所有阻塞调用。
给向量库一个有界的专属池，把影响限制在向量层内部。
"""
import threading
from concurrent.futures import ThreadPoolExecutor

from backend.middleware.logging import get_logger

logger = get_logger(__name__)

VECTOR_POOL_SIZE = 4

_vector_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()


def get_vector_executor() -> ThreadPoolExecutor:
    """获取向量库专属线程池（懒创建，进程内单例）。"""
    global _vector_executor
    if _vector_executor is None:
        with _lock:
            if _vector_executor is None:
                _vector_executor = ThreadPoolExecutor(
                    max_workers=VECTOR_POOL_SIZE,
                    thread_name_prefix="vec",
                )
                logger.info("向量库线程池已创建，max_workers=%s", VECTOR_POOL_SIZE)
    return _vector_executor


def shutdown_executors(wait: bool = True) -> None:
    """关停所有线程池；可重复调用。"""
    global _vector_executor
    with _lock:
        if _vector_executor is not None:
            _vector_executor.shutdown(wait=wait)
            _vector_executor = None
            logger.info("向量库线程池已关停")
```

- [ ] **Step 4: 让向量库使用这个池**

在 `backend/agents/memory/vector_store_manager.py` 顶部加入导入：

```python
from backend.core.executors import get_vector_executor
```

把 `add_document`、`delete_document`、`update_document`、`query` 中所有

```python
await loop.run_in_executor(None, partial(...))
```

改为

```python
await loop.run_in_executor(get_vector_executor(), partial(...))
```

共 4 处：`add_document`、`delete_document`、`update_document`、`query` 各 1 处。用下面的命令确认没有遗漏：

Run: `cd backend && grep -n "run_in_executor" agents/memory/vector_store_manager.py`
Expected: 输出中不再出现 `run_in_executor(None`

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_executors.py -v`
Expected: 3 passed

- [ ] **Step 6: 提交**

```bash
git add backend/core/executors.py backend/agents/memory/vector_store_manager.py backend/tests/test_executors.py
git commit -m "refactor: 向量库改用专属有界线程池，避免与默认池互相饿死"
```

---

## Task 4: 移除已死的同步 agent 路径

`tool_exec_node` 只 `await tool._arun(...)`，`_run` 在 ReAct 图里永远不会执行。留着三个同步 agent 函数只会造成「同步版本悄悄被调用从而阻塞事件循环」的风险。

**Files:**
- Modify: `backend/agents/tools/common_tool.py`
- Modify: `backend/agents/tools/extract_knowledge_tool.py`
- Modify: `backend/agents/tools/question_set_tool.py`
- Modify: `backend/agents/agent/common_agent.py`（删除 `common_tool`）
- Modify: `backend/agents/agent/extract_agent.py`（删除 `extract_tool`）
- Modify: `backend/agents/agent/question_set_agent.py`（删除 `question_set_tool`）
- Create: `backend/tests/test_tools_sync_disabled.py`

**Interfaces:**
- Consumes: 无
- Produces: 所有 `BaseTool` 子类的 `_run` 统一 `raise NotImplementedError`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/test_tools_sync_disabled.py`：

```python
import pytest

from backend.agents.tools import TOOLS


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
def test_sync_run_is_disabled(tool):
    """所有工具都只走异步路径；同步 _run 必须显式拒绝，不能悄悄阻塞事件循环。"""
    if tool.name == "load_skill_tool":
        pytest.skip("load_skill_tool 是纯文件读取，同步实现无阻塞风险")
    with pytest.raises(NotImplementedError):
        tool._run()


def test_sync_agent_functions_are_gone():
    from backend.agents.agent import common_agent, extract_agent, question_set_agent

    assert not hasattr(common_agent, "common_tool")
    assert not hasattr(extract_agent, "extract_tool")
    assert not hasattr(question_set_agent, "question_set_tool")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_tools_sync_disabled.py -v`
Expected: FAIL，`common_tool`/`extract_tool`/`question_set_tool` 三个工具的 `_run` 没有抛 `NotImplementedError`

- [ ] **Step 3: 禁用三个工具的同步入口**

`backend/agents/tools/common_tool.py` —— 删掉 `common_tool` 的导入，把 `_run` 换成：

```python
    def _run(self, *args, **kwargs):
        raise NotImplementedError("CommonTool 仅支持异步调用，请使用 _arun")
```

`backend/agents/tools/extract_knowledge_tool.py` —— 删掉 `extract_tool` 的导入，把 `_run` 换成：

```python
    def _run(self, *args, **kwargs):
        raise NotImplementedError("ExtractKnowledgeTool 仅支持异步调用，请使用 _arun")
```

`backend/agents/tools/question_set_tool.py` —— 删掉 `extract_tool` 与 `question_set_tool` 的导入（保留 `async_extract_tool`、`async_question_set_tool`），把 `_run` 换成：

```python
    def _run(self, *args, **kwargs):
        raise NotImplementedError("QuestionSetTool 仅支持异步调用，请使用 _arun")
```

- [ ] **Step 4: 删除三个同步 agent 函数**

- `backend/agents/agent/common_agent.py`：删除整个 `def common_tool(text: str) -> str:` 函数体（保留 `build_common_agent` 与 `async_common_tool`）。
- `backend/agents/agent/extract_agent.py`：删除整个 `def extract_tool(text: str) -> dict:` 函数体（保留 `build_extract_agent` 与 `async_extract_tool`）。同时删除文件顶部已不再使用的 `import json`？——不要删，`async_extract_tool` 仍在用。
- `backend/agents/agent/question_set_agent.py`：删除整个 `def question_set_tool(text: dict) -> dict:` 函数体（保留 `build_question_set_agent` 与 `async_question_set_tool`）。

删完后确认没有残留引用：

Run: `cd backend && grep -rn "common_tool\|extract_tool\|question_set_tool" --include=*.py agents/ api/ | grep -v "async_\|_tool.py:\|CommonTool\|QuestionSetTool"`
Expected: 无输出（或只剩工具类自身的 `name` 字符串）

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_tools_sync_disabled.py -v`
Expected: 全部 passed

- [ ] **Step 6: 提交**

```bash
git add backend/agents/tools/ backend/agents/agent/common_agent.py backend/agents/agent/extract_agent.py backend/agents/agent/question_set_agent.py backend/tests/test_tools_sync_disabled.py
git commit -m "refactor: 移除已死的同步 agent 路径，工具统一走异步"
```

---

## Task 5: 短期记忆改为 Redis LIST + Lua 原子淘汰

这是整个改造的地基。

**Files:**
- Modify: `backend/agents/memory/short_term_memory.py`（全面重写）
- Create: `backend/tests/test_short_term_memory.py`

**Interfaces:**
- Consumes: `get_redis_client()`（已存在）
- Produces:
  - `MemoryUnit(user_memory: str = "", model_memory: str = "")` —— 结构不变
  - `ShortTermMemory.__init__(max_memory_size: int = 10, ttl: int = 86400, pending_ttl: int = 604800)`
  - `ShortTermMemory.key(user_id, session_id) -> str`（静态）
  - `ShortTermMemory.pending_key(user_id, session_id) -> str`（静态）
  - `ShortTermMemory.parse_pending_key(key: str) -> tuple[int, int]`（静态）
  - `async add_memory(user_id, session_id, memory) -> list[str]` —— **返回被挤出窗口、已搬入 pending 的原始 JSON 字符串列表**
  - `async get_latest_memories(user_id, session_id, limit=5) -> list[dict]`
  - `async get_memory_size(user_id, session_id) -> int`
  - `async get_pending(user_id, session_id) -> list[str]`
  - `async ack_archived(user_id, session_id, raw_item: str) -> int`
  - `async scan_pending_keys() -> list[str]`
  - `async clear_all(user_id, session_id) -> None`
- **删除**：`remove_oldest_memory`、`delete_max_memory`、`get_max_memory_size`（淘汰已由 Lua 承担，这三个方法失去意义）

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/test_short_term_memory.py`：

```python
import asyncio
import json

import pytest

from backend.agents.memory.short_term_memory import MemoryUnit, ShortTermMemory

USER, SESSION = 1, 1


@pytest.fixture
def stm(redis_test_client, monkeypatch):
    """把 ShortTermMemory 指向测试库 db 15。"""
    memory = ShortTermMemory(max_memory_size=3)
    monkeypatch.setattr(memory, "_client", redis_test_client)
    return memory


async def test_newest_first(stm):
    for i in range(3):
        await stm.add_memory(USER, SESSION, MemoryUnit(f"问题{i}", f"回答{i}"))

    got = await stm.get_latest_memories(USER, SESSION, limit=3)
    assert [m["memory"]["user_memory"] for m in got] == ["问题2", "问题1", "问题0"]


async def test_no_eviction_below_limit(stm):
    evicted = await stm.add_memory(USER, SESSION, MemoryUnit("问题0", "回答0"))
    assert evicted == []
    assert await stm.get_memory_size(USER, SESSION) == 1


async def test_eviction_moves_oldest_to_pending(stm):
    for i in range(3):
        await stm.add_memory(USER, SESSION, MemoryUnit(f"问题{i}", f"回答{i}"))

    evicted = await stm.add_memory(USER, SESSION, MemoryUnit("问题3", "回答3"))

    assert len(evicted) == 1
    assert json.loads(evicted[0])["memory"]["user_memory"] == "问题0"
    assert await stm.get_memory_size(USER, SESSION) == 3
    pending = await stm.get_pending(USER, SESSION)
    assert pending == evicted, "被淘汰的条目必须进入 pending，不能凭空消失"


async def test_evicted_item_is_invisible_to_readers(stm):
    """验收标准 3：正在被归档的记忆，读路径物理上看不到。"""
    for i in range(4):
        await stm.add_memory(USER, SESSION, MemoryUnit(f"问题{i}", f"回答{i}"))

    visible = await stm.get_latest_memories(USER, SESSION, limit=10)
    assert "问题0" not in [m["memory"]["user_memory"] for m in visible]


async def test_concurrent_writes_lose_nothing(stm):
    """验收标准 1：20 个并发写入，窗口 + pending 必须恰好等于 20 条，一条不丢。"""
    results = await asyncio.gather(*[
        stm.add_memory(USER, SESSION, MemoryUnit(f"问题{i}", f"回答{i}"))
        for i in range(20)
    ])

    in_window = await stm.get_latest_memories(USER, SESSION, limit=100)
    in_pending = await stm.get_pending(USER, SESSION)

    assert len(in_window) == 3, "窗口必须严格等于 max_memory_size"
    assert len(in_window) + len(in_pending) == 20, "并发写入不得丢消息"

    evicted_total = sum(len(r) for r in results)
    assert evicted_total == len(in_pending), "每条被淘汰的记忆都应被返回给调用方一次"


async def test_ack_removes_from_pending(stm):
    for i in range(4):
        await stm.add_memory(USER, SESSION, MemoryUnit(f"问题{i}", f"回答{i}"))

    pending = await stm.get_pending(USER, SESSION)
    removed = await stm.ack_archived(USER, SESSION, pending[0])

    assert removed == 1
    assert await stm.get_pending(USER, SESSION) == []


async def test_ttl_is_set(stm):
    await stm.add_memory(USER, SESSION, MemoryUnit("问题", "回答"))
    ttl = await stm._client.ttl(ShortTermMemory.key(USER, SESSION))
    assert 0 < ttl <= 86400


async def test_scan_pending_keys(stm):
    for i in range(4):
        await stm.add_memory(USER, SESSION, MemoryUnit(f"问题{i}", f"回答{i}"))

    keys = await stm.scan_pending_keys()
    assert ShortTermMemory.pending_key(USER, SESSION) in keys
    assert ShortTermMemory.parse_pending_key(keys[0]) == (USER, SESSION)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_short_term_memory.py -v`
Expected: 大量 FAIL / ERROR（`ShortTermMemory` 没有 `_client`、`key`、`get_pending` 等）

- [ ] **Step 3: 重写 short_term_memory.py**

用下面的内容整体替换 `backend/agents/memory/short_term_memory.py`：

```python
"""
短期会话记忆：Redis LIST 实现。

为什么是 LIST 而不是「hash 字段里的 JSON 字符串」：
旧实现每次写入都要「读出整个列表 → Python 里 insert → 整个写回」，
是一次没有任何保护的 read-modify-write。两个并发请求即使都不触发摘要
也会丢消息（读-读-写-写）。改成 LIST 后，写入是 Redis 服务端的原子操作。

淘汰同样在服务端完成：LPUSH 与「超限时 RPOPLPUSH 到 pending 队列」
打包进一次 Lua EVAL 原子执行。被淘汰的条目在这一刻就已经物理移出窗口，
后续的归档（LLM 精炼 + 写向量库）读不到它、也不会被新消息插入干扰，
所以整条链路不需要任何锁。

存储结构：
- stm:{user_id}:{session_id}          LIST，index 0 为最新，长度上限 max_memory_size
- stm:pending:{user_id}:{session_id}  LIST，待归档队列，归档成功后 LREM 移除
"""
import json
from datetime import datetime
from typing import Any, Dict, List

from redis.exceptions import RedisError

from backend.middleware.logging import get_logger
from backend.utils.redis_client import get_redis_client

logger = get_logger(__name__)

DEFAULT_TTL = 86400          # 窗口 24 小时
DEFAULT_PENDING_TTL = 604800  # pending 7 天，给崩溃恢复留足余量

# KEYS[1]=窗口 key  KEYS[2]=pending key
# ARGV[1]=新记忆 JSON  ARGV[2]=最大条数  ARGV[3]=窗口 TTL  ARGV[4]=pending TTL
# 用 RPOPLPUSH 而非 LMOVE：语义等价，但兼容 Redis 6.2 以下版本
_PUSH_AND_EVICT_LUA = """
redis.call('LPUSH', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[3])
local evicted = {}
while redis.call('LLEN', KEYS[1]) > tonumber(ARGV[2]) do
  local item = redis.call('RPOPLPUSH', KEYS[1], KEYS[2])
  if not item then break end
  table.insert(evicted, item)
end
if #evicted > 0 then
  redis.call('EXPIRE', KEYS[2], tonumber(ARGV[4]))
end
return evicted
"""


class MemoryUnit(dict):
    def __init__(self, user_memory: str = "", model_memory: str = ""):
        super().__init__(
            memory={
                "user_memory": user_memory,
                "model_memory": model_memory,
            },
            timestamp=datetime.now().isoformat(),
        )


class ShortTermMemory:
    def __init__(
        self,
        max_memory_size: int = 10,
        ttl: int = DEFAULT_TTL,
        pending_ttl: int = DEFAULT_PENDING_TTL,
    ):
        self.max_memory_size = max_memory_size
        self.ttl = ttl
        self.pending_ttl = pending_ttl
        self._client = get_redis_client().client
        self._push_script = None

    # ---------- key 约定 ----------

    @staticmethod
    def key(user_id: int, session_id: int) -> str:
        return f"stm:{user_id}:{session_id}"

    @staticmethod
    def pending_key(user_id: int, session_id: int) -> str:
        return f"stm:pending:{user_id}:{session_id}"

    @staticmethod
    def parse_pending_key(key: str) -> tuple[int, int]:
        """从 stm:pending:{u}:{s} 反解出 (user_id, session_id)。"""
        parts = key.split(":")
        return int(parts[2]), int(parts[3])

    def _script(self):
        """懒注册 Lua 脚本；redis-py 会自动 EVALSHA，NOSCRIPT 时回退 EVAL。"""
        if self._push_script is None:
            self._push_script = self._client.register_script(_PUSH_AND_EVICT_LUA)
        return self._push_script

    # ---------- 写 ----------

    async def add_memory(
        self, user_id: int, session_id: int, memory: MemoryUnit
    ) -> List[str]:
        """
        原子地写入一条记忆，并把超出窗口的最旧记忆搬进 pending 队列。

        :return: 本次被挤出窗口的原始 JSON 字符串列表（通常 0 或 1 条）。
                 调用方负责把它们归档，并在成功后调用 ack_archived。
        """
        try:
            evicted = await self._script()(
                keys=[
                    self.key(user_id, session_id),
                    self.pending_key(user_id, session_id),
                ],
                args=[
                    json.dumps(memory, ensure_ascii=False),
                    self.max_memory_size,
                    self.ttl,
                    self.pending_ttl,
                ],
            )
            return list(evicted or [])
        except RedisError as e:
            logger.error("写入短期记忆失败：%s", e, exc_info=True)
            return []

    async def ack_archived(self, user_id: int, session_id: int, raw_item: str) -> int:
        """
        归档成功后，把该条目从 pending 队列移除。
        用原始 JSON 字符串精确匹配，保证幂等：重复归档最多产生一条重复向量，不会丢数据。

        :return: 实际移除的条数（0 表示已被移除过）
        """
        try:
            return await self._client.lrem(
                self.pending_key(user_id, session_id), 1, raw_item
            )
        except RedisError as e:
            logger.error("移除 pending 条目失败：%s", e, exc_info=True)
            return 0

    async def clear_all(self, user_id: int, session_id: int) -> None:
        """清空该会话的窗口与 pending 队列。"""
        await self._client.delete(
            self.key(user_id, session_id),
            self.pending_key(user_id, session_id),
        )

    # ---------- 读 ----------

    async def get_latest_memories(
        self, user_id: int, session_id: int, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """获取最新 N 条记忆（index 0 为最新）。"""
        try:
            raw = await self._client.lrange(
                self.key(user_id, session_id), 0, limit - 1
            )
        except RedisError as e:
            # Redis 不可用时返回空记忆，不让整个接口失败
            logger.error("读取短期记忆失败：%s", e)
            return []

        result: List[Dict[str, Any]] = []
        for item in raw:
            try:
                result.append(json.loads(item))
            except json.JSONDecodeError:
                logger.error("短期记忆反序列化失败，已跳过：%s", item[:100])
        return result

    async def get_memory_size(self, user_id: int, session_id: int) -> int:
        try:
            return await self._client.llen(self.key(user_id, session_id))
        except RedisError:
            return 0

    async def get_pending(self, user_id: int, session_id: int) -> List[str]:
        """待归档队列的原始 JSON 字符串列表。"""
        try:
            return await self._client.lrange(
                self.pending_key(user_id, session_id), 0, -1
            )
        except RedisError:
            return []

    async def scan_pending_keys(self) -> List[str]:
        """
        扫出所有非空的 pending 队列 key，供启动恢复使用。
        用 SCAN 而非 KEYS，避免阻塞 Redis。
        """
        keys: List[str] = []
        try:
            async for key in self._client.scan_iter(match="stm:pending:*", count=100):
                keys.append(key)
        except RedisError as e:
            logger.error("扫描 pending 队列失败：%s", e)
        return keys


async def get_short_term_memory() -> ShortTermMemory:
    return ShortTermMemory()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_short_term_memory.py -v`
Expected: 8 passed

关键要看 `test_concurrent_writes_lose_nothing` 通过——旧实现在这个测试上必然失败。

- [ ] **Step 5: 提交**

```bash
git add backend/agents/memory/short_term_memory.py backend/tests/test_short_term_memory.py
git commit -m "feat: 短期记忆改用 Redis LIST + Lua 原子淘汰，消除并发丢消息"
```

---

## Task 6: 归档转为后台任务

**Files:**
- Modify: `backend/agents/memory/memory_manager.py`（重写 `add_memory`，新增归档相关方法）
- Create: `backend/tests/test_memory_manager.py`

**Interfaces:**
- Consumes: Task 5 的 `ShortTermMemory.add_memory -> list[str]`、`ack_archived`、`get_pending`、`scan_pending_keys`、`parse_pending_key`
- Produces:
  - `async MemoryManager.add_memory(user_id, session_id, memory) -> None` —— 不再等待摘要
  - `async MemoryManager.drain_pending() -> int` —— 启动恢复，返回补做的条目数
  - `async MemoryManager.shutdown(timeout: float = 10.0) -> None` —— 等待在途归档

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/test_memory_manager.py`：

```python
import asyncio
import json

import pytest

from backend.agents.memory.memory_manager import MemoryManager
from backend.agents.memory.short_term_memory import MemoryUnit, ShortTermMemory

USER, SESSION = 7, 7


class FakeVectorStore:
    def __init__(self, should_fail: bool = False):
        self.docs: list[tuple[str, dict]] = []
        self.should_fail = should_fail

    async def add_document(self, text: str, metadata: dict) -> bool:
        if self.should_fail:
            return False
        self.docs.append((text, metadata))
        return True


@pytest.fixture
def manager(redis_test_client, monkeypatch):
    """构造一个只依赖测试 Redis 的 MemoryManager，长期记忆与精炼都打桩。"""
    stm = ShortTermMemory(max_memory_size=2)
    monkeypatch.setattr(stm, "_client", redis_test_client)

    vector = FakeVectorStore()

    async def fake_extract(units):
        return [
            {"text": f"精炼:{u['memory']['user_memory']}", "tags": ["事实"]}
            for u in units
        ]

    import backend.agents.memory.memory_manager as mm
    monkeypatch.setattr(mm, "get_extract_memory", fake_extract)

    # MemoryManager 用了 singleMeta 单例，测试里绕开它直接构造
    mgr = MemoryManager.__new__(MemoryManager)
    mgr.long_term_memory = None
    mgr.short_term_memory = stm
    mgr.vector_memory = vector
    mgr._tasks = set()
    return mgr


async def test_add_below_limit_spawns_no_archive(manager):
    await manager.add_memory(USER, SESSION, MemoryUnit("问题0", "回答0"))
    assert manager._tasks == set()
    assert manager.vector_memory.docs == []


async def test_overflow_archives_in_background(manager):
    for i in range(3):
        await manager.add_memory(USER, SESSION, MemoryUnit(f"问题{i}", f"回答{i}"))

    await manager.shutdown(timeout=5)

    assert len(manager.vector_memory.docs) == 1
    text, metadata = manager.vector_memory.docs[0]
    assert text == "精炼:问题0"
    assert metadata["user_id"] == USER
    assert isinstance(metadata["tags"], str), "Chroma 的 metadata 只接受标量"
    assert await manager.short_term_memory.get_pending(USER, SESSION) == []


async def test_request_path_does_not_wait_for_archive(manager, monkeypatch):
    """验收标准 2：触发溢出的请求不得被 LLM 摘要拖慢。"""
    import backend.agents.memory.memory_manager as mm

    async def slow_extract(units):
        await asyncio.sleep(1.0)
        return [{"text": "慢精炼", "tags": ["事实"]}]

    monkeypatch.setattr(mm, "get_extract_memory", slow_extract)

    for i in range(2):
        await manager.add_memory(USER, SESSION, MemoryUnit(f"问题{i}", f"回答{i}"))

    start = asyncio.get_running_loop().time()
    await manager.add_memory(USER, SESSION, MemoryUnit("问题2", "回答2"))
    elapsed = asyncio.get_running_loop().time() - start

    assert elapsed < 0.2, f"请求路径被归档阻塞了 {elapsed:.2f}s"
    await manager.shutdown(timeout=5)


async def test_failed_archive_keeps_item_in_pending(manager):
    """验收标准 4 的一半：写向量库失败时，条目必须留在 pending 等待补做。"""
    manager.vector_memory.should_fail = True

    for i in range(3):
        await manager.add_memory(USER, SESSION, MemoryUnit(f"问题{i}", f"回答{i}"))
    await manager.shutdown(timeout=5)

    pending = await manager.short_term_memory.get_pending(USER, SESSION)
    assert len(pending) == 1
    assert json.loads(pending[0])["memory"]["user_memory"] == "问题0"


async def test_drain_pending_recovers_orphans(manager):
    """验收标准 4 的另一半：模拟「已弹出、未入库」后重启，补做成功且不重复。"""
    manager.vector_memory.should_fail = True
    for i in range(3):
        await manager.add_memory(USER, SESSION, MemoryUnit(f"问题{i}", f"回答{i}"))
    await manager.shutdown(timeout=5)
    assert len(await manager.short_term_memory.get_pending(USER, SESSION)) == 1

    # 「重启」：向量库恢复正常，跑一次启动恢复
    manager.vector_memory.should_fail = False
    manager._tasks = set()
    recovered = await manager.drain_pending()

    assert recovered == 1
    assert len(manager.vector_memory.docs) == 1
    assert await manager.short_term_memory.get_pending(USER, SESSION) == []

    # 再跑一次不应重复归档
    assert await manager.drain_pending() == 0
    assert len(manager.vector_memory.docs) == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_memory_manager.py -v`
Expected: FAIL / ERROR，`MemoryManager` 没有 `_tasks`、`shutdown`、`drain_pending`

- [ ] **Step 3: 重写 memory_manager.py**

用下面的内容整体替换 `backend/agents/memory/memory_manager.py`：

```python
"""
三层记忆的统一入口。

归档流程为什么不需要锁：
被淘汰的记忆在 ShortTermMemory.add_memory 的那一次 Lua EVAL 里就已经
原子地移出了窗口、进入 pending 队列。此后它对读路径不可见，也不可能
被新消息插入干扰，因此归档（LLM 精炼 + 写向量库）可以放心地在后台跑，
请求路径不必等待。
"""
import asyncio
import json
import time
from typing import Any

from backend.agents.agent.extract_memory_agent import get_extract_memory
from backend.agents.memory.long_term_memory import LongTermMemory
from backend.agents.memory.short_term_memory import ShortTermMemory, MemoryUnit
from backend.agents.memory.vector_store_manager import VectorStoreManager
from backend.core.single_tool import singleMeta
from backend.middleware.logging import get_logger

logger = get_logger(__name__)


class MemoryManager(metaclass=singleMeta):
    def __init__(self,
                 long_term_memory: LongTermMemory,
                 short_term_memory: ShortTermMemory,
                 vector_memory: VectorStoreManager):
        self.long_term_memory = long_term_memory
        self.short_term_memory = short_term_memory
        self.vector_memory = vector_memory
        # 必须持有强引用：asyncio 只弱引用运行中的 task，否则可能在完成前被 GC
        self._tasks: set[asyncio.Task] = set()

    # ---------- 读 ----------

    async def get_memory_for_planner(self, user_id: int, session_id: int) -> dict[str, Any]:
        """获取规划器需要的记忆（短期列表 + 单个长期画像）"""
        short_memory = await self.short_term_memory.get_latest_memories(user_id, session_id)
        long_memory = await self.long_term_memory.get_by_user_id(user_id)
        return {
            "short_memory": short_memory,  # list[dict]
            "long_memory": long_memory,    # UserProfileResponse | None
        }

    # ---------- 写 ----------

    async def add_memory(self, user_id: int, session_id: int, memory: MemoryUnit) -> None:
        """
        写入一条短期记忆。超出窗口的旧记忆会被原子地移入 pending 队列，
        并交给后台任务归档——请求路径不等待 LLM 摘要。
        """
        evicted = await self.short_term_memory.add_memory(user_id, session_id, memory)
        if evicted:
            self._spawn(self._archive(user_id, session_id, evicted))

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _archive(self, user_id: int, session_id: int, raw_items: list[str]) -> int:
        """
        把被淘汰的记忆精炼后写入向量库；每条成功后才从 pending 移除。
        :return: 成功归档的条数
        """
        units: list[dict] = []
        keep: list[str] = []
        for raw in raw_items:
            try:
                units.append(json.loads(raw))
                keep.append(raw)
            except json.JSONDecodeError:
                logger.error("待归档记忆反序列化失败，直接丢弃：%s", raw[:100])
                await self.short_term_memory.ack_archived(user_id, session_id, raw)

        if not units:
            return 0

        try:
            refined = await get_extract_memory(units)
        except Exception as e:
            logger.error("记忆精炼调用失败，条目留在 pending 待重试：%s", e, exc_info=True)
            return 0

        if not refined:
            logger.warning("记忆精炼未产出内容，条目留在 pending 待重试")
            return 0

        archive_time = int(time.time())
        done = 0
        # refined 与 keep 按顺序一一对应；数量不等时按较短的来，多余的留在 pending
        for raw, item in zip(keep, refined):
            metadata = {
                'user_id': user_id,
                'session_id': session_id,
                'timestamp': archive_time,
                # Chroma 的 metadata 只接受标量，list 需展平
                'tags': ','.join(item['tags']),
            }
            ok = await self.vector_memory.add_document(item['text'], metadata)
            if not ok:
                await asyncio.sleep(0.5)
                ok = await self.vector_memory.add_document(item['text'], metadata)
            if ok:
                await self.short_term_memory.ack_archived(user_id, session_id, raw)
                done += 1
            else:
                logger.error("写入向量库失败，条目留在 pending 待重试")
        return done

    # ---------- 生命周期 ----------

    async def drain_pending(self) -> int:
        """
        启动恢复：扫出所有 pending 队列，补做进程崩溃时「已弹出、未入库」的归档。
        :return: 成功补做的条目数
        """
        keys = await self.short_term_memory.scan_pending_keys()
        total = 0
        for key in keys:
            try:
                user_id, session_id = ShortTermMemory.parse_pending_key(key)
            except (IndexError, ValueError):
                logger.error("无法解析 pending key，已跳过：%s", key)
                continue

            items = await self.short_term_memory.get_pending(user_id, session_id)
            if not items:
                continue
            logger.info("启动恢复：session %s:%s 有 %s 条待归档", user_id, session_id, len(items))
            total += await self._archive(user_id, session_id, items)
        if total:
            logger.info("启动恢复完成，共补做 %s 条", total)
        return total

    async def shutdown(self, timeout: float = 10.0) -> None:
        """等待在途归档完成；超时未完成的留在 pending，下次启动补做。"""
        if not self._tasks:
            return
        pending = list(self._tasks)
        logger.info("等待 %s 个归档任务完成……", len(pending))
        done, not_done = await asyncio.wait(pending, timeout=timeout)
        if not_done:
            logger.warning("%s 个归档任务超时未完成，将在下次启动时补做", len(not_done))
            for task in not_done:
                task.cancel()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_memory_manager.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add backend/agents/memory/memory_manager.py backend/tests/test_memory_manager.py
git commit -m "feat: 记忆归档转为后台任务，请求路径不再等待 LLM 摘要"
```

---

## Task 7: 启动恢复与优雅关停接线

**Files:**
- Modify: `backend/core/hooks.py`
- Modify: `backend/api/user_api/agent_api.py:30-35`（导出 `memory_manager` 供 hooks 使用）

**Interfaces:**
- Consumes: Task 6 的 `MemoryManager.drain_pending()`、`MemoryManager.shutdown()`；Task 3 的 `shutdown_executors()`
- Produces: 无新接口

- [ ] **Step 1: 写测试**

追加到 `backend/tests/test_memory_manager.py` 末尾：

```python
async def test_shutdown_order_is_documented():
    """关停顺序：先等归档（还要用 Redis 和向量库），再关线程池与连接。"""
    import inspect

    from backend.core import hooks

    source = inspect.getsource(hooks.shutdown_event)
    archive_pos = source.index("shutdown(")
    redis_pos = source.index("close_redis")
    assert archive_pos < redis_pos, "必须先等归档完成，再关 Redis 连接"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_memory_manager.py::test_shutdown_order_is_documented -v`
Expected: FAIL，`hooks.shutdown_event` 里没有 `shutdown(`

- [ ] **Step 3: 实现 hooks**

用下面的内容整体替换 `backend/core/hooks.py`：

```python
from backend.core.executors import shutdown_executors
from backend.middleware.logging import get_logger
from backend.model import engine, Base
from backend.utils.redis_client import close_redis

logger = get_logger(__name__)


def _get_memory_manager():
    """延迟导入：agent_api 在导入时才完成记忆模块的实例化。"""
    from backend.api.user_api.agent_api import memory_manager
    return memory_manager


async def startup_event():
    """应用启动：建表 + 补做上次未完成的记忆归档"""
    logger.info("应用启动中，正在创建数据库表...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库表创建完成")

    try:
        recovered = await _get_memory_manager().drain_pending()
        logger.info("记忆归档恢复完成，补做 %s 条", recovered)
    except Exception as e:
        # 恢复失败不应阻止服务启动；条目留在 pending，下次再试
        logger.error("记忆归档恢复失败：%s", e, exc_info=True)


async def shutdown_event():
    """应用关闭：先等在途归档做完，再释放线程池与连接"""
    logger.info("应用关闭中，等待在途记忆归档...")
    try:
        await _get_memory_manager().shutdown(timeout=10.0)
    except Exception as e:
        logger.error("等待归档任务时出错：%s", e, exc_info=True)

    logger.info("正在释放线程池与数据库连接...")
    shutdown_executors(wait=True)
    await engine.dispose()
    await close_redis()
    logger.info("资源已全部释放")
```

顺序很重要：归档任务还要用 Redis 和向量库，所以必须在 `shutdown_executors` / `close_redis` **之前**完成。

- [ ] **Step 4: 运行全部测试**

Run: `cd backend && python -m pytest tests/ -v`
Expected: 全部 passed

- [ ] **Step 5: 启动服务做一次人工验证**

```bash
cd backend
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

日志里应当出现「记忆归档恢复完成，补做 0 条」。按 Ctrl+C，应当出现「等待在途记忆归档...」→「资源已全部释放」。

- [ ] **Step 6: 提交**

```bash
git add backend/core/hooks.py backend/tests/test_memory_manager.py
git commit -m "feat: 启动时补做未完成归档，关停时等待在途任务"
```

---

## Task 8: 画像写入策略：JSON 合并 + notes 逃生口

两件事一起做，因为它们都是「画像的 JSON 字段该怎么写」这一个关注点：

1. `weak_points` / `preferences` 现在是整体替换：Agent 传入 `{"函数": "薄弱"}` 会把之前存的所有其他知识点冲掉。这与「画像只增不改」的既定策略直接矛盾。
2. 新增 `notes` 列作为**逃生口**。画像的键名受词表约束（见 Task 10），但总有观察放不进任何已知键。`notes` 是一个纯自由文本数组，追加式写入，不参与频次确认，也**不注入 system prompt**（只在 `user_profile_query_tool` 被主动调用时返回），避免它把每轮的画像段撑大。

**Files:**
- Modify: `backend/model/user_profile.py`（新增 `notes` 列）
- Modify: `backend/schemas/request/ltm_request.py`（新增 `notes`）
- Modify: `backend/schemas/response/user_profile_response.py`（新增 `notes`）
- Modify: `backend/agents/memory/long_term_memory.py`（新建画像时填 `notes`）
- Modify: `backend/dao/user_profile_mapper.py`（`update_user_profile` 与 `create_memory`）
- Create: `backend/tests/test_profile_merge.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `merge_json_field(old: dict | None, new: dict | None) -> dict` —— 浅合并，供 `weak_points` / `preferences` 使用
  - `append_notes(old: list | None, new: list | None, max_size: int = 50) -> list` —— 追加去重并限长，供 `notes` 使用
  - `UserProfile.notes` JSON 列，默认 `[]`
  - `LTMRequest.notes: Optional[list]`、`UserProfileResponse.notes: list`

- [ ] **Step 1: 写失败的测试**

这个测试不连真实 MySQL，只验证合并逻辑本身。因此先把合并逻辑抽成一个纯函数。

创建 `backend/tests/test_profile_merge.py`：

```python
from backend.dao.user_profile_mapper import merge_json_field


def test_new_keys_are_added():
    assert merge_json_field({"函数": "薄弱"}, {"方程": "薄弱"}) == {
        "函数": "薄弱",
        "方程": "薄弱",
    }


def test_existing_key_takes_new_value():
    """冲突时以新的为准。"""
    assert merge_json_field({"函数": "薄弱"}, {"函数": "已掌握"}) == {"函数": "已掌握"}


def test_old_keys_are_never_dropped():
    """核心保护：写入新知识点不得冲掉已有的。"""
    old = {"函数": "薄弱", "几何": "薄弱", "概率": "一般"}
    merged = merge_json_field(old, {"方程": "薄弱"})
    assert set(merged) == {"函数", "几何", "概率", "方程"}


def test_none_and_empty_are_safe():
    assert merge_json_field(None, {"方程": "薄弱"}) == {"方程": "薄弱"}
    assert merge_json_field({"函数": "薄弱"}, None) == {"函数": "薄弱"}
    assert merge_json_field(None, None) == {}


def test_notes_are_appended_not_replaced():
    old = ["做题时喜欢先看思路"]
    assert append_notes(old, ["晚上做题效率更高"]) == [
        "做题时喜欢先看思路",
        "晚上做题效率更高",
    ]


def test_notes_skip_exact_duplicates():
    old = ["做题时喜欢先看思路"]
    assert append_notes(old, ["做题时喜欢先看思路"]) == ["做题时喜欢先看思路"]


def test_notes_are_capped_dropping_oldest():
    old = [f"观察{i}" for i in range(50)]
    got = append_notes(old, ["最新观察"], max_size=50)
    assert len(got) == 50
    assert got[-1] == "最新观察"
    assert "观察0" not in got, "超限时丢最旧的"


def test_notes_none_is_safe():
    assert append_notes(None, ["第一条"]) == ["第一条"]
    assert append_notes(["已有"], None) == ["已有"]
    assert append_notes(None, None) == []
```

第一行的导入相应改为：

```python
from backend.dao.user_profile_mapper import append_notes, merge_json_field
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_profile_merge.py -v`
Expected: FAIL，`ImportError: cannot import name 'merge_json_field'`

- [ ] **Step 3: 新增 notes 列与 schema 字段**

`backend/model/user_profile.py` —— 在 `preferences` 之后加一列：

```python
    notes = Column(JSON, nullable=False, default=list, comment='自由观察记录（数组），放不进结构化键的内容')
```

`backend/schemas/request/ltm_request.py` —— 加一个字段：

```python
    notes: Optional[list] = Field(None, description='本次新增的自由观察记录（追加，不覆盖）')
```

`backend/schemas/response/user_profile_response.py` —— 加一个字段：

```python
    notes: list = Field(default_factory=list, description='自由观察记录', example=['做题时喜欢先看思路'])
```

`backend/agents/memory/long_term_memory.py` 的 `add_or_update` 里，新建画像的分支补上：

```python
                notes=request.notes or [],
```

`backend/dao/user_profile_mapper.py` 的 `create_memory` 里，构造 `UserProfile` 时补上：

```python
                    notes=user_profile.notes or [],
```

`backend/agents/tools/user_profile_query_tool.py` —— 返回文本补一行，否则 `notes` 写进去了却没人读得到：

```python
                f"长期偏好：{data.get('preferences', {})}
"
                f"自由观察：{data.get('notes', [])}"
```

**注意：`Base.metadata.create_all` 只建表，不会给已存在的表加列。** 如果 `user_profile` 表已经有数据，需要手动执行一次：

```sql
ALTER TABLE user_profile ADD COLUMN notes JSON NOT NULL;
```

MySQL 的 JSON 列不能有字面默认值，已有行需要一并回填：

```sql
UPDATE user_profile SET notes = JSON_ARRAY() WHERE notes IS NULL;
```

开发库里没有要紧数据的话，直接 `DROP TABLE user_profile` 让启动钩子重建更省事。

- [ ] **Step 4: 实现合并与追加函数并接入**

在 `backend/dao/user_profile_mapper.py` 的 import 之后、`class UserProfileMapper` 之前加入：

```python
# weak_points / preferences 这两个 JSON 字段必须合并而非替换，
# 否则写入一个新知识点会把已有的全部冲掉，违背「画像只增不改」。
_MERGE_FIELDS = ("weak_points", "preferences")

# notes 是自由文本数组，语义是追加而非合并
_APPEND_FIELDS = ("notes",)

NOTES_MAX_SIZE = 50


def merge_json_field(old: dict | None, new: dict | None) -> dict:
    """浅合并两个 JSON 字段：新 key 追加，同 key 以新值为准，老 key 一律保留。"""
    merged = dict(old or {})
    merged.update(new or {})
    return merged


def append_notes(old: list | None, new: list | None, max_size: int = NOTES_MAX_SIZE) -> list:
    """
    追加自由观察记录：跳过完全重复的条目，超过 max_size 时丢弃最旧的。
    限长是必要的——notes 只增不减的话，画像会无限膨胀。
    """
    result = list(old or [])
    for item in new or []:
        if item not in result:
            result.append(item)
    if len(result) > max_size:
        result = result[-max_size:]
    return result
```

然后把 `update_user_profile` 里的赋值循环改成：

```python
                dto_data = profile_dto.model_dump(exclude_none=True)
                for key, value in dto_data.items():
                    if not hasattr(user_profile, key):
                        continue
                    if key in _MERGE_FIELDS:
                        setattr(user_profile, key, merge_json_field(
                            getattr(user_profile, key), value
                        ))
                    elif key in _APPEND_FIELDS:
                        setattr(user_profile, key, append_notes(
                            getattr(user_profile, key), value
                        ))
                    else:
                        setattr(user_profile, key, value)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_profile_merge.py -v`
Expected: 8 passed

- [ ] **Step 6: 提交**

```bash
git add backend/dao/user_profile_mapper.py backend/model/user_profile.py backend/schemas/ backend/agents/memory/long_term_memory.py backend/tests/test_profile_merge.py
git commit -m "feat: 画像 JSON 字段改为合并写入，新增 notes 自由观察逃生口"
```

---

## Task 9: 画像候选池（频次确认）

事实类字段（`grade`、`subject`）直写；偏好类字段（`weak_points`、`preferences`）先进候选池，同一字段路径第二次命中才晋升为长期画像。

**Files:**
- Create: `backend/agents/memory/profile_candidates.py`
- Modify: `backend/agents/tools/user_profile_save_tool.py`
- Create: `backend/tests/test_profile_candidates.py`

**Interfaces:**
- Consumes: `get_redis_client()`
- Produces:
  - `ProfileCandidateStore.__init__(ttl: int = 604800, promote_threshold: int = 2)`
  - `async offer(user_id: int, field: str, sub_key: str, value) -> tuple[int, bool]` —— 返回 `(当前计数, 是否达到晋升阈值)`；达到阈值时自动清除该候选项
  - `async get_all(user_id: int) -> dict[str, dict]`
  - `async clear(user_id: int) -> None`
  - `field` 取值为 `"weak_points"` 或 `"preferences"`；候选池内部 key 为 `f"{field}.{sub_key}"`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/test_profile_candidates.py`：

```python
import pytest

from backend.agents.memory.profile_candidates import ProfileCandidateStore

USER = 42


@pytest.fixture
def store(redis_test_client, monkeypatch):
    s = ProfileCandidateStore(promote_threshold=2)
    monkeypatch.setattr(s, "_client", redis_test_client)
    return s


async def test_first_offer_does_not_promote(store):
    count, promoted = await store.offer(USER, "preferences", "题目风格", "不要雷同")
    assert (count, promoted) == (1, False)


async def test_second_offer_promotes(store):
    await store.offer(USER, "preferences", "题目风格", "不要雷同")
    count, promoted = await store.offer(USER, "preferences", "题目风格", "不要雷同")
    assert (count, promoted) == (2, True)


async def test_promoted_candidate_is_cleared(store):
    await store.offer(USER, "preferences", "题目风格", "不要雷同")
    await store.offer(USER, "preferences", "题目风格", "不要雷同")
    assert await store.get_all(USER) == {}, "晋升后候选项应被清除，避免重复晋升"


async def test_different_fields_count_separately(store):
    await store.offer(USER, "preferences", "题目风格", "不要雷同")
    count, promoted = await store.offer(USER, "weak_points", "一元一次方程", "薄弱")
    assert (count, promoted) == (1, False), "不同字段路径各自独立计数"


async def test_latest_value_wins(store):
    await store.offer(USER, "preferences", "难度", "简单")
    count, promoted = await store.offer(USER, "preferences", "难度", "困难")
    assert promoted is True
    # 晋升时返回的应当是最新的值，冲突时以新的为准
    assert (count, promoted) == (2, True)


async def test_ttl_is_set(store):
    await store.offer(USER, "preferences", "题目风格", "不要雷同")
    ttl = await store._client.ttl(ProfileCandidateStore.key(USER))
    assert 0 < ttl <= 604800
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_profile_candidates.py -v`
Expected: FAIL，`ModuleNotFoundError: backend.agents.memory.profile_candidates`

- [ ] **Step 3: 实现候选池**

创建 `backend/agents/memory/profile_candidates.py`：

```python
"""
画像候选池：用频次统计区分「一次性诉求」和「稳定偏好」。

为什么不靠单次 LLM 判断：
「这题再简单点」和「以后题目不要雷同」的区别，靠提示词约束准确率不够。
改用统计：偏好第一次出现只记候选，同一字段路径第二次出现才写入长期画像。
代价是偏好生效滞后一轮，换来的是判定不再依赖模型的主观判断。

「同一语义偏好如何认定为同一个」这个难题在这里被绕开了——
LLM 提取时已经把偏好结构化成了字段名，同键即同偏好，不需要语义相似度计算。

存储：Redis hash  profile_cand:{user_id}
  field = "{字段名}.{子键}"，如 "preferences.题目风格"
  value = {"value": ..., "count": n, "first_seen": ts}
"""
import json
import time

from redis.exceptions import RedisError

from backend.middleware.logging import get_logger
from backend.utils.redis_client import get_redis_client

logger = get_logger(__name__)

DEFAULT_TTL = 604800  # 7 天
DEFAULT_THRESHOLD = 2


class ProfileCandidateStore:
    def __init__(self, ttl: int = DEFAULT_TTL, promote_threshold: int = DEFAULT_THRESHOLD):
        self.ttl = ttl
        self.promote_threshold = promote_threshold
        self._client = get_redis_client().client

    @staticmethod
    def key(user_id: int) -> str:
        return f"profile_cand:{user_id}"

    async def offer(self, user_id: int, field: str, sub_key: str, value) -> tuple[int, bool]:
        """
        提交一个候选偏好，计数 +1。

        :return: (当前计数, 是否达到晋升阈值)。达到阈值时该候选项会被清除，
                 由调用方负责把它写入长期画像。
        """
        redis_key = self.key(user_id)
        field_path = f"{field}.{sub_key}"
        try:
            raw = await self._client.hget(redis_key, field_path)
            record = json.loads(raw) if raw else {"count": 0, "first_seen": int(time.time())}
            # 冲突时以新的为准
            record["value"] = value
            record["count"] = record.get("count", 0) + 1

            if record["count"] >= self.promote_threshold:
                await self._client.hdel(redis_key, field_path)
                return record["count"], True

            await self._client.hset(redis_key, field_path, json.dumps(record, ensure_ascii=False))
            await self._client.expire(redis_key, self.ttl)
            return record["count"], False
        except RedisError as e:
            # Redis 不可用时不阻断主流程，当作「未达阈值」处理
            logger.error("画像候选池写入失败：%s", e, exc_info=True)
            return 0, False

    async def get_all(self, user_id: int) -> dict[str, dict]:
        try:
            raw = await self._client.hgetall(self.key(user_id))
        except RedisError:
            return {}
        result = {}
        for field_path, value in raw.items():
            try:
                result[field_path] = json.loads(value)
            except json.JSONDecodeError:
                continue
        return result

    async def clear(self, user_id: int) -> None:
        await self._client.delete(self.key(user_id))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_profile_candidates.py -v`
Expected: 6 passed

- [ ] **Step 5: 把候选池接进 UserProfileSaveTool**

用下面的内容整体替换 `backend/agents/tools/user_profile_save_tool.py`：

```python
from typing import Type, Optional

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from backend.agents.memory.long_term_memory import LongTermMemory
from backend.agents.memory.profile_candidates import ProfileCandidateStore
from backend.agents.memory.short_term_memory import get_short_term_memory
from backend.dao.user_profile_mapper import get_user_profile_mapper
from backend.schemas.request.ltm_request import LTMRequest

# grade / subject 是事实陈述（「我初二」），不存在「一次性 vs 稳定」的歧义，第一次就落库。
# weak_points / preferences 是偏好，走候选池频次确认。
_FACT_FIELDS = ("grade", "subject")
_PREFERENCE_FIELDS = ("weak_points", "preferences")

_candidate_store = ProfileCandidateStore()


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
        try:
            mapper = await get_user_profile_mapper()
            stm = await get_short_term_memory()
            ltm = LongTermMemory(mapper, stm)

            # 事实类字段与 notes 直写：
            # grade/subject 是事实陈述，notes 是自由观察，两者都不存在
            # 「一次性 vs 稳定」的歧义，不需要频次确认
            to_write: dict = {}
            for field, value in (("grade", grade), ("subject", subject), ("notes", notes)):
                if value:
                    to_write[field] = value

            # 偏好类字段逐 key 走候选池，只有晋升的才写
            pending_notes: list[str] = []
            for field, payload in (("weak_points", weak_points), ("preferences", preferences)):
                if not payload:
                    continue
                promoted: dict = {}
                for sub_key, value in payload.items():
                    count, is_promoted = await _candidate_store.offer(
                        user_id, field, sub_key, value
                    )
                    if is_promoted:
                        promoted[sub_key] = value
                    else:
                        pending_notes.append(
                            f"{sub_key}（{count}/{_candidate_store.promote_threshold}）"
                        )
                if promoted:
                    to_write[field] = promoted

            if to_write:
                await ltm.add_or_update(LTMRequest(user_id=user_id, **to_write))

            # 如实汇报状态，避免 Agent 误以为候选偏好已经生效
            parts = []
            if to_write:
                parts.append(f"已写入长期画像：{'、'.join(to_write)}")
            if pending_notes:
                parts.append(f"已记录候选偏好，再次出现时写入：{'、'.join(pending_notes)}")
            if not parts:
                return "【用户画像】本次没有可保存的内容"
            return "【用户画像】" + "；".join(parts)
        except Exception as e:
            return f"【用户画像】保存失败：{str(e)}"
```

- [ ] **Step 6: 运行全部测试**

Run: `cd backend && python -m pytest tests/ -v`
Expected: 全部 passed

- [ ] **Step 7: 提交**

```bash
git add backend/agents/memory/profile_candidates.py backend/agents/tools/user_profile_save_tool.py backend/tests/test_profile_candidates.py
git commit -m "feat: 画像写入引入候选池频次确认，区分一次性诉求与稳定偏好"
```

---

## Task 10: 画像维度词表（profile_schema Skill）

Task 9 的工具描述里承诺了「键必须取自 profile_schema 的词表」，这个任务把它兑现。

**为什么键名必须受控**：Task 9 的频次确认建立在「同键即同偏好」这一条假设上。键名若自由生成，同一个偏好在三轮里可能变成 `preferences.题目风格`、`preferences.出题风格`、`preferences.题目偏好`，计数永远到不了阈值，偏好永远晋升不了——而且**不报任何错**。

**为什么词表放在 SKILL.md 而不是代码里**：`loader.py` 有 mtime 缓存，编辑 Markdown 下一次调用就生效。加一个新的画像维度不需要改表、不需要改代码、不需要重启，和 `question_variant` / `memory_refinement` 是同一套热加载机制。

**词表外的键不丢弃，自动转存 `notes`**——既不静默失效，也不丢信息。

**Files:**
- Create: `backend/agents/skills/profile_schema/SKILL.md`
- Modify: `backend/agents/skills/skill_runner.py`（新增 `load_vocab`）
- Modify: `backend/agents/tools/user_profile_save_tool.py`（按词表分流）
- Create: `backend/tests/test_profile_schema_skill.py`

**Interfaces:**
- Consumes: `load_skill_code(name, tag)`（已存在于 `loader.py`）
- Produces: `load_vocab(name: str, tag: str = "vocab") -> dict[str, list[str]]` —— 返回 `{"weak_points": [...], "preferences": [...]}`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/test_profile_schema_skill.py`：

```python
from backend.agents.skills import get_skill_meta, match_triggers
from backend.agents.skills.skill_runner import load_vocab


def test_skill_is_discoverable():
    meta = get_skill_meta("profile_schema")
    assert meta["name"] == "profile_schema"
    assert meta["description"], "描述会进 ReAct 主提示词，不能为空"
    assert meta["triggers"], "必须有触发词，否则字面兜底失效"


def test_triggers_match_profile_statements():
    hits = match_triggers("以后题目不要跟原题太像")
    assert "profile_schema" in hits


def test_vocab_is_loadable():
    vocab = load_vocab("profile_schema")
    assert set(vocab) == {"weak_points", "preferences"}
    assert vocab["preferences"], "偏好词表不能为空"
    assert all(isinstance(k, str) for k in vocab["preferences"])


def test_vocab_missing_returns_empty():
    """没有 vocab 代码块的 Skill 应当返回空字典，而不是抛异常。"""
    assert load_vocab("question_variant") == {}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_profile_schema_skill.py -v`
Expected: FAIL，`ImportError: cannot import name 'load_vocab'`

- [ ] **Step 3: 写 SKILL.md**

创建 `backend/agents/skills/profile_schema/SKILL.md`：

````markdown
---
name: profile_schema
description: 长期画像的可用维度词表。写入用户画像前加载，确保键名取自受控词表
triggers: [以后, 我是几年级, 我的偏好, 记住我, 我不喜欢, 我比较擅长, 我总是, 每次都]
version: 1.0
---

# 角色
你在把用户的陈述写入长期画像。画像的**键名必须取自下面的词表**，值可以自由撰写。

# 为什么键名不能自由发挥
画像的写入用频次统计来区分「一次性诉求」和「稳定偏好」：同一个键在不同轮次出现两次才会真正落库。
如果你每次给同一个偏好起不同的名字，计数永远累积不到阈值，这条偏好会被永久丢弃且不会报错。

# 可用维度

## weak_points（薄弱知识点）
键为知识点名称，取自人教版数学教材的标准表述。值描述掌握程度。

常用键：一元一次方程、一元二次方程、不等式、函数、几何证明、三角形、圆、
行程应用题、工程应用题、概率统计、有理数运算、整式运算、因式分解

值的取值：薄弱 / 一般 / 已掌握

## preferences（长期偏好）
键为偏好维度，值为该维度上的具体取向。

| 键 | 含义 | 值的示例 |
|---|---|---|
| 题目风格 | 出题的整体倾向 | 不要与原题雷同、贴近生活情境、偏纯计算 |
| 难度倾向 | 期望的难度 | 偏简单、循序渐进、偏挑战 |
| 讲解方式 | 希望怎么被讲解 | 先思路后答案、只要答案、要分步骤 |
| 题型偏好 | 偏爱的题型 | 选择题、填空题、解答题、应用题 |
| 输出长度 | 回答的详略 | 简短、详细 |

# 使用规则
1. 只使用上面列出的键。值可以自由撰写，不受词表限制。
2. **找不到合适的键时，写进 `notes` 而不是新造一个键。** `notes` 是自由文本数组，
   每条一句话，追加写入且立即生效。
3. 年级和学科（grade / subject）是事实陈述，直接写，不受频次确认约束。
4. 「这题再简单点」是一次性诉求，不要写画像；「以后题目都简单点」才是稳定偏好。

# 机器可读词表

```skill:vocab
# 供 UserProfileSaveTool 做键名分流；词表外的键会被自动转存到 notes
VOCAB = {
    "weak_points": [
        "一元一次方程", "一元二次方程", "不等式", "函数", "几何证明",
        "三角形", "圆", "行程应用题", "工程应用题", "概率统计",
        "有理数运算", "整式运算", "因式分解",
    ],
    "preferences": [
        "题目风格", "难度倾向", "讲解方式", "题型偏好", "输出长度",
    ],
}
```
````

- [ ] **Step 4: 实现 load_vocab**

在 `backend/agents/skills/skill_runner.py` 末尾追加：

```python
def load_vocab(name: str, tag: str = "vocab") -> dict:
    """
    执行指定 Skill 的 vocab 代码片段，返回其中定义的 VOCAB 字典。
    Skill 没有 vocab 片段时返回空字典——调用方应把「空词表」理解为「不做限制」。
    """
    from backend.agents.skills.loader import load_skill_code
    code = load_skill_code(name, tag)
    if code is None:
        return {}
    ns: dict = {}
    exec(compile(code, f"<skill:{name}:{tag}>", "exec"), ns)
    return ns.get("VOCAB", {})
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_profile_schema_skill.py -v`
Expected: 4 passed

- [ ] **Step 6: 让 UserProfileSaveTool 按词表分流**

在 `backend/agents/tools/user_profile_save_tool.py` 顶部加入导入：

```python
from backend.agents.skills.skill_runner import load_vocab
```

把 `_arun` 里遍历偏好字段的那段循环改成：

```python
            # 偏好类字段逐 key 走候选池，只有晋升的才写
            vocab = load_vocab("profile_schema")
            pending_notes: list[str] = []
            overflow: list[str] = []  # 词表外的键，转存 notes
            for field, payload in (("weak_points", weak_points), ("preferences", preferences)):
                if not payload:
                    continue
                allowed = set(vocab.get(field, []))
                promoted: dict = {}
                for sub_key, value in payload.items():
                    if allowed and sub_key not in allowed:
                        # 词表外的键不丢弃也不入池：入池会永远累积不到阈值，
                        # 丢弃会丢信息，所以转存到 notes 立即生效
                        overflow.append(f"{sub_key}：{value}")
                        continue
                    count, is_promoted = await _candidate_store.offer(
                        user_id, field, sub_key, value
                    )
                    if is_promoted:
                        promoted[sub_key] = value
                    else:
                        pending_notes.append(
                            f"{sub_key}（{count}/{_candidate_store.promote_threshold}）"
                        )
                if promoted:
                    to_write[field] = promoted

            if overflow:
                to_write["notes"] = to_write.get("notes", []) + overflow
```

并在汇报文本里补一句：

```python
            if overflow:
                parts.append(f"词表外的内容已转存 notes：{'、'.join(overflow)}")
```

- [ ] **Step 7: 写分流的测试**

追加到 `backend/tests/test_profile_schema_skill.py`：

```python
async def test_unknown_key_is_routed_to_notes(monkeypatch):
    """词表外的键必须转存 notes，既不静默失效也不丢信息。"""
    import backend.agents.tools.user_profile_save_tool as mod

    written = {}

    class FakeLTM:
        async def add_or_update(self, request):
            written.update(request.model_dump(exclude_none=True))

    class FakeStore:
        promote_threshold = 2

        async def offer(self, user_id, field, sub_key, value):
            return 1, False

    monkeypatch.setattr(mod, "LongTermMemory", lambda *a, **kw: FakeLTM())
    monkeypatch.setattr(mod, "_candidate_store", FakeStore())
    monkeypatch.setattr(mod, "get_user_profile_mapper", _coro(None))
    monkeypatch.setattr(mod, "get_short_term_memory", _coro(None))

    out = await mod.UserProfileSaveTool()._arun(
        user_id=1,
        preferences={"题目风格": "不要雷同", "做题时段": "晚上"},
    )

    assert "做题时段：晚上" in written.get("notes", []), "词表外的键应转存 notes"
    assert "题目风格" in out


def _coro(value):
    async def _inner(*args, **kwargs):
        return value
    return _inner
```

- [ ] **Step 8: 运行全部测试**

Run: `cd backend && python -m pytest tests/ -v`
Expected: 全部 passed

- [ ] **Step 9: 提交**

```bash
git add backend/agents/skills/profile_schema/ backend/agents/skills/skill_runner.py backend/agents/tools/user_profile_save_tool.py backend/tests/test_profile_schema_skill.py
git commit -m "feat: 画像维度词表下沉到 SKILL.md，词表外的键自动转存 notes"
```

---

## Task 11: 向量层改用 retriever 检索

现有的 `VectorStoreManager.query()` 走 `as_query_engine().query()`，那是 RAG 问答，内部会**额外调一次 LLM** 来合成答案。我们只要检索到的原文。

**Files:**
- Modify: `backend/agents/memory/vector_store_manager.py`（新增 `retrieve`）
- Create: `backend/tests/test_vector_retrieve.py`

**Interfaces:**
- Consumes: Task 3 的 `get_vector_executor()`
- Produces: `async VectorStoreManager.retrieve(query_text: str, user_id: int | None = None, top_k: int = 3, min_score: float = 0.3) -> list[str]`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/test_vector_retrieve.py`：

```python
from types import SimpleNamespace

import pytest

from backend.agents.memory.vector_store_manager import VectorStoreManager


def _node(text: str, score: float):
    """伪造 LlamaIndex 的 NodeWithScore。"""
    return SimpleNamespace(score=score, node=SimpleNamespace(get_content=lambda: text))


@pytest.fixture
def manager(monkeypatch):
    """绕过重量级 __init__，只装配 retrieve 需要的部件。"""
    m = VectorStoreManager.__new__(VectorStoreManager)
    m.embed_model = object()
    m._index = None
    return m


def _stub_retriever(manager, monkeypatch, nodes):
    class FakeRetriever:
        def retrieve(self, query):
            return nodes

    manager._index = SimpleNamespace(as_retriever=lambda **kw: FakeRetriever())


async def test_returns_texts_above_threshold(manager, monkeypatch):
    _stub_retriever(manager, monkeypatch, [
        _node("用户偏好带解析的题目", 0.82),
        _node("用户是七年级学生", 0.55),
    ])
    got = await manager.retrieve("生成一道变式题", user_id=1)
    assert got == ["用户偏好带解析的题目", "用户是七年级学生"]


async def test_filters_low_score_hits(manager, monkeypatch):
    """低相关命中必须丢弃——这正是「无关内容干扰模型」的直接防线。"""
    _stub_retriever(manager, monkeypatch, [
        _node("高相关", 0.9),
        _node("八竿子打不着", 0.11),
    ])
    got = await manager.retrieve("生成一道变式题", user_id=1, min_score=0.3)
    assert got == ["高相关"]


async def test_no_hits_returns_empty(manager, monkeypatch):
    _stub_retriever(manager, monkeypatch, [])
    assert await manager.retrieve("随便问问", user_id=1) == []


async def test_failure_degrades_to_empty(manager):
    """向量层是增强不是依赖：检索失败时返回空列表，不能抛异常打断工具。"""
    class Boom:
        def as_retriever(self, **kw):
            raise RuntimeError("chroma 挂了")

    manager._index = Boom()
    assert await manager.retrieve("生成一道变式题", user_id=1) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_vector_retrieve.py -v`
Expected: FAIL，`AttributeError: 'VectorStoreManager' object has no attribute 'retrieve'`

- [ ] **Step 3: 实现 retrieve**

在 `backend/agents/memory/vector_store_manager.py` 的 `query` 方法之后加入：

```python
    async def retrieve(
        self,
        query_text: str,
        user_id: int = None,
        top_k: int = 3,
        min_score: float = 0.3,
    ) -> list[str]:
        """
        纯向量检索，返回命中的原文列表。

        与 query() 的区别：query() 走 as_query_engine()，那是 RAG 问答，
        内部会额外调一次 LLM 合成答案。这里只要检索结果本身，用 as_retriever()，
        不触发任何 LLM 调用。

        低于 min_score 的命中直接丢弃，避免无关内容干扰模型。
        任何异常都降级为空列表：向量层是增强，不是依赖。
        """
        try:
            filters = None
            if user_id is not None:
                filters = MetadataFilters(
                    filters=[ExactMatchFilter(key="user_id", value=user_id)]
                )

            retriever = self._index.as_retriever(
                similarity_top_k=top_k,
                filters=filters,
                embed_model=self.embed_model,
            )
            loop = asyncio.get_running_loop()
            nodes = await loop.run_in_executor(
                get_vector_executor(), partial(retriever.retrieve, query_text)
            )

            texts: list[str] = []
            for node in nodes or []:
                score = getattr(node, "score", None)
                if score is not None and score < min_score:
                    continue
                texts.append(node.node.get_content())
            return texts
        except Exception as e:
            logger.error("向量检索失败，本次跳过记忆注入：%s", e, exc_info=True)
            return []
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_vector_retrieve.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add backend/agents/memory/vector_store_manager.py backend/tests/test_vector_retrieve.py
git commit -m "feat: 向量层新增 retrieve，只检索不做 RAG 合成"
```

---

## Task 12: 工具级定点注入向量记忆

只在生题和通用问答两个工具执行前检索一次，ReAct 主循环完全不碰向量库。

**Files:**
- Modify: `backend/agents/tools/question_set_tool.py`
- Modify: `backend/agents/tools/common_tool.py`
- Modify: `backend/agents/agent/react_agent.py`（`tool_exec_node` 注入 `user_id`）
- Create: `backend/agents/memory/recall.py`
- Create: `backend/tests/test_recall_injection.py`

**Interfaces:**
- Consumes: Task 11 的 `VectorStoreManager.retrieve`
- Produces: `async recall_context(query: str, user_id: int | None) -> str` —— 返回可直接拼进 prompt 的一段文本，无命中时返回空字符串

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/test_recall_injection.py`：

```python
import backend.agents.memory.recall as recall_mod
from backend.agents.memory.recall import recall_context


async def test_returns_empty_without_user_id():
    assert await recall_context("生成一道变式题", None) == ""


async def test_formats_hits_as_prompt_section(monkeypatch):
    class FakeStore:
        async def retrieve(self, query_text, user_id=None, top_k=3, min_score=0.3):
            return ["用户偏好带解析的题目", "用户不喜欢与原题雷同"]

    monkeypatch.setattr(recall_mod, "_store", lambda: FakeStore())

    got = await recall_context("生成一道变式题", 1)

    assert "历史偏好" in got
    assert "用户偏好带解析的题目" in got
    assert "用户不喜欢与原题雷同" in got


async def test_no_hits_returns_empty(monkeypatch):
    class FakeStore:
        async def retrieve(self, query_text, user_id=None, top_k=3, min_score=0.3):
            return []

    monkeypatch.setattr(recall_mod, "_store", lambda: FakeStore())
    assert await recall_context("生成一道变式题", 1) == ""


async def test_question_set_tool_injects_recall(monkeypatch):
    """命中历史偏好时，内容必须进入生题的 system prompt（验收标准 5）。"""
    from backend.agents.tools.question_set_tool import QuestionSetTool
    import backend.agents.tools.question_set_tool as qs_mod

    seen = {}

    async def fake_extract(text):
        return {"difficulty": "中等", "knowledge_points": ["一元一次方程"]}

    async def fake_question_set(payload):
        seen["recall"] = payload.get("recall", "")
        return {"result": "题目...答案：x=4"}

    monkeypatch.setattr(qs_mod, "async_extract_tool", fake_extract)
    monkeypatch.setattr(qs_mod, "async_question_set_tool", fake_question_set)
    monkeypatch.setattr(qs_mod, "recall_context", lambda q, u: _async("【历史偏好】不要雷同"))

    out = await QuestionSetTool()._arun(query="解方程 2x+9=5x-3", user_id=1)

    assert "不要雷同" in seen["recall"]
    assert "题目" in out


def _async(value):
    async def _inner():
        return value
    return _inner()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_recall_injection.py -v`
Expected: FAIL，`ModuleNotFoundError: backend.agents.memory.recall`

- [ ] **Step 3: 实现 recall 模块**

创建 `backend/agents/memory/recall.py`：

```python
"""
向量记忆的定点召回。

读取策略是「工具级定点注入」而不是「每轮请求统一检索」：
ReAct 主循环不碰向量库，成本只在真要生成内容时付一次，
也避免无关的历史记忆干扰路由决策。
"""
from backend.agents.memory.vector_store_manager import VectorStoreManager
from backend.middleware.logging import get_logger

logger = get_logger(__name__)

TOP_K = 3
MIN_SCORE = 0.3


def _store() -> VectorStoreManager:
    """延迟取单例：便于测试替换，也避免导入期就初始化 Chroma。"""
    return VectorStoreManager()


async def recall_context(query: str, user_id: int | None) -> str:
    """
    检索该用户的历史记忆，格式化成可直接拼进 system prompt 的一段文本。
    无 user_id、无命中或检索失败时一律返回空字符串——调用方照常执行。
    """
    if user_id is None:
        return ""

    hits = await _store().retrieve(
        query_text=query, user_id=user_id, top_k=TOP_K, min_score=MIN_SCORE
    )
    if not hits:
        return ""

    lines = ["【该学生的历史偏好与学情记录】"]
    lines.extend(f"- {h}" for h in hits)
    lines.append("请在不违反上述题目规范的前提下，尽量贴合这些历史偏好。")
    return "\n".join(lines)
```

- [ ] **Step 4: 让两个工具接收 user_id 并注入召回结果**

`backend/agents/tools/question_set_tool.py` 的 `_arun` 改为：

```python
    async def _arun(self, query: str, user_id: Optional[int] = None) -> str:
        """执行题目生成工具"""
        try:
            extract = await async_extract_tool(query)
            new_input = {
                'input': query,
                'extract': extract,
                'recall': await recall_context(query, user_id),
            }
            result = await async_question_set_tool(new_input)
            if 'error' in result:
                return f"【题目生成】生成变式题失败：{result['error']}"
            return f"【题目生成】已生成变式题：\n{result['result']}"
        except Exception as e:
            return f"【题目生成】生成变式题失败：{str(e)}"
```

顶部补上 `from typing import Optional` 与 `from backend.agents.memory.recall import recall_context`。

`backend/agents/agent/question_set_agent.py` 的 `async_question_set_tool` 里，把召回内容拼进 system prompt：

```python
        system_body = load_skill("question_variant")
        recall = text.get('recall', '')
        if recall:
            system_body = f"{system_body}\n\n{recall}"
```

`backend/agents/agent/common_agent.py` —— 在 system 模板末尾加一个 `{recall}` 占位符（原文里没有花括号，加这一个是安全的），并让 `async_common_tool` 接收它：

```python
COMMON_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是专业教育解题助手，负责：
        - 提供解题步骤、思路、方法、答案
        - 解释知识点、难度、考点、易错点
        - 解答题目相关疑问
        - 回答应该简练，避免使用复杂的词汇
        - 回答清晰易懂，不生成新题目。
{recall}"""),
    ("user", "{input}")
])


async def async_common_tool(text: str, recall: str = "") -> str:
    common_agent = build_common_agent(streaming=True)
    common_chain = COMMON_PROMPT | common_agent
    response = await common_chain.ainvoke({'input': text, 'recall': recall})
    return response.content
```

`backend/agents/tools/common_tool.py` 的 `_arun`：

```python
    async def _arun(self, query: str, user_id: Optional[int] = None) -> str:
        """执行通用工具"""
        recall = await recall_context(query, user_id)
        return await async_common_tool(query, recall=recall)
```

顶部补上 `from typing import Optional` 与 `from backend.agents.memory.recall import recall_context`。

- [ ] **Step 5: 让 tool_exec_node 注入 user_id**

在 `backend/agents/agent/react_agent.py` 的 `tool_exec_node` 中，把注入 `user_id` 的工具名集合扩充：

```python
    # user_profile_*_tool 与需要召回历史记忆的工具只需 user_id，由 state 注入
    if func_name in (
        "user_profile_save_tool",
        "user_profile_query_tool",
        "user_profile_delete_tool",
        "question_set_tool",
        "common_tool",
    ):
        args['user_id'] = state['user_id']
```

- [ ] **Step 6: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_recall_injection.py -v`
Expected: 4 passed

- [ ] **Step 7: 提交**

```bash
git add backend/agents/memory/recall.py backend/agents/tools/question_set_tool.py backend/agents/tools/common_tool.py backend/agents/agent/question_set_agent.py backend/agents/agent/common_agent.py backend/agents/agent/react_agent.py backend/tests/test_recall_injection.py
git commit -m "feat: 生题与问答工具执行前定点注入向量记忆"
```

---

## Task 13: 长期画像注入 ReAct system prompt

画像现在每轮都查了却被丢弃。把它注入 **system prompt** 而不是 `user_input`——画像是「这个学生是谁」的稳定背景，混进 `user_input` 会让模型把它当成当前诉求的一部分。

由 `agent_api` 在构建 `GraphState` 时填好文本，`react_think_node` 不做任何 IO。

**Files:**
- Modify: `backend/agents/agent/tools.py`（`GraphState` 新增 `profile_text`）
- Modify: `backend/agents/agent/react_agent.py`（`build_react_system_prompt` 增加画像段）
- Modify: `backend/api/user_api/agent_api.py`（两个端点都填 `profile_text`）
- Modify: `backend/tests/test_react_agent_async.py`（追加测试）

**Interfaces:**
- Consumes: `MemoryManager.get_memory_for_planner` 返回的 `long_memory`
- Produces:
  - `GraphState` 新增键 `profile_text: str`
  - `format_profile(profile) -> str`（定义在 `react_agent.py`）
  - `build_react_system_prompt(hint_skills: list[str] | None = None, profile_text: str = "") -> str`

- [ ] **Step 1: 写失败的测试**

追加到 `backend/tests/test_react_agent_async.py` 末尾：

```python
def test_profile_section_absent_when_no_profile():
    prompt = react_agent.build_react_system_prompt(None, "")
    assert "学生画像" not in prompt


def test_profile_section_present_when_given():
    prompt = react_agent.build_react_system_prompt(None, "年级：七年级 | 学科：数学")
    assert "学生画像" in prompt
    assert "七年级" in prompt


def test_format_profile_handles_none():
    assert react_agent.format_profile(None) == ""


def test_format_profile_renders_fields():
    from types import SimpleNamespace

    profile = SimpleNamespace(
        grade="七年级",
        subject="数学",
        weak_points={"一元一次方程": "薄弱"},
        preferences={"题目风格": "不要雷同"},
    )
    text = react_agent.format_profile(profile)
    assert "七年级" in text
    assert "一元一次方程" in text
    assert "不要雷同" in text


def test_format_profile_excludes_notes():
    """notes 是自由文本，只在主动查询画像时返回，不能进每轮的 system prompt。"""
    from types import SimpleNamespace

    profile = SimpleNamespace(
        grade="七年级",
        subject="数学",
        weak_points={},
        preferences={},
        notes=["做题时喜欢先看思路", "晚上效率更高"],
    )
    text = react_agent.format_profile(profile)
    assert "先看思路" not in text, "notes 会把每轮的画像段撑大，不应注入" 


async def test_react_think_node_passes_profile_into_prompt(monkeypatch):
    """画像必须进 system prompt，而不是混进 user_input。"""
    captured = {}
    content = '{"thought":"t","action":"","action_args":{},"final_result":"ok"}'

    class Recorder(_FakeLLM):
        async def ainvoke(self, messages):
            captured["system"] = messages[0].content
            captured["human"] = messages[1].content
            return await super().ainvoke(messages)

    monkeypatch.setattr(
        react_agent, "get_llm", lambda *a, **kw: Recorder(content, {})
    )

    state = _state()
    state["profile_text"] = "年级：八年级 | 学科：数学"
    await react_agent.react_think_node(state)

    assert "八年级" in captured["system"]
    assert "八年级" not in captured["human"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_react_agent_async.py -v`
Expected: FAIL，`build_react_system_prompt() takes 0 or 1 positional arguments`、`format_profile` 不存在

- [ ] **Step 3: GraphState 新增字段**

在 `backend/agents/agent/tools.py` 的 `GraphState` 里，`session_id` 之后加入：

```python
    profile_text: str  # 由 API 层预先格式化好的长期画像，节点内不做 IO
```

- [ ] **Step 4: 实现画像格式化与注入**

在 `backend/agents/agent/react_agent.py` 中，`build_react_system_prompt` 之前加入：

```python
def format_profile(profile) -> str:
    """
    把长期画像压成一行段落文本。画像为 None 时返回空字符串。
    在 API 层调用一次，结果放进 GraphState，避免每轮 ReAct 都查一次库。
    """
    if profile is None:
        return ""
    data = profile.model_dump() if hasattr(profile, "model_dump") else {
        "grade": getattr(profile, "grade", ""),
        "subject": getattr(profile, "subject", ""),
        "weak_points": getattr(profile, "weak_points", {}) or {},
        "preferences": getattr(profile, "preferences", {}) or {},
    }

    lines = [f"年级：{data.get('grade') or '未知'} | 学科：{data.get('subject') or '未知'}"]
    weak = data.get("weak_points") or {}
    if weak:
        lines.append("薄弱知识点：" + "、".join(weak.keys()))
    prefs = data.get("preferences") or {}
    if prefs:
        lines.append("长期偏好：" + "、".join(f"{k}={v}" for k, v in prefs.items()))
    return "\n".join(lines)
```

把 `build_react_system_prompt` 的签名与结尾改为：

```python
def build_react_system_prompt(
    hint_skills: list[str] | None = None,
    profile_text: str = "",
) -> str:
```

在函数末尾 `return prompt` 之前、`hint_skills` 段之后加入：

```python
    if profile_text:
        prompt += (
            f"\n# 学生画像\n"
            f"以下是该学生的稳定背景信息，用于让回答贴合其学情；"
            f"它不是本次的提问内容，不要直接复述。\n{profile_text}\n"
        )
```

在 `react_think_node` 里改为：

```python
    system_text = build_react_system_prompt(hint_skills, state.get('profile_text', ''))
```

- [ ] **Step 5: API 层填充 profile_text**

在 `backend/api/user_api/agent_api.py` 顶部导入：

```python
from backend.agents.agent.react_agent import get_app, format_profile
```

`analyse` 与 `_stream_generator` 两处，在拿到 `memory_data` 之后加：

```python
        profile_text = format_profile(memory_data.get('long_memory'))
```

并在两处的 `state` 字典里加入 `'profile_text': profile_text,`。

- [ ] **Step 6: 运行全部测试**

Run: `cd backend && python -m pytest tests/ -v`
Expected: 全部 passed

- [ ] **Step 7: 端到端人工验证**

启动服务，用同一个 `user_id` 依次发三次请求：

```bash
cd backend && python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

1. 「我是七年级的，数学不太好」→ 画像应写入 grade
2. 「以后题目不要跟原题太像」→ 日志应显示候选偏好 1/2
3. 「以后题目不要跟原题太像，出一道 2x+9=5x-3 的变式题」→ 偏好晋升写入画像，且生题时向量层召回被注入

- [ ] **Step 8: 提交**

```bash
git add backend/agents/agent/tools.py backend/agents/agent/react_agent.py backend/api/user_api/agent_api.py backend/tests/test_react_agent_async.py
git commit -m "feat: 长期画像注入 ReAct system prompt，不再查了就丢"
```

---

## Task 14: 更新 CLAUDE.md

`CLAUDE.md` 描述的仍是改造前、甚至是更早期的结构（写的是 `agents/skills/` 放 BaseTool、`SKILL_MAP`、节点名 `skill_exec_node`），与实际代码严重脱节。

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: 无
- Produces: 无

- [ ] **Step 1: 修正过时描述**

至少更正以下几处：

- 工具注册表在 `backend/agents/tools/__init__.py`，是 `TOOLS` / `TOOL_MAP`，不是 `agents/skills/` 的 `SKILL_MAP`
- 图节点名是 `react_think` 与 `execute_tool`，不是 `react_think_node` / `skill_exec_node`
- `agents/skills/` 现在是 Markdown 剧本层（`SKILL.md` + `loader.py` + `skill_runner.py`），与可执行工具是两个概念
- 工具清单补上 `user_profile_save_tool`、`user_profile_query_tool`、`user_profile_delete_tool`、`load_skill_tool`
- `python main.py` 这条启动方式在 `backend/` 下跑不通（代码用 `backend.*` 绝对导入），应删除或改成从仓库根目录运行

- [ ] **Step 2: 补写本次改造引入的约定**

新增一节，至少说明：

- 短期记忆存储结构为 Redis LIST，key 为 `stm:{user_id}:{session_id}`；写入与淘汰由一段 Lua 脚本原子完成，**新增代码不要再做读-改-写**
- 归档在后台 `asyncio.Task` 中进行，失败的条目留在 `stm:pending:*`，由启动钩子补做
- 偏好类画像字段走候选池，第二次命中才写库；事实类字段（grade / subject）与 `notes` 直写
- **画像的可用键名定义在 `agents/skills/profile_schema/SKILL.md` 的 `skill:vocab` 片段里，不在代码里**。加一个画像维度＝编辑这个 Markdown，不用改表也不用改代码。键名受控是频次确认能成立的前提（同键即同偏好），词表外的键会被自动转存到 `notes`
- `notes` 是自由文本数组，追加去重、上限 50 条，**只在 `user_profile_query_tool` 被主动调用时返回，不注入 system prompt**
- 向量层只在 `question_set_tool` / `common_tool` 执行前定点检索，ReAct 主循环不碰向量库
- 阻塞操作一律投递到 `backend/core/executors.py` 的专属线程池，不要用 `run_in_executor(None, ...)`

- [ ] **Step 3: 提交**

```bash
git add CLAUDE.md
git commit -m "docs: 更新 CLAUDE.md 至改造后的实际架构"
```

---

## 验收对照表

实施完成后，逐条核对设计文档的验收标准：

| # | 验收标准 | 覆盖它的测试 |
|---|---|---|
| 1 | 20 个并发请求不丢消息 | `test_short_term_memory.py::test_concurrent_writes_lose_nothing` |
| 2 | 触发溢出的请求不被摘要拖慢 | `test_memory_manager.py::test_request_path_does_not_wait_for_archive` |
| 3 | 归档中的记忆读不到 | `test_short_term_memory.py::test_evicted_item_is_invisible_to_readers` |
| 4 | 崩溃后重启补做且不重复 | `test_memory_manager.py::test_drain_pending_recovers_orphans` |
| 5 | 生题时注入向量召回 | `test_recall_injection.py::test_question_set_tool_injects_recall` |
| 6 | 偏好二次命中才写画像 | `test_profile_candidates.py::test_second_offer_promotes` |
| 7 | 新知识点不冲掉已有 | `test_profile_merge.py::test_old_keys_are_never_dropped` |
| 7b | notes 追加而非覆盖，且限长 | `test_profile_merge.py::test_notes_are_appended_not_replaced`、`::test_notes_are_capped_dropping_oldest` |
| 7c | 词表外的键转存 notes，不静默失效 | `test_profile_schema_skill.py::test_unknown_key_is_routed_to_notes` |
| 7d | notes 不进 system prompt | `test_react_agent_async.py::test_format_profile_excludes_notes` |
| 8 | 事件循环无同步 LLM 调用 | `test_react_agent_async.py::test_react_think_node_is_coroutine_function` |

全量跑一遍：

```bash
cd backend && python -m pytest tests/ -v
```
