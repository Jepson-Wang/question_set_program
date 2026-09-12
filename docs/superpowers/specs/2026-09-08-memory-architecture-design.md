# 三层记忆架构改造设计

日期：2026-09-08
状态：已确认，待实施
范围：`backend/agents/memory/`、`backend/agents/tools/`、`backend/agents/agent/react_agent.py`、`backend/core/hooks.py`

---

## 背景与问题

现有三层记忆（Redis 短期 / MySQL 画像 / Chroma 向量）已建成，但三条链路都存在缺口：

1. **向量层只写不读。** `VectorStoreManager.query()` 零调用方。溢出记忆精炼后存进 Chroma 就再没被碰过。
2. **画像查了不用。** `get_memory_for_planner()` 每轮请求都查一次 MySQL 画像，但 `agent_api.py` 只取 `short_memory`，`long_memory` 直接丢弃。画像只能靠 LLM 主动调 `user_profile_query_tool` 才进上下文。
3. **短期记忆存在无保护的 read-modify-write。** `memory_list` 是 Redis hash 里的一个 JSON 字符串字段，`add_memory` 读出整个列表、在 Python 里 insert、再整个写回。两个并发请求即使都不触发摘要也会丢消息（读-读-写-写）。LLM 摘要只是把竞态窗口从毫秒拉到几秒。

第 3 点是根因：问题不在"摘要期间要加锁"，而在于**窗口本身的写入不是原子的**。

## 关键决策

| 决策点 | 结论 | 理由 |
|---|---|---|
| 部署形态 | 单机单进程 | 互斥用进程内机制即可，不引 Redis 分布式锁 |
| 向量层读取时机 | 工具级定点注入 | 主循环不碰向量库，成本只在真要生成时付一次，也不干扰路由决策 |
| 画像写入判定 | 频次确认（候选池 + 二次命中） | 用统计而非单次 LLM 判断抓"稳定"，代价是偏好生效滞后一轮 |
| 画像 schema | 键名受控（词表在 SKILL.md）+ `notes` 逃生口 | 键名自由生成会让频次确认静默失效；词表放剧本层则加维度无需改表改码 |
| 并发方案 | 换数据结构消除竞态（方案 C） | 比"锁"和"双缓冲"都简单，且正确性更强 |

## 被否决的方案

- **单锁串行**：per-session `asyncio.Lock` 包住整个 `add_memory`。正确，但第二个请求要干等 2-5 秒的 LLM 摘要，用户可感知卡顿。
- **异步摘要 + 双缓冲**：方向对，但"两个窗口"要两个 key，而"原子切换"本身又是一次 RMW，仍需为切换加锁。锁没消失，只是换了位置，复杂度反而上升。

---

## 一、三层记忆接入策略

### 读路径

**短期记忆（必传，无条件）**
最近 3 轮，格式化后拼在 `user_input` 头部。行为不变，仅底层改为 `LRANGE key 0 2`。

**长期画像（条件注入，注入位置改变）**
不再丢弃 `get_memory_for_planner()` 查回来的画像。将其压成一段注入 **ReAct system prompt**，而不是拼进 `user_input`：

```
# 学生画像
年级：七年级 | 学科：数学
薄弱知识点：一元一次方程、行程应用题
长期偏好：题目不要雷同、偏好带解析
```

放 system prompt 而非 user_input 的理由：画像是"这个学生是谁"的稳定背景，不是"这次问了什么"。混进 user_input 会让模型把画像当成当前诉求的一部分。画像为 `None` 时整段不出现。成本固定在几十 token。

`user_profile_query_tool` 保留，供 Agent 需要完整画像时主动查。

**向量层（工具级定点注入）**
只在 `QuestionSetTool._arun` 和 `CommonTool._arun` 执行前检索一次，把命中结果拼进该工具自己的 system prompt。ReAct 主循环完全不碰向量库。

关键修正：**必须用 retriever，不能用 query engine。** 现有的 `VectorStoreManager.query()` 走 `as_query_engine().query()`，那是 RAG 问答，内部会额外调一次 LLM 合成答案。我们只要检索到的原文。新增：

