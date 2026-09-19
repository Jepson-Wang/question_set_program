# 三层记忆架构改造 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把短期记忆从「JSON 字符串 RMW」换成「Redis LIST + Lua 原子淘汰 + 后台归档」，消除并发丢消息与摘要卡顿；把溢出的对话原文沉淀到 MySQL 并维护一份会话要点，注入后续请求；同时打通长期画像的读取链路，并清掉事件循环里的阻塞点。

**Architecture:** 短期窗口用 Redis LIST 存储，「入队 + 超限淘汰」在一次 Lua EVAL 里原子完成，被淘汰的条目通过 `RPOPLPUSH` 原子搬进 pending 队列，再由后台 `asyncio.Task` 写进 MySQL——因为被归档的条目已经物理移出窗口，读路径看不到它，所以不需要任何锁。记忆分三层：Redis 存最近几轮原文，MySQL 的 `memory_record` 存全部原文（只增不改）、`memory_digest` 存会话要点（永远从原文重算，不在摘要上继续摘要），`user_profile` 存跨会话的稳定画像，走「事实类直写、偏好类候选池二次命中才晋升」。请求上下文 = 会话要点 + 最近 3 轮原文 + 画像。

**Tech Stack:** Python 3.14.3 / FastAPI 0.135.1 / LangGraph 1.0.10 / langchain-core 1.2.17 / redis 7.3.0 / SQLAlchemy 2.0.48（MySQL 用 asyncmy，测试用 aiosqlite）/ pytest 9.0.2

> **设计变更（2026-09-16）**：记忆层不再使用向量库。原计划把归档的记忆经 LLM 精炼后写入 Chroma，再按语义召回；现在改为「原文进 MySQL，只增不改；会话要点由原文重算」。
> 原因：稳定信息本来就该沉淀到结构化的 `user_profile`，向量层剩下的只是情景细节，而单会话的数据量很小，按相关性挑 3 条和「整段要点一起给」差别不大；代价却是多一个存储、每条要 embedding、这份数据还不在 `mysqldump` 的备份范围里。
> 受影响的任务：Task 11、12、15 重写，新增 Task 16；Task 6 的归档目标在 Task 12 改为 MySQL。RAG 知识库仍然要用向量库，那是另一回事（见 RAG 计划）。

**Spec:** `docs/superpowers/specs/2026-09-08-memory-architecture-design.md`

## Global Constraints

- 部署形态为**单机单进程**，互斥一律用进程内机制，禁止引入 Redis 分布式锁。
- 所有 I/O（DB、Redis、LLM）保持 async；新增代码不得在事件循环里做同步阻塞调用。
- Lua 脚本内使用 `RPOPLPUSH` 而非 `LMOVE`，以兼容 Redis 6.2 以下版本。
- 短期窗口 key：`stm:{user_id}:{session_id}`；待归档队列 key：`stm:pending:{user_id}:{session_id}`。
- 窗口 TTL `86400` 秒；pending 队列 TTL `604800` 秒；画像候选池 TTL `604800` 秒。
- `max_memory_size` 默认 `10`；画像候选池晋升阈值 `promote_threshold` 默认 `2`。
- 对话原文表 `memory_record` 只增不改；用原始条目的 sha1 做唯一键，归档重试不会写出第二行。
- 会话要点表 `memory_digest` 一个会话一行；每攒够 `DIGEST_EVERY = 5` 条新记录重算一次，单次最多回看 `DIGEST_SOURCE_LIMIT = 40` 条原文。
- 摘要**永远从原文重算**，绝不在上一版摘要的基础上再摘要。
- 注入请求的上下文固定为：会话要点 + 最近 3 轮原文（+ 画像，见 Task 13）。
- 线程池 `backend/core/executors.py` 仍然保留（`max_workers=4`，`thread_name_prefix="vec"`），记忆层已经用不到它，留给 RAG 知识库。
- 不写数据迁移脚本：短期记忆换 key 前缀，旧数据靠 24h TTL 自然过期。
- 提交信息用中文，遵循 `feat:` / `fix:` / `refactor:` / `test:` 前缀。

## 任务顺序与依赖

三个阶段有严格顺序：

- **阶段一（Task 1-4，对应设计文档第三节）** 阻塞点清理。独立、低风险、先落地缩小后续 diff。
- **阶段二（Task 5-7，对应设计文档第二节）** 短期记忆 LIST 化。**必须在阶段三之前**，因为它改变了 `ShortTermMemory` 的接口。
- **阶段三（Task 8-13，对应设计文档第一节）** 三层接入策略。依赖阶段二的新接口。

Task 0 是测试基建，必须最先做。

Task 11、12 把归档目标从向量库换成 MySQL（见开头的设计变更），必须在 Task 6、7 之后做。

Task 15（摘要的输入截断与限时）和 Task 16（会话要点注入上下文）都只依赖 Task 12，可以在它之后任何时候做。

## 文件结构

**新建**

| 文件 | 职责 |
|---|---|
| `backend/tests/conftest.py` | pytest fixture：测试用 Redis 客户端（db 15）、自动清库 |
| `backend/pytest.ini` | pytest 配置，开启 asyncio 自动模式 |
| `backend/core/config.py` | 环境变量的单一加载入口（绝对路径、幂等） |
| `backend/core/executors.py` | 集中管理进程级线程池，提供获取与关停入口 |
| `backend/agents/memory/profile_candidates.py` | 画像候选池：偏好频次统计与晋升判定 |
| `backend/agents/skills/profile_schema/SKILL.md` | 画像维度词表（热加载，改词表不用改代码） |
| `backend/tests/test_short_term_memory.py` | 短期记忆 LIST 化与原子淘汰的测试 |
| `backend/tests/test_memory_manager.py` | 后台归档、ack、启动恢复、关停的测试 |
| `backend/tests/test_profile_candidates.py` | 候选池与画像写入判定的测试 |
| `backend/model/memory.py` | 对话原文表与会话要点表 |
| `backend/dao/memory_mapper.py` | 记忆表的数据访问层 |
| `backend/tests/test_memory_mapper.py` | 记忆表读写与去重的测试（跑在 SQLite 上） |
| `backend/agents/agent/session_digest_agent.py` | 会话要点生成 |
| `backend/agents/skills/session_digest/SKILL.md` | 会话要点的提示词 |
| `backend/tests/test_session_digest_agent.py` | 要点生成、输入截断与限时的测试 |
| `backend/tests/test_memory_context.py` | 注入上下文的拼装测试 |
| `backend/tests/test_react_agent_async.py` | ReAct 节点异步化与画像注入的测试 |
| `backend/tests/test_tools_sync_disabled.py` | 同步 `_run` 路径已禁用的测试 |
| `backend/tests/test_config_env.py` | 环境变量加载与工作目录无关性的测试 |
| `backend/tests/test_profile_merge.py` | JSON 字段合并与 notes 追加的测试 |
| `backend/tests/test_profile_schema_skill.py` | 词表加载与词表外键分流的测试 |

**修改**

| 文件 | 改动 |
|---|---|
| `backend/agents/memory/short_term_memory.py` | 全面重写为 Redis LIST + Lua 脚本 |
| `backend/agents/memory/memory_manager.py` | 归档转后台任务，新增 ack / 启动恢复 / 关停 |
| `backend/agents/memory/memory_manager.py`（Task 12 再改一次） | 归档目标由向量库改为 MySQL，并维护会话要点 |
| `backend/api/user_api/agent_api.py` | 记忆层接线改为 `MemoryMapper`；上下文加入会话要点 |
| `backend/agents/agent/react_agent.py` | 节点改 async；system prompt 增加画像段 |
| `backend/agents/agent/get_llm.py` | 改调 `load_env()`（原本完全不加载 .env） |
| `backend/utils/redis_client.py` | 改调 `load_env()`（原本完全不加载 .env） |
| `backend/model/__init__.py`、`agents/agent/tools.py`、三个 `*_agent.py` | 旧的 `load_dotenv()` 统一换成 `load_env()` |
| `backend/agents/agent/tools.py` | `GraphState` 新增 `profile_text` 字段 |
| `backend/agents/agent/common_agent.py` | 删除同步 `common_tool` |
| `backend/agents/agent/extract_agent.py` | 删除同步 `extract_tool` |
| `backend/agents/agent/question_set_agent.py` | 删除同步 `question_set_tool` |
| `backend/agents/tools/common_tool.py` | `_run` 禁用 |
| `backend/agents/tools/extract_knowledge_tool.py` | `_run` 禁用 |
| `backend/agents/tools/question_set_tool.py` | `_run` 禁用 |
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
# 如果不加asyncio的话，需要在每个def开头显式写入@pytest.mark.asyncio装饰器，否则会静默跳过，不执行
asyncio_mode = auto
# 限定test的收集范围，不写的话就会全局扫描，将venv中的引用库的测试文件也给测试了，测试更耗时，但无用
testpaths = tests
# 只屏蔽改不了的第三方警告；自己代码发出的警告要去改代码，不要在这里屏蔽。
# 按消息内容匹配，而不是按模块：这条警告由 pydantic 发出，但通过 stacklevel
# 归属到了 import 它的 langchain_core，按 pydantic 模块名是匹配不到的。
filterwarnings =
    ignore:Core Pydantic V1 functionality isn't compatible:UserWarning
```

`asyncio_mode = auto` 让 `async def test_*` 不用逐个加 `@pytest.mark.asyncio` 装饰器。

- [ ] **Step 3: 写 conftest.py**

测试打真实 Redis 的 db 15（不是 fakeredis）。原因：本计划的核心是一段 Lua 脚本，fakeredis 的 Lua 支持依赖 `lupa` 且覆盖不全，用真实 Redis 才能验证正确性。Redis 连不上时整个测试文件跳过，而不是报错。

创建 `backend/tests/conftest.py`：

```python
import os

import pytest
import pytest_asyncio
import redis.asyncio as redis

from backend.core.config import load_env

# conftest 不 import 任何项目模块，没人替它加载 .env，必须自己来。
# 否则 REDIS_HOST 等一律读不到，全部回落 localhost，测试会以
# 「Redis 没开」的面目集体 skip。
load_env()

TEST_DB = 15


def _test_redis_url() -> str:
    """拼测试库（db 15）的连接串，与生产库隔离。"""
    host = os.getenv("REDIS_HOST", "localhost")
    port = os.getenv("REDIS_PORT", "6379")
    password = os.getenv("REDIS_PASSWORD")
    username = os.getenv("REDIS_USERNAME")

    # 用户名与密码之间是冒号；只有密码时要留一个空用户名位，
    # 写成 redis://password@host 会被解析成「用户名=password，密码=None」
    if password and username:
        return f"redis://{username}:{password}@{host}:{port}/{TEST_DB}"
    if password:
        return f"redis://:{password}@{host}:{port}/{TEST_DB}"
    if username:
        return f"redis://{username}@{host}:{port}/{TEST_DB}"
    return f"redis://{host}:{port}/{TEST_DB}"


