# 学生学情分析系统

基于 LangGraph ReAct 架构的 AI 智能辅导后端。学生提交题目或自然语言请求后，由 ReAct 决策 Agent 路由到合适的工具（知识点提取、变式题生成、通用问答、记忆查询、用户画像）；配合三级记忆体系（Redis 短期 + MySQL 长期 + Chroma 向量）维持对话上下文。

所有代码位于 `backend/` 目录，仓库中不包含前端实现。

---

## 功能特性

- **ReAct 决策调度**：单一 LLM 通过「思考 → 行动 → 观察」循环决定调用哪个工具，最多 5 轮
- **可插拔 Skill 能力包**：`backend/agents/skills/` 下的 `SKILL.md` 文件定义剧本化指引，教研人员可热编辑无需重启
- **可执行校验片段**：`SKILL.md` 中内嵌 `skill:validator` 代码块，由 SkillRunner 提取执行，实现规则与校验同源
- **三级记忆体系**：Redis 短期（24h TTL，上限 10 条）、MySQL 长期用户画像、Chroma 向量存档
- **记忆溢出归档**：短期记忆超限时最旧记录经 LLM 精炼后写入向量库
- **流式 SSE 响应**：`/agent/analyse/stream` 支持 `thinking` / `observation` / `skill_loaded` / `result` 四种事件
- **统一日志**：RotatingFileHandler 5MB × 3 本地轮转，HTTP 中间件按请求记录耗时

---

## 技术栈

| 领域 | 选型 |
|---|---|
| Web 框架 | FastAPI + Uvicorn + Starlette |
| Agent 框架 | LangChain + LangGraph |
| LLM 服务商 | 阿里云 DashScope（OpenAI 兼容端点） |
| 数据库 | MySQL（asyncmy）、Redis、Chroma |
| 向量索引 | LlamaIndex |
| 鉴权 | JWT（PyJWT）、bcrypt |
| 日志 | 标准库 logging + RotatingFileHandler |

---

## 目录结构

```
backend/
├── main.py                 # FastAPI 入口，注册中间件与路由
├── agents/
│   ├── agent/              # 各 Agent 实现（react_agent 为当前主调度）
│   ├── tools/              # BaseTool 子类（function calling）
│   ├── skills/             # SKILL.md 能力包 + loader + skill_runner
│   └── memory/             # 短期 / 长期 / 向量三级记忆
├── api/
│   ├── dependencies.py     # JWT 鉴权依赖
│   └── user_api/           # agent_api、login_api 路由
├── core/
│   ├── hooks.py            # 启停钩子（建表、关连接）
│   └── single_tool.py      # singleMeta + singleton_method
├── dao/                    # 数据访问层
├── model/                  # SQLAlchemy ORM 定义
├── schemas/                # Pydantic 输入输出
├── middleware/
│   ├── logging.py          # 日志配置 + HTTP 请求中间件
│   ├── exception.py
│   └── database.py
├── utils/                  # redis_client 等工具
└── logs/                   # 运行时日志目录（gitignore）
```
---

## 主要功能亮点

### 1. 全链路异步 I/O
数据库（SQLAlchemy async + asyncmy）、Redis、LLM 调用、向量库读写一律 `async / await`。`vector_store_manager` 中第三方同步 API（LlamaIndex 的 `insert` / `delete`）通过 `loop.run_in_executor` 下沉到线程池，保证事件循环不被阻塞。发现并修复了 `extract_memory_agent` 中 `async def` 内误用 `chain.invoke()` 的阻塞 bug。

### 2. 双层单例 + LRU+TTL 缓存淘汰
`backend/core/single_tool.py` 提供两套复用策略：
- **`singleMeta` 元类**：每个使用该元类的类持有独立锁（`_locks` 字典），单例在进程生命周期内常驻，适用于 `MemoryManager`、`VectorStoreManager` 等基础设施类
- **`@singleton_method` 装饰器**：按调用参数缓存返回值，内置 `_LRUTTLCache` 支持 **maxsize=32 LRU 淘汰 + ttl=3600s 过期**，用于 `get_llm(model, streaming)` 这类按参数分组的多实例场景