```python
async def retrieve(self, query_text: str, user_id: int, top_k: int = 3,
                   min_score: float = 0.3) -> list[str]:
    """只做向量检索，返回命中的原文列表，不触发任何 LLM 调用。"""
```

`min_score` 阈值用来丢弃低相关命中——这正是"无关内容干扰模型"的直接防线。检索失败或无命中时返回空列表，工具照常执行（向量层是增强，不是依赖）。

### 写路径

**短期记忆**：每轮必写。存储结构改造见第二节。

**画像（频次确认）**

区分两类字段，走不同通道：

- **事实类（`grade`、`subject`）直写。** "我初二"是事实陈述，不存在"一次性 vs 稳定"的歧义，第一次就该落库。
- **偏好类（`weak_points`、`preferences`）走候选池。**

候选池：Redis hash `profile_cand:{user_id}`，TTL 7 天。

- field = 结构化字段路径，如 `preferences.题目风格`、`weak_points.一元一次方程`
- value = JSON `{"value": ..., "count": n, "first_seen": ts}`

`UserProfileSaveTool` 行为变更：不再直接写 MySQL，而是对候选池对应 field 的 count +1；**count 达到 2 时才真正落 MySQL 画像**，并从候选池删除该 field。

"同一语义偏好如何认定为同一个"这个难题在这里被绕开了：LLM 提取时已经把偏好结构化成了字段名，同键即同偏好，不需要语义相似度计算。

**键名必须受控，词表下沉到 SKILL.md**

上面那句"同键即同偏好"是整个频次确认的前提，因此键名不能由 LLM 自由生成。否则同一个偏好会在不同轮次里变成 `preferences.题目风格`、`preferences.出题风格`、`preferences.题目偏好`，计数永远到不了阈值，偏好永久丢失且不报错。第二个代价在读侧：画像每轮都拼进 system prompt，近义键会让它缓慢累积成噪音。

词表放在 `backend/agents/skills/profile_schema/SKILL.md` 的 `skill:vocab` 片段里，而不是代码或表结构里。`loader.py` 已有 mtime 缓存，编辑 Markdown 下一轮即生效——加一个画像维度不用改表、不用改代码、不用重启，与 `question_variant` / `memory_refinement` 复用同一套热加载机制。

**`notes` 逃生口**

受控词表必然覆盖不全。新增 `notes` JSON 列作为逃生口：纯自由文本数组，追加去重、上限 50 条，由 LLM 直接写一句话。它不参与频次确认（没有"一次性 vs 稳定"的歧义），也**不注入 system prompt**（否则会把每轮的画像段撑大），只在 `user_profile_query_tool` 被主动调用时返回。

词表外的键**不丢弃、也不入候选池**，而是自动转存 `notes`——入池会永远累积不到阈值，丢弃会丢信息。

这一组决策的效果，等价于把 schema 从数据库搬到了热加载剧本层：灵活性接近"完全无状态"，但不必付出候选池失效和画像糊化的代价。

返回给 Agent 的观察结果要如实反映状态，例如"已记录候选偏好（1/2），再次出现时将写入长期画像"，这样 Agent 不会误以为已经生效。

**向量层**：短期记忆溢出时写。见第二节。

### 一致性

用户既定策略是"画像只增不改、冲突时以新的为准"。当前实现有一处与之矛盾：

`UserProfileMapper.update_user_profile()` 对 `weak_points` / `preferences` 这两个 JSON 字段是**整体替换**。Agent 传入 `{"函数": "薄弱"}` 会把之前存的所有其他知识点冲掉。必须改成 **dict 浅合并**（同 key 以新值覆盖，其余 key 保留），才符合"只增不改"。

---

## 二、短期记忆 LIST 化与后台归档

### 存储结构变更

| | 旧 | 新 |
|---|---|---|
| 短期窗口 | `user:{u}:session:{s}` hash，field `memory_list` = JSON 数组字符串 | `stm:{u}:{s}` Redis LIST，每元素一条 MemoryUnit JSON，index 0 最新 |
| 待归档队列 | 无 | `stm:pending:{u}:{s}` Redis LIST |
| TTL | 86400s | 窗口 86400s，pending 604800s（7 天，给恢复留余量） |