@pytest_asyncio.fixture
async def redis_test_client():
    """
    指向 db 15 的独立客户端；每个测试前后各清一次库，保证测试互不污染。
    Redis 连不上时 skip 而不是报错——测试基建不该因为环境缺失而变成红色噪音。
    """
    client = redis.from_url(
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
async def test_fixture_is_isolated(redis_test_client):
    await redis_test_client.set("k","v")
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

## Task 1: 环境变量的单一加载入口

现在项目里有 11 处 `load_dotenv`，三种写法，没有一种是可靠的：

| 写法 | 出现位置 | 问题 |
|---|---|---|
| `load_dotenv()` 不传参 | `model/__init__.py`、`agents/agent/tools.py`、`common_agent.py`、`extract_agent.py`、`question_set_agent.py` 等 | 从**当前工作目录**逐级上找 `.env`。只在 cwd 恰好是 `backend/` 或其子目录时成立 |
| `load_dotenv('.env')` | `vector_store_manager.py`、`extract_memory_agent.py` | 相对当前工作目录，比上一种更脆 |
| **完全不加载** | `get_llm.py`、`utils/redis_client.py` | 靠「别的模块碰巧先被导入」。现在能跑纯属 `model/__init__.py` 恰好在导入链前面 |

失败方式很隐蔽：`os.getenv` 静默返回 `None`，然后以一个和根因毫无关系的面目炸掉。实测过一次——从仓库根目录跑一段裸脚本 import `redis_client`，密码读成 `None`，报出来的是：

```
redis.exceptions.AuthenticationError: Authentication required.
```

你会去查 Redis 的 ACL 配置，而真正的原因是 `.env` 没被加载。

改法是收敛成一个入口：`backend/core/config.py` 用**绝对路径**加载一次，所有读 env 的模块统一调 `load_env()`。

**Files:**
- Create: `backend/core/config.py`
- Create: `backend/tests/test_config_env.py`
- Modify: `backend/agents/agent/get_llm.py`（原本完全不加载）
- Modify: `backend/utils/redis_client.py`（原本完全不加载）
- Modify: `backend/model/__init__.py`
- Modify: `backend/agents/memory/vector_store_manager.py`
- Modify: `backend/agents/agent/tools.py`
- Modify: `backend/agents/agent/common_agent.py`
- Modify: `backend/agents/agent/extract_agent.py`
- Modify: `backend/agents/agent/question_set_agent.py`
- Modify: `backend/agents/agent/extract_memory_agent.py`
- Modify: `backend/tests/conftest.py`（复用 `load_env`，去掉重复的路径推导）

**Interfaces:**
- Consumes: 无
- Produces:
  - `BACKEND_ROOT: Path` —— `backend/` 目录的绝对路径
  - `ENV_PATH: Path` —— `backend/.env` 的绝对路径
  - `load_env() -> None` —— 幂等；用绝对路径加载，不覆盖已存在的真实环境变量

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/test_config_env.py`：

```python
import importlib
import os


def test_env_path_is_absolute_and_points_at_backend():
    """保证env_path是绝对路径，并且指向backend"""
    from backend.core.config import BACKEND_ROOT,ENV_PATH

    assert ENV_PATH.is_absolute()
    assert ENV_PATH.name == ".env"
    assert ENV_PATH.parent == BACKEND_ROOT
    assert BACKEND_ROOT.name == "backend"

def test_load_env_works_from_any_cwd(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("API_KEY",raising=False)

    import backend.core.config as cfg
    cfg._loaded = False
    cfg.load_env()

    assert os.getenv("API_KEY"), "换个工作目录就读不到 .env，说明用的是相对路径"

def test_load_env_is_idempotent():
    import backend.core.config as cfg
    cfg._loaded = False
    cfg.load_env()
    cfg.load_env()
    assert cfg._loaded is True

def test_real_env_overrides_dotenv(monkeypatch):
    """容器中注入的环境变量优先级必定高于.env文件"""
    monkeypatch.setenv("API_KEY","sentinel-from-real-env")

    import backend.core.config as cfg
    cfg._loaded = False
    cfg.load_env()

    assert os.getenv("API_KEY") == "sentinel-from-real-env"

def test_get_llm_reads_config_without_prior_load(tmp_path,monkeypatch):
    """get_llm 原本完全不加载.env，靠导入拿到值"""
    monkeypatch.chdir(tmp_path)
    for key in ("API_KEY", "API_URL", "MODEL_NAME", "EMBEDDING_MODEL"):
        monkeypatch.delenv(key,raising=False)

    import backend.core.config as cfg
    cfg._loaded = False
    import backend.agents.agent.get_llm as m
    importlib.reload(m)

    assert m.api_key, "get_llm 必须自己调 load_env，不能依赖导入顺序"
    assert m.base_url

def test_redis_url_carries_credentials_without_prior_load(tmp_path,monkeypatch):
    """
    redis_clent原本完全不加载.env，密码读成None
    表现为AuthenticationError -> 一个和根因毫无关系的错误
    """
    monkeypatch.chdir(tmp_path)
    for key in ("REDIS_URL", "REDIS_HOST", "REDIS_PORT",
                "REDIS_PASSWORD", "REDIS_USERNAME"):
        monkeypatch.delenv(key,raising=False)

    import backend.core.config as cfg
    cfg._loaded = False
    import backend.utils.redis_client as rc
    importlib.reload(rc)

    url = rc._build_redis_url()
    assert os.getenv("REDIS_PASSWORD"), "load_env 应当已把 .env 里的密码读进来"
    assert "@" in url, "URL 必须带认证信息，否则会以 AuthenticationError 的面目失败"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_config_env.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'backend.core.config'`

- [ ] **Step 3: 实现 config 模块**

创建 `backend/core/config.py`：

```python
from pathlib import Path

from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = BACKEND_ROOT / ".env"

_loaded = False

def load_env() -> None:
    """
    幂等加载
    用绝对路径，不依赖当前工作目录，不覆盖已存在的真实环境变量
    """

    global _loaded
    if _loaded:
        return
    load_dotenv(ENV_PATH)
    _loaded = True
```

本模块只依赖 `pathlib` 和 `dotenv`，不 import 任何项目模块，因此谁都可以安全地导入它，不会产生循环依赖。

- [ ] **Step 4: 给两个「完全不加载」的模块补上**

这两个是真正的隐患，优先改。

`backend/agents/agent/get_llm.py` —— 在 `os.getenv` 调用**之前**插入：

```python
from backend.core.config import load_env

load_env()
```

`backend/utils/redis_client.py` —— 同样在文件顶部 import 之后插入：

```python
from backend.core.config import load_env

load_env()
```

注意 `redis_client` 的 `os.getenv` 是在 `_build_redis_url()` **调用时**才执行的，模块级 `load_env()` 一定早于它，没有时序问题。

- [ ] **Step 5: 把其余 9 处旧写法统一过来**

下列文件中，删掉 `from dotenv import load_dotenv` 及其对应的 `load_dotenv()` / `load_dotenv('.env')` 调用，换成：

```python
from backend.core.config import load_env

load_env()
```

- `backend/model/__init__.py`
- `backend/agents/memory/vector_store_manager.py`
- `backend/agents/agent/tools.py`
- `backend/agents/agent/common_agent.py`
- `backend/agents/agent/extract_agent.py`
- `backend/agents/agent/question_set_agent.py`
- `backend/agents/agent/extract_memory_agent.py`（这个用的是 `import dotenv` + `dotenv.load_dotenv('.env')`，把 `import dotenv` 也一并删掉）

`backend/model/__init__.py` 是唯一需要留意导入方向的：它 import `backend.core.config`，而 `core/config.py` 不 import 任何项目模块，因此不会成环。

改完确认没有漏网的：

Run: `cd backend && grep -rn "load_dotenv" --include=*.py --exclude-dir=.venv --exclude-dir=__pycache__ . | grep -v "core/config.py"`

注意必须用 `--exclude-dir`：写成 `grep -rn ... . | grep -v "\.venv"` 的话，第二个 grep 只能过滤输出，第一个 grep 照样会把整个 `.venv` 扫一遍，会卡上好几分钟。

Expected: 只剩下面两类，都在预期内：
- `analyse_agent.py`、`image_gene_agent.py`、`planner_agent.py` 各两行（没有调用方的死模块，见下文）
- `tests/conftest.py` 两行（由 Step 6 处理，做完 Step 6 这两行就没了）

顺带一提，`analyse_agent.py`、`planner_agent.py`、`image_gene_agent.py` 也各有一处 `load_dotenv()`，但这三个模块目前没有任何调用方（属于旧 planner 架构的遗留）。改不改都行；如果你打算删掉它们，就别在这里花力气。

- [ ] **Step 6: 让 conftest 复用同一个入口**

`backend/tests/conftest.py` 目前自己推导了一遍 `.env` 路径。换成同一个入口，避免两处路径逻辑将来走偏：

```python
from backend.core.config import load_env

load_env()
```

并删掉原来的 `from pathlib import Path`、`from dotenv import load_dotenv` 和那行 `load_dotenv(Path(__file__).resolve().parents[1] / ".env")`（`Path` 若在别处仍有使用则保留）。

- [ ] **Step 7: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/ -v`
Expected: 全部 passed（含原有的 2 个冒烟测试）

- [ ] **Step 8: 验证「换个工作目录也能起来」**

这是本任务的真正目的，务必手工验证一次：

```bash
cd D:/yonghu/Program/python_program/question_set_program
python -c "
from backend.utils.redis_client import get_redis_client
import asyncio
c = get_redis_client()
asyncio.run(c.client.ping()) and print('PING OK')
"
```

改造前这条命令会报 `AuthenticationError: Authentication required`（因为 cwd 是仓库根目录，`.env` 找不到）；改造后应当正常返回。

- [ ] **Step 9: 提交**

```bash
git add backend/core/config.py backend/tests/test_config_env.py backend/agents/ backend/model/__init__.py backend/utils/redis_client.py backend/tests/conftest.py
git commit -m "fix: 环境变量收敛到单一加载入口，消除工作目录与导入顺序依赖"
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
    """记录被调用的是同步还是异步入口"""

    def __init__(self,content:str,recorder:dict):
        self._content = content
        self._recorder = recorder

    def bind_tools(self,tools):
        return self

    def invoke(self,messages):
        self._recorder["sync"] = True
        return SimpleNamespace(content=self._content)

    async def ainvoke(self,messages):
        self._recorder["async"] = True
        return SimpleNamespace(content=self._content)

def test_react_think_node_is_coroutine_function():
    assert inspect.iscoroutinefunction(react_agent.react_think_node),(
        "react_think_node 必须是 async，否则 LangGraph 会把它丢进默认线程池，"
        "和向量库操作抢同一个池"
    )

async def test_react_think_node_uses_ainvoke(monkeypatch):
    recorder = {}
    content = '{"thought":"够了","action":"","action_args":{},"final_result":"答案是 x=4"}'
    monkeypatch.setattr(
        react_agent,"get_llm",lambda *a,**kw: _FakeLLM(content,recorder)
    )

    out = await react_agent.react_think_node(_state())
    assert recorder == {'async':True},'不允许走同步 invoke'
    assert out['final_result'] == "答案是 x=4"
    assert out['action'] == ""
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

> **使用者在 Task 12 变了**：本任务把 `vector_store_manager.py` 的三处 `run_in_executor(None, ...)` 改到专属池；
> Task 12 删掉了向量记忆，这个文件不复存在。**线程池本身照常保留**——RAG 知识库要用同一个池（见 RAG 计划），
> 「为什么需要专属有界池」的推理也完全不变，所以本任务照做，只是 Step 4 的改造对象换成 RAG 的向量层。

**这个任务解决什么问题**：向量库的调用是同步的，只能靠 `run_in_executor` 丢到线程里跑。问题在于 `run_in_executor(None, ...)` 用的是**默认线程池**——一个全局共享资源，LangGraph 的同步节点、`asyncio.to_thread` 都在用它，大小只有 `min(32, cpu + 4)`。一次 embedding 风暴就能把它占满，饿死其他所有阻塞调用，而且从日志上完全看不出是谁占的。

给向量库一个有界的专属池，把影响限制在向量层内部：池满了排队的只有向量任务，线程名带 `vec` 前缀，`py-spy` 或线程 dump 里一眼就能认出来是谁在阻塞。

**Files:**
- Create: `backend/core/executors.py`
- Modify: `backend/agents/memory/vector_store_manager.py`（三处 `run_in_executor(None, ...)`）
- Create: `backend/tests/test_executor.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `get_vector_executor() -> concurrent.futures.ThreadPoolExecutor`
  - `shutdown_executors(wait: bool = True) -> None`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/test_executor.py`：

```python
import asyncio
import threading

from backend.core import executors


def test_vector_executor_is_bounded_and_named():
    ex = executors.get_vector_executor()
    # ThreadPoolExecutor 没有公开的 max_workers 属性，池大小存在 _max_workers 里
    assert ex._max_workers == 4,"必须有界，避免embedding风暴打满线程"
    assert executors.get_vector_executor() is ex,"必须复用同一个池"

async def test_vector_executor_threads_are_prefixed():
    loop = asyncio.get_running_loop()
    name = await loop.run_in_executor(
        executors.get_vector_executor(),lambda: threading.current_thread().name
    )
    assert name.startswith("vec"),f"线程名应该以 vec 开头，实际为{name}"

def test_shutdown_is_idempotent():
    executors.get_vector_executor()
    executors.shutdown_executors()
    executors.shutdown_executors()  #重复执行两次
    # 关闭后再取应该拿到一个可用的新池
    assert executors.get_vector_executor()._max_workers == 4
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_executor.py -v`
Expected: FAIL，`ModuleNotFoundError: backend.core.executors`

- [ ] **Step 3: 实现 executors 模块**

创建 `backend/core/executors.py`：

```python
"""
集中管理进程级线程池，提供获取与关停入口
"""
import threading
from concurrent.futures import ThreadPoolExecutor

from backend.middleware.logging import get_logger

logger = get_logger(__name__)

VECTOR_POOL_SIZE = 4

_vector_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()

def get_vector_executor() -> ThreadPoolExecutor:
    global _vector_executor
    if _vector_executor is None:
        with _lock:
            if _vector_executor is None:
                _vector_executor = ThreadPoolExecutor(
                    max_workers=VECTOR_POOL_SIZE,
                    thread_name_prefix="vec",
                )
                logger.info("向量库线程池已创建，max_workers=%s",VECTOR_POOL_SIZE)
    return _vector_executor

def shutdown_executors(wait: bool = True) -> None:
    global _vector_executor
    with _lock:
        if _vector_executor is not None:
            _vector_executor.shutdown(wait=wait)
            _vector_executor = None
            logger.info("向量库线程池已关闭")
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

Run: `cd backend && grep -n "None, partial" agents/memory/vector_store_manager.py`
Expected: 无输出

不要用 `grep "run_in_executor"` 然后看输出里有没有 `run_in_executor(None`：`query` 里那一处是跨行写的，`None` 在下一行，那样检查的话，漏改了也会显示为通过。

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_executor.py -v`
Expected: 3 passed

- [ ] **Step 6: 提交**

```bash
git add backend/core/executors.py backend/agents/memory/vector_store_manager.py backend/tests/test_executor.py
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
from sympy import Lambda

from backend.agents.tools import TOOLS, TOOL_MAP

# 例外：load_skill_tool 只是读一个很小的 SKILL.md（loader 还带 mtime 缓存），同步实现不会阻塞事件循环。
# 把例外写成清单，比在测试里 skip 更清楚：结果里不会多出一条跳过，新增工具也不会悄悄跟着被放过。
SYNC_ALLOWED = {"load_skill_tool"}


@pytest.mark.parametrize("tool",[t for t in TOOLS if t.name not in SYNC_ALLOWED],ids=lambda t:t.name)
def test_sync_run_is_disabled(tool):
    """除清单里的例外之外，所有工具只走异步路径，同步_run必须显式拒绝，不能悄悄阻塞事件循环"""
    with pytest.raises(NotImplementedError):
        tool._run()

def test_sync_allowed_tool_really_works():
    """例外也要测：load_skill_tool 的同步入口要能正常读出剧本，而不是抛异常"""
    result = TOOL_MAP["load_skill_tool"]._run(name="question_variant")
    assert "【Skill: question_variant】已加载" in result

def test_sync_agent_functions_are_gone():
    from backend.agents.agent import common_agent,extract_agent,question_set_agent

    assert not hasattr(common_agent,"common_tool")
    assert not hasattr(extract_agent,"extract_tool")
    assert not hasattr(question_set_agent,"question_set_tool")
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

Run（在 `backend/` 下）：

```bash
python -c "import re,pathlib; p=re.compile(r'(?<![\w\".])(common_tool|extract_tool|question_set_tool)(?![\w\"])'); [print(f'{f}:{i}: {l.strip()}') for f in pathlib.Path('.').rglob('*.py') if '.venv' not in f.parts for i,l in enumerate(f.read_text(encoding='utf-8').splitlines(),1) if p.search(l)]"
```

Expected: 无输出

这条检查匹配的是「作为标识符出现的同步函数名」，排除了 `async_` 前缀、带引号的工具名（`name = "common_tool"`）和模块路径（`tools.common_tool`）。

不要用 `grep ... | grep -v "async_\|_tool.py:"` 这种写法：`-v "_tool.py:"` 会把三个 `*_tool.py` 文件整个排除掉，而残留最可能出现的恰恰是这三个文件；`-v "async_"` 又会把 `import async_extract_tool, extract_tool` 这种没删干净的导入一起过滤掉——两处漏洞叠加，漏改了也显示通过。也不要换成 `grep -P`，Windows 的 Git Bash 下它要求 UTF-8 locale，常常直接报错。

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

from backend.agents.memory.short_term_memory import ShortTermMemory, MemoryUnit

USER,SESSION = 1,1

@pytest.fixture
def stm(redis_test_client,monkeypatch):
    """把ShortTermMemory 指向测试库db 15"""
    memory = ShortTermMemory(max_memory_size=3)
    monkeypatch.setattr(memory,"_client",redis_test_client)
    return memory

async def test_newest_first(stm):
    for i in range(3):
        await stm.add_memory(USER,SESSION,MemoryUnit(f"问题{i}",f"回答{i}"))

    got = await stm.get_latest_memories(USER,SESSION,limit=3)
    assert [m["memory"]["user_memory"] for m in got] == ["问题2","问题1","问题0"]

async def test_no_eviction_below_limit(stm):
    evicted = await stm.add_memory(USER,SESSION,MemoryUnit("问题0","回答0"))
    assert evicted == []
    assert await stm.get_memory_size(USER,SESSION) == 1

async def test_eviction_moves_oldest_to_pending(stm):
    for i in range(3):
        await stm.add_memory(USER,SESSION,MemoryUnit(f"问题{i}",f"回答{i}"))

    evicted = await stm.add_memory(USER,SESSION,MemoryUnit("问题3","回答3"))
    assert len(evicted) == 1
    assert json.loads(evicted[0])["memory"]["user_memory"] == "问题0"
    assert await stm.get_memory_size(USER,SESSION) == 3
    pending = await stm.get_pending(USER,SESSION)
    assert pending == evicted

async def test_evicted_item_is_invisible_to_readers(stm):
    for i in range(4):
        await stm.add_memory(USER,SESSION,MemoryUnit(f"问题{i}",f"回答{i}"))

    visible = await stm.get_latest_memories(USER,SESSION,limit=10)
    assert "问题0" not in [m["memory"]["user_memory"] for m in visible]

async def test_concurrent_writes_lose_nothing(stm):
    results = await asyncio.gather(*[
        stm.add_memory(USER,SESSION,MemoryUnit(f"问题{i}",f"回答{i}"))
        for i in range(20)
    ])

    in_window = await stm.get_latest_memories(USER,SESSION,limit = 100)
    in_pending = await stm.get_pending(USER,SESSION)

    assert len(in_window) == 3,"窗口大小必须严格等于max_memory_size"
    assert len(in_window) + len(in_pending) == 20,"并发写入不会丢失消息"

    evicted_total = sum(len(r) for r in results)
    assert evicted_total == len(in_pending),"每条被淘汰的记忆都应该被返回给调用方一次"

async def test_ack_removes_from_pending(stm):
    for i in range(4):
        await stm.add_memory(USER,SESSION,MemoryUnit(f"问题{i}",f"回答{i}"))

    pending = await stm.get_pending(USER,SESSION)
    removed = await stm.ack_archived(USER,SESSION,pending[0])

    assert removed == 1
    assert await stm.get_pending(USER,SESSION) == []

async def test_ttl_is_set(stm):
    await stm.add_memory(USER,SESSION,MemoryUnit('问题','回答'))
    ttl = await stm._client.ttl(ShortTermMemory.key(USER,SESSION))
    assert 0 < ttl <= 86400

async def test_scan_pending_keys(stm):
    for i in range(4):
        await stm.add_memory(USER,SESSION,MemoryUnit(f"问题{i}",f"回答{i}"))

    keys = await stm.scan_pending_keys()
    assert ShortTermMemory.pending_key(USER,SESSION) in keys
    assert ShortTermMemory.parse_pending_key(keys[0]) == (USER,SESSION)
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
旧实现每次写入都要「读出整个列表 -> Python 里 insert -> 整个写回」，
是一次没有任何保护的 read-modify-write，两个并发请求会丢消息（读-读-写-写）。
改成 LIST 后，写入是 Redis 服务端的原子操作。

淘汰同样在服务端完成：LPUSH 与「超限时 RPOPLPUSH 到 pending 队列」
打包进一次 Lua EVAL 原子执行。被淘汰的条目在这一刻就已经物理移出窗口，
后续的归档读不到它、也不会被新消息插入干扰，所以整条链路不需要任何锁。

存储结构：
- stm:{user_id}:{session_id}          LIST，index 0 为最新，长度上限 max_memory_size
- stm:pending:{user_id}:{session_id}  LIST，待归档队列，归档成功后 LREM 移除
"""
import json
from typing import Dict, List, Any, Optional
from datetime import datetime

from backend.middleware.logging import get_logger
from backend.utils.redis_client import get_redis_client
from redis.exceptions import RedisError

logger = get_logger(__name__)

DEFAULT_TTL = 86400
DEFAULT_PENDING_TTL = 604800

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

_short_term_memory: Optional[ShortTermMemory] = None


class MemoryUnit(dict):
    def __init__(self, user_memory: str = "", model_memory: str = ""):
        # 注意：这里不要使用 typing.Dict（不可实例化），而要用普通 dict。
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
            pending_ttl = DEFAULT_PENDING_TTL
    ):
        """
        初始化短期记忆模块
        
        Args:
            max_memory_size: 最大记忆条数
            redis_client: Redis客户端实例，可选，若不传则自动获取全局实例
        """
        self.max_memory_size = max_memory_size
        self._client = get_redis_client().client
        self.ttl = ttl
        self.pending_ttl = pending_ttl
        self._push_script = None

    @staticmethod
    def key(user_id: int,session_id: int) -> str:
        return f"stm:{user_id}:{session_id}"

    @staticmethod
    def pending_key(user_id: int,session_id: int) -> str:
        return f"stm:pending:{user_id}:{session_id}"

    @staticmethod
    def parse_pending_key(key: str) -> tuple[int,int]:
        parts = key.split(":")
        return int(parts[2]),int(parts[3])

    def _script(self):
        if self._push_script is None:
            self._push_script = self._client.register_script(_PUSH_AND_EVICT_LUA)
        return self._push_script

    async def add_memory(self, user_id: int, session_id: int, memory: MemoryUnit) -> List[str]:
        """
        原子写入一条记忆，并把超过窗口容量的最旧记忆搬进pending队列

        :return: 本次被挤出窗口的原始JSON字符串列表，调用方将他们归档
        """
        try:
            evicted = await self._script()(
                keys = [
                    self.key(user_id,session_id),
                    self.pending_key(user_id,session_id)
                ],
                args = [
                    json.dumps(memory,ensure_ascii=False),
                    self.max_memory_size,
                    self.ttl,
                    self.pending_ttl
                ],
            )
            return list(evicted or [])
        except RedisError as e:
            logger.error("写入短期记忆失败: %s",e,exc_info=True)
            return []
        

    async def ack_archived(self,user_id: int,session_id: int,raw_item: str) -> int:
        """
        归档成功后，把该条目从pending队列中删除
        :return: 实际移除的条数
        """
        try:
            return await self._client.lrem(
                self.pending_key(user_id,session_id),1,raw_item
            )
        except RedisError as e:
            logger.error("移除pending条目失败: %s",e,exc_info=True)
            return 0


    async def clear_all(self,user_id: int,session_id: int) -> None:
        """清空会话窗口和pending队列"""
        await self._client.delete(
            self.key(user_id,session_id),
            self.pending_key(user_id,session_id),
        )

    async def get_latest_memories(self, user_id: int, session_id: int, limit: int = 5) -> List[Dict[str, Any]]:
        """获取最新N条记忆"""
        try:
            raw = await self._client.lrange(
                self.key(user_id,session_id),0,limit-1
            )
        except RedisError as e:
            logger.error("读取短期记忆失败: %s",e)
            return []

        result: List[Dict[str,Any]] = []
        for item in raw:
            try:
                result.append(json.loads(item))
            except json.JSONDecodeError:
                logger.error("短期记忆反序列化失败，已跳过: %s",item[:100])
        return result

    async def get_memory_size(self, user_id: int, session_id: int) -> int:
        try:
            return await self._client.llen(self.key(user_id,session_id))
        except RedisError:
            return 0

    async def get_pending(self,user_id: int,session_id: int) -> List[str]:
        try:
            return await self._client.lrange(
                self.pending_key(user_id,session_id),0,-1
            )
        except RedisError:
            return []

    async def scan_pending_keys(self) -> List[str]:
        keys:List[str] = []
        try:
            async for key in self._client.scan_iter(match="stm:pending:*",count = 100):
                keys.append(key)
        except RedisError as e:
            logger.error("扫描 pending 队列失败: %s", e)
        return keys


async def get_short_term_memory() -> ShortTermMemory:
    """
    获取短期记忆实例
    
    Returns: ShortTermMemory实例
    """
    global _short_term_memory
    if _short_term_memory is None:
        _short_term_memory = ShortTermMemory()
    return _short_term_memory #type: ignore
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

> **归档的目标在 Task 12 改了**：本任务完成时写入的是向量库（当时的设计），Task 12 把目标换成 MySQL 的 `memory_record`。
> 「后台任务 + pending 重试 + 关停等待」这套骨架两者完全一样，所以本任务的内容仍然有效，只是最后一步的写入对象不同。

**这个任务解决什么问题**：短期记忆溢出时，被挤出窗口的记录要先经 LLM 精炼，再写进向量库。现在这一步是在请求路径上同步做的——用户发一条消息恰好触发溢出，就得等一次 LLM 摘要（几秒）才能拿到回复。

**怎么做**：`add_memory` 拿到被挤出的记录后，交给后台任务去归档，自己立即返回。归档成功才把条目从 pending 队列里 ack 掉；失败就留在队列里，等下次重试。

**为什么不需要锁**：被归档的条目在 Task 5 的 Lua 脚本里已经用 `RPOPLPUSH` 物理移出了窗口，读路径根本看不到它。所以后台归档和窗口的读写不会互相干扰，也就不需要加锁。

**为什么逐条归档，而不是多条一起精炼**：一次把 N 条交给 LLM、再按顺序和返回结果配对（`zip`），看起来省调用，实际会错位——模型可能把两条合并成一条，也可能把一条拆成两条。输出条数和输入对不上时，`zip` 会把 A 的精炼结果配到 B 头上，于是 B 被错误地 ack 掉、内容永久丢失。逐条归档虽然多花几次调用，但每条记录的成败互不影响。Task 15 会给这些调用加上限时和输入截断。

**做完之后**：触发溢出的那个请求不再等待摘要；归档失败的条目留在 pending，由 Task 7 在下次启动时补做。

**前后依赖**：用到 Task 5 的 `add_memory -> list[str]`、`ack_archived`、`get_pending`、`scan_pending_keys`、`parse_pending_key`。产出 `drain_pending()` 与 `shutdown()`，Task 7 接线使用；`_archive_one` 是 Task 15 的修改对象。

**实现时注意**：

1. **`asyncio.create_task` 必须自己持有强引用**。事件循环对任务只持弱引用，不存一份的话，任务可能跑到一半被垃圾回收。所以 `_spawn` 把任务放进 `self._tasks`，并注册 done 回调负责移除。
2. **回调里先判断 `task.cancelled()`，再调用 `task.exception()`**。对已取消的任务调用 `exception()` 会抛 `CancelledError`。
3. **回调里没有「当前异常」**，`logger.error(..., exc_info=True)` 取不到堆栈，要把异常对象本身传给 `exc_info`。
4. **`shutdown` 取消任务后要等它们真正退出**（`await asyncio.gather(*pending, return_exceptions=True)`）。`cancel()` 只是发出取消请求，不等的话，事件循环关闭时会报 "Task was destroyed but it is pending"。
5. **失败的处理分两类**：精炼失败、向量库写失败都留在 pending 等重试；无法反序列化的条目直接丢弃——它重试多少次都不会好，留着只会每次启动都卡在它上面。
6. 本任务在仓库里已经完成（提交 `40f833f`，修复见 `b329d85`），下面的代码块就是最终版本，可以直接对照。

**Files:**
- Modify: `backend/agents/memory/memory_manager.py`（重写 `add_memory`，新增归档相关方法）
- Create: `backend/tests/test_memory_manager.py`

**Interfaces:**
- Consumes: Task 5 的 `ShortTermMemory.add_memory -> list[str]`、`ack_archived`、`get_pending`、`scan_pending_keys`、`parse_pending_key`
- Produces:
  - `async MemoryManager.add_memory(user_id, session_id, memory) -> None` —— 不再等待摘要
  - `async MemoryManager.drain_pending() -> int` —— 启动恢复，返回补做的条目数
  - `async MemoryManager.shutdown(timeout: float = 10.0) -> None` —— 等待在途归档
  - `async MemoryManager._archive_one(user_id, session_id, raw) -> bool` —— 归档单条（Task 15 在它上面加限时）

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/test_memory_manager.py`：

```python
import asyncio
import json
import logging

import pytest

import backend.agents.memory.memory_manager as mm
from backend.agents.memory.memory_manager import MemoryManager
from backend.agents.memory.short_term_memory import ShortTermMemory, MemoryUnit

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
def manager(redis_test_client,monkeypatch):
    """创建一个manager对象"""
    stm = ShortTermMemory(max_memory_size=2)
    monkeypatch.setattr(target=stm,name="_client",value=redis_test_client)

    vector = FakeVectorStore()

    async def extract_memory(units):
        return [{
            "text": f"精炼:{u['memory']['user_memory']}",
            "tags": ['事实']
        } for u in units]

    monkeypatch.setattr(mm,'get_extract_memory',extract_memory)

    mgr = MemoryManager.__new__(MemoryManager)
    mgr.long_term_memory = None
    mgr.short_term_memory = stm
    mgr.vector_memory = vector
    mgr._tasks = set()
    return mgr

async def test_below_limit_spawns_nothing(manager):
    await manager.add_memory(USER,SESSION,MemoryUnit("问题","回答"))
    assert manager._tasks == set()
    assert manager.vector_memory.docs == []

async def test_overflow_archive_in_background(manager):
    for i in range(3):
        await manager.add_memory(USER,SESSION,MemoryUnit(f"问题{i}",f"回答{i}"))
    await manager.shutdown(timeout = 5)
    assert len(manager.vector_memory.docs) == 1
    text, metadata = manager.vector_memory.docs[0]
    assert text == "精炼:问题0"
    assert metadata["user_id"] == USER
    assert isinstance(metadata['tags'],str),"Chroma 的 metadata 只接受标量"
    assert await manager.short_term_memory.get_pending(USER,SESSION) == []

async def test_request_path_docs_not_wait_for_archive(manager,monkeypatch):
    async def slow_extract(units):
        await asyncio.sleep(1)
        return [{"text": "慢精炼","tags": ["事实"]}]

    monkeypatch.setattr(mm,"get_extract_memory",slow_extract)

    for i in range(2):
        await manager.add_memory(USER,SESSION,MemoryUnit(f'问题{i}',f'回答{i}'))

    start = asyncio.get_running_loop().time()
    await manager.add_memory(USER,SESSION,MemoryUnit(f'问题2',f'回答2'))
    expire = asyncio.get_running_loop().time() - start

    assert expire < 0.5,"归档操作应当是后台运行的，不阻塞用户请求路径"
    await manager.shutdown(timeout=5)

async def test_failed_archive_write_keeps_item_pending(manager):
    """失败的归档操作不会将pending队列中的相应doc给删除"""
    # 也就是说那个pending应该还存在于pending队列中，通过get_pending()可以查询到
    manager.vector_memory.should_fail = True

    for i in range(3):
        await manager.add_memory(USER,SESSION,MemoryUnit(f'问题{i}',f'回答{i}'))

    await manager.shutdown(5)
    # 然后判断 pending 中是否有相应的消息
    result = await manager.short_term_memory.get_pending(USER,SESSION)
    assert len(result) == 1
    assert json.loads(result[0])['memory']['user_memory'] == '问题0'

async def test_drain_recovers_is_idempotent(manager):
    """保证drain_pending只执行一次"""
    manager.vector_memory.should_fail = True

    for i in range(3):
        await manager.add_memory(USER, SESSION, MemoryUnit(f'问题{i}', f'回答{i}'))

    await manager.shutdown(5)
    assert len(await manager.short_term_memory.get_pending(USER,SESSION)) == 1

    manager.vector_memory.should_fail = False
    recovered = await manager.drain_pending()

    assert recovered == 1
    assert len(manager.vector_memory.docs) == 1
    assert await manager.short_term_memory.get_pending(USER,SESSION) == []

    assert await manager.drain_pending() == 0
    assert len(manager.vector_memory.docs) == 1


async def _fill_pending(manager, n):
    """直接写短期记忆，让 pending 里积压 n 条（max=2，第 3 条起每条挤出一条），不触发后台归档"""
    for i in range(n + 2):
        await manager.short_term_memory.add_memory(USER, SESSION, MemoryUnit(f'问题{i}', f'回答{i}'))


async def test_finished_tasks_are_released(manager):
    """任务结束后要从 _tasks 里移除，否则每溢出一次就多攒一个"""
    for i in range(4):
        await manager.add_memory(USER, SESSION, MemoryUnit(f'问题{i}', f'回答{i}'))
    await manager.shutdown(5)
    assert manager._tasks == set()
    assert len(manager.vector_memory.docs) == 2


async def test_background_exception_is_logged(manager, monkeypatch, caplog):
    async def boom(*args, **kwargs):
        raise RuntimeError("意料之外的错误")
    monkeypatch.setattr(manager, "_archive", boom)

    with caplog.at_level(logging.ERROR):
        for i in range(3):
            await manager.add_memory(USER, SESSION, MemoryUnit(f'问题{i}', f'回答{i}'))
        await manager.shutdown(5)
    assert "意料之外的错误" in caplog.text
    assert manager._tasks == set()


async def test_one_record_refined_into_several_is_not_lost(manager, monkeypatch):
    """
    模型把每条记录拆成两条事实。按顺序 zip 配对的旧实现里，第 2 条记录会配上第 1 条的第二条事实被 ack，
    它自己的内容永久丢失；逐条归档则每条的产出都完整写入。
    """
    async def split(units):
        q = units[0]['memory']['user_memory']
        return [{"text": f"{q}-事实A", "tags": ["事实"]}, {"text": f"{q}-事实B", "tags": ["事实"]}]
    monkeypatch.setattr(mm, "get_extract_memory", split)

    await _fill_pending(manager, 2)
    assert await manager.drain_pending() == 2
    assert sorted(t for t, _ in manager.vector_memory.docs) == ["问题0-事实A", "问题0-事实B", "问题1-事实A", "问题1-事实B"]
    assert await manager.short_term_memory.get_pending(USER, SESSION) == []


async def test_items_are_archived_independently(manager, monkeypatch):
    """一条精炼失败不影响另一条：成功的照常 ack，失败的留在 pending"""
    async def flaky(units):
        if units[0]['memory']['user_memory'] == '问题0':
            raise RuntimeError("LLM 超时")
        return [{"text": "精炼", "tags": ["事实"]}]
    monkeypatch.setattr(mm, "get_extract_memory", flaky)

    await _fill_pending(manager, 2)
    assert await manager.drain_pending() == 1
    [left] = await manager.short_term_memory.get_pending(USER, SESSION)
    assert json.loads(left)['memory']['user_memory'] == '问题0'


async def test_unparseable_item_is_dropped(manager, redis_test_client):
    """坏数据重试也不会好，要从 pending 删掉，不能每次启动都卡在它上面"""
    await redis_test_client.rpush(ShortTermMemory.pending_key(USER, SESSION), "{不是json")
    assert await manager.drain_pending() == 0
    assert await manager.short_term_memory.get_pending(USER, SESSION) == []


async def test_shutdown_timeout_cancels_and_waits(manager, monkeypatch):
    """超时的任务被取消，并且 shutdown 返回前已经真正退出；条目留在 pending 等下次启动补做"""
    async def forever(units):
        await asyncio.sleep(30)
    monkeypatch.setattr(mm, "get_extract_memory", forever)

    for i in range(3):
        await manager.add_memory(USER, SESSION, MemoryUnit(f'问题{i}', f'回答{i}'))
    [task] = manager._tasks
    await manager.shutdown(timeout=0.1)

    assert task.done() and task.cancelled()
    assert manager._tasks == set()
    assert len(await manager.short_term_memory.get_pending(USER, SESSION)) == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_memory_manager.py -v`
Expected: 11 个测试全部失败或报错——`MemoryManager` 还没有 `_tasks`、`_spawn`、`shutdown`、`drain_pending`

- [ ] **Step 3: 重写 memory_manager.py**

用下面的内容整体替换 `backend/agents/memory/memory_manager.py`：

```python
"""
后续还可添加功能
将长期记忆存入RAG知识库中：根据用户输入的问题，整合RAG检索和长短期记忆，返回规划器需要的记忆
"""
import asyncio
import json
import time
from typing import Any,TYPE_CHECKING

from backend.agents.agent.extract_memory_agent import get_extract_memory
from backend.agents.memory.long_term_memory import LongTermMemory
from backend.agents.memory.short_term_memory import ShortTermMemory, MemoryUnit
if TYPE_CHECKING:
    from backend.agents.memory.vector_store_manager import VectorStoreManager
from backend.core.single_tool import singleMeta
from backend.middleware.logging import get_logger

logger = get_logger(__name__)


class MemoryManager(metaclass=singleMeta):
    def __init__(self,
                 long_term_memory:LongTermMemory,
                 short_term_memory:ShortTermMemory,
                 vector_memory:"VectorStoreManager"):
        self.long_term_memory = long_term_memory
        self.short_term_memory = short_term_memory
        self.vector_memory = vector_memory
        self._tasks: set[asyncio.Task] = set()

    async def get_memory_for_planner(self, user_id: int, session_id: int) -> dict[str, Any]:
        """获取规划器需要的记忆（短期列表 + 单个长期画像）"""
        short_memory = await self.short_term_memory.get_latest_memories(user_id, session_id)
        long_memory = await self.long_term_memory.get_by_user_id(user_id)
        return {
            "short_memory": short_memory,  # list[dict]
            "long_memory": long_memory,    # UserProfileResponse | None
        }

    async def add_memory(self, user_id: int, session_id: int, memory: MemoryUnit):
        """
        对短期记忆进行修改操作，并检查记忆是否已满
        如果已满，则进行记忆的删除，同时将修改后的记忆添加到长期记忆中
        :param user_id:
        :param session_id:
        :param memory:
        :return:
        """

        evicted = await self.short_term_memory.add_memory(user_id,session_id,memory)
        if not evicted:
            return
        # 有 evicted说明记忆溢出了，需要归档，交给 spawn 后台做归档，不阻塞
        self._spawn(self._archive(user_id,session_id,evicted))

    def _spawn(self,coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        # 事件循环对任务只持有弱引用，自己不存一份的话，任务可能跑到一半被垃圾回收
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self,task: asyncio.Task):
        """任务结束（成功、失败或被取消）时调用：释放引用，并把异常记进日志"""
        self._tasks.discard(task)
        # 被取消的任务调用 exception() 会抛 CancelledError，要先排除
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            # 回调里没有「当前异常」，exc_info=True 取不到堆栈，要把异常对象直接传进去
            logger.error("归档任务异常退出: %s", exc, exc_info=exc)

    async def _archive(self,user_id,session_id,raw_items: list[str]) -> int:
        """
        逐条归档，返回成功的条数。
        不把多条记录一起交给 LLM 再按顺序配对：模型可能把一条拆成多条，也可能把多条合成一条，
        输出条数和输入对不上时，按顺序配对会 ack 错条目，造成内容丢失或重复写入。
        """
        success = 0
        for raw in raw_items:
            if await self._archive_one(user_id,session_id,raw):
                success += 1
        return success

    async def _archive_one(self,user_id,session_id,raw: str) -> bool:
        """归档一条：精炼出的内容全部写进向量库之后才 ack；任何一步失败，条目都留在 pending 待重试"""
        try:
            unit = json.loads(raw)
        except json.JSONDecodeError as e:
            # 坏数据重试多少次都不会好，直接从 pending 删掉，否则每次启动都会卡在它上面
            logger.error("待归档记忆无法反序列化，已丢弃: %s | %s", e, raw[:100])
            await self.short_term_memory.ack_archived(user_id,session_id,raw)
            return False

        try:
            refined = await get_extract_memory([unit])
        except Exception as e:
            logger.error("记忆精炼失败，留在 pending 待重试: %s", e, exc_info=True)
            return False
        if not refined:
            # 精炼的提示词要求每条记录都有输出，空结果说明模型输出无法解析
            logger.warning("记忆精炼没有产出内容，留在 pending 待重试")
            return False

        archive_time = int(time.time())
        for item in refined:
            metadata = {
                "user_id": user_id,
                "session_id": session_id,
                "tags": ','.join(item['tags']),
                "time_stamp": archive_time
            }
            if not await self.vector_memory.add_document(item['text'],metadata):
                # 前面几条可能已经写进去了，重试时会再写一遍；向量库不按内容去重，这里接受少量重复
                logger.error("写入向量库失败，留在 pending 待重试")
                return False

        await self.short_term_memory.ack_archived(user_id,session_id,raw)
        return True

    async def drain_pending(self) -> int:
        """重启恢复之后，将已弹出但未归档的进行归档"""
        keys = await self.short_term_memory.scan_pending_keys()
        total = 0
        for key in keys:
            try:
                user_id,session_id = self.short_term_memory.parse_pending_key(key)
            except (IndexError,ValueError):
                logger.error("无法解析pending key，已跳过: %s",key)
                continue

            # 然后就是归档，通过_archive来执行
            texts = await self.short_term_memory.get_pending(user_id,session_id)
            logger.info("启动恢复：session %s:%s 有 %s 条待归档", user_id, session_id, len(texts))
            # 如果 texts 没有读取出来，那么就在这一层失败，不要抛给上层去处理，不合适
            if not texts:
                continue
            total += await self._archive(user_id,session_id,texts)

        if total:
            logger.info("drain_pending 成功归档: %s 条消息",total)
        return total

    async def shutdown(self, timeout: float = 10.0):
        tasks = list(self._tasks)
        if not tasks:
            return
        logger.info("shutdown 等待 %s 个归档任务", len(tasks))
        done,pending = await asyncio.wait(tasks,timeout=timeout)
        if pending:
            logger.warning("shutdown 时有 %s 个归档任务超时，已取消，下次启动由 drain_pending 补做",len(pending))
            for cancel_task in pending:
                cancel_task.cancel()
            # cancel() 只是发出取消请求，要等任务真正退出；否则事件循环关闭时会报 Task was destroyed but it is pending
            await asyncio.gather(*pending, return_exceptions=True)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_memory_manager.py -v`
Expected: 11 passed。这些测试都要连本机 Redis（db 15）；Redis 没开时会整体跳过，那样等于没测，记得先把 Redis 起起来

- [ ] **Step 5: 提交**

```bash
git add backend/agents/memory/memory_manager.py backend/tests/test_memory_manager.py
git commit -m "feat: 记忆归档转为后台任务，请求路径不再等待 LLM 摘要"
```

---

## Task 7: 启动恢复与优雅关停接线

**这个任务解决什么问题**：Task 6 写好的 `drain_pending()` 和 `shutdown()` 目前没有任何调用方。也就是说，上次没做完的归档没人补做，进程退出时在途的归档任务会被直接掐断。这个任务把它们接到应用的启动与关停钩子上。

**现状**：`backend/core/hooks.py` 只做两件事——启动时建表，关停时释放数据库连接和 Redis 连接。

**怎么做**：`startup_event` 里调用 `drain_pending()` 补做积压；`shutdown_event` 里先 `await memory_manager.shutdown(timeout=10)` 等在途任务结束，再依次关线程池、数据库、Redis。

**为什么顺序不能反**：归档任务自己还要用 Redis（ack 掉已归档的条目）和向量库（写入时要走 Task 3 的线程池）。先关 Redis 或线程池，在途任务就会失败，条目留在 pending，下次启动重做一遍——重做意味着同一条记忆被写进向量库两次。

**做完之后**：进程重启不再丢归档；崩溃后遗留在 pending 里的条目，下次启动自动补做。

**前后依赖**：用到 Task 6 的 `drain_pending` / `shutdown`、Task 3 的 `shutdown_executors`。不产出新接口，但 Task 15 会调整这里 `drain_pending()` 的调用方式。

**实现时注意**：

1. **Step 1 的测试测的是真实执行顺序，不是源码文本**。它用替身换掉 `hooks` 里的四个依赖，每个替身被调用时往一个列表里记一笔，然后真跑一遍 `shutdown_event()`，最后断言归档排在第一位。这样既不需要真实的数据库和 Redis，也能覆盖线程池。（早先的写法是用 `inspect.getsource` 比较两个字符串在源码里的先后，把 `close_redis()` 挪进一个辅助函数它就失效了，而且漏掉了线程池。）
2. **`drain_pending()` 返回的是补做的条数（int），不是列表**。写成 `len(recovered)` 会抛 `TypeError`，而它恰好在 `try` 里，于是被吞成一条「记忆归档恢复失败」的错误日志——恢复其实是成功的，日志却在报错。直接 `logger.info("...补做 %s 条", recovered)` 即可。
3. **`startup_event` 里 `await drain_pending()` 会阻塞启动**。pending 里积压 N 条，就要串行调用 N 次 LLM，服务要等它做完才开始接请求。做完 Task 15（单次限时约 35 秒）后最坏情况可控，但积压多时仍然慢。如果在意启动速度，可以改成后台任务，代价是要处理「启动补做」与「新溢出的归档」同时处理同一个会话的问题，见 Task 15 末尾的「后续可选」。
4. **`memory_manager` 必须延迟导入**。它是在 `agent_api` 模块里实例化的，写在 `hooks.py` 顶部会造成循环导入，所以计划里用了一个 `_get_memory_manager()` 函数在调用时才导入。
5. `main.py` 现在用的是 `app.on_event("startup")`，FastAPI 已经把它标记为弃用（运行测试时能看到 DeprecationWarning）。迁移到 lifespan 是以后的事，本任务不动它。

**Files:**
- Modify: `backend/core/hooks.py`
- Modify: `backend/api/user_api/agent_api.py:30-35`（导出 `memory_manager` 供 hooks 使用）

**Interfaces:**
- Consumes: Task 6 的 `MemoryManager.drain_pending()`、`MemoryManager.shutdown()`；Task 3 的 `shutdown_executors()`
- Produces: 无新接口

- [ ] **Step 1: 写测试**

追加到 `backend/tests/test_memory_manager.py` 末尾：

```python
async def test_shutdown_waits_for_archive_before_closing(monkeypatch):
    """关停顺序：先等归档（它还要用 Redis 和向量库的线程池），再关线程池与连接"""
    from backend.core import hooks

    calls = []

    class FakeManager:
        async def shutdown(self, timeout):
            calls.append("archive")

    class FakeEngine:
        async def dispose(self):
            calls.append("engine")

    async def fake_close_redis():
        calls.append("redis")

    monkeypatch.setattr(hooks, "_get_memory_manager", lambda: FakeManager())
    monkeypatch.setattr(hooks, "shutdown_executors", lambda wait=True: calls.append("executors"))
    monkeypatch.setattr(hooks, "engine", FakeEngine())
    monkeypatch.setattr(hooks, "close_redis", fake_close_redis)

    await hooks.shutdown_event()

    assert calls[0] == "archive", f"必须最先等归档，实际顺序：{calls}"
    assert set(calls) == {"archive", "executors", "engine", "redis"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_memory_manager.py::test_shutdown_waits_for_archive_before_closing -v`
Expected: FAIL，`AttributeError: module 'backend.core.hooks' has no attribute '_get_memory_manager'`

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

顺序很重要：归档任务还要用 Redis 和数据库，所以必须在 `shutdown_executors` / `close_redis` **之前**完成。

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

**这个任务解决什么问题**：两件事一起做，因为它们都属于「画像的 JSON 字段该怎么写」这一个关注点。

1. **`weak_points` / `preferences` 现在是整体替换**。`user_profile_mapper.update_user_profile` 对 DTO 里的每个字段直接 `setattr`，所以 Agent 传一个 `{"函数": "薄弱"}` 进来，会把之前存的所有其他知识点冲掉。这与「画像只增不改」的既定策略直接矛盾，而且丢数据时不报任何错。
2. **新增 `notes` 列作为逃生口**。画像的键名受词表约束（Task 10），但总有观察放不进任何已知键。`notes` 是纯自由文本数组，追加式写入，不参与频次确认，也**不注入 system prompt**（只在 `user_profile_query_tool` 被主动调用时返回），避免它把每轮的画像段越撑越大。

**怎么做**：把合并逻辑抽成两个纯函数 `merge_json_field`（浅合并，新键追加、同键取新值、老键一律保留）和 `append_notes`（追加、跳过完全重复、超过 50 条丢最旧的），在 mapper 里按字段名分派：`_MERGE_FIELDS` 走合并，`_APPEND_FIELDS` 走追加，其余照旧覆盖。

**做完之后**：写入一个新知识点不再冲掉已有的；模型遇到放不进词表的观察时，有地方可放，不用自造键名。

**前后依赖**：不依赖前面的任务。产出的 `notes` 列与 `merge_json_field` / `append_notes`，Task 9、10、13 都会用到。

**实现时注意**：

1. **`notes` 有两条写入路径，容易只改一条**。新建画像走 `UserProfile(...)`，更新画像走 `UserProfileUpdateRequest`。只给新建分支补 `notes`（这是最容易犯的错）的话，画像已经存在时——也就是绝大多数情况——传进来的 `notes` 会被静默丢掉，mapper 里的 `_APPEND_FIELDS` 分支永远不会被触发，而且不报任何错。Step 3 里两条路径都改了，实现时不要漏。
2. **`create_all` 只建表、不加列**。`user_profile` 表已经存在时，必须手动执行 Step 3 里的 `ALTER TABLE`。MySQL 的 JSON 列不能有字面默认值，已有的行要用 `JSON_ARRAY()` 回填。
3. **`notes` 的去重是按整条字符串完全相同来判断的**。「喜欢先看思路」和「做题时喜欢先看思路」会各占一条，上限 50 条只能靠丢最旧的兜底。这是有意选的简单策略：语义去重要么靠模型、要么靠向量，成本都不划算。

**Files:**
- Modify: `backend/model/user_profile.py`（新增 `notes` 列）
- Modify: `backend/schemas/request/ltm_request.py`（新增 `notes`）
- Modify: `backend/schemas/response/user_profile_response.py`（新增 `notes`）
- Modify: `backend/agents/memory/long_term_memory.py`（新建画像时填 `notes`）
- Modify: `backend/dao/user_profile_mapper.py`（`update_user_profile` 与 `create_memory`）
- Modify: `backend/schemas/request/user_profile_update_request.py`（新增 `notes`，更新路径要用）
- Create: `backend/tests/test_profile_merge.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `merge_json_field(old: dict | None, new: dict | None) -> dict` —— 浅合并，供 `weak_points` / `preferences` 使用
  - `append_notes(old: list | None, new: list | None, max_size: int = 50) -> list` —— 追加去重并限长，供 `notes` 使用
  - `UserProfile.notes` JSON 列，默认 `[]`
  - `LTMRequest.notes: Optional[list]`、`UserProfileUpdateRequest.notes: Optional[list]`、`UserProfileResponse.notes: list`

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

`backend/schemas/request/user_profile_update_request.py` —— 加一个字段。**这一步不能省**：更新已有画像走的是这个 schema，少了它，`notes` 在更新时会被静默丢掉：

```python
    notes: Optional[list] = Field(None, description='本次新增的自由观察记录（追加，不覆盖）')
```

`backend/schemas/response/user_profile_response.py` —— 加一个字段：

```python
    notes: list = Field(default_factory=list, description='自由观察记录', examples=[['做题时喜欢先看思路']])
```

注意是 `examples`（复数）且外面多套一层列表：`examples` 本身就是「示例的列表」，这个字段的一个示例值又是一个列表。不要写成 `example=`——Pydantic V2 已弃用、V3 会移除，同文件其他字段也已统一改为 `examples=[...]`。

`backend/agents/memory/long_term_memory.py` 的 `add_or_update` 里，**两个分支都要补**。新建画像的分支：

```python
                notes=request.notes or [],
```

更新画像的分支，构造 `UserProfileUpdateRequest` 时加上：

```python
            notes=request.notes,
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

**这个任务解决什么问题**：Agent 分不清「这题再简单点」和「以后题目都简单点」。前者是一次性诉求，写进长期画像会污染后续所有请求；后者才是稳定偏好。靠提示词约束模型自己判断，准确率不够。

**怎么做**：改用统计。事实类字段（`grade`、`subject`）和 `notes` 直写；偏好类字段（`weak_points`、`preferences`）先进候选池，同一个字段路径**第二次**出现才晋升为长期画像。代价是偏好生效滞后一轮，换来的是判定不再依赖模型的主观发挥。

**这里绕开了一个难题**：「两次说的是不是同一个偏好」本来需要语义相似度计算。但 LLM 在提取时已经把偏好结构化成了字段名，所以同键即同偏好，比较字符串就够了——**前提是键名受控**，这由 Task 10 保证。

**做完之后**：用户随口一说的要求不会立刻进画像；反复出现的偏好会自动沉淀下来。

**前后依赖**：用到 `get_redis_client()`。产出 `ProfileCandidateStore`（`offer` / `get_all` / `clear`）和改写后的 `UserProfileSaveTool`；Task 10 会在这个工具里再插入一层词表分流。

**实现时注意**：

1. **`offer` 是读-改-写，不是原子操作**：先 `hget` 读出计数，改完再 `hset` 写回。同一个用户的两个请求并发进来时，可能各自读到 `count=0`，最后都写成 1，少计一次。单个用户同时发两个请求的情况很少，漏记一次的后果也只是偏好晚一轮生效，所以可以接受。这一点和 Task 5 的短期记忆正好相反：那里丢的是用户消息，所以必须用 Lua 保证原子。要严格的话，这里也可以改成 `HINCRBY` 或一小段 Lua。
2. **晋升后先删候选、再写库**。`offer` 达到阈值时会立刻 `hdel` 掉候选项，如果紧接着写库失败，这次晋升就丢了，用户得再说一遍。想更稳妥，可以改成写库成功后再删。
3. **Redis 不可用时不能阻断主流程**：`offer` 捕获 `RedisError` 后返回 `(0, False)` 并记日志，画像写不进去，但用户的请求照常完成。
4. **工具返回的文本要如实汇报**。写入了什么、哪些还在候选池里（`题目风格（1/2）`），都要说清楚。否则 Agent 会以为偏好已经生效，转头就告诉用户「已经记住了」。

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

**这个任务解决什么问题**：Task 9 的工具描述里承诺了「键必须取自 profile_schema 的词表」，这个任务把它兑现。

**为什么键名必须受控**：频次确认建立在「同键即同偏好」这一条假设上。键名如果自由生成，同一个偏好在三轮里可能变成 `preferences.题目风格`、`preferences.出题风格`、`preferences.题目偏好`，计数永远累积不到阈值，偏好永远晋升不了——而且**不报任何错**，这是最难排查的一类问题。

**为什么词表放在 SKILL.md 而不是代码里**：`loader.py` 有 mtime 缓存，编辑 Markdown 下一次调用就生效。加一个新的画像维度，不需要改表、不需要改代码、不需要重启，和 `question_variant`、`memory_refinement` 是同一套热加载机制。

**词表外的键不丢弃，自动转存 `notes`**——既不静默失效，也不丢信息。

**做完之后**：模型写画像时有一份明确的可用键清单；写错了也不会悄悄丢失，而是落到 `notes` 里。

**前后依赖**：用到 `loader.py` 里已有的 `load_skill_code`。产出 `load_vocab` 和 `profile_schema/SKILL.md`，并给 Task 9 的工具加上分流逻辑。

**实现时注意**：

1. **`load_vocab` 会 `exec` 执行 Markdown 里的代码块**。SKILL.md 和代码在同一个仓库、同等信任，这样做没问题，但要清楚这条边界：任何能修改 SKILL.md 的人，都能在服务进程里执行任意代码。不要把这个目录开放给不受信任的编辑者。
2. **空词表被当作「不做限制」**。分流那段写的是 `if allowed and sub_key not in allowed`，所以 SKILL.md 写坏了、解析不出 `VOCAB` 时，工具会退回到「什么键都接受」。这是有意的降级——词表出问题不该让画像写入整个失效——但也意味着词表失效时没人会发现，建议在 `load_vocab` 返回空字典时补一条 warning 日志。
3. **触发词很宽**。`以后`、`每次都`、`我总是` 这类词在很多无关输入里都会出现，命中后只是让 Agent 多看一眼 Skill 清单，代价可以接受。
4. **词表是给模型看的提示，不是硬校验**。真正拦截发生在工具里（词表外的键转存 `notes`）。模型看不看那份 Markdown、看了听不听，都不影响最终结果。

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

## Task 11: 记忆表与数据访问层

**这个任务解决什么问题**：归档下来的对话要有个地方存。这里建两张表：`memory_record` 存原文，`memory_digest` 存会话要点。

**为什么原文只增不改、不做压缩**：「太长了就压缩」最容易写成「在上一版摘要的基础上继续摘要」，压到第五轮，最早的内容已经被改写过五次，细节没了，甚至可能被改得和原意不符，而且原文已经不在，无从回溯。把两件事分开就没有这个问题：原文一条几百字节，存得起，永不改动；要点是派生数据，随时可以从原文重算，摘坏了、换了提示词，重跑一遍就行。

**为什么放 MySQL 而不是向量库**：见文档开头的设计变更说明。顺带两个好处：`mysqldump` 天然覆盖这份数据（向量库的目录不在备份范围里），排查问题时可以直接用 SQL 按会话、按时间翻原文。

**做完之后**：有了可用的读写接口，Task 12 的归档就有地方落了。

**前后依赖**：不依赖前面的任务，产出 `MemoryMapper`，Task 12 使用。

**实现时注意**：

1. **归档是「至少一次」语义**：写库成功、ack 失败时会重试同一条。用原始条目的 sha1 做唯一键，重复写入被数据库直接挡下，比「先查再写」可靠（后者仍有并发窗口）。
2. **DAO 返回 dict 和 dataclass，不返回 ORM 对象**：会话一关，ORM 对象就是游离状态，调用方再访问属性会踩 `DetachedInstanceError`。
3. **表是靠 `Base.metadata.create_all` 建的**，而 `create_all` 只会建「已经被导入的模型类」对应的表。`dao/memory_mapper.py` 导入了 `model/memory.py`，`agent_api` 又导入了 `MemoryMapper`，所以启动钩子跑到 `create_all` 时这两张表已经注册进 metadata 了。新增模型时务必确认这条导入链，否则表不会被创建，而且不报错。

**Files:**
- Create: `backend/model/memory.py`
- Create: `backend/dao/memory_mapper.py`
- Test: `backend/tests/test_memory_mapper.py`

**Interfaces:**
- Consumes: `backend.model.Base`、`AsyncSessionLocal`
- Produces:
  - `MemoryRecord`（表 `memory_record`）：`id, user_id, session_id, user_text, model_text, fingerprint(唯一), created_at`
  - `MemoryDigest`（表 `memory_digest`）：`id, user_id, session_id, summary, covered_until_id, updated_at`，`(user_id, session_id)` 唯一
  - `DigestState(summary: str, covered_until_id: int)`
  - `MemoryMapper(session_factory)`：
    - `async add_record(user_id, session_id, user_text, model_text, fingerprint) -> bool`（fingerprint 重复时返回 False）
    - `async list_records(user_id, session_id, limit) -> list[dict]`（最近 limit 条，按时间从旧到新）
    - `async count_since(user_id, session_id, after_id) -> tuple[int, int]`（未覆盖条数, 最大记录 id）
    - `async get_digest(user_id, session_id) -> DigestState | None`
    - `async save_digest(user_id, session_id, summary, covered_until_id) -> None`
  - `get_memory_mapper() -> MemoryMapper`（进程内单例）

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/test_memory_mapper.py`：

```python
"""
记忆表的数据访问测试。跑在临时的 SQLite 文件库上：DAO 只依赖会话工厂，
换个数据库照样能测，不需要本机装 MySQL，也不会碰到生产库。
"""
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.dao.memory_mapper import MemoryMapper
from backend.model import Base
from backend.model.memory import MemoryDigest, MemoryRecord  # noqa: F401  建表需要先导入模型

USER, SESSION = 7, 7


@pytest_asyncio.fixture
async def mapper(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield MemoryMapper(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


async def _add(mapper, n: int, session_id: int = SESSION) -> None:
    for i in range(n):
        await mapper.add_record(USER, session_id, f"问题{i}", f"回答{i}", f"fp{session_id}-{i}")


async def test_records_come_back_oldest_first(mapper):
    """摘要要按时序读，所以取出来必须是从旧到新"""
    await _add(mapper, 3)
    assert [r["user_text"] for r in await mapper.list_records(USER, SESSION, limit=10)] == ["问题0", "问题1", "问题2"]


async def test_limit_keeps_the_newest(mapper):
    """超过上限时保留最近的几条，而不是最早的几条"""
    await _add(mapper, 5)
    assert [r["user_text"] for r in await mapper.list_records(USER, SESSION, limit=2)] == ["问题3", "问题4"]


async def test_duplicate_fingerprint_is_ignored(mapper):
    """归档是「至少一次」，重试会把同一条再写一遍；靠唯一键挡住，不能出现两条"""
    assert await mapper.add_record(USER, SESSION, "问题", "回答", "same-fp") is True
    assert await mapper.add_record(USER, SESSION, "问题", "回答", "same-fp") is False
    assert len(await mapper.list_records(USER, SESSION, limit=10)) == 1


async def test_sessions_are_isolated(mapper):
    await _add(mapper, 2, session_id=1)
    await _add(mapper, 3, session_id=2)
    assert len(await mapper.list_records(USER, 1, limit=10)) == 2
    assert len(await mapper.list_records(USER, 2, limit=10)) == 3


async def test_count_since_tracks_uncovered_records(mapper):
    await _add(mapper, 3)
    pending, latest = await mapper.count_since(USER, SESSION, after_id=0)
    assert (pending, latest) == (3, 3)
    pending, latest = await mapper.count_since(USER, SESSION, after_id=2)
    assert (pending, latest) == (1, 3)


async def test_count_since_on_empty_session(mapper):
    assert await mapper.count_since(USER, 999, after_id=0) == (0, 0)


async def test_digest_is_created_then_updated(mapper):
    assert await mapper.get_digest(USER, SESSION) is None

    await mapper.save_digest(USER, SESSION, "第一版摘要", covered_until_id=3)
    first = await mapper.get_digest(USER, SESSION)
    assert (first.summary, first.covered_until_id) == ("第一版摘要", 3)

    await mapper.save_digest(USER, SESSION, "第二版摘要", covered_until_id=8)
    second = await mapper.get_digest(USER, SESSION)
    assert (second.summary, second.covered_until_id) == ("第二版摘要", 8)
    assert len(await mapper.list_records(USER, SESSION, limit=10)) == 0, "保存摘要不应影响原文"
```

测试跑在临时的 SQLite 文件库上：DAO 只依赖会话工厂，换个数据库照样测，既不需要本机装 MySQL，也不会碰到真实数据。`aiosqlite` 已经在 `requirements.txt` 里。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_memory_mapper.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.dao.memory_mapper'`

- [ ] **Step 3: 建表**

创建 `backend/model/memory.py`：

```python
from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from backend.model import Base


class MemoryRecord(Base):
    """
    一轮对话的原文，只增不改。

    短期窗口（Redis）挤出来的记录会落到这里。原文永不压缩：一条几百字节，存得起；
    摘要是从它重算出来的派生数据，摘坏了、换了提示词，重跑一遍就行。
    """
    __tablename__ = 'memory_record'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键')
    user_id = Column(Integer, nullable=False, comment='用户ID')
    session_id = Column(Integer, nullable=False, comment='会话ID')
    user_text = Column(Text, nullable=False, comment='用户说的话')
    model_text = Column(Text, nullable=False, comment='模型的回答')
    # 归档是「至少一次」语义：写库成功但 ack 失败时会重试。用原始条目的哈希做唯一键，
    # 重复写入会被数据库直接拦下，不需要先查再写（那样仍然有并发窗口）
    fingerprint = Column(String(40), nullable=False, unique=True, comment='原始条目的 sha1，用于去重')
    created_at = Column(DateTime, nullable=False, default=func.current_timestamp(), comment='归档时间')

    __table_args__ = (
        Index('ix_memory_record_session', 'user_id', 'session_id', 'id'),
    )


class MemoryDigest(Base):
    """
    一个会话一条摘要，由 memory_record 的原文重算得到。

    covered_until_id 记录这份摘要覆盖到了哪条记录，用来判断「新攒了多少条还没进摘要」。
    """
    __tablename__ = 'memory_digest'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键')
    user_id = Column(Integer, nullable=False, comment='用户ID')
    session_id = Column(Integer, nullable=False, comment='会话ID')
    summary = Column(Text, nullable=False, comment='会话要点摘要')
    covered_until_id = Column(Integer, nullable=False, comment='摘要覆盖到的最大 memory_record.id')
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp(), comment='更新时间')

    __table_args__ = (
        UniqueConstraint('user_id', 'session_id', name='uq_memory_digest_session'),
    )
```

`id` 用 `Integer` 而不是 `BigInteger`：SQLite 只把 `INTEGER PRIMARY KEY` 当作自增的 rowid 别名，写成 `BIGINT` 时自增行为要额外适配，而 21 亿行对这个项目绰绰有余。

- [ ] **Step 4: 写数据访问层**

创建 `backend/dao/memory_mapper.py`：

```python
"""
对话记忆的数据访问层：原文表 memory_record、会话摘要表 memory_digest。

返回的都是普通 dict 和 dataclass，不是 ORM 对象：会话一关，ORM 对象就处于游离状态，
调用方再访问属性容易踩到 DetachedInstanceError。
"""
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.model.memory import MemoryDigest, MemoryRecord


@dataclass
class DigestState:
    summary: str
    covered_until_id: int


class MemoryMapper:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def add_record(self, user_id: int, session_id: int, user_text: str,
                         model_text: str, fingerprint: str) -> bool:
        """
        写入一条对话原文。返回是否真的写入：fingerprint 已存在说明这条之前归档过，
        返回 False（幂等，调用方照常 ack 即可）。
        """
        async with self.session_factory() as session:
            session.add(MemoryRecord(
                user_id=user_id, session_id=session_id,
                user_text=user_text, model_text=model_text, fingerprint=fingerprint,
            ))
            try:
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                return False

    async def list_records(self, user_id: int, session_id: int, limit: int) -> list[dict]:
        """取这个会话最近 limit 条原文，按时间从旧到新返回（摘要要按时序读）"""
        async with self.session_factory() as session:
            stmt = (
                select(MemoryRecord)
                .where(MemoryRecord.user_id == user_id, MemoryRecord.session_id == session_id)
                .order_by(MemoryRecord.id.desc())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).scalars().all()
        return [{"id": r.id, "user_text": r.user_text, "model_text": r.model_text} for r in reversed(rows)]

    async def count_since(self, user_id: int, session_id: int, after_id: int) -> tuple[int, int]:
        """返回 (id 大于 after_id 的记录条数, 这个会话最大的记录 id)。用来判断该不该重算摘要"""
        async with self.session_factory() as session:
            base = (MemoryRecord.user_id == user_id, MemoryRecord.session_id == session_id)
            pending = (await session.execute(
                select(func.count()).select_from(MemoryRecord).where(*base, MemoryRecord.id > after_id)
            )).scalar_one()
            latest = (await session.execute(
                select(func.max(MemoryRecord.id)).where(*base)
            )).scalar()
        return pending, latest or 0

    async def get_digest(self, user_id: int, session_id: int) -> Optional[DigestState]:
        async with self.session_factory() as session:
            stmt = select(MemoryDigest).where(
                MemoryDigest.user_id == user_id, MemoryDigest.session_id == session_id
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                return None
            return DigestState(summary=row.summary, covered_until_id=row.covered_until_id)

    async def save_digest(self, user_id: int, session_id: int, summary: str, covered_until_id: int) -> None:
        """有则更新、无则插入"""
        async with self.session_factory() as session:
            stmt = select(MemoryDigest).where(
                MemoryDigest.user_id == user_id, MemoryDigest.session_id == session_id
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                session.add(MemoryDigest(
                    user_id=user_id, session_id=session_id,
                    summary=summary, covered_until_id=covered_until_id,
                ))
            else:
                row.summary = summary
                row.covered_until_id = covered_until_id
            await session.commit()


_mapper: Optional[MemoryMapper] = None


def get_memory_mapper() -> MemoryMapper:
    """进程内单例。与 get_short_term_memory 同一个用法。"""
    global _mapper
    if _mapper is None:
        from backend.model import AsyncSessionLocal
        _mapper = MemoryMapper(AsyncSessionLocal)
    return _mapper
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_memory_mapper.py -v`
Expected: 7 passed

- [ ] **Step 6: 提交**

```bash
git add backend/model/memory.py backend/dao/memory_mapper.py backend/tests/test_memory_mapper.py
git commit -m "feat: 新增对话原文表与会话要点表及其数据访问层"
```

---

## Task 12: 归档写入 MySQL，并维护会话要点

**这个任务解决什么问题**：Task 6 的归档把记录经 LLM 精炼后写进向量库。这个任务把落点换成 Task 11 的两张表：原文直接入库（**不再需要精炼那次 LLM 调用**），攒够几条之后再用一次调用重算会话要点。

**为什么这样更省也更稳**：原来每归档一条就要调一次 LLM（精炼），现在每 5 条才调一次（摘要），调用次数降到五分之一；而且写原文这一步不依赖模型，模型出问题时记录照样落库，最多是要点晚一点更新。

**做完之后**：溢出的对话不会再丢，`memory_digest` 里有一份随会话推进不断更新的要点，Task 16 把它注入请求上下文。应用也不再依赖 chromadb——删掉向量记忆模块之后，`import backend.main` 不会再加载 chromadb。

**前后依赖**：用到 Task 11 的 `MemoryMapper`、Task 6 的后台任务骨架。产出 `build_session_digest` 与新的 `MemoryManager` 构造签名，Task 15、16 使用。

**实现时注意**：

1. **摘要失败不能回滚原文**。原文已经落库、pending 也已经 ack，这时摘要调用失败只记日志，下一批归档会再试一次。反过来做（摘要失败就不 ack）会让同一批记录反复重写。
2. **`covered_until_id` 要取「这次真正喂给模型的最后一条记录的 id」**，不是「当前最大 id」。摘要生成期间可能又有新记录落库，取最大 id 会让要点声称覆盖了其实没看过的内容。
3. **删掉的三个文件是死代码**：`vector_store_manager.py`（记忆层唯一的使用者已经不在了）、`extract_memory_agent.py` 与 `agents/skills/memory_refinement/`（精炼流程被摘要取代）。留着它们会让人误以为还有第二条归档路径。
4. **新的 skill 会出现在 ReAct 的技能清单里**。`session_digest` 只给摘要用，不该让 Agent 去加载它，但 `loader.py` 现在会列出所有 SKILL.md。等 RAG 计划 Task 6 的 `visibility: internal` 落地后，给它补上这个字段。眼下 `triggers` 留空，至少不会被字面匹配触发。

**Files:**
- Create: `backend/agents/skills/session_digest/SKILL.md`
- Create: `backend/agents/agent/session_digest_agent.py`
- Test: `backend/tests/test_session_digest_agent.py`
- Modify: `backend/agents/memory/memory_manager.py`（整体替换）
- Modify: `backend/tests/test_memory_manager.py`（整体替换）
- Modify: `backend/api/user_api/agent_api.py`（接线）
- Delete: `backend/agents/memory/vector_store_manager.py`、`backend/agents/agent/extract_memory_agent.py`、`backend/agents/skills/memory_refinement/`

**Interfaces:**
- Consumes: Task 11 的 `MemoryMapper`；`load_skill`、`get_llm`
- Produces:
  - `async build_session_digest(records: list[dict]) -> str`
  - `MemoryManager(long_term_memory, short_term_memory, memory_mapper)`
  - `MemoryManager.get_memory_for_planner` 返回值新增 `session_digest: str`
  - 常量 `DIGEST_EVERY = 5`、`DIGEST_SOURCE_LIMIT = 40`

- [ ] **Step 1: 写摘要的提示词**

创建 `backend/agents/skills/session_digest/SKILL.md`：

````markdown
---
name: session_digest
description: 会话要点摘要协议：把一段辅导对话压成客观要点，供后续对话作为上下文使用
triggers: []
version: 1.0
---

# 角色
你负责把一段师生辅导对话压缩成「会话要点」，供后续对话作为背景信息使用。

# 要求
1. 用第三人称客观陈述，不要用「你」「我」
2. 保留这几类信息，其余一律略去：
   - 学生问过哪些题、属于什么知识点
   - 学生卡在哪里、犯过什么错
   - 给出过什么关键结论或建议
   - 学生明确表达的偏好和要求
3. 不要复述题目原文和完整解题过程，只留结论
4. 不要编造对话里没有的内容；信息不足时就少写
5. 总长度控制在 300 字以内，用短句，一行一条
6. 只输出要点本身，不要任何开场白、标题或解释

# 示例

输入：
1. 用户：解方程 2x+9=5x-3
   助手：移项得 3x=12，所以 x=4。
2. 用户：这类题我老是把移项的符号弄错
   助手：移项时要变号，建议每步写出中间结果。
3. 用户：以后出题别和原题太像
   助手：好的，后续会换情境和数字。

输出：
学生练习一元一次方程的求解，能跟上移项求解的步骤。
学生自述容易在移项时弄错符号，已建议逐步写出中间结果。
学生要求后续题目不要与原题过于相似。
````

- [ ] **Step 2: 写摘要生成的测试**

创建 `backend/tests/test_session_digest_agent.py`（先写前 4 个测试；输入截断与限时的测试在 Task 15 补）：

```python
import backend.agents.agent.session_digest_agent as sd

RECORDS = [
    {"id": 1, "user_text": "解方程 2x+9=5x-3", "model_text": "移项得 3x=12，x=4"},
    {"id": 2, "user_text": "我老是把移项的符号弄错", "model_text": "移项要变号，建议写出中间结果"},
]


class FakeLLM:
    def __init__(self, content="学生练习一元一次方程，容易在移项时弄错符号。"):
        self.content = content
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        return type("Response", (), {"content": self.content})()


def _patch(monkeypatch, llm):
    monkeypatch.setattr(sd, "build_session_digest_agent", lambda: llm)
    monkeypatch.setattr(sd, "load_skill", lambda name: f"【{name} 剧本】")
    return llm


def test_records_are_formatted_in_order():
    text = sd.format_records(RECORDS)
    assert text.index("解方程") < text.index("移项的符号"), "必须按时序排列"
    assert "1. 用户：" in text and "   助手：" in text


async def test_digest_uses_the_skill_and_returns_text(monkeypatch):
    llm = _patch(monkeypatch, FakeLLM())
    result = await sd.build_session_digest(RECORDS)
    assert result == "学生练习一元一次方程，容易在移项时弄错符号。"
    assert llm.messages[0].content == "【session_digest 剧本】", "system 必须来自 SKILL.md，提示词不写在代码里"
    assert "解方程 2x+9=5x-3" in llm.messages[1].content


async def test_empty_records_make_no_call(monkeypatch):
    class Exploding:
        async def ainvoke(self, messages):
            raise AssertionError("没有原文时不应该调用模型")
    _patch(monkeypatch, Exploding())
    assert await sd.build_session_digest([]) == ""


async def test_whitespace_and_non_text_are_handled(monkeypatch):
    _patch(monkeypatch, FakeLLM(content="  要点  \n"))
    assert await sd.build_session_digest(RECORDS) == "要点"

    _patch(monkeypatch, FakeLLM(content=[{"type": "text"}]))
    assert await sd.build_session_digest(RECORDS) == "", "模型返回非文本时给空摘要，而不是让整条链路报错"
```

- [ ] **Step 3: 实现摘要生成**

创建 `backend/agents/agent/session_digest_agent.py`（Task 15 会在此基础上加截断与限时）：

```python
"""
会话摘要：把一个会话已归档的原文压成一段要点，作为后续请求的上下文。

摘要永远从原文重算，不在上一版摘要的基础上继续摘要——那样每压一次信息就失真一点，
几轮之后早期内容已经面目全非，而且原文若已被覆盖就无从回溯。原文在 memory_record 里
只增不改，重算的代价只是一次 LLM 调用。
"""
import os

from langchain_core.messages import HumanMessage, SystemMessage

from backend.agents.agent.get_llm import get_llm
from backend.agents.skills import load_skill
from backend.core.config import load_env
from backend.core.single_tool import singleton_method
from backend.middleware.logging import get_logger

logger = get_logger(__name__)

load_env()


@singleton_method
def build_session_digest_agent():
    """摘要用的模型可以比主模型便宜：DIGEST_MODEL 没配就回落到 MODEL_NAME"""
    model = os.getenv('DIGEST_MODEL')
    return get_llm(model=model) if model else get_llm()


def format_records(records: list[dict]) -> str:
    """把原文排成带序号的对话，模型按时序阅读"""
    lines: list[str] = []
    for index, record in enumerate(records, 1):
        lines.append(f"{index}. 用户：{record['user_text']}")
        lines.append(f"   助手：{record['model_text']}")
    return "\n".join(lines)


async def build_session_digest(records: list[dict]) -> str:
    """把一个会话的原文压成要点。没有原文时返回空字符串。"""
    if not records:
        return ""

    system_body = load_skill("session_digest")
    llm = build_session_digest_agent()
    response = await llm.ainvoke([
        SystemMessage(content=system_body),
        HumanMessage(content=format_records(records)),
    ])
    content = response.content if isinstance(response.content, str) else ""
    return content.strip()
```

Run: `cd backend && python -m pytest tests/test_session_digest_agent.py -v`
Expected: 4 passed

- [ ] **Step 4: 重写归档逻辑的测试**

用下面的内容整体替换 `backend/tests/test_memory_manager.py`：

```python
import asyncio
import json
import logging

import pytest

import backend.agents.memory.memory_manager as mm
from backend.agents.memory.memory_manager import DIGEST_EVERY, MemoryManager
from backend.agents.memory.short_term_memory import MemoryUnit, ShortTermMemory
from backend.dao.memory_mapper import DigestState

USER, SESSION = 7, 7


class FakeMapper:
    """内存版的记忆表：行为与 MemoryMapper 一致，包括 fingerprint 去重"""

    def __init__(self, fail: bool = False):
        self.records: list[dict] = []
        self.digests: dict[tuple[int, int], DigestState] = {}
        self.fail = fail

    async def add_record(self, user_id, session_id, user_text, model_text, fingerprint) -> bool:
        if self.fail:
            raise RuntimeError("数据库写入失败")
        if any(r["fingerprint"] == fingerprint for r in self.records):
            return False
        self.records.append({
            "id": len(self.records) + 1, "user_id": user_id, "session_id": session_id,
            "user_text": user_text, "model_text": model_text, "fingerprint": fingerprint,
        })
        return True

    def _of_session(self, user_id, session_id) -> list[dict]:
        return [r for r in self.records if r["user_id"] == user_id and r["session_id"] == session_id]

    async def list_records(self, user_id, session_id, limit) -> list[dict]:
        return self._of_session(user_id, session_id)[-limit:]

    async def count_since(self, user_id, session_id, after_id) -> tuple[int, int]:
        rows = self._of_session(user_id, session_id)
        return len([r for r in rows if r["id"] > after_id]), (rows[-1]["id"] if rows else 0)

    async def get_digest(self, user_id, session_id):
        return self.digests.get((user_id, session_id))

    async def save_digest(self, user_id, session_id, summary, covered_until_id) -> None:
        self.digests[(user_id, session_id)] = DigestState(summary, covered_until_id)


@pytest.fixture
def manager(redis_test_client, monkeypatch):
    stm = ShortTermMemory(max_memory_size=2)
    monkeypatch.setattr(stm, "_client", redis_test_client)

    async def fake_digest(records):
        return f"摘要：共{len(records)}条"

    monkeypatch.setattr(mm, "build_session_digest", fake_digest)

    mgr = MemoryManager.__new__(MemoryManager)
    mgr.long_term_memory = None
    mgr.short_term_memory = stm
    mgr.memory_mapper = FakeMapper()
    mgr._tasks = set()
    return mgr


async def _talk(manager, n: int, start: int = 0) -> None:
    for i in range(start, start + n):
        await manager.add_memory(USER, SESSION, MemoryUnit(f"问题{i}", f"回答{i}"))


async def _fill_pending(manager, n: int) -> None:
    """直接写短期记忆，让 pending 里积压 n 条（max=2，第 3 条起每条挤出一条），不触发后台归档"""
    for i in range(n + 2):
        await manager.short_term_memory.add_memory(USER, SESSION, MemoryUnit(f"问题{i}", f"回答{i}"))


async def test_below_limit_spawns_nothing(manager):
    await _talk(manager, 1)
    assert manager._tasks == set()
    assert manager.memory_mapper.records == []


async def test_overflow_archives_original_text_in_background(manager):
    await _talk(manager, 3)
    await manager.shutdown(5)

    [record] = manager.memory_mapper.records
    assert (record["user_text"], record["model_text"]) == ("问题0", "回答0"), "存的是原文，不是摘要"
    assert record["user_id"] == USER and record["session_id"] == SESSION
    assert await manager.short_term_memory.get_pending(USER, SESSION) == []


async def test_request_path_does_not_wait_for_archive(manager, monkeypatch):
    async def slow_digest(records):
        await asyncio.sleep(1.0)
        return "慢摘要"
    monkeypatch.setattr(mm, "build_session_digest", slow_digest)

    await _talk(manager, 2)
    start = asyncio.get_running_loop().time()
    await manager.add_memory(USER, SESSION, MemoryUnit("问题2", "回答2"))
    assert asyncio.get_running_loop().time() - start < 0.2, "归档必须在后台跑，不能拖慢用户请求"
    await manager.shutdown(5)


async def test_failed_write_keeps_item_pending(manager):
    manager.memory_mapper.fail = True
    await _talk(manager, 3)
    await manager.shutdown(5)

    [raw] = await manager.short_term_memory.get_pending(USER, SESSION)
    assert json.loads(raw)["memory"]["user_memory"] == "问题0"


async def test_drain_recovers_and_is_idempotent(manager):
    manager.memory_mapper.fail = True
    await _talk(manager, 3)
    await manager.shutdown(5)
    assert len(await manager.short_term_memory.get_pending(USER, SESSION)) == 1

    manager.memory_mapper.fail = False
    assert await manager.drain_pending() == 1
    assert await manager.drain_pending() == 0
    assert len(manager.memory_mapper.records) == 1


async def test_same_item_archived_twice_writes_one_row(manager, redis_test_client):
    """写库成功、ack 失败时会重试同一条：靠 fingerprint 去重，不能出现两行"""
    await _fill_pending(manager, 1)
    [raw] = await manager.short_term_memory.get_pending(USER, SESSION)

    assert await manager._archive_one(USER, SESSION, raw) is True
    await redis_test_client.rpush(ShortTermMemory.pending_key(USER, SESSION), raw)   # 模拟 ack 没成功
    assert await manager._archive_one(USER, SESSION, raw) is True

    assert len(manager.memory_mapper.records) == 1


async def test_digest_is_rebuilt_after_enough_records(manager):
    await _fill_pending(manager, DIGEST_EVERY)
    assert await manager.drain_pending() == DIGEST_EVERY

    digest = await manager.memory_mapper.get_digest(USER, SESSION)
    assert digest is not None, f"攒够 {DIGEST_EVERY} 条就该重算摘要"
    assert digest.summary == f"摘要：共{DIGEST_EVERY}条"
    assert digest.covered_until_id == DIGEST_EVERY, "覆盖到最后一条记录"


async def test_digest_waits_until_enough_records(manager):
    await _fill_pending(manager, DIGEST_EVERY - 1)
    await manager.drain_pending()
    assert await manager.memory_mapper.get_digest(USER, SESSION) is None, "不够条数时不调用大模型"


async def test_digest_is_rebuilt_from_raw_records(manager, monkeypatch):
    """重算摘要时喂给模型的必须是原文，绝不是上一版摘要——否则信息会一轮轮失真"""
    seen: list[list[dict]] = []

    async def spy(records):
        seen.append(records)
        return f"摘要{len(seen)}"
    monkeypatch.setattr(mm, "build_session_digest", spy)

    await _fill_pending(manager, DIGEST_EVERY)
    await manager.drain_pending()
    await _fill_pending(manager, DIGEST_EVERY)          # 再来一批，触发第二次重算
    await manager.drain_pending()

    assert len(seen) == 2
    assert all("user_text" in r for r in seen[1]), "第二次拿到的仍然是原文"
    assert not any("摘要1" in str(r) for r in seen[1]), "上一版摘要不能作为输入"


async def test_digest_failure_does_not_lose_records(manager, monkeypatch, caplog):
    async def boom(records):
        raise RuntimeError("摘要模型超时")
    monkeypatch.setattr(mm, "build_session_digest", boom)

    await _fill_pending(manager, DIGEST_EVERY)
    with caplog.at_level(logging.ERROR):
        assert await manager.drain_pending() == DIGEST_EVERY

    assert len(manager.memory_mapper.records) == DIGEST_EVERY, "原文已经落库，不能因为摘要失败而回滚"
    assert await manager.short_term_memory.get_pending(USER, SESSION) == []
    assert "会话摘要更新失败" in caplog.text


async def test_unparseable_item_is_dropped(manager, redis_test_client):
    await redis_test_client.rpush(ShortTermMemory.pending_key(USER, SESSION), "{不是json")
    assert await manager.drain_pending() == 0
    assert await manager.short_term_memory.get_pending(USER, SESSION) == []


async def test_finished_tasks_are_released(manager):
    await _talk(manager, 4)
    await manager.shutdown(5)
    assert manager._tasks == set()
    assert len(manager.memory_mapper.records) == 2


async def test_background_exception_is_logged(manager, monkeypatch, caplog):
    async def boom(*args, **kwargs):
        raise RuntimeError("意料之外的错误")
    monkeypatch.setattr(manager, "_archive", boom)

    with caplog.at_level(logging.ERROR):
        await _talk(manager, 3)
        await manager.shutdown(5)
    assert "意料之外的错误" in caplog.text
    assert manager._tasks == set()


async def test_shutdown_timeout_cancels_and_waits(manager, monkeypatch):
    async def forever(records):
        await asyncio.sleep(30)
    monkeypatch.setattr(mm, "build_session_digest", forever)

    await _fill_pending(manager, DIGEST_EVERY)
    task = manager._spawn(manager.drain_pending())   # 后台补做：原文写完后卡在摘要那一步
    await asyncio.sleep(0.2)
    await manager.shutdown(timeout=0.05)

    assert task.done() and task.cancelled()
    assert manager._tasks == set()
    assert len(manager.memory_mapper.records) == DIGEST_EVERY, "原文已经落库，只是摘要没来得及做"


async def test_planner_gets_short_memory_and_digest(manager):
    class FakeProfile:
        async def get_by_user_id(self, user_id):
            return "画像"
    manager.long_term_memory = FakeProfile()

    await _fill_pending(manager, DIGEST_EVERY)
    await manager.drain_pending()
    await manager.add_memory(USER, SESSION, MemoryUnit("最新问题", "最新回答"))

    data = await manager.get_memory_for_planner(USER, SESSION)
    assert data["short_memory"][0]["memory"]["user_memory"] == "最新问题"
    assert data["session_digest"] == f"摘要：共{DIGEST_EVERY}条"
    assert data["long_memory"] == "画像"


async def test_planner_digest_is_empty_for_new_session(manager):
    class FakeProfile:
        async def get_by_user_id(self, user_id):
            return None
    manager.long_term_memory = FakeProfile()
    data = await manager.get_memory_for_planner(USER, 999)
    assert data["session_digest"] == ""
```

`FakeMapper` 是内存版的记忆表，连 fingerprint 去重都照着实现了一遍——测试替身要和真实实现同构，否则测过的行为在生产上未必成立。真实的 `MemoryMapper` 由 Task 11 的测试覆盖。

- [ ] **Step 5: 重写 memory_manager**

用下面的内容整体替换 `backend/agents/memory/memory_manager.py`：

```python
"""
三层记忆的统一入口。

归档流程为什么不需要锁：被挤出窗口的条目在 Lua 脚本里已经原子地搬进了 pending 队列，
读路径看不到它，后台归档和窗口读写不会互相干扰。

原文与摘要的分工：
- memory_record 存原文，只增不改。一条几百字节，存得起；出问题能追溯，mysqldump 天然备份
- memory_digest 存会话要点，是从原文重算出来的派生数据。摘坏了、换了提示词，重跑一遍就行，
  不会像「在摘要上继续摘要」那样一轮轮失真
"""
import asyncio
import hashlib
import json
from typing import Any

from backend.agents.agent.session_digest_agent import build_session_digest
from backend.agents.memory.long_term_memory import LongTermMemory
from backend.agents.memory.short_term_memory import ShortTermMemory, MemoryUnit
from backend.core.single_tool import singleMeta
from backend.dao.memory_mapper import MemoryMapper
from backend.middleware.logging import get_logger

logger = get_logger(__name__)

# 攒够这么多条还没进摘要的记录，就重算一次会话摘要。太小则频繁调用大模型，太大则上下文跟不上进度
DIGEST_EVERY = 5
# 重算摘要时最多回看多少条原文：会话再长，单次摘要的输入也有上限
DIGEST_SOURCE_LIMIT = 40


class MemoryManager(metaclass=singleMeta):
    def __init__(self,
                 long_term_memory: LongTermMemory,
                 short_term_memory: ShortTermMemory,
                 memory_mapper: MemoryMapper):
        self.long_term_memory = long_term_memory
        self.short_term_memory = short_term_memory
        self.memory_mapper = memory_mapper
        self._tasks: set[asyncio.Task] = set()

    async def get_memory_for_planner(self, user_id: int, session_id: int) -> dict[str, Any]:
        """规划器需要的三样：最近几轮原文、本会话要点、长期画像"""
        short_memory = await self.short_term_memory.get_latest_memories(user_id, session_id)
        long_memory = await self.long_term_memory.get_by_user_id(user_id)
        digest = await self.memory_mapper.get_digest(user_id, session_id)
        return {
            "short_memory": short_memory,                       # list[dict]
            "long_memory": long_memory,                         # UserProfileResponse | None
            "session_digest": digest.summary if digest else "",  # str
        }

    async def add_memory(self, user_id: int, session_id: int, memory: MemoryUnit) -> None:
        """写入短期记忆；窗口满了就把挤出来的条目交给后台归档，不占用请求路径"""
        evicted = await self.short_term_memory.add_memory(user_id, session_id, memory)
        if evicted:
            self._spawn(self._archive(user_id, session_id, evicted))

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        # 事件循环对任务只持弱引用，自己不存一份的话，任务可能跑到一半被垃圾回收
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self, task: asyncio.Task) -> None:
        """任务结束（成功、失败或被取消）时调用：释放引用，并把异常记进日志"""
        self._tasks.discard(task)
        # 被取消的任务调用 exception() 会抛 CancelledError，要先排除
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            # 回调里没有「当前异常」，exc_info=True 取不到堆栈，要把异常对象直接传进去
            logger.error("归档任务异常退出: %s", exc, exc_info=exc)

    async def _archive(self, user_id, session_id, raw_items: list[str]) -> int:
        """逐条归档，返回成功的条数；真的写进新记录时，顺带看看要不要重算摘要"""
        success = 0
        for raw in raw_items:
            if await self._archive_one(user_id, session_id, raw):
                success += 1
        if success:
            await self._refresh_digest(user_id, session_id)
        return success

    async def _archive_one(self, user_id, session_id, raw: str) -> bool:
        """把一条原文写进 memory_record，写成功才 ack；失败就留在 pending 等下次重试"""
        try:
            unit = json.loads(raw)
        except json.JSONDecodeError as e:
            # 坏数据重试多少次都不会好，直接从 pending 删掉，否则每次启动都会卡在它上面
            logger.error("待归档记忆无法反序列化，已丢弃: %s | %s", e, raw[:100])
            await self.short_term_memory.ack_archived(user_id, session_id, raw)
            return False

        memory = unit.get("memory", {}) if isinstance(unit, dict) else {}
        # 用原始条目的哈希做唯一键：重试写入同一条时被数据库挡下，不会出现两行
        fingerprint = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        try:
            await self.memory_mapper.add_record(
                user_id, session_id,
                memory.get("user_memory", ""), memory.get("model_memory", ""), fingerprint,
            )
        except Exception as e:
            logger.error("写入记忆表失败，留在 pending 待重试: %s", e, exc_info=True)
            return False

        await self.short_term_memory.ack_archived(user_id, session_id, raw)
        return True

    async def _refresh_digest(self, user_id: int, session_id: int) -> bool:
        """
        攒够 DIGEST_EVERY 条新记录就重算一次会话摘要。
        摘要失败不影响已经落库的原文：下一批归档还会再试一次。
        """
        try:
            digest = await self.memory_mapper.get_digest(user_id, session_id)
            covered = digest.covered_until_id if digest else 0
            pending, _ = await self.memory_mapper.count_since(user_id, session_id, covered)
            if pending < DIGEST_EVERY:
                return False

            records = await self.memory_mapper.list_records(user_id, session_id, DIGEST_SOURCE_LIMIT)
            if not records:
                return False
            summary = await build_session_digest(records)
            if not summary:
                logger.warning("会话摘要为空，本次不更新: user=%s session=%s", user_id, session_id)
                return False

            await self.memory_mapper.save_digest(user_id, session_id, summary, records[-1]["id"])
            logger.info("会话摘要已更新: user=%s session=%s 覆盖到记录 %s", user_id, session_id, records[-1]["id"])
            return True
        except Exception as e:
            logger.error("会话摘要更新失败: %s", e, exc_info=True)
            return False

    async def drain_pending(self) -> int:
        """重启恢复：把已弹出但没归档成功的条目补做掉"""
        keys = await self.short_term_memory.scan_pending_keys()
        total = 0
        for key in keys:
            try:
                user_id, session_id = self.short_term_memory.parse_pending_key(key)
            except (IndexError, ValueError):
                logger.error("无法解析pending key，已跳过: %s", key)
                continue

            texts = await self.short_term_memory.get_pending(user_id, session_id)
            if not texts:
                continue
            logger.info("启动恢复：session %s:%s 有 %s 条待归档", user_id, session_id, len(texts))
            total += await self._archive(user_id, session_id, texts)

        if total:
            logger.info("drain_pending 成功归档: %s 条消息", total)
        return total

    async def shutdown(self, timeout: float = 10.0) -> None:
        tasks = list(self._tasks)
        if not tasks:
            return
        logger.info("shutdown 等待 %s 个归档任务", len(tasks))
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            logger.warning("shutdown 时有 %s 个归档任务超时，已取消，下次启动由 drain_pending 补做", len(pending))
            for task in pending:
                task.cancel()
            # cancel() 只是发出取消请求，要等任务真正退出，否则事件循环关闭时会报 Task was destroyed but it is pending
            await asyncio.gather(*pending, return_exceptions=True)
```

Run: `cd backend && python -m pytest tests/test_memory_manager.py -v`
Expected: 16 passed（需要本机 Redis 可用）

- [ ] **Step 6: 接线，并删掉死代码**

`backend/api/user_api/agent_api.py`：把向量库换成记忆表。

```python
from backend.agents.memory.short_term_memory import ShortTermMemory, MemoryUnit
from backend.dao.memory_mapper import MemoryMapper
from backend.dao.user_profile_mapper import UserProfileMapper
...
user_profile_mapper = UserProfileMapper(AsyncSessionLocal)
short_term_memory = ShortTermMemory(max_memory_size=10)
long_term_memory = LongTermMemory(user_profile_mapper, short_term_memory)
memory_mapper = MemoryMapper(AsyncSessionLocal)
memory_manager = MemoryManager(long_term_memory, short_term_memory, memory_mapper)
```

然后删掉三个已经没有使用者的文件：

```bash
cd backend
rm agents/memory/vector_store_manager.py agents/agent/extract_memory_agent.py
rm -r agents/skills/memory_refinement
grep -rn "vector_store_manager\|extract_memory_agent\|memory_refinement" --include=*.py . | grep -v __pycache__
```

Expected: grep 没有输出。

- [ ] **Step 7: 运行全部测试，并确认应用不再加载 chromadb**

```bash
cd backend
python -m pytest -v
python -c "import sys; sys.path.insert(0, '..'); import backend.main; print('chromadb 被导入了吗:', 'chromadb' in sys.modules)"
```

Expected: 测试全部通过；最后一行输出 `chromadb 被导入了吗: False`。记忆层不再碰向量库之后，服务启动更快，也少了一整条可能出错的依赖链。

- [ ] **Step 8: 提交**

```bash
git add backend/agents backend/api backend/tests
git commit -m "feat: 归档改写 MySQL 原文表，并按批重算会话要点"
```

---

## Task 13: 长期画像注入 ReAct system prompt

**这个任务解决什么问题**：长期画像每轮都查了，却被丢掉。`get_memory_for_planner` 返回 `short_memory` 和 `long_memory` 两部分，而 API 只用了前者——查库的钱花了，画像却从没影响过模型。

**为什么注入 system prompt 而不是拼进 `user_input`**：画像是「这个学生是谁」的稳定背景信息。混进 `user_input`，模型会把它当成本次诉求的一部分，比如看到「薄弱知识点：函数」就以为用户现在要讲函数。放进 system prompt，并明确写上「这不是本次的提问内容，不要直接复述」，才是正确的位置。

**为什么在 API 层格式化**：`react_think_node` 每轮都会执行，节点里不能做 IO。API 层在构建 `GraphState` 时查一次、格式化成一段文本放进 `profile_text`，之后每轮直接取用。

**做完之后**：模型在决定调哪个工具、怎么回答时，能看到学生的年级、学科、薄弱点和长期偏好。

**前后依赖**：用到 `get_memory_for_planner` 返回的 `long_memory`（Task 8 之后它还会带上 `notes`）。产出 `GraphState.profile_text`、`format_profile`，以及 `build_react_system_prompt` 的新参数。

**实现时注意**：

1. **`notes` 不进 prompt**，有一条测试专门守着这一点。`notes` 是自由文本、最多能涨到 50 条，全塞进去会把每轮的输入撑大，也会稀释真正重要的信息。它只在 `user_profile_query_tool` 被主动调用时才返回。
2. **`GraphState` 是 TypedDict，新增的键不会自动出现在已有的构造处**。`agent_api` 里有两个地方构建 state（`analyse` 和 `_stream_generator`），两处都要改，漏掉一个的表现是画像在流式接口上不生效。所以节点里读的是 `state.get('profile_text', '')`，少一个键也不会崩。
3. **画像会进入每一轮的 prompt**，轮数越多、成本越高。`format_profile` 里 `weak_points` 只列键名不列值，就是为了控制这段文本的长度。

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
3. 「以后题目不要跟原题太像，出一道 2x+9=5x-3 的变式题」→ 偏好晋升写入画像，并出现在 system prompt 里

- [ ] **Step 8: 提交**

```bash
git add backend/agents/agent/tools.py backend/agents/agent/react_agent.py backend/api/user_api/agent_api.py backend/tests/test_react_agent_async.py
git commit -m "feat: 长期画像注入 ReAct system prompt，不再查了就丢"
```

---

## Task 14: 更新 CLAUDE.md

**这个任务解决什么问题**：`CLAUDE.md` 描述的还是改造前、甚至更早期的结构，和实际代码严重脱节。它是新人和 AI 读这个项目时的第一份地图，过时的地图比没有地图更糟糕——照着它写代码，会一路找不到文件。

**现在错在哪**（至少这些）：

- 工具注册表说在 `agents/skills/` 的 `SKILL_MAP`，实际在 `backend/agents/tools/__init__.py`，是 `TOOLS` / `TOOL_MAP`
- 图节点名写的是 `react_think_node` / `skill_exec_node`，实际是 `react_think` / `execute_tool`
- 没有区分 `agents/skills/`（Markdown 剧本层）和 `agents/tools/`（可执行工具），这是两个概念
- 工具清单缺了 `user_profile_save_tool`、`user_profile_query_tool`、`user_profile_delete_tool`、`load_skill_tool`
- 启动方式写的 `python main.py` 在 `backend/` 下跑不通（代码用的是 `backend.*` 绝对导入）
- 「仓库中不包含前端」已经不成立，仓库里有一个 Vue 3 + Vite 的前端

**还要补什么**：本次改造引入的约定——短期记忆是 Redis LIST、写入与淘汰由 Lua 原子完成（不要再做读-改-写）、归档在后台任务里进行、对话原文存 MySQL 且只增不改、会话要点永远从原文重算、偏好走候选池、画像键名定义在 SKILL.md 里、阻塞操作一律投递到专属线程池。

**前后依赖**：放在最后做，这样所有约定都已经定型。

**实现时注意**：另外两份计划也会改 `CLAUDE.md`——RAG 计划的 Task 23 增加 RAG 一节，部署计划的 Task 9 增加 CI/CD 与部署一节。三者各加各的章节，不要互相覆盖；谁后做，谁负责检查前面的内容还在不在。

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
- 对话记忆存 MySQL：`memory_record` 存原文只增不改，`memory_digest` 存会话要点。要点**永远从原文重算**，不要在上一版摘要上继续摘要
- 注入请求的上下文＝会话要点 + 最近 3 轮原文（`user_input`）+ 长期画像（system prompt）
- 记忆层不使用向量库；`core/executors.py` 的线程池留给 RAG 知识库
- 阻塞操作一律投递到 `backend/core/executors.py` 的专属线程池，不要用 `run_in_executor(None, ...)`

- [ ] **Step 3: 提交**

```bash
git add CLAUDE.md
git commit -m "docs: 更新 CLAUDE.md 至改造后的实际架构"
```

---

## Task 15: 摘要调用的输入截断与限时

**这个任务解决什么问题**：会话要点是靠一次 LLM 调用生成的，而**这个调用没有任何超时**。我在当前环境里查过：`ChatOpenAI` 的 `request_timeout` 是 None，底层 httpx 的超时是 `Timeout(timeout=None)`，服务端一旦不响应，请求就会一直挂着；`max_retries=2` 只在出错时重试，卡住不算出错。这类任务到关停时一定会超时被取消，下次启动重做。

另一头是输入可能很长：一次要点最多回看 40 条原文，而 `model_text` 是 Agent 的完整回答，生成变式题时可能是整套题加解析。既拖慢调用又浪费费用，而摘要并不需要完整的解题过程。

**怎么做**，三件小事：

1. 截断单条记录：`user_text` 最多 200 字，`model_text` 最多 300 字
2. 限制整体输入：超过 6000 字就从最早的记录开始丢——最近发生的事对后续对话更有用
3. 单次限时：20 秒起，每千字加 10 秒，最多 60 秒；超时就抛出去，由 `_refresh_digest` 记日志，下一批归档再试

**做完之后**：摘要不会被一个卡死的请求拖住；日志里有每次摘要的输入字数和耗时，可以据此判断阈值调得合不合适。

**前后依赖**：只依赖 Task 12，可以在它之后任何时候做。

**不做的事：不给「超时过的任务」在关停时多留时间。** 理由有四个：

- 耗时主要花在生成输出和服务端排队上，输入长度只影响读取输入那一步，而这一步很快。
- 卡住的请求，给再多时间也不会返回。
- 关停时间的上限由部署环境决定（`docker stop` 默认 10 秒，k8s 默认 30 秒），超过就被 SIGKILL。
- 原文已经落库，要点晚一批更新没有任何损失。

**Files:**
- Modify: `backend/agents/agent/session_digest_agent.py`
- Modify: `backend/tests/test_session_digest_agent.py`（追加 5 个测试）

**Interfaces:**
- Produces: 常量 `RECORD_USER_MAX_CHARS = 200`、`RECORD_MODEL_MAX_CHARS = 300`、`DIGEST_INPUT_MAX_CHARS = 6000`、`DIGEST_TIMEOUT_BASE/PER_1K/MAX`；`digest_timeout(input_chars) -> float`；`build_session_digest` 超时时抛 `TimeoutError`

- [ ] **Step 1: 写失败的测试**

追加到 `backend/tests/test_session_digest_agent.py` 末尾：

```python
def test_long_records_are_truncated():
    text = sd.format_records([{"id": 1, "user_text": "问" * 500, "model_text": "答" * 500}])
    assert "问" * sd.RECORD_USER_MAX_CHARS + "…" in text
    assert "答" * sd.RECORD_MODEL_MAX_CHARS + "…" in text


def test_oldest_records_are_dropped_when_input_is_too_long():
    """整体超长时丢最早的，保住最近的：最近发生的事对后续对话更有用"""
    many = [{"id": i, "user_text": f"第{i}问" + "x" * 190, "model_text": "y" * 290} for i in range(60)]
    text = sd.format_records(many)
    assert len(text) <= sd.DIGEST_INPUT_MAX_CHARS
    assert "第59问" in text and "第0问" not in text


def test_timeout_grows_with_input_and_is_capped():
    assert sd.digest_timeout(0) == sd.DIGEST_TIMEOUT_BASE
    assert sd.digest_timeout(1000) == sd.DIGEST_TIMEOUT_BASE + sd.DIGEST_TIMEOUT_PER_1K
    assert sd.digest_timeout(10 ** 6) == sd.DIGEST_TIMEOUT_MAX


async def test_slow_model_times_out(monkeypatch, caplog):
    """LLM 客户端本身没有超时，卡住的请求会一直挂着；摘要必须自己限时"""
    import asyncio
    import logging

    class SlowLLM:
        async def ainvoke(self, messages):
            await asyncio.sleep(1.0)
    _patch(monkeypatch, SlowLLM())
    monkeypatch.setattr(sd, "digest_timeout", lambda chars: 0.05)

    with caplog.at_level(logging.WARNING):
        try:
            await sd.build_session_digest(RECORDS)
            raise AssertionError("应当超时")
        except TimeoutError:
            pass
    assert "会话摘要超时" in caplog.text


async def test_success_logs_input_size_and_duration(monkeypatch, caplog):
    import logging
    _patch(monkeypatch, FakeLLM())
    with caplog.at_level(logging.INFO):
        await sd.build_session_digest(RECORDS)
    assert "会话摘要完成" in caplog.text and "耗时" in caplog.text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_session_digest_agent.py -v`
Expected: 5 failed（`AttributeError`：模块里还没有 `RECORD_USER_MAX_CHARS`、`digest_timeout` 等）

- [ ] **Step 3: 加上常量与截断**

在 `backend/agents/agent/session_digest_agent.py` 顶部补上 `import asyncio`、`import time`，并在 `load_env()` 之后加入：

```python
# 单条记录截断：摘要只需要知道问过什么、卡在哪，不需要完整的解题过程
RECORD_USER_MAX_CHARS = 200
RECORD_MODEL_MAX_CHARS = 300
# 一次摘要的输入上限：超过就从最早的记录开始丢，保住最近的
DIGEST_INPUT_MAX_CHARS = 6000

# 单次摘要的限时：基础 20 秒，每千字加 10 秒，最多 60 秒。
# LLM 客户端本身没有超时（httpx Timeout(None)），不设的话卡住的请求会一直挂着
DIGEST_TIMEOUT_BASE = 20.0
DIGEST_TIMEOUT_PER_1K = 10.0
DIGEST_TIMEOUT_MAX = 60.0
```

用下面的内容替换原来的 `format_records`（顺带加上 `_truncate` 与 `digest_timeout`）：

```python
def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def format_records(records: list[dict]) -> str:
    """
    把原文排成带序号的对话，模型按时序阅读。
    每条先截断，整体仍然超长时从最早的记录开始丢——最近发生的事对后续对话更有用。
    """
    blocks = [
        f"{index}. 用户：{_truncate(r['user_text'], RECORD_USER_MAX_CHARS)}\n"
        f"   助手：{_truncate(r['model_text'], RECORD_MODEL_MAX_CHARS)}"
        for index, r in enumerate(records, 1)
    ]
    while len(blocks) > 1 and sum(len(b) + 1 for b in blocks) > DIGEST_INPUT_MAX_CHARS:
        blocks.pop(0)
    return "\n".join(blocks)


def digest_timeout(input_chars: int) -> float:
    """按输入长度给单次摘要限时"""
    return min(DIGEST_TIMEOUT_BASE + input_chars / 1000 * DIGEST_TIMEOUT_PER_1K, DIGEST_TIMEOUT_MAX)
```

- [ ] **Step 4: 给调用加上限时**

`build_session_digest` 里，从 `system_body = load_skill(...)` 到 `return` 的部分替换为：

```python
    system_body = load_skill("session_digest")
    llm = build_session_digest_agent()
    payload = format_records(records)
    timeout = digest_timeout(len(payload))

    started = time.perf_counter()
    try:
        response = await asyncio.wait_for(
            llm.ainvoke([SystemMessage(content=system_body), HumanMessage(content=payload)]),
            timeout=timeout,
        )
    except TimeoutError:
        logger.warning("会话摘要超时：输入 %s 字，限时 %.0f 秒", len(payload), timeout)
        raise
    logger.info("会话摘要完成：输入 %s 字，耗时 %.1f 秒", len(payload), time.perf_counter() - started)

    content = response.content if isinstance(response.content, str) else ""
    return content.strip()
```

用 `asyncio.wait_for` 包住整个 `ainvoke`，客户端自己的重试也算在这次限时里；超时后 `wait_for` 会取消底层的 httpx 请求。

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_session_digest_agent.py -v`
Expected: 9 passed

- [ ] **Step 6: 提交**

```bash
git add backend/agents/agent/session_digest_agent.py backend/tests/test_session_digest_agent.py
git commit -m "feat: 会话要点生成加入输入截断与按长度限时"
```

- [ ] **Step 7: 上线后看数据**

服务跑一段时间后：

```bash
grep "会话摘要完成" logs/*.log | tail -20
grep "会话摘要超时" logs/*.log
```

几乎不超时、耗时稳定，就不用再动；超时集中在输入长的会话，把 `DIGEST_INPUT_MAX_CHARS` 调小；耗时普遍偏长，考虑把 `DIGEST_MODEL` 配成更便宜更快的模型。

---

## Task 16: 会话要点注入请求上下文

**这个任务解决什么问题**：`memory_digest` 里已经有要点了，但没有人读它。现在每次请求只把最近 3 轮原文拼进 `user_input`，超出窗口的内容对模型来说等于没发生过。

**怎么做**：`_format_memory_context` 多接一个参数，把要点放在最近对话之前；两个端点（`analyse` 与 `_stream_generator`）都从 `get_memory_for_planner` 的返回值里取。

**为什么要点在前、原文在后**：要点是压缩过的早期内容，原文是刚刚发生的对话。按时间顺序读，模型更容易把「以前怎么样」和「现在问什么」区分开。

**做完之后**：一个持续很久的会话，早期的关键信息不会因为窗口滚动而丢失。

**前后依赖**：用到 Task 12 的 `session_digest`。与 Task 13 的画像注入互不影响：画像进 system prompt（稳定背景），会话要点进 `user_input`（本次会话的经过）。

**实现时注意**：注入的内容会进入**每一轮** ReAct 的输入，长度直接换算成 token 成本。所以要点在 SKILL.md 里限定了 300 字以内，原文固定只取 3 轮。

**Files:**
- Modify: `backend/api/user_api/agent_api.py`
- Test: `backend/tests/test_memory_context.py`

**Interfaces:**
- Consumes: Task 12 的 `get_memory_for_planner()['session_digest']`
- Produces: `_format_memory_context(short_memories, session_digest="") -> str`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/test_memory_context.py`：

```python
"""注入 prompt 的上下文怎么拼：会话要点在前，最近几轮原文在后。"""
from backend.api.user_api.agent_api import _format_memory_context


def _unit(user_text: str, model_text: str) -> dict:
    return {"memory": {"user_memory": user_text, "model_memory": model_text}}


RECENT = [_unit("第三问", "第三答"), _unit("第二问", "第二答"), _unit("第一问", "第一答")]  # 新的在前


def test_nothing_to_inject():
    assert _format_memory_context([], "") == ""


def test_only_recent_dialogue():
    out = _format_memory_context(RECENT)
    assert "【本次会话要点】" not in out
    assert out.index("第一问") < out.index("第二问") < out.index("第三问"), "原文按从旧到新展示"


def test_digest_comes_before_recent_dialogue():
    out = _format_memory_context(RECENT, "学生在一元一次方程上容易弄错符号。")
    assert out.index("【本次会话要点】") < out.index("【近期对话记录】")
    assert "学生在一元一次方程上容易弄错符号。" in out


def test_only_digest():
    out = _format_memory_context([], "学生偏好不要与原题雷同。")
    assert out == "【本次会话要点】\n学生偏好不要与原题雷同。"


def test_at_most_three_rounds():
    many = [_unit(f"问{i}", f"答{i}") for i in range(10)]
    out = _format_memory_context(many)
    assert "问3" not in out and "问2" in out, "只取最近 3 轮，避免每次请求都把整段历史塞进去"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_memory_context.py -v`
Expected: 3 failed（`_format_memory_context() takes 1 positional argument but 2 were given`）

- [ ] **Step 3: 实现**

用下面的内容替换 `backend/api/user_api/agent_api.py` 里的 `_format_memory_context`：

```python
def _format_memory_context(short_memories: list, session_digest: str = "") -> str:
    """
    拼出注入 user_input 的上下文：先放本次会话的要点，再放最近 3 轮原文。
    要点是早期对话压缩来的，原文是刚刚发生的，两者都不经过 LLM，直接拼接。
    """
    lines: list[str] = []
    if session_digest:
        lines.append("【本次会话要点】")
        lines.append(session_digest)

    recent = short_memories[:3]
    if recent:
        if lines:
            lines.append("")
        lines.append("【近期对话记录】")
        for mem in reversed(recent):  # 从旧到新展示，保持时序
            user_mem = mem.get('memory', {}).get('user_memory', '')
            model_mem = mem.get('memory', {}).get('model_memory', '')
            if user_mem:
                lines.append(f"用户：{user_mem}")
            if model_mem:
                lines.append(f"助手：{model_mem}")
    return "\n".join(lines)
```

两个端点里的调用都改成把要点传进去：

```python
        memory_context = _format_memory_context(short_memories, memory_data.get('session_digest', ''))
```

`analyse` 里这一段有 8 个空格缩进，`_stream_generator` 里是 4 个，改的时候注意别把缩进带错。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/ -v`
Expected: 全部通过

- [ ] **Step 5: 端到端人工验证**

启动服务，用同一个 `user_id` 和 `session_id` 连续对话 12 轮以上（窗口是 10 条，第 11 轮起开始归档），然后查数据库：

```bash
SELECT COUNT(*) FROM memory_record WHERE user_id = ? AND session_id = ?;
SELECT summary, covered_until_id FROM memory_digest WHERE user_id = ? AND session_id = ?;
```

Expected: `memory_record` 里有归档的原文；攒够 5 条后 `memory_digest` 里出现要点。再发一次请求，日志里 `user_input` 的开头应当能看到「【本次会话要点】」。

- [ ] **Step 6: 提交**

```bash
git add backend/api/user_api/agent_api.py backend/tests/test_memory_context.py
git commit -m "feat: 请求上下文注入会话要点"
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
| 5 | 会话要点进入请求上下文 | `test_memory_context.py::test_digest_comes_before_recent_dialogue` |
| 5b | 原文只增不改，要点从原文重算 | `test_memory_manager.py::test_digest_is_rebuilt_from_raw_records` |
| 5c | 归档重试不产生重复记录 | `test_memory_manager.py::test_same_item_archived_twice_writes_one_row` |
| 6 | 偏好二次命中才写画像 | `test_profile_candidates.py::test_second_offer_promotes` |
| 7 | 新知识点不冲掉已有 | `test_profile_merge.py::test_old_keys_are_never_dropped` |
| 7b | notes 追加而非覆盖，且限长 | `test_profile_merge.py::test_notes_are_appended_not_replaced`、`::test_notes_are_capped_dropping_oldest` |
| 7c | 词表外的键转存 notes，不静默失效 | `test_profile_schema_skill.py::test_unknown_key_is_routed_to_notes` |
| 7d | notes 不进 system prompt | `test_react_agent_async.py::test_format_profile_excludes_notes` |
| 8 | 事件循环无同步 LLM 调用 | `test_react_agent_async.py::test_react_think_node_is_coroutine_function` |
| 9 | 摘要调用不会无限挂起，失败也不影响已落库的原文 | `test_session_digest_agent.py::test_slow_model_times_out`、`test_memory_manager.py::test_digest_failure_does_not_lose_records` |

全量跑一遍：

```bash
cd backend && python -m pytest tests/ -v
```