缓存清理采用 **惰性删除 + 后台守护线程** 双机制，守护线程三阶段持锁（快照 → 无锁计算 → 逐个 O(1) 删除 + `sleep(0)` 主动让出 GIL），将主线程被阻塞的时间压到最小。使用双重检查锁（Double-Checked Locking）避免并发首次创建竞态。

### 3. ReAct 决策 + function calling 混合架构
单一 LLM 通过 `bind_tools(TOOLS)` 感知所有可用工具，每轮输出严格 JSON `{thought, action, action_args, final_result}`。`should_continue` 条件边根据 `action` 是否为空 + 轮次上限决定是否循环，最多 5 轮自动终止，防止 LLM 陷入死循环。

### 4. Tools / Skills 双轨设计
- **Tools**：编译期注册的 function calling 工具（7 个业务工具 + 1 个 `load_skill_tool`）
- **Skills**：运行期从 `SKILL.md` 动态加载的剧本化知识包，支持 mtime 缓存的热编辑（教研改文件 → 下次请求立即生效，无需重启）
- **可执行 Validator**：`SKILL.md` 中的 ```skill:validator``` 代码块由 `skill_runner.run_validator()` 提取并 exec，实现「规则描述」与「校验逻辑」单一事实源

### 5. 三级记忆 + 自动溢出归档
Redis 短期记忆达到 10 条上限时，最旧记录经 `memory_refinement` Skill 转为第三人称摘要，带 tags 写入 Chroma 向量库，再从 Redis 淘汰。整个流程对上层透明，由 `MemoryManager.add_memory()` 统一编排，含向量写入失败的 **自动重试 + 0.5s 退避**。

### 6. SSE 流式响应 + 类型化事件
`/agent/analyse/stream` 基于 LangGraph `app.astream(state, stream_mode="updates")` 逐节点推送事件。通过识别 `ToolMessage.tool_call_id` 前缀，将 `load_skill_tool_*` 结果单独标记为 `skill_loaded` 事件类型，前端可区分「正在加载能力包」与「普通工具观察」两种状态。

### 7. 统一日志 + HTTP 中间件
标准库 `logging` + `RotatingFileHandler`（5MB × 3）本地轮转，无额外依赖。`LoggingMiddleware` 基于 `starlette.BaseHTTPMiddleware`，用 `time.perf_counter()` 记录每个请求的 `method / path / status / 耗时(ms)`。第三方库（httpcore / httpx / uvicorn.access）级别统一降为 WARNING 避免日志噪音。

### 8. JWT + Depends 鉴权
通过 FastAPI `Depends(get_current_user)` 注入用户上下文，token 解码失败直接抛 `HTTPException`，业务代码无需重复写鉴权逻辑。密码存储使用 bcrypt 哈希，不存明文。

---

## 架构：Tools vs Skills

项目明确区分两种概念：

| 维度 | Tools（工具） | Skills（能力包） |
|---|---|---|
| 形态 | LangChain `BaseTool` 子类 | 磁盘上的 `SKILL.md` 文件 |
| 性质 | 可执行的 function calling | 声明式剧本（prompt + 可选校验代码） |
| 注册 | `agents/tools/__init__.py` 的 `TOOLS` 列表 | 文件夹 + frontmatter 自动发现 |
| 更新 | 改代码 → 重启 | 改文件 → 下次请求生效（mtime 缓存） |

ReAct Agent 同时感知两者：LLM 若识别到用户诉求命中某 Skill 触发词，先调用 `load_skill_tool` 拉取剧本注入上下文，再决定下一步业务工具调用。

---

## 请求流程