换 key 前缀，旧数据靠 24h TTL 自然过期，**不写迁移脚本**。

### 写入：一次 Lua EVAL 完成"入队 + 淘汰"

```lua
-- KEYS[1]=窗口 key  KEYS[2]=pending key
-- ARGV[1]=新记忆 JSON  ARGV[2]=最大条数  ARGV[3]=窗口 TTL  ARGV[4]=pending TTL
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
```

- 整段在 Redis 服务端原子执行，RMW 竞态从根上消失，`add_memory` 不需要任何锁。
- 用 `RPOPLPUSH` 而非 `LMOVE`：语义等价，但兼容 Redis 6.2 以下版本，省掉版本探测和降级分支。
- `while` 而非 `if`：一次调用可能需要淘汰多条（例如 max_size 被调小之后）。
- 通过 `client.register_script(LUA)` 注册，redis-py 会自动走 EVALSHA 并在 NOSCRIPT 时回退 EVAL。

`ShortTermMemory.add_memory()` 签名变更：返回 `list[str]`，即本次被挤出窗口、已搬进 pending 队列的原始 JSON 字符串。

### 归档：后台任务

`MemoryManager.add_memory()` 简化为：

```python
evicted = await self.short_term_memory.add_memory(user_id, session_id, memory)
if evicted:
    self._spawn_archive(user_id, session_id, evicted)
```

请求路径到此结束，**不等待摘要**，用户无感知延迟。

`_spawn_archive` 用 `asyncio.create_task`，必须把 task 存进实例上的 `set` 并用 `add_done_callback(discard)` 清理——否则 task 只被事件循环弱引用，可能在完成前被 GC 掉。

`_archive(user_id, session_id, raw_items)` 步骤：

1. 逐条 `json.loads`，跳过解析失败的（记 error）
2. `await get_extract_memory(units)` —— LLM 精炼，几秒
3. 逐条 `await vector_memory.add_document(text, metadata)`
4. **仅在第 3 步成功后**，`LREM pending_key 1 <原始JSON字符串>` 删除对应条目
5. 任何一步失败：条目留在 pending，记 error，等启动恢复补做

第 4 步用原始 JSON 字符串精确匹配，保证幂等——重复归档最多产生一条重复向量，不会丢数据。

### 启动恢复与优雅关停

**启动**（`core/hooks.py:startup_event`）：用 `SCAN`（不是 `KEYS`）遍历 `stm:pending:*`，对每个非空队列跑一次 `_archive`，补做进程崩溃时"已弹出、未入库"的记忆。单机单进程且数据量小，全量 SCAN 可接受。

**关停**（`shutdown_event`）：`asyncio.gather(*in_flight_tasks)` 带超时（建议 10s），把在途归档做完；超时未完成的留在 pending，下次启动补。顺序上必须在 `engine.dispose()` 和 `close_redis()` **之前**。

### 为什么不再需要锁

对照原始的两个担忧：

- "正在被摘要的消息又被读到" —— 不可能。被归档的条目已被 `RPOPLPUSH` 移出窗口，读路径 `LRANGE stm:{u}:{s} 0 n` 物理上看不到它。
- "新消息插进了正在被裁剪的区间" —— 不可能。新消息 `LPUSH` 到 head，淘汰从 tail 弹出，且两者在同一次原子 EVAL 内完成，不存在中间态。

额外收益：修掉了原方案（锁 + 双缓冲）都没覆盖的"不触发摘要时的并发丢消息"。

---

## 三、全链路异步的阻塞点

现状：DB、Redis、LLM 已异步；向量库通过 `run_in_executor` 投递线程池。剩余问题：

**1. `react_think_node` 是同步函数**（`react_agent.py`）
它内部调 `llm.invoke()`。LangGraph 对同步节点的处理是丢进默认线程池执行——而向量库的 `run_in_executor(None, ...)` 用的是**同一个默认池**（`min(32, cpu+4)` 个线程）。ReAct 主循环和向量检索互相抢线程。

改法：`async def react_think_node` + `await llm.ainvoke(...)`。LLM 调用本来就是纯 IO，根本不需要线程。这是收益最大的一处。

**2. 向量库应使用专属有界线程池**
把 `run_in_executor(None, ...)` 改为 `run_in_executor(self._executor, ...)`，其中 `self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="vec")`，由 `VectorStoreManager` 持有，关停时 `shutdown(wait=True)`。

理由：默认池是全局共享资源，被 LangGraph 同步节点、`asyncio.to_thread` 等共用。一次 embedding 风暴会把默认池占满，饿死其他所有阻塞调用。有界专属池把影响限制在向量层内部。

**3. 删除已死的同步 agent 路径**
`common_tool()`、`extract_tool()`、`question_set_tool()` 这三个同步函数只被各自 Tool 的 `_run` 调用，而 `tool_exec_node` 只 `await tool._arun(...)`，`_run` 在 ReAct 图里永远不会执行。

改法：让这些 `_run` 统一 `raise NotImplementedError`（与 `QueryMemoryTool`、`UserProfile*Tool` 保持一致），删掉三个同步 agent 函数。减少一半维护面，也消除"同步版本悄悄被调用从而阻塞事件循环"的风险。

**4. 环境变量加载不可靠（范围比初稿大）**
项目里有 11 处 `load_dotenv`，三种写法，没有一种可靠：

- `load_dotenv()` 不传参（`model/__init__.py`、`agents/agent/tools.py`、三个 `*_agent.py` 等）——从**当前工作目录**逐级上找，只在 cwd 恰好是 `backend/` 时成立
- `load_dotenv('.env')`（`vector_store_manager.py`、`extract_memory_agent.py`）——相对 cwd，更脆
- **完全不加载**（`get_llm.py`、`utils/redis_client.py`）——靠"别的模块碰巧先被导入"

失败方式很隐蔽：`os.getenv` 静默返回 `None`，然后以一个和根因无关的面目炸掉。实测：从仓库根目录 import `redis_client` 并 ping，报的是 `AuthenticationError: Authentication required`——真实原因却是 `.env` 没被加载，密码读成了 `None`。

改法：新建 `backend/core/config.py` 作为单一入口，用**绝对路径**（`Path(__file__).resolve().parents[1] / ".env"`）幂等加载，所有读 env 的模块统一调 `load_env()`。该模块只依赖 `pathlib` 和 `dotenv`，不 import 任何项目模块，因此不会成环。

`load_dotenv` 默认不覆盖已存在的真实环境变量，这个行为要保留——容器/CI 注入的配置应当优先于 `.env` 文件。

**不在本次范围**：`DashScopeEmbedding` 内部用同步 `OpenAI` client。它在线程池里跑，属于"线程内的同步 HTTP"，不阻塞事件循环。改用 LlamaIndex 的 `ainsert` 需要确认版本 API，收益有限，标记为后续可选优化。

---

## 验收标准

1. 并发向同一 session 发 20 个请求，窗口内条数正确、无消息丢失（现状会丢）。
2. 触发溢出的请求，端到端响应时间与不触发溢出的请求处于同一量级（现状会多等一次 LLM 摘要）。
3. 归档任务执行期间发起的读请求，不会读到正在被归档的记忆。
4. 在 `_archive` 第 3 步与第 4 步之间强制中断进程，重启后该记忆被成功归档且不重复。
5. 生题请求命中历史偏好时，向量层内容进入 `question_set_tool` 的 system prompt；无命中时工具正常执行。
6. 同一偏好首次出现只进候选池，第二次出现才写入 MySQL 画像。
7. 写入 `weak_points` 中的新知识点，不会冲掉已有的其他知识点。
8. 事件循环中不存在同步 LLM 调用（`react_think_node` 为 async）。
8b. 在任意工作目录下导入任意模块，环境变量都能正确读到（不再依赖 cwd 或导入顺序）。
9. 词表外的画像键被转存到 `notes`，既不进候选池也不被丢弃。
10. `notes` 追加写入、去重、超过 50 条时丢弃最旧的，且不出现在 system prompt 中。