```
POST /agent/analyse
  ↓ JWT 鉴权
  ↓ 取最近 3 条短期记忆 → 拼接为上下文前缀
  ↓ 构造 GraphState，进入 ReAct 图
      react_think_node  ←─────────────────┐
          ↓ LLM 输出 {thought, action, args, final_result}
      should_continue                      │
          ↓ action != "" 且 round < 5      │
      tool_exec_node  ─────────────────────┘
          ↓ action == ""
         END
  ↓ 写入短期记忆（原始 text，不含记忆上下文）
  → 返回 final_result
```

---

## API 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/agent/analyse` | 同步调用 ReAct 图，一次性返回完整 state |
| POST | `/agent/analyse/stream` | SSE 流式推送思考/观察/结果事件 |
| POST | `/login/login` | 账号密码登录，返回 JWT |
| POST | `/login/register` | 注册新用户 |

所有 `/agent/*` 接口要求在 `Authorization: Bearer <token>` 头中携带有效 JWT。

**SSE 事件类型**：
- `thinking`：LLM 决定调用哪个 tool
- `observation`：普通 tool 执行结果
- `skill_loaded`：加载 Skill 剧本（`load_skill_tool` 的执行结果）
- `result`：ReAct 最终回答

---

## 记忆系统

| 层级 | 存储 | 内容 | 生命周期 |
|---|---|---|---|
| 短期 | Redis hash `user:{id}:session:{id}` | 原始 `{user_memory, model_memory}` | 24h TTL，最多 10 条 |
| 长期 | MySQL `user_profile` 表 | 年级、学科、薄弱点、偏好 | 每个用户持久化 |
| 向量 | Chroma（`backend/chroma_db/`） | LLM 精炼后的带 tags 摘要 | 短期溢出时追加 |

短期记忆达到上限时，最旧 1 条通过 `extract_memory_agent`（走 `memory_refinement` Skill）转为第三人称摘要，写入 Chroma，再从 Redis 删除。

---

## 环境准备

### 1. 安装依赖

```bash
cd backend
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置环境变量

在 `backend/.env` 中填写：

```dotenv
API_KEY=                 # DashScope API Key
MODEL_NAME=qwen-plus     # 默认 LLM
API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
PLANNER_MODEL=           # 意图路由模型（可选，留空走默认）
EXTRACT_MODEL=           # 知识点提取模型（可选）
EMBEDDING_MODEL=text-embedding-v4

SQL_DATABASE_URL=mysql+asyncmy://user:pass@host:3306/dbname

REDIS_HOST=
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_USERNAME=
```

启动钩子（`backend/core/hooks.py`）会在首次运行时自动创建所有 MySQL 表。

### 3. 启动服务

```bash
cd backend
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

或直接：

```bash
cd backend
python main.py
```

默认监听 `http://127.0.0.1:8000`，OpenAPI 文档位于 `/api`。

---

## 开发约定

- **全链路异步**：DB、Redis、LLM 调用一律 async，新增代码保持一致
- **新增 Tool**：在 `agents/tools/` 新建 `BaseTool` 子类，加入 `agents/tools/__init__.py` 的 `TOOLS` 列表即可
- **新增 Skill**：在 `agents/skills/<name>/SKILL.md` 写好 frontmatter（name/description/triggers/version）与正文，loader 会自动列出
- **新增 Validator**：在 SKILL.md 中追加 ```` ```skill:validator ```` 代码块，内含 `def validate(result: str) -> tuple[bool, str]`
- **日志**：统一 `from backend.middleware.logging import get_logger; logger = get_logger(__name__)`，不使用 `print()`
- **记忆写入**：必须使用原始 `text`，不要写入拼接了历史上下文的 `user_input`，避免跨 session 污染
- **废弃模块**：`agents/agent/graph_build.py` 为旧线性架构，已被 `react_agent.py` 完全替代，不要引用

---

## 日志

- 输出位置：`backend/logs/app.log`
- 轮转策略：单文件 5MB，保留 3 个历史文件
- 请求日志格式：`2024-01-01 12:00:00 | INFO | middleware.logging | POST /agent/analyse 200 153ms`
- 调整级别：修改 `main.py` 中 `setup_logging(level="DEBUG")` 可打印 ReAct 思考链等详细信息
