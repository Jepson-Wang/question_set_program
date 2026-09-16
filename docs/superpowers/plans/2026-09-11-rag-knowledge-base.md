# RAG 知识库接入 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为生成变式题建立题库与教材知识库：PDF / Word 与 LLM 生成的资料经第三方模型复核后入库；生题前经多路召回、RRF 融合、重排得到参考材料交给模型决策；用离线评测量化每项检索技术的收益。

**Architecture:** 向量操作统一经 LlamaIndex：向量化用 LlamaIndex 的 `BaseEmbedding`，向量库经 LlamaIndex 的 Chroma 集成读写两个 collection（题目一题一文档，教材按段切块）；进程内的词法索引同时承担 BM25、知识点倒排与题目目录，重建时整体替换快照。入库流水线由命令行与后台接口共用，去重在复核之前、复核失败即拒。检索三路并行、逐级降级，任何环节失败都不影响生题。

**Tech Stack:** Python 3.14 / FastAPI / LangChain（ChatOpenAI 对接 DashScope）/ LlamaIndex 0.14.18（llama-index-core + llama-index-vector-stores-chroma 0.5.5）/ chromadb 1.5.5 / jieba 0.42.1 / rank_bm25 0.2.2 / pypdf 6.18.0 / python-docx 1.2.0 / httpx / SQLAlchemy async / pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-09-11-rag-knowledge-base-design.md`

## 依赖清单

开工前先过一遍这张清单。版本号是编写本计划时在 Python 3.14.3 上实测通过的版本，「状态」是当时 `backend/.venv` 里的实际情况。

**需要新装**（Task 0 Step 2）

| 包（pip 名） | import 名 | 版本 | 用途 | 首次用到 |
|---|---|---|---|---|
| jieba | `jieba` | 0.42.1 | 中文分词，BM25 用它的搜索引擎模式 `cut_for_search` | Task 3 |
| rank_bm25 | `rank_bm25` | 0.2.2 | BM25 打分 | Task 3 |
| pypdf | `pypdf` | 6.18.0 | 抽取 PDF 文本 | Task 5 |
| python-docx | `docx` | 1.2.0 | 读取 Word（.docx） | Task 5 |
| lxml | `lxml` | 6.1.3 | python-docx 的依赖，随它自动安装；也要写进 requirements.txt | Task 5 |

**已安装，但缺二进制文件，必须修复**（Task 0 Step 1）

安装记录（`RECORD`）里登记了、磁盘上却不存在的编译扩展，导致 `import chromadb` 失败。这不是版本不兼容：PyPI 上这三个版本都有 Python 3.14 的 Windows 二进制包，补回文件后全部 RAG 测试通过。

| 包 | 版本 | 缺失的文件 | 作用 |
|---|---|---|---|
| grpcio | 1.78.0 | `grpc/_cython/cygrpc.cp314-win_amd64.pyd` | chromadb 导入时要用 |
| chromadb | 1.5.5 | `chromadb_rust_bindings/chromadb_rust_bindings.pyd` | 向量库本体（本地持久化） |
| onnxruntime | 1.24.4 | `onnxruntime/capi/onnxruntime_pybind11_state.pyd` | chromadb 默认 embedding 函数的依赖。本计划自己算向量、用不到它，但一起修好，免得以后踩到 |

**已安装，直接可用**

| 包 | 版本 | 用途 | 首次用到 |
|---|---|---|---|
| llama-index-core | 0.14.18 | `TextNode`、`VectorStoreQuery`、`BaseEmbedding`：向量操作的统一接口 | Task 2 |
| llama-index-vector-stores-chroma | 0.5.5 | LlamaIndex 的 Chroma 集成 | Task 2 |
| openai | 2.26.0 | `get_llm.py` 里的 `DashScopeEmbedding` 通过它调用向量化接口 | Task 2 |
| pydantic | 2.12.5 | 领域模型 | Task 1 |
| SQLAlchemy / aiosqlite | 2.0.48 / 0.22.1 | 入库任务表、隔离区表；测试里用 SQLite 代替 MySQL | Task 4 |
| langchain-openai / langchain-core | 1.1.10 / 1.2.17 | `ChatOpenAI`：结构化、种子题生成、裁判 | Task 6 |
| httpx | 0.28.1 | 调用重排接口；测试里用 `MockTransport` 做替身 | Task 0 |
| fastapi / python-multipart | 0.135.1 / 0.0.20 | 管理接口；`UploadFile` 需要 python-multipart | Task 17 |
| numpy | 2.4.3 | rank_bm25 的依赖 | Task 3 |
| pytest / pytest-asyncio | 9.0.2 / 1.3.0 | 测试 | Task 0 |

**用不到，不要装**

| 包 | 为什么不用 |
|---|---|
| llama-index-retrievers-bm25 | 基于 bm25s，默认按英文分词、取词干；中文要另接分词器。本计划的词法索引还兼做知识点倒排和题目目录，所以保持 jieba + rank_bm25 |
| llama-index-postprocessor-dashscope-rerank | 会引入 dashscope SDK。重排直接用 httpx 调接口，失败降级与响应解析都有测试覆盖 |
| llama-index-embeddings-dashscope | `get_llm.py` 已有自定义的 `DashScopeEmbedding`（绕过了模型名的枚举校验），直接复用 |
| llama-index-readers-file | 依赖很多；PDF、Word 直接用 pypdf、python-docx |
| torch、sentence-transformers、FlagEmbedding | 向量化和重排都走 DashScope 接口，不在本地跑模型 |
| ragas | 评测指标（召回率、MRR、成对胜率）自己算，代码量很小 |
| reportlab | 测试用的 PDF 是手写字节生成的 |

LlamaIndex 的 `VectorStoreIndex`、`IngestionPipeline`、`QueryFusionRetriever` 都在 llama-index-core 里，不用另装；但本计划不用它们，理由见 Task 2 开头。

一次装齐（逐步操作与核对见 Task 0 Step 1-2）：

```bash
cd backend
pip install --force-reinstall --no-deps --no-cache-dir grpcio==1.78.0 chromadb==1.5.5 onnxruntime==1.24.4
pip install jieba==0.42.1 rank_bm25==0.2.2 pypdf==6.18.0 python-docx==1.2.0
python -c "import chromadb, llama_index.vector_stores.chroma, jieba, rank_bm25, pypdf, docx; print('OK')"
```

## 这份计划里的代码是跑通过的

写这份计划之前，所有代码都在仓库的一个临时副本里完整实现并运行过，文档里的代码块直接从那份副本导出，没有手抄：

- 补回缺失的二进制文件、用真实的 LlamaIndex + Chroma 运行：**218 passed**
- 当前环境（chromadb 缺二进制文件，无法导入）：**191 passed，10 skipped**。跳过的全部依赖 chromadb（3 个接口测试文件整体跳过，另有 7 条单独跳过），并给出明确原因
- 向量存储改为 LlamaIndex 版本时，LlamaIndex 与 Chroma 的每一处特殊行为都先在真实 Chroma 上实测，再写进适配层；适配层的每一道防护都做过变异验证——去掉任何一道，都恰好有一条测试失败

验证过程中发现、并已在本计划中修正的问题（spec 已同步）：

1. **BM25 改用 jieba 的搜索引擎模式**。普通分词下「分解因式」与「因式分解」一个共同 token 都没有，同一知识点的不同说法互相召回不到
2. **防照抄阈值从 0.8 提到 0.9**。实测同题型的正常变式题与参考题相似度约 0.85，0.8 会误伤
3. **`pytest.importorskip` 必须传 `exc_type=ImportError`**。chromadb「找得到但导入报错」时，pytest 9.1 起默认会把跳过改成报错
4. **RAG 的 skill 需要 `visibility: internal`**。否则会出现在 ReAct 主提示词的 Skill 清单里，诱导 Agent 在处理用户请求时去加载
5. **上传文件要单独传原始文件名**。落盘名是任务 id，不传的话题目来源里记的是一串 id，无法追溯
6. **后台接口的测试依赖 chromadb 可导入**。`backend/api/__init__.py` 会连带导入 agent_api，这是项目原有结构，相关测试已用 `importorskip` 守住
7. **chromadb 导入失败的原因是二进制文件缺失，不是版本不兼容**。重装三个包即可，不用换 Python 版本（见依赖清单）
8. **LlamaIndex 返回的分数不是余弦相似度**。它的 Chroma 集成返回 exp(-距离)：余弦 0.6 会变成 0.67，余弦 0 会变成 0.37；去重、防泄漏、教材阈值都按余弦定义，适配层要换算回来
9. **LlamaIndex 的 add 不是 upsert**。对已有 id 再写一次会被 Chroma 静默忽略，改了内容也不生效；同一批里 id 重复直接报错；`delete_nodes([])` 也报错
10. **LlamaIndex 的异步方法会阻塞事件循环**。`async_add`、`aquery` 只是直接调用同步方法，仍然要投递到线程池
11. **LlamaIndex 的批量向量化没有并发上限**。`aget_text_embedding_batch` 会把所有批次一起发出去，一份 200 段的教材就是 200 个并发请求；`LlamaIndexEmbedder` 改为逐批等待

## Global Constraints

- 单机单进程；互斥与后台任务一律用进程内机制。
- 所有 I/O 保持 async；同步阻塞或 CPU 密集的调用（PDF 解析、BM25 检索与重建、LlamaIndex 向量库操作、写上传文件）投递到 `backend.core.executors.get_vector_executor()`，不要用 `run_in_executor(None, ...)`。
- 所有目录相对 `backend/` 解析，不依赖当前工作目录：`RAG_DB_DIR` 默认 `rag_db`，`RAG_UPLOAD_DIR` 默认 `rag_uploads`，`RAG_EVAL_DIR` 默认 `rag_eval`。
- 向量操作统一经 LlamaIndex：向量化用 `BaseEmbedding`，读写向量库用 `TextNode` 与 `VectorStoreQuery`。只有 `from_chroma` 创建 client 与 collection 时直接用 chromadb（要指定 cosine 空间）。
- 向量一律归一化为单位长度；Chroma collection 使用 cosine 空间。LlamaIndex 返回的分数是 exp(-距离)，由适配层换算回余弦相似度，**RAG 这一层**的业务代码拿到的一律是余弦。（记忆层是另一套：`VectorStoreManager` 建 collection 时没指定距离函数，用的是 chromadb 默认的 l2，`min_score` 卡的是 `exp(-平方欧氏距离)`，详见记忆计划 Task 11。两层的阈值不能直接互相套用。）
- 题目 id = `q_` + 规范化题干 sha1 的前 16 位；教材段 id = `k_` + 规范化文本 sha1 的前 16 位。
- 检索参数：每路召回 20，融合后 30，最终 3，教材 2；RRF k = 60；重排分数阈值 0.3；教材相似度阈值 0.5。
- 近似去重与防泄漏阈值：余弦相似度 0.95。
- 复核：通过分 0.8（`RAG_JUDGE_PASS_SCORE`），并发 4；四项布尔检查必须是真正的 `true`；失败即拒。
- 裁判模型必须与 `MODEL_NAME` 不同家族。
- 上传大小上限 20MB，只接受 `.pdf`、`.docx`，落盘文件名 = 任务 id + 后缀。
- 参考材料注入 prompt 的总长度上限 1200 字；照抄判定阈值 0.9。
- RAG 的 prompt 放在 `backend/agents/skills/<name>/SKILL.md`，frontmatter 必须带 `visibility: internal`。
- 依赖 chromadb 的测试用 `pytest.importorskip("chromadb", exc_type=ImportError)` 守住。
- 提交信息用中文，遵循 `feat:` / `fix:` / `refactor:` / `test:` / `docs:` 前缀。

## 前置依赖

本计划依赖记忆计划（`docs/superpowers/plans/2026-09-08-memory-architecture.md`）中的三个任务，开始对应任务前必须先完成：

| 记忆计划任务 | 提供的东西 | 本计划中最早用到的任务 |
|---|---|---|
| Task 3 | `backend.core.executors.get_vector_executor()` | Task 2 |
| Task 7 | 带记忆归档恢复与关停等待的 `core/hooks.py` | Task 16 |
| Task 12 | `question_set_tool._arun` 接收 `user_id`，并注入记忆召回 `recall` | Task 16 |

## 任务顺序与阶段

| 阶段 | 任务 | 完成后能做什么 |
|---|---|---|
| 零：准备 | Task 0 | 依赖就绪，外部接口已用真实请求确认 |
| 一：数据层 | Task 1-4 | 领域模型、向量存储、词法索引、任务与隔离区表 |
| 二：入库 | Task 5-11 | 用命令行把 PDF / Word 和生成的种子题灌进库 |
| 三：检索 | Task 12-15 | 给定一道题，拿到重排后的参考题与教材段 |
| 四：接入生题 | Task 16 | 生题请求真正用上 RAG |
| 五：后台接口 | Task 17-19 | 管理员上传资料、查看进度、处理隔离区 |
| 六：离线评测 | Task 20-22 | 检索消融报告与生题 A/B 胜率 |
| — | Task 23 | 更新 CLAUDE.md |

阶段四到六彼此独立，可以按需调整顺序，但都依赖阶段一到三。

## 测试说明

- 全部 RAG 测试放在 `backend/tests/rag/`。共享替身在 `helpers.py`，fixture 在 `conftest.py`，这两个文件随任务**逐段追加**，每段都自带所需的 import。
- 全量 RAG 测试首次约 70 秒（jieba 要构建词典缓存），之后约 30 秒，主要花在每个词法索引实例加载 jieba 词典上。
- 测试全部离线：LLM、embedding、重排、数据库都有替身，不花钱、结果确定。数据库用 SQLite 文件库代替 MySQL（DAO 只依赖会话工厂）。

## 文件结构

**新建**

| 文件 | 职责 | 任务 |
|---|---|---|
| `backend/agents/rag/config.py` | RAG 配置 | 0 |
| `backend/scripts/__init__.py`、`backend/scripts/rag_probe.py` | 外部接口探针 | 0 |
| `backend/agents/rag/models.py` | 领域模型、文本规范化、id、年级排序 | 1 |
| `backend/agents/rag/store/embedder.py` | 向量化协议、归一化、LlamaIndex 实现 | 2 |
| `backend/agents/rag/store/vector_store.py` | 向量存储协议、内存实现 | 2 |
| `backend/agents/rag/store/llama_store.py` | LlamaIndex 适配层（生产实现，后端 Chroma） | 2 |
| `backend/agents/rag/store/lexical_index.py` | BM25 + 知识点倒排 + 题目目录 | 3 |
| `backend/model/rag.py` | 入库任务表、隔离区表 | 4 |
| `backend/dao/rag_mapper.py` | 任务与隔离区的数据访问 | 4 |
| `backend/agents/rag/ingest/loaders.py` | PDF / Word 加载 | 5 |
| `backend/agents/rag/llm_json.py` | 从 LLM 输出截取 JSON | 6 |
| `backend/agents/rag/ingest/structurer.py` | LLM 结构化（滑动窗口） | 6 |
| `backend/agents/skills/rag_structuring/SKILL.md` | 结构化 prompt | 6 |
| `backend/agents/rag/ingest/generator.py` | 种子题生成 | 7 |
| `backend/agents/skills/rag_seed_generation/SKILL.md` | 生成 prompt | 7 |
| `backend/agents/rag/ingest/judge.py` | 第三方复核 | 8 |
| `backend/agents/skills/rag_judge/SKILL.md` | 复核 prompt | 8 |
| `backend/agents/rag/ingest/dedup.py` | 精确 + 近似去重 | 9 |
| `backend/agents/rag/ingest/service.py` | 入库服务编排 | 10 |
| `backend/agents/rag/runtime.py` | 组件组装（进程内单例） | 11、15 |
| `backend/agents/rag/ingest/__main__.py` | 入库命令行 | 11 |
| `backend/agents/rag/retrieval/routes.py` | 三路召回 | 12 |
| `backend/agents/rag/retrieval/fusion.py` | RRF 融合 | 13 |
| `backend/agents/rag/retrieval/reranker.py` | 重排 | 14 |
| `backend/agents/rag/retrieval/retriever.py` | 检索编排 | 15 |
| `backend/agents/rag/retrieval/context.py` | 参考材料格式化、防照抄 | 16 |
| `backend/agents/rag/integration.py` | 生题链路的检索入口 | 16 |
| `backend/agents/rag/lifecycle.py` | 启动与关停时要做的事 | 16、17 |
| `backend/agents/rag/jobs.py` | 后台任务执行器 | 17 |
| `backend/api/manage_api/dependencies.py` | 管理员鉴权 | 17 |
| `backend/api/manage_api/rag_api.py` | RAG 管理接口 | 18、19 |
| `backend/agents/rag/eval/metrics.py` | 检索指标 | 20 |
| `backend/agents/rag/eval/dataset.py` | 评测集构建 | 20 |
| `backend/agents/rag/eval/retrieval_eval.py` | 检索消融 | 21 |
| `backend/agents/rag/eval/generation_eval.py` | 生题 A/B | 22 |
| `backend/agents/rag/eval/__main__.py` | 评测命令行 | 22 |
| `backend/agents/rag/{store,ingest,retrieval,eval}/__init__.py` | 空文件 | 首次用到时 |
| `backend/tests/rag/__init__.py`、`conftest.py`、`helpers.py` | 测试基建（逐段追加） | 0 起 |

**修改**

| 文件 | 改动 | 任务 |
|---|---|---|
| `backend/requirements.txt` | 新增 jieba、rank_bm25、pypdf、python-docx、lxml | 0 |
| `.gitignore` | 忽略 RAG 的三个数据目录 | 0 |
| `backend/agents/skills/loader.py` | 支持 `visibility: internal` | 6 |
| `backend/agents/agent/question_set_agent.py` | 注入参考材料、防照抄重试 | 16 |
| `backend/agents/tools/question_set_tool.py` | 调用检索 | 16 |
| `backend/core/hooks.py` | 启动加载索引、恢复任务状态；关停等待任务 | 16、17 |
| `backend/api/manage_api/__init__.py` | 挂载 RAG 路由 | 18 |
| `backend/main.py` | 接入 `manage_api` | 18 |
| `CLAUDE.md` | 补充 RAG 架构与约定 | 23 |

---

## Task 0: 环境、依赖与配置

> **这个任务做什么**：给 RAG 打地基，还不写任何检索或入库逻辑。一共四件事：① 确保 chromadb 和 LlamaIndex 的 Chroma 集成能正常导入——导入失败通常是 grpcio、chromadb、onnxruntime 的编译文件（`.pyd`）在磁盘上缺失，按步骤只重装这几个包即可；② 装齐 RAG 的新依赖（jieba 中文分词、rank_bm25 关键词检索、pypdf 读 PDF、python-docx 读 Word），把实际版本写进 `requirements.txt`，并在 `.gitignore` 里忽略 RAG 运行时生成的三个数据目录（知识库、上传文件、评测报告）；③ 新建 `backend/agents/rag/config.py`，用一个只读的 `RagSettings` 把所有 RAG 配置项（开关、目录、重排接口、裁判模型等）集中从 `.env` 读进来，后面所有任务都从这里取配置；④ 写一个一次性探针脚本 `rag_probe.py`，用真实请求确认四件项目外部的事：DashScope 重排接口的返回结构、embedding 能否拿到向量、LlamaIndex 读写 Chroma 时查询分数怎么换算成余弦相似度、裁判模型能不能调通。
>
> **做完之后**：`backend/tests/rag/` 测试目录和 `rag_settings` fixture 建好了，后续任务的测试都用它；探针的输出决定 Task 2 和 Task 14 里有没有要按实际情况调整的地方。chromadb 暂时修不好也不耽误往下做，依赖它的测试会被明确跳过。

要先解决的是 **chromadb 现在无法导入**：`import chromadb` 在当前 venv 里报 `ImportError: cannot import name 'cygrpc'`。原因不是版本不兼容，而是有三个包的编译扩展（`.pyd`）在安装记录里登记了、磁盘上却不存在（见依赖清单）。LlamaIndex 的 Chroma 集成和服务的启动链路都会导入 chromadb，所以这个问题不解决，服务就起不来。RAG 的大部分测试跑在内存实现上，不受影响。

**Files:**
- Modify: `backend/requirements.txt`、`.gitignore`
- Create: `backend/agents/rag/config.py`
- Create: `backend/scripts/__init__.py`（空文件）、`backend/scripts/rag_probe.py`
- Create: `backend/tests/rag/__init__.py`（空文件）、`backend/tests/rag/conftest.py`
- Test: `backend/tests/rag/test_config.py`

**Interfaces:**
- Consumes: `backend.core.config.BACKEND_ROOT`、`load_env()`（记忆计划 Task 1）
- Produces:
  - `RagSettings`（frozen dataclass），`RagSettings.from_env()`；字段见下方代码
  - `DEFAULT_RERANK_URL`、`DEFAULT_RERANK_MODEL`
  - fixture `rag_settings`：目录指向临时目录、裁判与生成模型为不同家族的测试配置

- [ ] **Step 1: 修复 chromadb 的导入**

> **这一步已经做完了**（2026-09-15）：三个缺失的 `.pyd` 已经通过 `pip install --force-reinstall --no-deps --no-cache-dir grpcio==1.78.0 chromadb==1.5.5 onnxruntime==1.24.4` 补回，`import chromadb` 正常。下面保留排查过程，供以后再遇到时参考。**因此本计划里所有「chromadb 不可用时 X skipped」的分支都不会出现，实际看到的是「可用」那一档的数字。**

```bash
cd backend
python -c "import chromadb, llama_index.vector_stores.chroma; print('OK')"
```

报 `cannot import name 'cygrpc'` 或 `No module named 'chromadb_rust_bindings.chromadb_rust_bindings'` 时，先列出 venv 里缺失的二进制文件：

```bash
python -c "
import csv, pathlib, sysconfig
sp = pathlib.Path(sysconfig.get_paths()['purelib'])
for rec in sp.glob('*.dist-info/RECORD'):
    for row in csv.reader(rec.read_text(encoding='utf-8').splitlines()):
        if row and row[0].endswith(('.pyd', '.dll')) and not (sp / row[0]).exists():
            print(rec.parent.name, row[0])
"
```

编写本计划时输出的是 grpcio、chromadb、onnxruntime 各一个文件。只重装这几个包本身（`--no-deps` 表示不动它们的依赖）：

```bash
pip install --force-reinstall --no-deps --no-cache-dir grpcio==1.78.0 chromadb==1.5.5 onnxruntime==1.24.4
python -c "import chromadb, llama_index.vector_stores.chroma; print('OK')"
```

再跑一次上面的检查脚本，应当没有输出。如果重装后文件又不见了，多半是杀毒软件把它们隔离了：到隔离区恢复，并把 `backend/.venv` 加入排除目录。

**这一步没解决之前**：依赖 chromadb 的测试会被明确跳过（全部任务做完时共 10 个），其余 RAG 任务都可以照常推进。

- [ ] **Step 2: 安装依赖并回填版本**

> **这一步也已经做完了**（2026-09-15）：jieba 0.42.1、rank-bm25 0.2.2、pypdf 6.18.0、python-docx 1.2.0、lxml 6.1.3 都已安装并写进 `requirements.txt`（提交 `f9d1cff`）。那次提交同时把 `asyncmy` 升到 0.2.12，并清掉了 25 个没用到的包，其中包括 `llama-index` 元包和它的几个子包——RAG 用到的 `llama-index-core` 与 `llama-index-vector-stores-chroma` 都保留着。

```bash
pip install jieba rank_bm25 pypdf python-docx
python -c "import jieba, rank_bm25, pypdf, docx; print('OK')"
pip show jieba rank_bm25 pypdf python-docx lxml | grep -iE "^(name|version)"
```

把实际版本写进 `backend/requirements.txt`（lxml 是 python-docx 的依赖，一并写入）。编写本计划时在 Python 3.14 上验证过的版本是：jieba 0.42.1、rank_bm25 0.2.2、pypdf 6.18.0、python-docx 1.2.0、lxml 6.1.3。

LlamaIndex 相关的包已经在 requirements.txt 里（llama-index-core 0.14.18、llama-index-vector-stores-chroma 0.5.5），不用另装，也不要单独升级：Task 2 适配层依赖的几处行为是在这两个版本上实测的，升级后要先重跑 Step 9 的探针。

- [ ] **Step 3: 忽略数据目录**

在仓库根目录的 `.gitignore` 末尾追加：

```
backend/rag_db/
backend/rag_uploads/
backend/rag_eval/
```

- [ ] **Step 4: 在 `backend/.env` 里补上裁判配置**

```
# 第三方裁判模型：必须与 MODEL_NAME 不同家族（例如生成用 qwen，裁判就不能用 qwen）
JUDGE_API_URL=
JUDGE_API_KEY=
JUDGE_MODEL=
```

其余 RAG 配置都有默认值，需要时再加（见 spec 的「配置项」表）。

- [ ] **Step 5: 写失败的测试**

创建空文件 `backend/tests/rag/__init__.py`，然后创建 `backend/tests/rag/test_config.py`：

```python
from backend.agents.rag.config import RagSettings
from backend.core.config import BACKEND_ROOT


def test_relative_dirs_resolve_against_backend_root(tmp_path, monkeypatch):
    """与 core/config 同一原则：从哪个目录启动都不影响读写位置。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RAG_DB_DIR", "my_rag")
    assert RagSettings.from_env().db_dir == (BACKEND_ROOT / "my_rag").resolve()


def test_absolute_dir_is_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DB_DIR", str(tmp_path / "abs"))
    assert RagSettings.from_env().db_dir == tmp_path / "abs"


def test_defaults(monkeypatch):
    for key in ("RAG_DB_DIR", "RAG_UPLOAD_DIR", "RAG_EVAL_DIR", "RAG_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    s = RagSettings.from_env()
    assert s.enabled is True
    assert s.db_dir == (BACKEND_ROOT / "rag_db").resolve()
    assert (s.route_top_k, s.fusion_top_n, s.final_top_k, s.knowledge_top_k) == (20, 30, 3, 2)
    assert s.rrf_k == 60
    assert s.near_dup_threshold == s.leak_threshold == 0.95


def test_enabled_flag_parsing(monkeypatch):
    for raw, expected in [("false", False), ("0", False), ("true", True), ("ON", True)]:
        monkeypatch.setenv("RAG_ENABLED", raw)
        assert RagSettings.from_env().enabled is expected


def test_rerank_key_falls_back_to_api_key(monkeypatch):
    monkeypatch.delenv("RERANK_API_KEY", raising=False)
    monkeypatch.setenv("API_KEY", "k-main")
    assert RagSettings.from_env().rerank_api_key == "k-main"
```

- [ ] **Step 6: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_config.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.agents.rag.config'`

- [ ] **Step 7: 实现配置**

创建 `backend/agents/rag/config.py`：

```python
"""
RAG 子系统的配置。

所有目录都相对 backend/ 解析，而不是相对当前工作目录——
与 core/config.py 同一个原则：进程从哪里启动，都不影响读写位置。
"""
import os
from dataclasses import dataclass
from pathlib import Path

from backend.core.config import BACKEND_ROOT, load_env

load_env()

# 以 Task 0 探针的实际请求结果为准
DEFAULT_RERANK_URL = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
DEFAULT_RERANK_MODEL = "gte-rerank-v2"


def _resolve_dir(value: str | None, default: str) -> Path:
    path = Path(value or default)
    return path if path.is_absolute() else (BACKEND_ROOT / path).resolve()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class RagSettings:
    enabled: bool
    db_dir: Path
    upload_dir: Path
    eval_dir: Path
    rerank_url: str
    rerank_model: str
    rerank_api_key: str | None
    judge_api_url: str | None
    judge_api_key: str | None
    judge_model: str | None
    generator_model: str
    # 检索
    route_top_k: int = 20
    fusion_top_n: int = 30
    final_top_k: int = 3
    knowledge_top_k: int = 2
    rrf_k: int = 60
    rerank_min_score: float = 0.3
    knowledge_min_similarity: float = 0.5
    leak_threshold: float = 0.95
    context_max_chars: int = 1200
    # 入库
    near_dup_threshold: float = 0.95
    judge_pass_score: float = 0.8
    judge_concurrency: int = 4
    max_upload_mb: int = 20

    @classmethod
    def from_env(cls) -> "RagSettings":
        return cls(
            enabled=_env_bool("RAG_ENABLED", True),
            db_dir=_resolve_dir(os.getenv("RAG_DB_DIR"), "rag_db"),
            upload_dir=_resolve_dir(os.getenv("RAG_UPLOAD_DIR"), "rag_uploads"),
            eval_dir=_resolve_dir(os.getenv("RAG_EVAL_DIR"), "rag_eval"),
            rerank_url=os.getenv("RERANK_API_URL") or DEFAULT_RERANK_URL,
            rerank_model=os.getenv("RERANK_MODEL") or DEFAULT_RERANK_MODEL,
            rerank_api_key=os.getenv("RERANK_API_KEY") or os.getenv("API_KEY"),
            judge_api_url=os.getenv("JUDGE_API_URL"),
            judge_api_key=os.getenv("JUDGE_API_KEY"),
            judge_model=os.getenv("JUDGE_MODEL"),
            generator_model=os.getenv("MODEL_NAME", "glm-5"),
            judge_pass_score=float(os.getenv("RAG_JUDGE_PASS_SCORE", "0.8")),
        )
```

创建 `backend/tests/rag/conftest.py`，写入第一段 fixture：

```python
import dataclasses

import pytest

from backend.agents.rag.config import RagSettings


@pytest.fixture
def rag_settings(tmp_path):
    """
    测试用配置：目录全部指向临时目录；裁判与生成模型设成不同家族，
    保证家族校验通过；去重阈值放宽到 0.9，适配 HashingEmbedder 的精度。
    """
    return dataclasses.replace(
        RagSettings.from_env(),
        enabled=True,
        db_dir=tmp_path / "rag_db",
        upload_dir=tmp_path / "rag_uploads",
        eval_dir=tmp_path / "rag_eval",
        judge_api_url="http://judge.invalid/v1",
        judge_api_key="test-key",
        judge_model="deepseek-v3",
        generator_model="qwen-plus",
        near_dup_threshold=0.9,
        judge_concurrency=2,
    )
```

- [ ] **Step 8: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_config.py -v`
Expected: 5 passed

- [ ] **Step 9: 用真实请求确认外部接口**

创建空文件 `backend/scripts/__init__.py`，然后创建 `backend/scripts/rag_probe.py`：

```python
"""
一次性探针：在写检索与入库代码之前，用真实请求确认四件事。会产生极少量 API 费用。

    python -m backend.scripts.rag_probe      （在仓库根目录运行）

1. 重排接口：地址、模型名是否可用，响应结构长什么样（Task 14 的解析函数以此为准）
2. 向量化：LlamaIndex 的 embedding 封装能否拿到向量、维度多少
3. LlamaIndex + Chroma：查询分数与 cosine 距离的关系、重复 id 的写入行为（Task 2 的适配层以此为准）
4. 裁判模型：配置是否可用
"""
import asyncio
import json
import math
import tempfile

import httpx

from backend.agents.rag.config import RagSettings


async def probe_rerank(settings: RagSettings) -> None:
    payload = {
        "model": settings.rerank_model,
        "input": {"query": "解一元一次方程", "documents": ["解方程 2x+3=7", "计算三角形的面积"]},
        "parameters": {"top_n": 2, "return_documents": False},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            settings.rerank_url, json=payload, headers={"Authorization": f"Bearer {settings.rerank_api_key}"}
        )
    print(f"[rerank] {settings.rerank_model} -> HTTP {response.status_code}")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2)[:1500])
    print("[rerank] 预期：结果在 output.results 里，每项含 index 与 relevance_score，且方程那条分数更高")


async def probe_embedding(settings: RagSettings) -> None:
    from backend.agents.agent.get_llm import get_embedding_model

    vectors = await get_embedding_model().aget_text_embedding_batch(["解方程 2x+3=7", "计算三角形的面积"])
    print(f"[embedding] 返回 {len(vectors)} 条，维度 {len(vectors[0])}")
    print("[embedding] 预期：2 条，维度与 EMBEDDING_MODEL 的说明一致")


def probe_llamaindex_chroma() -> None:
    import chromadb
    from llama_index.core.schema import TextNode
    from llama_index.core.vector_stores.types import VectorStoreQuery
    from llama_index.vector_stores.chroma import ChromaVectorStore

    client = chromadb.PersistentClient(path=tempfile.mkdtemp())
    collection = client.get_or_create_collection("probe", metadata={"hnsw:space": "cosine"})
    store = ChromaVectorStore(chroma_collection=collection)
    store.add([
        TextNode(id_="same", text="a", embedding=[1.0, 0.0]),
        TextNode(id_="mid", text="b", embedding=[0.6, 0.8]),
    ])
    result = store.query(VectorStoreQuery(query_embedding=[1.0, 0.0], similarity_top_k=2))
    cosines = [round(1 + math.log(score), 4) for score in result.similarities]
    print(f"[chroma] ids={result.ids} 分数={[round(s, 4) for s in result.similarities]} 按 1+ln(分数) 换算={cosines}")
    print("[chroma] 预期：换算后 same 约 1.0、mid 约 0.6（LlamaIndex 返回的分数 = exp(-cosine 距离)）")

    store.add([TextNode(id_="same", text="changed", embedding=[1.0, 0.0])])
    print(f"[chroma] 对已有 id 再 add 一次后，原文={collection.get(ids=['same'])['documents']}")
    print("[chroma] 预期：仍是 ['a']（add 不覆盖已有记录，所以适配层的 upsert 要先删后加）")


async def probe_judge(settings: RagSettings) -> None:
    # 这里直接构建客户端：探针在 Task 0 运行，裁判模块要到 Task 8 才会写
    from langchain_openai import ChatOpenAI

    if not (settings.judge_model and settings.judge_api_url and settings.judge_api_key):
        print("[judge] 未配置 JUDGE_MODEL / JUDGE_API_URL / JUDGE_API_KEY，跳过")
        return
    llm = ChatOpenAI(model=settings.judge_model, api_key=settings.judge_api_key,
                     base_url=settings.judge_api_url, temperature=0)
    response = await llm.ainvoke("只回答一个数字：方程 2x+3=7 中 x 等于几？")
    print(f"[judge] {settings.judge_model} -> {response.content!r}")


async def main() -> None:
    settings = RagSettings.from_env()
    for name, probe in (("rerank", probe_rerank), ("embedding", probe_embedding), ("judge", probe_judge)):
        try:
            await probe(settings)
        except Exception as e:
            print(f"[{name}] 失败：{type(e).__name__}: {e}")
    try:
        probe_llamaindex_chroma()
    except Exception as e:
        print(f"[chroma] 失败：{type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
```

在仓库根目录运行（会产生极少量 API 费用）：

```bash
python -m backend.scripts.rag_probe
```

逐项核对输出：

1. **rerank**：记下结果列表在响应中的路径和字段名。如果不是 `output.results[].index / relevance_score`，Task 14 的 `parse_rerank_response` 要按实际结构改；模型名不可用时改 `.env` 里的 `RERANK_MODEL`。
2. **embedding**：返回 2 条向量即可。失败时检查 `.env` 里的 `EMBEDDING_MODEL`。
3. **chroma**：换算后 `same` 约 1.0、`mid` 约 0.6，且重复 add 之后原文仍是 `['a']`。换算结果不对，说明 LlamaIndex 的 Chroma 集成改了分数公式（当前是 exp(-距离)），Task 2 的 `chroma_score_to_cosine` 要跟着改；原文变成了 `'changed'`，说明 add 已经会覆盖，这时先删后加仍然正确，不用改。
4. **judge**：能返回「2」即可。

- [ ] **Step 10: 提交**

```bash
git add .gitignore backend/requirements.txt backend/agents/rag/config.py backend/scripts/ backend/tests/rag/__init__.py backend/tests/rag/conftest.py backend/tests/rag/test_config.py
git commit -m "feat: RAG 配置与外部接口探针"
```

---

## Task 1: 领域模型

> **这个任务做什么**：定义整个 RAG 模块共用的「数据长什么样」，全是纯 Python 数据类，不连数据库，也不调模型。包括：一道题 `QuestionItem`（题干、答案、解析、题型、难度、知识点、年级、来源）、一段教材讲解 `KnowledgeChunk`、裁判复核结果 `JudgeResult`、一次检索的输入 `RagQuery` 和输出 `RagContext`。另外定三条小规则：文本规范化（全角转半角、去空白、转小写）；用规范化文本的哈希当 id，同一道题无论从哪来，id 永远相同；年级排序，用来避免给低年级学生推高年级的题。
>
> **做完之后**：后续所有任务都用这些类型传数据。这是最适合入门的任务：没有外部依赖，读懂它就知道 RAG 里流转的都是些什么。

题目与教材段的数据结构、文本规范化与 id 规则、年级排序。之后所有任务都建立在这里定义的类型上。

**Files:**
- Create: `backend/agents/rag/models.py`
- Test: `backend/tests/rag/test_models.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `normalize_text(text) -> str`、`content_id(text, prefix) -> str`、`grade_rank(grade) -> int | None`
  - 常量 `GRADE_ORDER`、`DIFFICULTIES`、`KP_SEPARATOR`
  - `QuestionItem`（pydantic）：字段 `stem, answer, analysis, question_type, difficulty, knowledge_points, grade, source_type, source_ref`；属性 `id`；方法 `to_metadata()`、类方法 `from_record(document, metadata)`
  - `KnowledgeChunk`（pydantic）：字段 `text, knowledge_points, grade, source_ref`；同样有 `id`、`to_metadata()`、`from_record()`
  - `JudgeResult(passed, score, reasons, judge_answer, judge_model)`，方法 `to_dict()`
  - `RagQuery(text, knowledge_points, difficulty, grade)`
  - `RetrievedQuestion(item, score, routes)`
  - `RagContext(questions, knowledge, degraded, timings_ms)`，属性 `empty`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/rag/test_models.py`：

```python
import pytest
from pydantic import ValidationError

from backend.agents.rag.models import (
    KnowledgeChunk,
    QuestionItem,
    RagContext,
    content_id,
    grade_rank,
    normalize_text,
)


def test_normalize_folds_fullwidth_and_whitespace():
    assert normalize_text("解方程　２x ＋ 3 = 7") == normalize_text("解方程2x+3=7")


def test_content_id_is_stable_and_prefixed():
    a = content_id("解方程 2x+3=7", "q_")
    assert a == content_id("解方程2x+3=7", "q_")
    assert a.startswith("q_") and len(a) == 18
    assert a != content_id("解方程2x+3=8", "q_")


@pytest.mark.parametrize("grade, expected", [
    ("一年级", 0), ("七年级", 6), ("初一", 6), ("高三", 11), ("大一", None), (None, None), ("", None),
])
def test_grade_rank(grade, expected):
    assert grade_rank(grade) == expected


def _item(**overrides):
    data = {"stem": "解方程 2x+3=7", "answer": "x=2", "knowledge_points": ["一元一次方程"], "grade": "七年级"}
    data.update(overrides)
    return QuestionItem(**data)


def test_blank_stem_is_rejected():
    with pytest.raises(ValidationError):
        _item(stem="   ")


def test_llm_nulls_fall_back_to_defaults():
    """LLM 常把缺失字段输出成 null，不应导致整道题被丢弃。"""
    item = _item(difficulty=None, question_type=None, analysis=None, grade="  ")
    assert (item.difficulty, item.question_type, item.analysis, item.grade) == ("中等", "未知", "", None)


def test_invalid_difficulty_falls_back_to_medium():
    assert _item(difficulty="超难").difficulty == "中等"


def test_knowledge_points_are_stripped_and_deduplicated():
    item = _item(knowledge_points=[" 一元一次方程 ", "一元一次方程", "", "移项"])
    assert item.knowledge_points == ["一元一次方程", "移项"]


def test_metadata_is_scalar_only():
    """Chroma 的 metadata 只接受标量。"""
    meta = _item(grade=None).to_metadata()
    assert all(isinstance(v, (str, int, float, bool)) for v in meta.values())


def test_question_roundtrip_through_record():
    item = _item(analysis="移项得 2x=4", difficulty="简单", question_type="计算题",
                 source_type="docx", source_ref="练习册.docx#p2")
    restored = QuestionItem.from_record(item.stem, item.to_metadata())
    assert restored == item
    assert restored.id == item.id


def test_question_without_grade_roundtrips_as_none():
    item = _item(grade=None)
    assert QuestionItem.from_record(item.stem, item.to_metadata()).grade is None


def test_knowledge_chunk_roundtrip():
    chunk = KnowledgeChunk(text="含有一个未知数、未知数的次数是 1 的方程叫一元一次方程。",
                           knowledge_points=["一元一次方程"], grade="七年级", source_ref="教材.pdf#p3")
    assert KnowledgeChunk.from_record(chunk.text, chunk.to_metadata()) == chunk
    assert chunk.id.startswith("k_")


def test_empty_context():
    assert RagContext().empty
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_models.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.agents.rag.models'`

- [ ] **Step 3: 实现**

创建 `backend/agents/rag/models.py`：

```python
"""
RAG 的领域模型。

id 规则：前缀 + 规范化文本的 sha1 前 16 位。同一道题无论来自哪份资料、第几次入库，
id 都相同——重复入库因此天然幂等，精确去重也只需比较 id。
"""
import hashlib
import unicodedata
from dataclasses import asdict, dataclass, field

from pydantic import BaseModel, Field, field_validator

GRADE_ORDER = (
    "一年级", "二年级", "三年级", "四年级", "五年级", "六年级",
    "七年级", "八年级", "九年级", "高一", "高二", "高三",
)
_GRADE_ALIASES = {"初一": "七年级", "初二": "八年级", "初三": "九年级"}

DIFFICULTIES = ("简单", "中等", "困难")
KP_SEPARATOR = "|"


def normalize_text(text: str) -> str:
    """全角转半角、去掉所有空白、英文小写。只用于算 id 和比较相似度，不改动原文。"""
    folded = unicodedata.normalize("NFKC", text or "")
    return "".join(ch for ch in folded if not ch.isspace()).lower()


def content_id(text: str, prefix: str) -> str:
    digest = hashlib.sha1(normalize_text(text).encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:16]}"


def grade_rank(grade: str | None) -> int | None:
    """年级在 GRADE_ORDER 中的位置，无法识别时返回 None。「初一」等别名按七年级处理。"""
    if not grade:
        return None
    name = grade.strip()
    name = _GRADE_ALIASES.get(name, name)
    return GRADE_ORDER.index(name) if name in GRADE_ORDER else None


def _clean_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    cleaned: list[str] = []
    for raw in value:
        item = str(raw).strip()
        if item and item not in cleaned:
            cleaned.append(item)
    return cleaned


def _split_kps(value: str) -> list[str]:
    return [kp for kp in (value or "").split(KP_SEPARATOR) if kp]


def _blank_to_none(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


class QuestionItem(BaseModel):
    stem: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    analysis: str = ""
    question_type: str = "未知"
    difficulty: str = "中等"
    knowledge_points: list[str] = Field(default_factory=list)
    grade: str | None = None
    source_type: str = "pdf"  # pdf / docx / llm
    source_ref: str = ""

    @field_validator("stem", "answer", mode="before")
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("analysis", "source_ref", mode="before")
    @classmethod
    def _none_to_empty(cls, value):
        return "" if value is None else value

    @field_validator("question_type", mode="before")
    @classmethod
    def _default_question_type(cls, value):
        return value.strip() if isinstance(value, str) and value.strip() else "未知"

    @field_validator("difficulty", mode="before")
    @classmethod
    def _normalize_difficulty(cls, value):
        return value if value in DIFFICULTIES else "中等"

    @field_validator("knowledge_points", mode="before")
    @classmethod
    def _clean_knowledge_points(cls, value):
        return _clean_list(value)

    @field_validator("grade", mode="before")
    @classmethod
    def _clean_grade(cls, value):
        return _blank_to_none(value)

    @property
    def id(self) -> str:
        return content_id(self.stem, "q_")

    def to_metadata(self) -> dict:
        """Chroma 的 metadata 只接受标量：列表用分隔符拼接，None 存成空串。"""
        return {
            "answer": self.answer,
            "analysis": self.analysis,
            "question_type": self.question_type,
            "difficulty": self.difficulty,
            "knowledge_points": KP_SEPARATOR.join(self.knowledge_points),
            "grade": self.grade or "",
            "source_type": self.source_type,
            "source_ref": self.source_ref,
        }

    @classmethod
    def from_record(cls, document: str, metadata: dict) -> "QuestionItem":
        return cls(
            stem=document,
            answer=metadata.get("answer", ""),
            analysis=metadata.get("analysis", ""),
            question_type=metadata.get("question_type", "未知"),
            difficulty=metadata.get("difficulty", "中等"),
            knowledge_points=_split_kps(metadata.get("knowledge_points", "")),
            grade=metadata.get("grade") or None,
            source_type=metadata.get("source_type", "pdf"),
            source_ref=metadata.get("source_ref", ""),
        )


class KnowledgeChunk(BaseModel):
    text: str = Field(min_length=1)
    knowledge_points: list[str] = Field(default_factory=list)
    grade: str | None = None
    source_ref: str = ""

    @field_validator("text", mode="before")
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("knowledge_points", mode="before")
    @classmethod
    def _clean_knowledge_points(cls, value):
        return _clean_list(value)

    @field_validator("grade", mode="before")
    @classmethod
    def _clean_grade(cls, value):
        return _blank_to_none(value)

    @field_validator("source_ref", mode="before")
    @classmethod
    def _none_to_empty(cls, value):
        return "" if value is None else value

    @property
    def id(self) -> str:
        return content_id(self.text, "k_")

    def to_metadata(self) -> dict:
        return {
            "knowledge_points": KP_SEPARATOR.join(self.knowledge_points),
            "grade": self.grade or "",
            "source_ref": self.source_ref,
        }

    @classmethod
    def from_record(cls, document: str, metadata: dict) -> "KnowledgeChunk":
        return cls(
            text=document,
            knowledge_points=_split_kps(metadata.get("knowledge_points", "")),
            grade=metadata.get("grade") or None,
            source_ref=metadata.get("source_ref", ""),
        )


@dataclass
class JudgeResult:
    passed: bool
    score: float
    reasons: list[str] = field(default_factory=list)
    judge_answer: str = ""
    judge_model: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RagQuery:
    text: str
    knowledge_points: list[str] = field(default_factory=list)
    difficulty: str | None = None
    grade: str | None = None


@dataclass
class RetrievedQuestion:
    item: QuestionItem
    score: float | None  # 重排分；重排不可用时为融合分
    routes: list[str]    # 命中这道题的召回路


@dataclass
class RagContext:
    questions: list[RetrievedQuestion] = field(default_factory=list)
    knowledge: list[KnowledgeChunk] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.questions and not self.knowledge
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_models.py -v`
Expected: 18 passed

- [ ] **Step 5: 提交**

```bash
git add backend/agents/rag/models.py backend/tests/rag/test_models.py
git commit -m "feat: RAG 领域模型与内容哈希 id"
```

---

## Task 2: 向量化与向量存储

> **这个任务做什么**：解决「文本怎么变成向量、向量存到哪、怎么按相似度查」。定义两个协议（可以理解成接口约定）：`Embedder` 把文本变成向量，生产环境用 `LlamaIndexEmbedder` 包装项目里已有的 LlamaIndex embedding 模型；`VectorStore` 负责存向量、按相似度查询，有两个实现——`LlamaIndexVectorStore`（生产用，经 LlamaIndex 读写落盘的 Chroma，单独写在适配层 `llama_store.py` 里）和 `InMemoryVectorStore`（测试用，只存在内存里）。适配层要抹平 LlamaIndex + Chroma 的几处特殊行为，比如查询返回的分数要换算回余弦相似度、写入已存在的 id 时不会覆盖。所有向量都归一化成单位长度，这样余弦相似度就等于点积，两种实现算出来的相似度可以直接比较。测试里用 `HashingEmbedder` 代替真实模型，不花钱、结果固定。
>
> **做完之后**：能把题目的向量存进库，并查出最相似的几条。它是三路召回里「向量召回」那一路的底座，Task 9 的近似去重也要用它。

入库与检索只依赖 `VectorStore` 协议：测试与评测用内存实现，生产用 LlamaIndex 读写 Chroma。LlamaIndex 适配层放在单独的 `llama_store.py`，chromadb 到 `from_chroma` 里才导入，它坏掉时只影响真正用到它的地方。

**为什么直接操作 LlamaIndex 的向量库，而不用 `VectorStoreIndex`**：向量要事先算好。去重在入库前就要拿到向量；一次检索的查询向量要在题库、教材库两个库之间复用；向量化失败时检索要能降级。`VectorStoreIndex` 会自己再向量化一次，没配 embed_model 时还会去用 LlamaIndex 全局 Settings 里默认的 OpenAI。`QueryFusionRetriever` 同理：三路召回、降级和计时都要自己控制，所以融合也自己写（Task 13）。

**适配层要处理的四处特殊行为**（都在真实 Chroma 上测过，详见 `llama_store.py` 的模块说明）：分数是 exp(-距离)，要换算回余弦；add 不覆盖已有 id，upsert 要先删后加，同批 id 要去重；`delete_nodes([])` 会报错；异步方法其实是同步的。

**Files:**
- Create: `backend/agents/rag/store/__init__.py`（空文件）
- Create: `backend/agents/rag/store/embedder.py`
- Create: `backend/agents/rag/store/vector_store.py`
- Create: `backend/agents/rag/store/llama_store.py`
- Create: `backend/tests/rag/helpers.py`
- Test: `backend/tests/rag/test_vector_store.py`

**Interfaces:**
- Consumes: `get_vector_executor()`（记忆计划 Task 3）、`get_embedding_model()`（已有的 `get_llm.py`，返回 LlamaIndex 的 `BaseEmbedding`）、llama-index-core、llama-index-vector-stores-chroma
- Produces:
  - `Embedder` 协议：`async embed(texts) -> list[list[float]]`
  - `l2_normalize(vec)`、`dot(a, b)`
  - `LlamaIndexEmbedder(model=None, batch_size=10)`：包装任意 LlamaIndex `BaseEmbedding`，默认用 `get_embedding_model()`
  - `VectorHit(id, document, metadata, similarity)`
  - `VectorStore` 协议：`upsert(ids, embeddings, documents, metadatas)`、`query(embedding, top_k) -> list[VectorHit]`、`get_all() -> list[(id, document, metadata)]`、`count()`、`delete(ids)`，全部为 async
  - `InMemoryVectorStore()`
  - `LlamaIndexVectorStore(backend, score_to_similarity)`、`LlamaIndexVectorStore.from_chroma(persist_dir, collection_name)`、`chroma_score_to_cosine(score)`
  - 测试替身 `HashingEmbedder(dim=256)`

- [ ] **Step 1: 写测试替身**

创建 `backend/tests/rag/helpers.py`，写入第一段：

```python
"""RAG 测试用的替身与工具。全部离线、结果确定。"""
import hashlib

from backend.agents.rag.models import normalize_text
from backend.agents.rag.store.embedder import l2_normalize


class HashingEmbedder:
    """
    确定性的假 embedding：规范化文本的字符二元组，用 md5 哈希到固定维度计数。
    文本越相似，向量越接近；不联网、不花钱。
    用 md5 而不是内置 hash()：后者每个进程随机加盐，测试结果会不稳定。
    """

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        s = normalize_text(text)
        grams = [s[i:i + 2] for i in range(len(s) - 1)] or [s]
        for gram in grams:
            idx = int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16) % self.dim
            vec[idx] += 1.0
        return l2_normalize(vec)
```

- [ ] **Step 2: 写失败的测试**

创建 `backend/tests/rag/test_vector_store.py`：

```python
import asyncio
import math

import pytest
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.vector_stores import SimpleVectorStore

from backend.agents.rag.store.embedder import LlamaIndexEmbedder, dot, l2_normalize
from backend.agents.rag.store.llama_store import LlamaIndexVectorStore, chroma_score_to_cosine
from backend.agents.rag.store.vector_store import InMemoryVectorStore
from backend.tests.rag.helpers import HashingEmbedder


def test_l2_normalize_gives_unit_length():
    v = l2_normalize([3.0, 4.0])
    assert v == pytest.approx([0.6, 0.8])
    assert math.isclose(dot(v, v), 1.0)


def test_l2_normalize_zero_vector_is_safe():
    assert l2_normalize([0.0, 0.0]) == [0.0, 0.0]


# ---------- 向量化 ----------

async def test_embedder_normalizes_model_output():
    class FakeModel:
        async def aget_text_embedding_batch(self, texts):
            return [[3.0, 4.0] for _ in texts]

    vectors = await LlamaIndexEmbedder(model=FakeModel()).embed(["a", "b"])
    assert vectors == [pytest.approx([0.6, 0.8]), pytest.approx([0.6, 0.8])]


async def test_embedder_empty_input_makes_no_call():
    class ExplodingModel:
        async def aget_text_embedding_batch(self, texts):
            raise AssertionError("空输入不应调用接口")

    assert await LlamaIndexEmbedder(model=ExplodingModel()).embed([]) == []


async def test_embedder_sends_one_bounded_batch_at_a_time():
    """25 条文本分成 10、10、5 三批，且同一时刻只有一批在请求：并发量不随文本数增长。"""
    class RecordingModel:
        def __init__(self):
            self.batches, self.in_flight, self.max_in_flight = [], 0, 0

        async def aget_text_embedding_batch(self, texts):
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0)
            self.in_flight -= 1
            self.batches.append(len(texts))
            return [[1.0, 0.0] for _ in texts]

    model = RecordingModel()
    vectors = await LlamaIndexEmbedder(model=model, batch_size=10).embed([f"t{i}" for i in range(25)])
    assert len(vectors) == 25
    assert model.batches == [10, 10, 5]
    assert model.max_in_flight == 1


async def test_embedder_accepts_a_real_llamaindex_embedding():
    """真正的 LlamaIndex BaseEmbedding 子类可以直接接入（MockEmbedding 固定返回 [0.5] * 维度）。"""
    [vector] = await LlamaIndexEmbedder(model=MockEmbedding(embed_dim=4)).embed(["任意文本"])
    assert vector == pytest.approx([0.5, 0.5, 0.5, 0.5])


async def test_hashing_embedder_is_a_trustworthy_double():
    """测试替身本身也要可信：几乎相同的文本相似度要高，无关文本要低。"""
    a, b, c = await HashingEmbedder().embed([
        "已知长方形的长是宽的2倍，周长是36厘米，求长和宽各是多少",
        "已知长方形的长是宽的2倍，周长是36厘米，求长和宽各是多少？",
        "计算半径为3的圆的面积",
    ])
    assert dot(a, b) > 0.9
    assert dot(a, c) < 0.5


# ---------- 向量存储：两种实现跑同一套契约测试 ----------
# 其余测试都用内存实现，生产用 LlamaIndex + Chroma；这组测试保证两者行为一致，可以互换。

@pytest.fixture(params=["memory", "llamaindex_chroma"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryVectorStore()
    # 必须显式传 exc_type=ImportError：chromadb「找得到但导入报错」（如编译扩展缺失）时，
    # pytest 9.1 起默认会把跳过改成报错
    pytest.importorskip("chromadb", exc_type=ImportError)
    return LlamaIndexVectorStore.from_chroma(tmp_path / "chroma", "contract")


async def test_query_returns_cosine_similarity_in_order(store):
    """mid 与查询向量的余弦是 0.6：不换算的话，LlamaIndex + Chroma 会给出 exp(-0.4) ≈ 0.67。"""
    await store.upsert(
        ["same", "mid", "orth"],
        [[1.0, 0.0], [0.6, 0.8], [0.0, 1.0]],
        ["doc-same", "doc-mid", "doc-orth"],
        [{"k": "same", "n": 1}, {"k": "mid"}, {"k": "orth"}],
    )
    hits = await store.query([1.0, 0.0], top_k=3)
    assert [h.id for h in hits] == ["same", "mid", "orth"]
    assert [h.similarity for h in hits] == pytest.approx([1.0, 0.6, 0.0], abs=1e-3)
    assert hits[0].document == "doc-same" and hits[0].metadata == {"k": "same", "n": 1}
    assert [h.id for h in await store.query([1.0, 0.0], top_k=2)] == ["same", "mid"]
    assert len(await store.query([1.0, 0.0], top_k=10)) == 3


async def test_upsert_overwrites_and_delete_removes(store):
    await store.upsert(["x"], [[1.0, 0.0]], ["old"], [{"v": 1}])
    await store.upsert(["x"], [[0.0, 1.0]], ["new"], [{"v": 2}])
    assert await store.count() == 1
    assert await store.get_all() == [("x", "new", {"v": 2})]
    [hit] = await store.query([0.0, 1.0], top_k=1)
    assert hit.similarity == pytest.approx(1.0, abs=1e-3)  # 向量也跟着更新了
    await store.delete(["x", "never-existed"])
    assert await store.count() == 0
    assert await store.query([1.0, 0.0], top_k=5) == []


async def test_duplicate_ids_in_one_batch_keep_the_last(store):
    await store.upsert(["x", "x"], [[1.0, 0.0], [1.0, 0.0]], ["first", "second"], [{}, {}])
    assert await store.get_all() == [("x", "second", {})]


async def test_empty_operations_are_noops(store):
    await store.upsert([], [], [], [])
    await store.delete([])
    assert await store.query([1.0, 0.0], top_k=3) == []
    assert await store.get_all() == []
    assert await store.count() == 0


# ---------- LlamaIndex 适配层 ----------

def test_chroma_score_conversion():
    for cosine in (1.0, 0.6, 0.0, -0.5):
        assert chroma_score_to_cosine(math.exp(-(1.0 - cosine))) == pytest.approx(cosine)


def test_rejects_backend_that_does_not_store_text():
    """SimpleVectorStore 只存向量、不存原文，查询结果里拿不到题干，必须在构造时就拒绝。"""
    with pytest.raises(ValueError, match="不保存原文"):
        LlamaIndexVectorStore(SimpleVectorStore(), lambda score: score)


async def test_llamaindex_chroma_persists_across_instances(tmp_path):
    pytest.importorskip("chromadb", exc_type=ImportError)
    first = LlamaIndexVectorStore.from_chroma(tmp_path / "chroma", "persist")
    await first.upsert(["x"], [[1.0, 0.0]], ["doc"], [{"k": "v"}])
    reopened = LlamaIndexVectorStore.from_chroma(tmp_path / "chroma", "persist")
    assert await reopened.get_all() == [("x", "doc", {"k": "v"})]
    assert (tmp_path / "chroma" / "chroma.sqlite3").exists()
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_vector_store.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.agents.rag.store'`

- [ ] **Step 4: 实现**

创建空文件 `backend/agents/rag/store/__init__.py`。

创建 `backend/agents/rag/store/embedder.py`：

```python
"""
向量化。

所有向量都归一化为单位长度：余弦相似度因此等于点积，
InMemoryVectorStore 与 LlamaIndex + Chroma（cosine 空间）给出的相似度可以直接比较，
去重与防泄漏的阈值在两种实现下含义一致。
"""
import math
from typing import Protocol, Sequence


class Embedder(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def l2_normalize(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return [0.0 for _ in vec]
    return [x / norm for x in vec]


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    """单位向量的点积即余弦相似度。"""
    return sum(x * y for x, y in zip(a, b))


class LlamaIndexEmbedder:
    """
    包装任意 LlamaIndex BaseEmbedding，输出归一化后的向量。默认用 get_llm.py 里已有的 DashScope 封装。

    按 batch_size 分段、一段一段地等，而不是把全部文本一次交给 aget_text_embedding_batch：
    后者会把所有批次一起 gather，而 get_llm.py 的封装没有实现批量接口、每条文本单独发一个请求——
    不分段的话，入库一份 200 段的教材会同时打出 200 个请求，很容易被限流。
    """

    def __init__(self, model=None, batch_size: int = 10):
        if model is None:
            from backend.agents.agent.get_llm import get_embedding_model
            model = get_embedding_model()
        self._model = model
        self._batch_size = batch_size

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        texts = list(texts)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start:start + self._batch_size]
            vectors.extend(await self._model.aget_text_embedding_batch(batch))
        return [l2_normalize(v) for v in vectors]
```

创建 `backend/agents/rag/store/vector_store.py`：

```python
"""
向量存储。入库与检索代码只依赖 VectorStore 协议：

- InMemoryVectorStore：纯 Python 实现，测试与离线评测用
- LlamaIndexVectorStore（llama_store.py）：生产实现，经 LlamaIndex 读写 Chroma。
  放在单独的模块里，只用内存实现的代码就不必导入 LlamaIndex 和 chromadb
"""
from dataclasses import dataclass
from typing import Protocol, Sequence

from backend.agents.rag.store.embedder import dot


@dataclass
class VectorHit:
    id: str
    document: str
    metadata: dict
    similarity: float  # 余弦相似度


class VectorStore(Protocol):
    async def upsert(self, ids: Sequence[str], embeddings: Sequence[Sequence[float]],
                     documents: Sequence[str], metadatas: Sequence[dict]) -> None: ...

    async def query(self, embedding: Sequence[float], top_k: int) -> list[VectorHit]: ...

    async def get_all(self) -> list[tuple[str, str, dict]]: ...

    async def count(self) -> int: ...

    async def delete(self, ids: Sequence[str]) -> None: ...


class InMemoryVectorStore:
    def __init__(self):
        self._rows: dict[str, tuple[list[float], str, dict]] = {}

    async def upsert(self, ids, embeddings, documents, metadatas) -> None:
        for row_id, vec, doc, meta in zip(ids, embeddings, documents, metadatas, strict=True):
            self._rows[row_id] = (list(vec), doc, dict(meta))

    async def query(self, embedding, top_k) -> list[VectorHit]:
        hits = [
            VectorHit(row_id, doc, dict(meta), dot(embedding, vec))
            for row_id, (vec, doc, meta) in self._rows.items()
        ]
        hits.sort(key=lambda h: (-h.similarity, h.id))
        return hits[:top_k]

    async def get_all(self) -> list[tuple[str, str, dict]]:
        return [(row_id, doc, dict(meta)) for row_id, (_, doc, meta) in self._rows.items()]

    async def count(self) -> int:
        return len(self._rows)

    async def delete(self, ids) -> None:
        for row_id in ids:
            self._rows.pop(row_id, None)
```

创建 `backend/agents/rag/store/llama_store.py`：

```python
"""
向量存储的生产实现：把 LlamaIndex 的向量库接口（BasePydanticVectorStore）适配成 VectorStore 协议，
后端用 Chroma。以后换成 LlamaIndex 支持的其他向量库，只需照着 from_chroma 另写一个工厂。

LlamaIndex 与 Chroma 有几处行为和直觉不一样，都已用真实的 Chroma 验证过（Task 0 的探针会再确认一次）：

1. 查询分数不是余弦相似度。LlamaIndex 的 Chroma 集成返回 exp(-距离)，cosine 空间里距离 = 1 - 余弦，
   所以余弦 = 1 + ln(分数)。去重、防泄漏、教材阈值都按余弦定义，必须换算回来
2. add 不是 upsert。id 已存在时 Chroma 静默忽略这次写入、保留旧内容，所以 upsert = 先删后加；
   同一批里 id 重复会报 DuplicateIDError，先在批内去重（后出现的覆盖先出现的，与内存实现一致）
3. delete_nodes([]) 会报错，空列表要提前返回
4. async_add、aquery 等异步方法只是直接调用同步方法，照样阻塞事件循环；
   所以一律调用同步方法，投递到向量库专属线程池
5. 不经过 VectorStoreIndex，直接操作向量库：向量由 Embedder 事先算好（去重要先拿到向量，
   一次查询的向量要在题库和教材库之间复用），这样不会重复向量化，也不会用到 LlamaIndex 的全局 Settings
   （它默认的 embedding 是 OpenAI）
"""
import asyncio
import math
from functools import partial
from pathlib import Path
from typing import Callable

from llama_index.core.schema import TextNode
from llama_index.core.vector_stores.types import BasePydanticVectorStore, VectorStoreQuery

from backend.agents.rag.store.vector_store import VectorHit
from backend.core.executors import get_vector_executor


def chroma_score_to_cosine(score: float) -> float:
    """LlamaIndex 的 Chroma 集成返回 exp(-cosine 距离)，换算回余弦相似度。"""
    return 1.0 + math.log(score)


class LlamaIndexVectorStore:
    def __init__(self, backend: BasePydanticVectorStore, score_to_similarity: Callable[[float], float]):
        if not backend.stores_text:
            # 例如 LlamaIndex 自带的 SimpleVectorStore：原文要另存在 docstore 里，查询结果拿不到文本
            raise ValueError(f"{type(backend).__name__} 不保存原文，不能作为 RAG 的向量库")
        self._backend = backend
        self._to_similarity = score_to_similarity

    @classmethod
    def from_chroma(cls, persist_dir: Path, collection_name: str) -> "LlamaIndexVectorStore":
        # 延迟导入：chromadb 导入失败时，只有真正要用它的地方报错
        import chromadb
        from llama_index.vector_stores.chroma import ChromaVectorStore

        persist_dir.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(persist_dir))
        collection = client.get_or_create_collection(collection_name, metadata={"hnsw:space": "cosine"})
        return cls(ChromaVectorStore(chroma_collection=collection), chroma_score_to_cosine)

    async def _run(self, fn, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(get_vector_executor(), partial(fn, *args, **kwargs))

    async def upsert(self, ids, embeddings, documents, metadatas) -> None:
        latest: dict[str, TextNode] = {}
        for row_id, vec, doc, meta in zip(ids, embeddings, documents, metadatas, strict=True):
            latest[row_id] = TextNode(id_=row_id, text=doc, embedding=list(vec), metadata=dict(meta))
        if latest:
            await self._run(self._replace, list(latest.values()))

    def _replace(self, nodes: list[TextNode]) -> None:
        # 查询恰好落在两步之间时，会暂时查不到这几条；入库是低频操作，可以接受
        self._backend.delete_nodes([node.node_id for node in nodes])
        self._backend.add(nodes)

    async def query(self, embedding, top_k) -> list[VectorHit]:
        result = await self._run(
            self._backend.query,
            VectorStoreQuery(query_embedding=list(embedding), similarity_top_k=top_k),
        )
        return [
            VectorHit(node.node_id, node.get_content(), dict(node.metadata), self._to_similarity(score))
            for node, score in zip(result.nodes or [], result.similarities or [])
        ]

    async def get_all(self) -> list[tuple[str, str, dict]]:
        nodes = await self._run(self._backend.get_nodes, node_ids=None)
        return [(node.node_id, node.get_content(), dict(node.metadata)) for node in nodes]

    async def count(self) -> int:
        # LlamaIndex 没有通用的计数接口；这个方法只在测试与统计里用，全量读取可以接受
        return len(await self.get_all())

    async def delete(self, ids) -> None:
        ids = list(ids)
        if ids:
            await self._run(self._backend.delete_nodes, ids)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_vector_store.py -v -rs`
Expected: chromadb 可用时 18 passed；不可用时 13 passed、5 skipped（4 条契约测试的 `llamaindex_chroma` 版本，加上持久化测试），跳过原因写明 chromadb 导入失败

- [ ] **Step 6: 提交**

```bash
git add backend/agents/rag/store/ backend/tests/rag/helpers.py backend/tests/rag/test_vector_store.py
git commit -m "feat: RAG 向量化与向量存储（内存实现与 LlamaIndex + Chroma 实现）"
```

---

## Task 3: 词法索引

> **这个任务做什么**：做一个常驻内存的索引，补上向量检索的短板——向量擅长找「意思相近」的内容，对具体术语和关键词却不够敏感。这个索引同时干三件事：① BM25 关键词召回：用 jieba 分词后按关键词匹配程度给题目打分（BM25 是搜索引擎里经典的关键词打分算法）；② 知识点倒排表：记录「某个知识点 → 有哪些题」，用来按知识点找题；③ 题目目录：按 id 直接取出完整题目，检索完不用再回查向量库。
>
> **做完之后**：给一段文本或一组知识点，能返回相关题目的 id 和分数。它是三路召回里「关键词」和「知识点」两路的底座。

同时承担三件事：BM25 关键词召回、知识点倒排表、题目目录。rank_bm25 不支持增量添加，所以每次整体重建，重建完成后用一次赋值替换快照。

分词用 jieba 的**搜索引擎模式**，这是验证时从失败的测试里发现的：普通分词下「分解因式」与「因式分解」一个共同 token 都没有，同一知识点的不同说法互相召回不到。

**Files:**
- Create: `backend/agents/rag/store/lexical_index.py`
- Test: `backend/tests/rag/test_lexical_index.py`

**Interfaces:**
- Consumes: Task 1 的 `QuestionItem`、`grade_rank`；Task 2 的 `VectorStore`（`rebuild_from_store` 里用）；`get_vector_executor()`
- Produces:
  - `LexicalIndex(extra_words=())`
  - `add_words(words)`、`tokenize(text) -> list[str]`
  - `size`（属性）、`contains(id)`、`get(id) -> QuestionItem | None`
  - `rebuild(items)`、`async rebuild_from_store(store) -> int`
  - `search_bm25(text, top_k) -> list[(id, score)]`
  - `search_knowledge_points(knowledge_points, top_k, max_grade_rank=None, difficulty=None) -> list[(id, score)]`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/rag/test_lexical_index.py`：

```python
from backend.agents.rag.models import QuestionItem
from backend.agents.rag.store.lexical_index import LexicalIndex
from backend.agents.rag.store.vector_store import InMemoryVectorStore


def _q(stem, kps, grade="七年级", difficulty="中等"):
    return QuestionItem(stem=stem, answer="略", knowledge_points=kps, grade=grade, difficulty=difficulty)


# BM25 的 idf 需要语料里有足够多篇文档：只有 2 篇时，只出现在其中 1 篇的词 idf 恰好为 0
CORPUS = [
    _q("解一元二次方程 x²-5x+6=0", ["一元二次方程"], grade="九年级"),
    _q("解方程 2x+3=7", ["一元一次方程"]),
    _q("因式分解：x²-9", ["因式分解"], grade="八年级"),
    _q("鸡兔同笼，头 35 个，脚 94 只，鸡兔各几只", ["鸡兔同笼问题"], grade="四年级", difficulty="困难"),
    _q("长方形长 5 厘米、宽 3 厘米，求周长", ["长方形周长"], grade="三年级", difficulty="简单"),
]


def test_whole_terms_and_sub_words_are_both_produced():
    tokens = LexicalIndex().tokenize("解一元二次方程x²-5x+6=0")
    assert "一元二次方程" in tokens  # 整词：标准术语精确匹配
    assert "方程" in tokens          # 子词：不同说法之间也能召回


def test_punctuation_and_symbols_are_dropped():
    tokens = LexicalIndex().tokenize("解方程：2x+3=7。")
    assert not {"：", "+", "=", "。"} & set(tokens)


def test_knowledge_points_join_the_dictionary_on_rebuild():
    index = LexicalIndex()
    index.rebuild(CORPUS)
    assert "鸡兔同笼问题" in index.tokenize("鸡兔同笼问题怎么列式")


def test_bm25_matches_a_different_wording_of_the_same_term():
    """「分解因式」与「因式分解」：普通分词下两者没有任何共同 token，这里必须能召回。"""
    index = LexicalIndex()
    index.rebuild(CORPUS)
    results = index.search_bm25("分解因式 a²-16", top_k=3)
    assert results and results[0][0] == CORPUS[2].id


def test_bm25_ranks_the_matching_word_problem_first():
    index = LexicalIndex()
    index.rebuild(CORPUS)
    results = index.search_bm25("鸡兔同笼问题：头 20 个、脚 54 只", top_k=3)
    assert results and results[0][0] == CORPUS[3].id


def test_empty_index_returns_nothing():
    index = LexicalIndex()
    assert index.search_bm25("方程", 5) == []
    assert index.search_knowledge_points(["方程"], 5) == []


def test_knowledge_point_search_orders_by_overlap_then_difficulty():
    a = _q("题A", ["一元一次方程", "移项"])
    b = _q("题B", ["一元一次方程"], difficulty="困难")
    c = _q("题C", ["一元一次方程"])
    index = LexicalIndex()
    index.rebuild([a, b, c])
    ids = [i for i, _ in index.search_knowledge_points(["一元一次方程", "移项"], top_k=3, difficulty="困难")]
    assert ids == [a.id, b.id, c.id]  # A 重合 2 个知识点；B 重合 1 个但难度一致加分


def test_knowledge_point_search_filters_higher_grades():
    index = LexicalIndex()
    index.rebuild(CORPUS)
    ids = [i for i, _ in index.search_knowledge_points(["一元二次方程", "一元一次方程"], top_k=5, max_grade_rank=6)]
    assert CORPUS[1].id in ids
    assert CORPUS[0].id not in ids  # 九年级的题不推给七年级学生


def test_rebuild_swaps_snapshot_atomically():
    index = LexicalIndex()
    index.rebuild(CORPUS[:2])
    old = index._snapshot
    index.rebuild(CORPUS)
    assert len(old.ids) == 2, "旧快照不能被原地修改：正在用它的检索必须看到完整一致的数据"
    assert index.size == len(CORPUS)


def test_duplicate_items_are_kept_once():
    index = LexicalIndex()
    index.rebuild([CORPUS[0], CORPUS[0]])
    assert index.size == 1


def test_get_and_contains():
    index = LexicalIndex()
    index.rebuild(CORPUS)
    assert index.contains(CORPUS[2].id)
    assert index.get(CORPUS[2].id).stem == CORPUS[2].stem
    assert index.get("q_missing") is None


async def test_rebuild_from_store_skips_bad_records():
    good = CORPUS[1]
    store = InMemoryVectorStore()
    await store.upsert([good.id, "bad"], [[1.0], [1.0]], [good.stem, "坏记录"],
                       [good.to_metadata(), {"answer": ""}])
    index = LexicalIndex()
    assert await index.rebuild_from_store(store) == 1
    assert index.contains(good.id)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_lexical_index.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.agents.rag.store.lexical_index'`

- [ ] **Step 3: 实现**

创建 `backend/agents/rag/store/lexical_index.py`：

```python
"""
词法索引：BM25 关键词召回 + 知识点倒排表 + 题目目录（id -> QuestionItem）。

- rank_bm25 不支持增量添加，只能整体重建。rebuild() 先在旁边建好一份完整快照，
  再用一次赋值替换引用；每个检索方法开头只读一次 self._snapshot，
  拿到的永远是完整一致的一份，不会看到只建了一半的索引。
- 用独立的 jieba.Tokenizer 实例而不是全局 jieba：词典改动不会影响进程里
  其他使用 jieba 的代码，测试之间也不会互相污染。
- 索引与查询都用 jieba 的搜索引擎模式（cut_for_search），它同时产出整词与子词：
  整词让「一元二次方程」这类标准术语精确匹配、得分更高；子词保证「分解因式」与
  「因式分解」、「鸡兔同笼问题」与「鸡兔同笼」这类不同说法仍能互相召回。
  用普通 cut 时，后者两两之间一个共同 token 都没有。
- 题目的知识点名称会被加进分词词典，让它们作为整词出现。
- 知识点倒排放在这里而不是依赖 Chroma 的 metadata 过滤：Chroma 的 metadata
  只接受标量，没法可靠地做「列表里包含某一项」的查询。
"""
import asyncio
import logging
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

import jieba
from pydantic import ValidationError
from rank_bm25 import BM25Okapi

from backend.agents.rag.models import QuestionItem, grade_rank
from backend.core.executors import get_vector_executor
from backend.middleware.logging import get_logger

jieba.setLogLevel(logging.WARNING)
logger = get_logger(__name__)

BUILTIN_MATH_TERMS = (
    "一元一次方程", "一元二次方程", "二元一次方程组", "分式方程", "不等式组",
    "因式分解", "整式运算", "有理数运算", "平方根", "立方根", "勾股定理",
    "全等三角形", "相似三角形", "平行四边形", "一次函数", "二次函数",
    "反比例函数", "概率统计", "行程应用题", "工程应用题",
)


@dataclass(frozen=True)
class _Snapshot:
    ids: tuple[str, ...] = ()
    bm25: BM25Okapi | None = None
    items: dict[str, QuestionItem] = field(default_factory=dict)
    kp_index: dict[str, frozenset[str]] = field(default_factory=dict)


def _is_meaningful(token: str) -> bool:
    """去掉只由空白、标点（P*）或符号（S*，如 + = ×）组成的 token：几乎每道题都有，对检索没有区分度。"""
    token = token.strip()
    if not token:
        return False
    return not all(unicodedata.category(ch).startswith(("P", "S")) for ch in token)


class LexicalIndex:
    def __init__(self, extra_words: Iterable[str] = ()):
        self._tokenizer = jieba.Tokenizer()
        self.add_words(BUILTIN_MATH_TERMS)
        self.add_words(extra_words)
        self._snapshot = _Snapshot()

    def add_words(self, words: Iterable[str]) -> None:
        for word in words:
            word = (word or "").strip()
            if len(word) >= 2:
                self._tokenizer.add_word(word)

    def tokenize(self, text: str) -> list[str]:
        return [t.strip() for t in self._tokenizer.lcut_for_search(text or "") if _is_meaningful(t)]

    @property
    def size(self) -> int:
        return len(self._snapshot.ids)

    def contains(self, item_id: str) -> bool:
        return item_id in self._snapshot.items

    def get(self, item_id: str) -> QuestionItem | None:
        return self._snapshot.items.get(item_id)

    def rebuild(self, items: Iterable[QuestionItem]) -> None:
        by_id: dict[str, QuestionItem] = {}
        for item in items:
            by_id.setdefault(item.id, item)
        self.add_words(kp for item in by_id.values() for kp in item.knowledge_points)

        ids = tuple(by_id)
        corpus = [self.tokenize(by_id[i].stem) for i in ids]
        # 空语料或全部分词为空时 BM25Okapi 会除零，直接不建
        bm25 = BM25Okapi(corpus) if any(corpus) else None

        kp_index: dict[str, set[str]] = {}
        for item_id in ids:
            for kp in by_id[item_id].knowledge_points:
                kp_index.setdefault(kp, set()).add(item_id)

        # 一次赋值完成替换；正在检索的调用手里仍是旧快照，互不影响
        self._snapshot = _Snapshot(
            ids=ids,
            bm25=bm25,
            items=by_id,
            kp_index={kp: frozenset(s) for kp, s in kp_index.items()},
        )

    async def rebuild_from_store(self, store) -> int:
        """从向量库全量读出题目重建索引，返回题目数。无法解析的记录跳过并记日志。"""
        items: list[QuestionItem] = []
        for record_id, document, metadata in await store.get_all():
            try:
                items.append(QuestionItem.from_record(document, metadata))
            except ValidationError as e:
                logger.warning("跳过无法解析的题目记录 %s：%s", record_id, e)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(get_vector_executor(), self.rebuild, items)
        return len(items)

    def search_bm25(self, text: str, top_k: int) -> list[tuple[str, float]]:
        snap = self._snapshot
        if snap.bm25 is None:
            return []
        tokens = self.tokenize(text)
        if not tokens:
            return []
        scores = snap.bm25.get_scores(tokens)
        ranked = [(snap.ids[i], float(s)) for i, s in enumerate(scores) if s > 0]
        ranked.sort(key=lambda p: (-p[1], p[0]))
        return ranked[:top_k]

    def search_knowledge_points(
        self,
        knowledge_points: Iterable[str],
        top_k: int,
        max_grade_rank: int | None = None,
        difficulty: str | None = None,
    ) -> list[tuple[str, float]]:
        """按与查询知识点的重合数打分，难度一致加 0.5；已知年级时排除更高年级的题。"""
        snap = self._snapshot
        overlap: dict[str, int] = {}
        for kp in knowledge_points:
            for item_id in snap.kp_index.get(kp, ()):
                overlap[item_id] = overlap.get(item_id, 0) + 1

        results: list[tuple[str, float]] = []
        for item_id, count in overlap.items():
            item = snap.items[item_id]
            rank = grade_rank(item.grade)
            if max_grade_rank is not None and rank is not None and rank > max_grade_rank:
                continue
            bonus = 0.5 if difficulty and item.difficulty == difficulty else 0.0
            results.append((item_id, count + bonus))
        results.sort(key=lambda p: (-p[1], p[0]))
        return results[:top_k]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_lexical_index.py -v`
Expected: 12 passed（首次运行 jieba 会构建词典缓存，稍慢）

- [ ] **Step 5: 提交**

```bash
git add backend/agents/rag/store/lexical_index.py backend/tests/rag/test_lexical_index.py
git commit -m "feat: RAG 词法索引（BM25 + 知识点倒排 + 快照替换）"
```

---

## Task 4: 入库任务表与隔离区表

> **这个任务做什么**：在 MySQL 里建两张表，并写好对应的数据访问层（DAO）。`rag_ingest_job` 记录每次入库任务：什么类型、来源是什么、状态（排队 / 运行中 / 成功 / 失败）、入库报告。`rag_quarantine` 是「隔离区」，存放裁判复核没通过的题，等管理员人工决定通过还是丢弃。测试用 SQLite 代替 MySQL，本地不装数据库也能跑。
>
> **做完之后**：能创建、更新、查询入库任务，能把题放进隔离区并修改处理状态。Task 17～19 的后台接口都建在这上面。

隔离区放 MySQL 而不是向量库：它是工作流状态，不是检索数据。测试用 SQLite 文件库代替 MySQL——DAO 只依赖会话工厂，与具体数据库无关。

**Files:**
- Create: `backend/model/rag.py`
- Create: `backend/dao/rag_mapper.py`
- Modify: `backend/tests/rag/conftest.py`（追加一段）
- Test: `backend/tests/rag/test_rag_mapper.py`

**Interfaces:**
- Consumes: Task 1 的 `QuestionItem`、`JudgeResult`
- Produces:
  - ORM `RagIngestJob`（表 `rag_ingest_job`）、`RagQuarantine`（表 `rag_quarantine`）
  - `RagJobMapper(session_factory)`：`async create(kind, source, params=None, created_by=None) -> str`、`mark_running(id)`、`mark_succeeded(id, report: dict)`、`mark_failed(id, error)`、`get(id) -> dict | None`、`fail_interrupted() -> int`
  - `RagQuarantineMapper(session_factory)`：`async put(item, result, job_id=None) -> int`、`list(status="pending", limit=20, offset=0) -> list[dict]`、`get(id) -> dict | None`、`set_status(id, status, reviewer_id) -> bool`
  - 隔离区条目 dict 的键：`id, question_id, item, judge, status, job_id, reviewed_by, create_time, review_time`
  - fixture `sqlite_session_factory`

- [ ] **Step 1: 追加 SQLite fixture**

在 `backend/tests/rag/conftest.py` 末尾追加：

```python
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def sqlite_session_factory(tmp_path):
    """用 SQLite 文件库代替 MySQL：DAO 只依赖会话工厂，与具体数据库无关。"""
    import backend.model.rag  # noqa: F401  注册表结构
    from backend.model import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'rag.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
```

- [ ] **Step 2: 写失败的测试**

创建 `backend/tests/rag/test_rag_mapper.py`：

```python
from backend.agents.rag.models import JudgeResult, QuestionItem
from backend.dao.rag_mapper import RagJobMapper, RagQuarantineMapper


async def test_job_lifecycle(sqlite_session_factory):
    jobs = RagJobMapper(sqlite_session_factory)
    job_id = await jobs.create("file", source="教材.pdf", params={"grade": "七年级"}, created_by=1)

    job = await jobs.get(job_id)
    assert job["status"] == "pending" and job["params"] == {"grade": "七年级"}

    await jobs.mark_running(job_id)
    await jobs.mark_succeeded(job_id, {"passed": 3})
    job = await jobs.get(job_id)
    assert job["status"] == "succeeded" and job["report"] == {"passed": 3}


async def test_unknown_job_is_none(sqlite_session_factory):
    assert await RagJobMapper(sqlite_session_factory).get("nope") is None


async def test_fail_interrupted_only_touches_unfinished_jobs(sqlite_session_factory):
    jobs = RagJobMapper(sqlite_session_factory)
    pending, running, done = [await jobs.create("file", source=s) for s in ("a", "b", "c")]
    await jobs.mark_running(running)
    await jobs.mark_succeeded(done, {})

    assert await jobs.fail_interrupted() == 2
    assert (await jobs.get(pending))["status"] == "failed"
    assert "重新提交" in (await jobs.get(running))["error"]
    assert (await jobs.get(done))["status"] == "succeeded"


def _item():
    return QuestionItem(stem="解方程 2x+3=7", answer="x=3", knowledge_points=["一元一次方程"])


async def test_quarantine_put_list_and_review(sqlite_session_factory):
    q = RagQuarantineMapper(sqlite_session_factory)
    entry_id = await q.put(
        _item(), JudgeResult(False, 0.2, ["答案错误"], judge_answer="x=2", judge_model="deepseek-v3"),
        job_id="job1",
    )

    [entry] = await q.list(status="pending")
    assert entry["id"] == entry_id
    assert entry["item"]["stem"] == "解方程 2x+3=7"
    assert entry["judge"]["reasons"] == ["答案错误"] and entry["job_id"] == "job1"

    assert await q.set_status(entry_id, "approved", reviewer_id=7) is True
    assert await q.list(status="pending") == []
    assert (await q.get(entry_id))["status"] == "approved"


async def test_review_is_single_shot(sqlite_session_factory):
    q = RagQuarantineMapper(sqlite_session_factory)
    entry_id = await q.put(_item(), JudgeResult(False, 0.1))
    assert await q.set_status(entry_id, "discarded", reviewer_id=1) is True
    assert await q.set_status(entry_id, "approved", reviewer_id=1) is False, "已处理过的条目不能再改状态"
    assert await q.set_status(99999, "approved", reviewer_id=1) is False
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_rag_mapper.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.dao.rag_mapper'`

- [ ] **Step 4: 实现**

创建 `backend/model/rag.py`：

```python
from sqlalchemy import JSON, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from backend.model import Base


class RagIngestJob(Base):
    """入库任务：后台接口与命令行共用，记录状态与入库报告。"""
    __tablename__ = "rag_ingest_job"

    id = Column(String(32), primary_key=True, comment="任务 id（uuid4 hex）")
    kind = Column(String(16), nullable=False, comment="file / generate")
    status = Column(String(16), nullable=False, default="pending",
                    comment="pending / running / succeeded / failed")
    source = Column(String(255), nullable=False, default="", comment="原始文件名或生成参数摘要")
    params = Column(JSON, nullable=True, comment="年级提示、生成参数等")
    report = Column(JSON, nullable=True, comment="IngestReport")
    error = Column(Text, nullable=True)
    created_by = Column(Integer, nullable=True, comment="发起任务的用户 id")
    create_time = Column(DateTime, default=func.current_timestamp())
    update_time = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())


class RagQuarantine(Base):
    """复核未通过的题目，等待人工处理。放 MySQL 而不是向量库：它是工作流状态，不是检索数据。"""
    __tablename__ = "rag_quarantine"

    id = Column(Integer, primary_key=True, autoincrement=True)
    question_id = Column(String(40), nullable=False, index=True)
    payload = Column(JSON, nullable=False, comment="QuestionItem 的全部字段")
    judge = Column(JSON, nullable=False, comment="JudgeResult")
    status = Column(String(16), nullable=False, default="pending",
                    comment="pending / approved / discarded")
    job_id = Column(String(32), nullable=True)
    reviewed_by = Column(Integer, nullable=True)
    create_time = Column(DateTime, default=func.current_timestamp())
    review_time = Column(DateTime, nullable=True)
```

创建 `backend/dao/rag_mapper.py`：

```python
import uuid
from datetime import datetime

from sqlalchemy import select, update

from backend.agents.rag.models import JudgeResult, QuestionItem
from backend.model.rag import RagIngestJob, RagQuarantine

JOB_PENDING = "pending"
JOB_RUNNING = "running"
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"

Q_PENDING = "pending"
Q_APPROVED = "approved"
Q_DISCARDED = "discarded"

INTERRUPTED_MESSAGE = "服务重启，任务中断；重新提交即可，已入库的部分会被自动跳过"


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _job_to_dict(job: RagIngestJob) -> dict:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "source": job.source,
        "params": job.params,
        "report": job.report,
        "error": job.error,
        "created_by": job.created_by,
        "create_time": _iso(job.create_time),
        "update_time": _iso(job.update_time),
    }


def _entry_to_dict(entry: RagQuarantine) -> dict:
    return {
        "id": entry.id,
        "question_id": entry.question_id,
        "item": entry.payload,
        "judge": entry.judge,
        "status": entry.status,
        "job_id": entry.job_id,
        "reviewed_by": entry.reviewed_by,
        "create_time": _iso(entry.create_time),
        "review_time": _iso(entry.review_time),
    }


class RagJobMapper:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def create(self, kind: str, source: str, params: dict | None = None,
                     created_by: int | None = None) -> str:
        job_id = uuid.uuid4().hex
        async with self.session_factory() as session:
            session.add(RagIngestJob(
                id=job_id, kind=kind, status=JOB_PENDING, source=(source or "")[:255],
                params=params, created_by=created_by,
            ))
            await session.commit()
        return job_id

    async def _set(self, job_id: str, **values) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(RagIngestJob)
                .where(RagIngestJob.id == job_id)
                .values(**values, update_time=datetime.now())
            )
            await session.commit()

    async def mark_running(self, job_id: str) -> None:
        await self._set(job_id, status=JOB_RUNNING)

    async def mark_succeeded(self, job_id: str, report: dict) -> None:
        await self._set(job_id, status=JOB_SUCCEEDED, report=report)

    async def mark_failed(self, job_id: str, error: str) -> None:
        await self._set(job_id, status=JOB_FAILED, error=(error or "")[:2000])

    async def get(self, job_id: str) -> dict | None:
        async with self.session_factory() as session:
            job = await session.get(RagIngestJob, job_id)
            return None if job is None else _job_to_dict(job)

    async def fail_interrupted(self) -> int:
        """
        服务启动时调用：上次没跑完的任务（待运行、运行中）一律标记为失败。
        不做断点续传——入库是幂等的，重新提交即可。返回被标记的任务数。
        """
        async with self.session_factory() as session:
            result = await session.execute(
                update(RagIngestJob)
                .where(RagIngestJob.status.in_([JOB_PENDING, JOB_RUNNING]))
                .values(status=JOB_FAILED, error=INTERRUPTED_MESSAGE, update_time=datetime.now())
            )
            await session.commit()
            return result.rowcount or 0


class RagQuarantineMapper:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def put(self, item: QuestionItem, result: JudgeResult, job_id: str | None = None) -> int:
        async with self.session_factory() as session:
            entry = RagQuarantine(
                question_id=item.id,
                payload=item.model_dump(),
                judge=result.to_dict(),
                status=Q_PENDING,
                job_id=job_id,
            )
            session.add(entry)
            await session.commit()
            return entry.id

    async def list(self, status: str = Q_PENDING, limit: int = 20, offset: int = 0) -> list[dict]:
        async with self.session_factory() as session:
            rows = await session.execute(
                select(RagQuarantine)
                .where(RagQuarantine.status == status)
                .order_by(RagQuarantine.id)
                .limit(limit)
                .offset(offset)
            )
            return [_entry_to_dict(e) for e in rows.scalars()]

    async def get(self, entry_id: int) -> dict | None:
        async with self.session_factory() as session:
            entry = await session.get(RagQuarantine, entry_id)
            return None if entry is None else _entry_to_dict(entry)

    async def set_status(self, entry_id: int, status: str, reviewer_id: int | None) -> bool:
        """
        只允许从 pending 变更，且只能改一次。条件更新保证并发下也只有一个人能处理成功。
        返回是否处理成功（条目不存在或已处理过时为 False）。
        """
        async with self.session_factory() as session:
            result = await session.execute(
                update(RagQuarantine)
                .where(RagQuarantine.id == entry_id, RagQuarantine.status == Q_PENDING)
                .values(status=status, reviewed_by=reviewer_id, review_time=datetime.now())
            )
            await session.commit()
            return (result.rowcount or 0) == 1
```

`set_status` 用的是条件更新（`WHERE id = ? AND status = 'pending'`），靠影响行数判断成败：两个管理员同时处理同一条时，数据库保证只有一个人能成功，不需要额外加锁。

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_rag_mapper.py -v`
Expected: 5 passed

- [ ] **Step 6: 提交**

```bash
git add backend/model/rag.py backend/dao/rag_mapper.py backend/tests/rag/conftest.py backend/tests/rag/test_rag_mapper.py
git commit -m "feat: RAG 入库任务表与隔离区表"
```

---

## Task 5: 文档加载

> **这个任务做什么**：入库流水线的第一步——把 PDF 或 Word 文件读成「一页一页的纯文本」。PDF 用 pypdf 按页取文字；Word 没有页的概念，用 python-docx 按顺序读段落和表格，每约 1500 字算一页。扫描件 PDF（全是图片，几乎读不出字）直接报错拒收，因为这次不做 OCR。这一步只管把字读出来，不管里面哪些是题目。
>
> **做完之后**：`load_document(path)` 返回 `PageText` 列表，交给 Task 6 做结构化。

只负责把文件变成「逐页文本」，不做任何理解。Word 没有「页」的概念，按约 1500 字切成一页，供后面的滑动窗口使用。没有文字层的 PDF 直接拒收。

测试需要真实的 PDF 与 Word 文件，但不想引入 reportlab 之类的依赖：Word 用 python-docx 现写；PDF 手写一个最小的合法文件（单页、Helvetica 字体、一行 ASCII 文字），pypdf 能正常读出来。

**Files:**
- Create: `backend/agents/rag/ingest/__init__.py`（空文件）
- Create: `backend/agents/rag/ingest/loaders.py`
- Modify: `backend/tests/rag/helpers.py`（追加一段）
- Test: `backend/tests/rag/test_loaders.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `load_document(path) -> list[PageText]`
  - `PageText(number, text)`，`number` 从 1 开始
  - `UnsupportedDocumentError(ValueError)`、常量 `SUPPORTED_SUFFIXES = (".pdf", ".docx")`、`DOCX_PAGE_CHARS`
  - 测试工具 `write_text_pdf(path, text)`、`write_docx(path, paragraphs, table_rows=())`

- [ ] **Step 1: 追加测试工具**

在 `backend/tests/rag/helpers.py` 末尾追加：

```python
def write_text_pdf(path, text: str) -> None:
    """
    手写一个最小的合法 PDF：单页、Helvetica 字体、一行文字。
    只支持 ASCII 文本，足够验证加载器；不引入 reportlab 之类的依赖。
    """
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
    out += b"startxref\n" + str(xref_at).encode() + b"\n%%EOF\n"
    path.write_bytes(bytes(out))


def write_docx(path, paragraphs, table_rows=()) -> None:
    """段落在前、表格在后；table_rows 是二维列表。"""
    import docx

    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    if table_rows:
        table = document.add_table(rows=len(table_rows), cols=len(table_rows[0]))
        for r, row in enumerate(table_rows):
            for c, value in enumerate(row):
                table.cell(r, c).text = value
    document.save(str(path))
```

- [ ] **Step 2: 写失败的测试**

创建 `backend/tests/rag/test_loaders.py`：

```python
import pytest

from backend.agents.rag.ingest.loaders import DOCX_PAGE_CHARS, UnsupportedDocumentError, load_document
from backend.tests.rag.helpers import write_docx, write_text_pdf


def test_pdf_text_is_extracted_per_page(tmp_path):
    path = tmp_path / "paper.pdf"
    write_text_pdf(path, "Solve the equation 2x + 3 = 7 and check your answer")
    pages = load_document(path)
    assert len(pages) == 1 and pages[0].number == 1
    assert "2x" in pages[0].text and "equation" in pages[0].text


def test_pdf_without_text_layer_is_rejected_as_scanned(tmp_path):
    path = tmp_path / "scan.pdf"
    write_text_pdf(path, "")
    with pytest.raises(UnsupportedDocumentError, match="扫描件"):
        load_document(path)


def test_docx_keeps_paragraph_and_table_order(tmp_path):
    path = tmp_path / "exercise.docx"
    write_docx(path, ["第一题：解方程 2x+3=7", "第二题：分解因式 x²-9"], table_rows=[["答案", "x=2"]])
    [page] = load_document(path)
    text = page.text
    assert text.index("第一题") < text.index("第二题") < text.index("答案 | x=2")


def test_docx_is_split_into_pages_of_bounded_size(tmp_path):
    path = tmp_path / "long.docx"
    write_docx(path, [f"第{i}题：" + "题干内容" * 50 for i in range(20)])
    pages = load_document(path)
    assert len(pages) > 1
    assert [p.number for p in pages] == list(range(1, len(pages) + 1))
    # 每页至多超出一个段落的长度
    assert all(len(p.text) <= DOCX_PAGE_CHARS + 250 for p in pages)


def test_empty_docx_is_rejected(tmp_path):
    path = tmp_path / "empty.docx"
    write_docx(path, [])
    with pytest.raises(UnsupportedDocumentError, match="没有可提取的文字"):
        load_document(path)


def test_unsupported_suffix_is_rejected(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello", encoding="utf-8")
    with pytest.raises(UnsupportedDocumentError, match="不支持的文件类型"):
        load_document(path)
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_loaders.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.agents.rag.ingest'`

- [ ] **Step 4: 实现**

创建空文件 `backend/agents/rag/ingest/__init__.py`，然后创建 `backend/agents/rag/ingest/loaders.py`：

```python
"""
文档加载：只负责把文件变成「逐页文本」，不做任何理解。

- PDF：pypdf 按页提取文字层
- Word：python-docx 没有「页」的概念。按文档顺序读取段落与表格，
  累积约 1500 字切成一「页」，供后面的滑动窗口使用
- 没有文字层的 PDF（扫描件）直接拒收：这次不做 OCR，
  与其悄悄入库一堆空内容，不如明确报错
"""
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_SUFFIXES = (".pdf", ".docx")
DOCX_PAGE_CHARS = 1500
MIN_CHARS_PER_PDF_PAGE = 20


class UnsupportedDocumentError(ValueError):
    pass


@dataclass
class PageText:
    number: int  # 从 1 开始
    text: str


def load_document(path: Path) -> list[PageText]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        pages = _load_pdf(path)
    elif suffix == ".docx":
        pages = _load_docx(path)
    else:
        raise UnsupportedDocumentError(
            f"不支持的文件类型：{suffix or '无后缀'}（仅支持 {'、'.join(SUPPORTED_SUFFIXES)}）"
        )
    if not pages:
        raise UnsupportedDocumentError(f"{path.name} 中没有可提取的文字")
    return pages


def _load_pdf(path: Path) -> list[PageText]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [PageText(i + 1, (page.extract_text() or "").strip()) for i, page in enumerate(reader.pages)]
    total_chars = sum(len(p.text) for p in pages)
    if pages and total_chars < MIN_CHARS_PER_PDF_PAGE * len(pages):
        raise UnsupportedDocumentError(
            f"{path.name} 平均每页可提取的文字不足 {MIN_CHARS_PER_PDF_PAGE} 字，疑似扫描件；当前不支持 OCR"
        )
    return [p for p in pages if p.text]


def _load_docx(path: Path) -> list[PageText]:
    import docx
    from docx.table import Table

    document = docx.Document(str(path))
    # python-docx >= 1.0 提供按文档顺序遍历段落与表格的 iter_inner_content；
    # 旧版本退化为「先段落后表格」，题目与答案的相对顺序可能被打乱
    if hasattr(document, "iter_inner_content"):
        blocks_in_order = document.iter_inner_content()
    else:
        blocks_in_order = [*document.paragraphs, *document.tables]

    blocks: list[str] = []
    for block in blocks_in_order:
        if isinstance(block, Table):
            for row in block.rows:
                line = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                if line:
                    blocks.append(line)
        else:
            text = block.text.strip()
            if text:
                blocks.append(text)

    pages: list[PageText] = []
    buffer: list[str] = []
    size = 0
    for text in blocks:
        if buffer and size + len(text) > DOCX_PAGE_CHARS:
            pages.append(PageText(len(pages) + 1, "\n".join(buffer)))
            buffer, size = [], 0
        buffer.append(text)
        size += len(text)
    if buffer:
        pages.append(PageText(len(pages) + 1, "\n".join(buffer)))
    return pages
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_loaders.py -v`
Expected: 6 passed

- [ ] **Step 6: 提交**

```bash
git add backend/agents/rag/ingest/ backend/tests/rag/helpers.py backend/tests/rag/test_loaders.py
git commit -m "feat: RAG 文档加载（PDF / Word，拒收扫描件）"
```

---

## Task 6: LLM 结构化

> **这个任务做什么**：入库流水线的第二步——让大模型把 Task 5 读出的原始页面文字，整理成一道道结构化的题目（`QuestionItem`）和教材讲解段（`KnowledgeChunk`）。每次喂给模型 2 页，下一次往后挪 1 页，相邻两次重叠一页，这样跨页的题至少在某一次里是完整的；重叠带来的重复交给 Task 9 去重。顺带做两件配套的事：① 写 `llm_json.py`，从模型回复里抠出 JSON（模型常在 JSON 前后加说明文字）；② 修改 skill 加载器，支持 `visibility: internal`，让 RAG 专用的 prompt 不出现在 ReAct Agent 的能力清单里。
>
> **做完之后**：给一份资料的页面列表，能得到其中的题目和讲解段。测试用 `ScriptedLLM` 预设模型回复，不调真实模型。

把页面文本整理成题目与知识讲解。按滑动窗口处理，每次 2 页、相邻重叠 1 页，保证跨页的题目至少在一个窗口里完整出现。

这是第一个 RAG 专用的 prompt，要先让 skill 加载器支持 `visibility: internal`：`list_skills()` 的结果会进 ReAct 主提示词的 Skill 清单，入库用的 prompt 对 Agent 没有用，列出来只会诱导它在处理用户请求时去加载。

**Files:**
- Modify: `backend/agents/skills/loader.py`（`SkillMeta`、`get_skill_meta`、`list_skills`）
- Create: `backend/agents/skills/rag_structuring/SKILL.md`
- Create: `backend/agents/rag/llm_json.py`
- Create: `backend/agents/rag/ingest/structurer.py`
- Modify: `backend/tests/rag/helpers.py`（追加一段）
- Test: `backend/tests/rag/test_structurer.py`

**Interfaces:**
- Consumes: Task 1 的 `QuestionItem`、`KnowledgeChunk`；Task 5 的 `PageText`
- Produces:
  - `list_skills(include_internal=False)`；`SkillMeta` 新增字段 `visibility`
  - `parse_json_object(text) -> dict | None`、`parse_json_array(text) -> list | None`
  - `make_windows(pages, window=2) -> list[list[PageText]]`
  - `StructureResult(questions, knowledge, errors)`
  - `Structurer(llm, window=2)`：`async structure(pages, source_ref, source_type, grade_hint=None) -> StructureResult`
  - 测试替身 `ScriptedLLM(*outputs)`、`FunctionLLM(fn)`，都有 `calls` 记录

- [ ] **Step 1: 追加 LLM 替身**

在 `backend/tests/rag/helpers.py` 末尾追加：

```python
from types import SimpleNamespace


class ScriptedLLM:
    """
    按调用顺序依次返回预设内容，模拟 LangChain 聊天模型的 ainvoke。
    预设内容是 Exception 实例时抛出它；calls 记录每次收到的消息列表。
    """

    def __init__(self, *outputs):
        self._outputs = list(outputs)
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if not self._outputs:
            raise AssertionError("ScriptedLLM 的预设输出已经用完")
        output = self._outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return SimpleNamespace(content=output)


class FunctionLLM:
    """根据收到的消息动态决定返回内容：fn(messages) -> str。"""

    def __init__(self, fn):
        self._fn = fn
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content=self._fn(messages))
```

- [ ] **Step 2: 写失败的测试**

创建 `backend/tests/rag/test_structurer.py`：

```python
import json

from backend.agents.rag.ingest.loaders import PageText
from backend.agents.rag.ingest.structurer import Structurer, make_windows
from backend.agents.rag.llm_json import parse_json_array, parse_json_object
from backend.agents.skills import get_skill_list_prompt, list_skills, load_skill, match_triggers
from backend.tests.rag.helpers import ScriptedLLM


# ---------- llm_json ----------

# 三个反引号用拼接得到：直接写在字符串里，这段代码被嵌进 Markdown 文档时会截断代码块
FENCE = "`" * 3


def test_parse_json_object_tolerates_fences_and_chatter():
    assert parse_json_object(f'好的：\n{FENCE}json\n{{"a": 1}}\n{FENCE}') == {"a": 1}


def test_parse_json_rejects_wrong_shape_and_garbage():
    assert parse_json_object("[1, 2]") is None
    assert parse_json_array('{"a": 1}') is None
    assert parse_json_object("{不是 json}") is None
    assert parse_json_object(None) is None


# ---------- skill 可见性 ----------

def test_internal_skills_are_hidden_from_the_agent():
    """RAG 的 prompt 不能出现在 ReAct 主提示词的 Skill 清单里，也不能被触发词命中。"""
    names = [m["name"] for m in list_skills()]
    assert "rag_structuring" not in names
    assert "rag_structuring" in [m["name"] for m in list_skills(include_internal=True)]
    assert "rag_structuring" not in get_skill_list_prompt()
    assert "rag_structuring" not in match_triggers("帮我整理一下教材")


def test_internal_skills_are_still_loadable_by_code():
    assert "完整的题目" in load_skill("rag_structuring")


def test_public_skills_are_unaffected():
    assert "question_variant" in [m["name"] for m in list_skills()]


# ---------- 滑动窗口 ----------

def _pages(n):
    return [PageText(i, f"第{i}页内容") for i in range(1, n + 1)]


def test_windows_overlap_by_one_page():
    assert [[p.number for p in w] for w in make_windows(_pages(1))] == [[1]]
    assert [[p.number for p in w] for w in make_windows(_pages(3))] == [[1, 2], [2, 3]]


def test_last_page_is_always_covered():
    assert [[p.number for p in w] for w in make_windows(_pages(4), window=3)] == [[1, 2, 3], [2, 3, 4]]


# ---------- 结构化 ----------

def _output(questions=(), knowledge=()):
    return json.dumps({"questions": list(questions), "knowledge": list(knowledge)}, ensure_ascii=False)


Q1 = {"stem": "解方程 2x+3=7", "answer": "x=2", "knowledge_points": ["一元一次方程"], "difficulty": "简单"}
Q2 = {"stem": "分解因式 x²-9", "answer": "(x+3)(x-3)", "knowledge_points": ["因式分解"], "grade": "八年级"}
K1 = {"text": "含有一个未知数、未知数的次数是 1 的方程叫一元一次方程。", "knowledge_points": ["一元一次方程"]}


async def test_overlapping_windows_are_deduplicated():
    """同一道题出现在两个相邻窗口里，只保留一份。"""
    llm = ScriptedLLM(_output([Q1], [K1]), _output([Q1, Q2], [K1]))
    result = await Structurer(llm).structure(_pages(3), source_ref="练习册.pdf", source_type="pdf",
                                             grade_hint="七年级")
    assert [q.stem for q in result.questions] == [Q1["stem"], Q2["stem"]]
    assert len(result.knowledge) == 1
    assert result.errors == []


async def test_source_fields_and_grade_hint_are_applied():
    llm = ScriptedLLM(_output([Q1, Q2]))
    result = await Structurer(llm).structure(_pages(2), source_ref="练习册.pdf", source_type="pdf",
                                             grade_hint="七年级")
    q1, q2 = result.questions
    assert (q1.source_type, q1.source_ref, q1.grade) == ("pdf", "练习册.pdf#p1", "七年级")
    assert q2.grade == "八年级", "原文给出的年级优先于年级提示"


async def test_incomplete_items_are_skipped_not_fatal():
    broken = {"stem": "只有题干没有答案"}
    llm = ScriptedLLM(_output([broken, Q1], ["不是对象的讲解"]))
    result = await Structurer(llm).structure(_pages(1), source_ref="a.pdf", source_type="pdf")
    assert [q.stem for q in result.questions] == [Q1["stem"]]
    assert len(result.errors) == 2


async def test_bad_llm_output_and_failures_are_recorded_and_skipped():
    llm = ScriptedLLM("抱歉，我无法处理", RuntimeError("超时"), _output([Q2]))
    result = await Structurer(llm).structure(_pages(4), source_ref="a.pdf", source_type="pdf")
    assert [q.stem for q in result.questions] == [Q2["stem"]]
    assert any("不是合法的 JSON" in e for e in result.errors)
    assert any("LLM 调用失败" in e for e in result.errors)


async def test_prompt_uses_the_skill_and_page_markers():
    llm = ScriptedLLM(_output())
    await Structurer(llm).structure(_pages(1), source_ref="a.pdf", source_type="pdf")
    system, human = llm.calls[0]
    assert "完整的题目" in system.content
    assert "【第1页】" in human.content
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_structurer.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.agents.rag.ingest.structurer'`

- [ ] **Step 4: 让 skill 加载器支持 internal**

`backend/agents/skills/loader.py` 中，`SkillMeta` 增加一个字段：

```python
class SkillMeta(TypedDict):
    name: str
    description: str
    triggers: list[str]
    version: str
    # public（默认）：出现在 ReAct 主提示词的 Skill 清单里，参与触发词匹配
    # internal：只供代码通过 load_skill 使用（如 RAG 入库的 prompt），对 Agent 不可见
    visibility: str
```

`get_skill_meta` 的返回值里加一行：

```python
        visibility=meta.get("visibility", "public"),
```

`list_skills` 整体替换为：

```python
def list_skills(include_internal: bool = False) -> list[SkillMeta]:
    """
    遍历 skills 目录，返回 Skill 的元数据列表。
    默认不含 internal skill：这份清单会进 ReAct 主提示词，internal skill 对 Agent 没有用，
    列出来只会诱导它在处理用户请求时去加载。
    """
    result: list[SkillMeta] = []
    for child in sorted(_SKILLS_ROOT.iterdir()):
        if not child.is_dir():
            continue
        if not (child / "SKILL.md").exists():
            continue
        try:
            meta = get_skill_meta(child.name)
        except Exception:
            continue
        if meta["visibility"] == "internal" and not include_internal:
            continue
        result.append(meta)
    return result
```

`get_skill_list_prompt()` 与 `match_triggers()` 都调用 `list_skills()`，改完之后它们自动排除 internal skill，不用另外改。

- [ ] **Step 5: 写 prompt**

创建 `backend/agents/skills/rag_structuring/SKILL.md`：

````markdown
---
name: rag_structuring
description: 入库专用：把教材、教辅、试卷的页面文本整理成结构化题目与知识讲解
visibility: internal
version: 1.0
---

# 角色
你是教研资料整理员。输入是从 PDF / Word 中提取的原始文本，可能有换行错乱、页眉页脚、页码等噪音。你要把它整理成结构化数据。

# 任务
从【待整理文本】中找出两类内容：
1. **完整的题目**：必须同时有题干和答案。题干或答案被页面截断、只出现了一半的，不要输出——相邻的窗口里会有完整版本。
2. **知识讲解**：概念定义、公式、方法说明、例题中讲方法的部分。按语义切分，每段不超过 400 字。

# 规则
- 题干和答案必须忠实于原文，不得改写数字，不得自行补全缺失的答案。
- 答案集中印在文末或书末的，只有在同一段文本里能确定对应关系时才配对；无法确定就不要输出这道题。
- 知识点使用人教版教材的标准名称，如「一元一次方程」「因式分解」。
- 难度只能是：简单、中等、困难。
- 年级：原文能判断就填，如「七年级」；不能判断就填 null。
- 页眉、页脚、页码、广告、目录一律忽略。

# 输出格式
只输出一个 JSON 对象，不要任何解释：
{
  "questions": [
    {"stem": "题干", "answer": "答案", "analysis": "解析，没有就留空", "question_type": "选择题/填空题/解答题/应用题/计算题", "difficulty": "中等", "knowledge_points": ["一元一次方程"], "grade": "七年级"}
  ],
  "knowledge": [
    {"text": "讲解原文", "knowledge_points": ["一元一次方程"], "grade": "七年级"}
  ]
}
没有可提取的内容时输出 {"questions": [], "knowledge": []}。
````

- [ ] **Step 6: 实现**

创建 `backend/agents/rag/llm_json.py`：

```python
"""从 LLM 输出中截取 JSON。模型常在 JSON 外面包一层 Markdown 围栏，或加一两句说明。"""
import json


def parse_json_object(text) -> dict | None:
    return _parse(text, "{", "}", dict)


def parse_json_array(text) -> list | None:
    return _parse(text, "[", "]", list)


def _parse(text, open_ch: str, close_ch: str, kind: type):
    if not isinstance(text, str):
        return None
    start, end = text.find(open_ch), text.rfind(close_ch)
    if start == -1 or end < start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, kind) else None
```

创建 `backend/agents/rag/ingest/structurer.py`：

```python
"""
LLM 结构化：页面文本 → QuestionItem / KnowledgeChunk。

按滑动窗口处理：每次读 window 页，相邻窗口重叠一页，跨页的题目至少会在某个窗口里完整出现。
重叠带来的重复靠 id 去重；同一道题在两个窗口里被转写得略有不同（哈希不同）的情况，
交给后面的近似去重兜住。
"""
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from backend.agents.rag.ingest.loaders import PageText
from backend.agents.rag.llm_json import parse_json_object
from backend.agents.rag.models import KnowledgeChunk, QuestionItem
from backend.agents.skills import load_skill

SKILL_NAME = "rag_structuring"


@dataclass
class StructureResult:
    questions: list[QuestionItem] = field(default_factory=list)
    knowledge: list[KnowledgeChunk] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def make_windows(pages: list[PageText], window: int = 2) -> list[list[PageText]]:
    """步长 window-1，相邻窗口重叠一页；保证最后一页一定被覆盖。"""
    if not pages:
        return []
    if len(pages) <= window:
        return [pages]
    step = max(1, window - 1)
    windows = [pages[start:start + window] for start in range(0, len(pages) - window + 1, step)]
    if windows[-1][-1].number != pages[-1].number:
        windows.append(pages[-window:])
    return windows


class Structurer:
    def __init__(self, llm, window: int = 2):
        self._llm = llm
        self._window = window

    async def structure(self, pages: list[PageText], source_ref: str, source_type: str,
                        grade_hint: str | None = None) -> StructureResult:
        result = StructureResult()
        seen_questions: set[str] = set()
        seen_knowledge: set[str] = set()
        system = load_skill(SKILL_NAME)

        for win in make_windows(pages, self._window):
            label = f"{source_ref} 第{win[0].number}-{win[-1].number}页"
            text = "\n\n".join(f"【第{p.number}页】\n{p.text}" for p in win)
            prompt = f"【年级提示】{grade_hint or '无'}\n【待整理文本】\n{text}"
            try:
                response = await self._llm.ainvoke([SystemMessage(content=system), HumanMessage(content=prompt)])
            except Exception as e:
                result.errors.append(f"{label}：LLM 调用失败（{e}）")
                continue

            data = parse_json_object(getattr(response, "content", None))
            if data is None:
                result.errors.append(f"{label}：输出不是合法的 JSON 对象")
                continue

            ref = f"{source_ref}#p{win[0].number}"
            for raw in data.get("questions") or []:
                try:
                    item = QuestionItem(**{
                        **raw,
                        "grade": raw.get("grade") or grade_hint,
                        "source_type": source_type,
                        "source_ref": ref,
                    })
                except (ValidationError, TypeError, AttributeError):
                    result.errors.append(f"{label}：有一道题字段不完整或格式不对，已跳过")
                    continue
                if item.id not in seen_questions:
                    seen_questions.add(item.id)
                    result.questions.append(item)

            for raw in data.get("knowledge") or []:
                try:
                    chunk = KnowledgeChunk(**{**raw, "grade": raw.get("grade") or grade_hint, "source_ref": ref})
                except (ValidationError, TypeError, AttributeError):
                    result.errors.append(f"{label}：有一段讲解格式不对，已跳过")
                    continue
                if chunk.id not in seen_knowledge:
                    seen_knowledge.add(chunk.id)
                    result.knowledge.append(chunk)
        return result
```

- [ ] **Step 7: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_structurer.py -v`
Expected: 12 passed。其中 `test_internal_skills_are_hidden_from_the_agent` 直接检查 ReAct 主提示词用到的 `get_skill_list_prompt()` 与 `match_triggers()`，确认改了加载器之后 Agent 看不到 internal skill。

- [ ] **Step 8: 提交**

```bash
git add backend/agents/skills/loader.py backend/agents/skills/rag_structuring/ backend/agents/rag/llm_json.py backend/agents/rag/ingest/structurer.py backend/tests/rag/helpers.py backend/tests/rag/test_structurer.py
git commit -m "feat: RAG 结构化入库，skill 支持 internal 可见性"
```

---

## Task 7: 种子题生成

> **这个任务做什么**：除了从资料里抽题，语料的另一个来源是让大模型直接出题。按「知识点 + 年级 + 难度 + 数量」批量生成，每次调用最多 10 道，要更多就分几次调用。prompt 放在 `rag_seed_generation/SKILL.md`。这个任务只负责生成，不判断题目对错。
>
> **做完之后**：`SeedGenerator.generate()` 返回一批 `QuestionItem`，在 Task 10 里送去复核。题库还是空的时候，可以靠它快速灌进一批数据。

按「知识点 + 年级 + 难度 + 数量」批量生成题目，每次调用最多 10 道。生成结果一律视为不可信，必须经过 Task 8 的复核才能入库。

**Files:**
- Create: `backend/agents/skills/rag_seed_generation/SKILL.md`
- Create: `backend/agents/rag/ingest/generator.py`
- Test: `backend/tests/rag/test_generator.py`

**Interfaces:**
- Consumes: Task 1 的 `QuestionItem`；Task 6 的 `parse_json_array`、`ScriptedLLM`
- Produces: `SeedGenerator(llm)`：`async generate(knowledge_point, grade, difficulty, n) -> (list[QuestionItem], list[str])`；常量 `BATCH_SIZE = 10`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/rag/test_generator.py`：

```python
import json

from backend.agents.rag.ingest.generator import BATCH_SIZE, SeedGenerator
from backend.tests.rag.helpers import ScriptedLLM


def _batch(count, kps=("一元一次方程",)):
    return json.dumps([
        {"stem": f"第{i}题：解方程 {i}x+1={i + 1}", "answer": "x=1", "knowledge_points": list(kps)}
        for i in range(count)
    ], ensure_ascii=False)


async def test_generated_items_are_marked_as_llm_source():
    items, errors = await SeedGenerator(ScriptedLLM(_batch(3))).generate("一元一次方程", "七年级", "简单", 3)
    assert errors == []
    assert len(items) == 3
    assert {i.source_type for i in items} == {"llm"}
    assert all(i.source_ref.startswith("llm:") for i in items)
    assert len({i.source_ref for i in items}) == 1, "同一次生成的题共享一个批次号"
    assert {(i.grade, i.difficulty) for i in items} == {("七年级", "简单")}


async def test_requested_knowledge_point_is_always_attached():
    items, _ = await SeedGenerator(ScriptedLLM(_batch(1, kps=["移项"]))).generate("一元一次方程", "七年级", "中等", 1)
    assert items[0].knowledge_points == ["一元一次方程", "移项"]


async def test_large_requests_are_split_into_batches():
    llm = ScriptedLLM(_batch(BATCH_SIZE), _batch(5))
    items, _ = await SeedGenerator(llm).generate("一元一次方程", "七年级", "中等", BATCH_SIZE + 5)
    assert len(llm.calls) == 2
    assert len(items) == BATCH_SIZE + 5
    assert "数量：5" in llm.calls[1][1].content


async def test_extra_items_beyond_request_are_dropped():
    items, _ = await SeedGenerator(ScriptedLLM(_batch(5))).generate("一元一次方程", "七年级", "中等", 2)
    assert len(items) == 2


async def test_invalid_output_is_reported():
    llm = ScriptedLLM("我来出几道题：……", json.dumps([{"stem": "缺答案"}], ensure_ascii=False))
    gen = SeedGenerator(llm)
    items, errors = await gen.generate("一元一次方程", "七年级", "中等", 1)
    assert items == [] and "不是合法的 JSON 数组" in errors[0]
    items, errors = await gen.generate("一元一次方程", "七年级", "中等", 1)
    assert items == [] and "字段不完整" in errors[0]


async def test_system_prompt_comes_from_the_skill():
    llm = ScriptedLLM(_batch(1))
    await SeedGenerator(llm).generate("一元一次方程", "七年级", "中等", 1)
    assert "原创题目" in llm.calls[0][0].content
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_generator.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.agents.rag.ingest.generator'`

- [ ] **Step 3: 实现**

创建 `backend/agents/skills/rag_seed_generation/SKILL.md`：

````markdown
---
name: rag_seed_generation
description: 入库专用：按知识点、年级、难度批量生成种子题
visibility: internal
version: 1.0
---

# 角色
你是一名拥有 5 年教学经验的人教版数学教师，正在为题库编写原创题目。

# 要求
1. 每道题都必须考查指定的知识点，符合指定年级的教材范围，不得超纲。
2. 每道题必须有明确、唯一的答案，并给出简要解析。
3. 同一批题目之间要有真正的差异：变换情境、数值、设问方式，不要只改一个数字。
4. 难度只能是：简单、中等、困难，按指定难度出题。

# 输出格式
只输出一个 JSON 数组，不要任何解释：
[
  {"stem": "题干", "answer": "答案", "analysis": "简要解析", "question_type": "解答题", "difficulty": "中等", "knowledge_points": ["一元一次方程"], "grade": "七年级"}
]
````

创建 `backend/agents/rag/ingest/generator.py`：

```python
"""
种子题生成：按「知识点 + 年级 + 难度 + 数量」批量生成题目。

生成结果一律视为不可信，后续必须经过第三方复核才能入库。
每次调用最多生成 BATCH_SIZE 道：一次要太多，模型输出会被截断或质量下滑。
"""
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from backend.agents.rag.llm_json import parse_json_array
from backend.agents.rag.models import QuestionItem
from backend.agents.skills import load_skill

SKILL_NAME = "rag_seed_generation"
BATCH_SIZE = 10


class SeedGenerator:
    def __init__(self, llm):
        self._llm = llm

    async def generate(self, knowledge_point: str, grade: str, difficulty: str,
                       n: int) -> tuple[list[QuestionItem], list[str]]:
        """返回 (题目列表, 错误信息列表)。"""
        batch_id = uuid.uuid4().hex[:8]
        system = load_skill(SKILL_NAME)
        items: list[QuestionItem] = []
        errors: list[str] = []

        remaining = n
        while remaining > 0:
            size = min(BATCH_SIZE, remaining)
            remaining -= size
            prompt = f"知识点：{knowledge_point}\n年级：{grade}\n难度：{difficulty}\n数量：{size}"
            try:
                response = await self._llm.ainvoke([SystemMessage(content=system), HumanMessage(content=prompt)])
            except Exception as e:
                errors.append(f"生成调用失败（{e}）")
                continue
            data = parse_json_array(getattr(response, "content", None))
            if data is None:
                errors.append("生成结果不是合法的 JSON 数组")
                continue

            for raw in data[:size]:
                try:
                    kps = list(raw.get("knowledge_points") or [])
                    if knowledge_point not in kps:
                        kps.insert(0, knowledge_point)  # 按指定知识点出的题，必须带上它
                    items.append(QuestionItem(**{
                        **raw,
                        "knowledge_points": kps,
                        "difficulty": raw.get("difficulty") or difficulty,
                        "grade": raw.get("grade") or grade,
                        "source_type": "llm",
                        "source_ref": f"llm:{batch_id}",
                    }))
                except (ValidationError, TypeError, AttributeError):
                    errors.append("有一道生成的题字段不完整或格式不对，已跳过")
        return items, errors
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_generator.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add backend/agents/skills/rag_seed_generation/ backend/agents/rag/ingest/generator.py backend/tests/rag/test_generator.py
git commit -m "feat: RAG 种子题批量生成"
```

---

## Task 8: 第三方复核（入库闸门）

> **这个任务做什么**：入库前的质量把关。用另一家的大模型当「裁判」逐道检查：裁判先自己独立把题做一遍，再和题目给的答案比对，同时检查题干是否完整、是否超出年级范围、知识点标注是否准确。四项全过、且总分不低于 0.8 才算通过；出现任何意外（调用失败、输出解析不了）一律判不通过，送进隔离区。另外要校验裁判和出题的模型不是同一家（比如都是 qwen），否则裁判会偏袒同家族模型的输出。
>
> **做完之后**：`QuestionJudge.judge(item)` 返回 `JudgeResult`；裁判没配置或配成同家族模型时，在构建阶段就报错并说明原因。

三个关键设计：
1. **先让裁判独立解题，再与给定答案比对**，而不是问它「这个答案对不对」——后一种问法会诱导模型附和。
2. **失败即拒**：裁判调用失败、输出无法解析、布尔项不是真正的 `true`，题目都进隔离区而不是放行。
3. **裁判必须与生成模型不同家族**，否则失去第三方的意义。

**Files:**
- Create: `backend/agents/skills/rag_judge/SKILL.md`
- Create: `backend/agents/rag/ingest/judge.py`
- Test: `backend/tests/rag/test_judge.py`

**Interfaces:**
- Consumes: Task 0 的 `RagSettings`、fixture `rag_settings`；Task 1 的 `QuestionItem`、`JudgeResult`
- Produces:
  - `JudgeConfigError(RuntimeError)`
  - `model_family(name) -> str`、`ensure_third_party(judge_model, generator_model)`
  - `build_judge_llm(settings)`：返回 ChatOpenAI；配置不全或同家族时抛 `JudgeConfigError`
  - `QuestionJudge(llm, model_name, pass_score=0.8)`：`async judge(item) -> JudgeResult`，不抛异常
  - 常量 `REQUIRED_CHECKS`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/rag/test_judge.py`：

```python
import dataclasses
import json

import pytest

from backend.agents.rag.ingest.judge import (
    JudgeConfigError,
    QuestionJudge,
    build_judge_llm,
    ensure_third_party,
    model_family,
)
from backend.agents.rag.models import QuestionItem
from backend.tests.rag.helpers import ScriptedLLM

ITEM = QuestionItem(stem="解方程 2x+3=7", answer="x=2", grade="七年级", knowledge_points=["一元一次方程"])


def _verdict(**overrides):
    data = {
        "judge_answer": "x=2", "answer_consistent": True, "stem_complete": True,
        "within_grade": True, "knowledge_points_accurate": True, "score": 0.9, "reasons": [],
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


@pytest.mark.parametrize("name, family", [
    ("qwen-plus", "qwen"), ("qwen3-max", "qwen"), ("glm-5", "glm"),
    ("deepseek-v3", "deepseek"), ("moonshot-v1-8k", "kimi"), ("", ""),
])
def test_model_family(name, family):
    assert model_family(name) == family


def test_same_family_judge_is_refused():
    with pytest.raises(JudgeConfigError, match="同一家族"):
        ensure_third_party("qwen3-max", "qwen-plus")


def test_missing_judge_model_is_refused():
    with pytest.raises(JudgeConfigError, match="JUDGE_MODEL"):
        ensure_third_party(None, "qwen-plus")


def test_build_judge_llm_requires_endpoint_and_key(rag_settings):
    with pytest.raises(JudgeConfigError, match="JUDGE_API_URL"):
        build_judge_llm(dataclasses.replace(rag_settings, judge_api_key=None))


def test_build_judge_llm_accepts_a_third_party_model(rag_settings):
    assert build_judge_llm(rag_settings) is not None


async def test_passes_when_every_check_is_true():
    result = await QuestionJudge(ScriptedLLM(_verdict()), "deepseek-v3").judge(ITEM)
    assert result.passed and result.score == 0.9
    assert result.judge_answer == "x=2" and result.judge_model == "deepseek-v3"


async def test_any_failed_check_rejects():
    result = await QuestionJudge(ScriptedLLM(_verdict(answer_consistent=False, reasons=["答案应为 x=2"])),
                                 "deepseek-v3").judge(ITEM)
    assert not result.passed
    assert "答案应为 x=2" in result.reasons
    assert "未通过检查项：answer_consistent" in result.reasons


async def test_low_score_rejects():
    result = await QuestionJudge(ScriptedLLM(_verdict(score=0.5)), "deepseek-v3", pass_score=0.8).judge(ITEM)
    assert not result.passed
    assert any("低于通过线" in r for r in result.reasons)


async def test_string_true_is_not_true():
    """布尔检查必须是真正的 true，模型输出 "true" 字符串也要拒——宁可错杀。"""
    result = await QuestionJudge(ScriptedLLM(_verdict(within_grade="true")), "deepseek-v3").judge(ITEM)
    assert not result.passed


async def test_unparseable_output_fails_closed():
    result = await QuestionJudge(ScriptedLLM("这道题没问题，通过。"), "deepseek-v3").judge(ITEM)
    assert not result.passed and "无法解析" in result.reasons[0]


async def test_llm_failure_fails_closed():
    result = await QuestionJudge(ScriptedLLM(TimeoutError("超时")), "deepseek-v3").judge(ITEM)
    assert not result.passed and "裁判调用失败" in result.reasons[0]


async def test_judge_is_asked_to_solve_before_comparing():
    llm = ScriptedLLM(_verdict())
    await QuestionJudge(llm, "deepseek-v3").judge(ITEM)
    system, human = llm.calls[0]
    assert "先独立解题" in system.content
    assert "【题干】解方程 2x+3=7" in human.content and "【给定答案】x=2" in human.content
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_judge.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.agents.rag.ingest.judge'`

- [ ] **Step 3: 实现**

创建 `backend/agents/skills/rag_judge/SKILL.md`：

````markdown
---
name: rag_judge
description: 入库专用：由第三方模型复核题目的正确性与规范性
visibility: internal
version: 1.0
---

# 角色
你是严格的数学题审核员。你的结论决定这道题能否进入题库；错题一旦入库，会被当作参考真题反复使用，所以宁可错杀，不可放过。

# 审核步骤（必须按顺序进行）
1. **先独立解题**：只看题干，不看【给定答案】，自己完整地解一遍，得出你的答案。
2. **再比对**：把你的答案与【给定答案】比较，判断是否一致。等价形式视为一致，例如 1/2 与 0.5。
3. 检查题干是否完整、无歧义、条件是否充分。
4. 检查是否超出【年级】对应的人教版教材范围；年级为「未知」时此项视为通过。
5. 检查【标注知识点】是否确实是这道题考查的知识点。

# 输出格式
只输出一个 JSON 对象，不要任何解释：
{"judge_answer": "你独立解出的答案", "answer_consistent": true, "stem_complete": true, "within_grade": true, "knowledge_points_accurate": true, "score": 0.9, "reasons": ["不通过或扣分的原因；全部通过时可以为空"]}
score 是 0 到 1 之间的总体质量评分。
````

创建 `backend/agents/rag/ingest/judge.py`：

```python
"""
第三方模型复核（入库闸门）。

三个关键设计：
1. 先让裁判独立解题，再与给定答案比对——而不是问它「这个答案对不对」，
   后一种问法会诱导模型附和给定答案。
2. 失败即拒（fail closed）：裁判调用失败、输出无法解析、布尔项不是真正的 true，
   题目都进隔离区而不是放行。闸门的意义就在于不确定时不放行。
3. 裁判必须与生成模型不同家族：同家族模型会系统性地偏向自己的输出。
"""
import re

from langchain_core.messages import HumanMessage, SystemMessage

from backend.agents.rag.llm_json import parse_json_object
from backend.agents.rag.models import JudgeResult, QuestionItem
from backend.agents.skills import load_skill

SKILL_NAME = "rag_judge"
REQUIRED_CHECKS = ("answer_consistent", "stem_complete", "within_grade", "knowledge_points_accurate")
_FAMILY_ALIASES = {"moonshot": "kimi", "chatglm": "glm", "tongyi": "qwen"}


class JudgeConfigError(RuntimeError):
    pass


def model_family(model_name: str | None) -> str:
    """
    取模型名开头的字母段作为家族：qwen-plus、qwen3-max -> qwen；glm-5 -> glm；
    deepseek-v3 -> deepseek。这是启发式规则，目的是拦住明显的配置错误。
    """
    match = re.match(r"[a-z]+", (model_name or "").strip().lower())
    family = match.group(0) if match else ""
    return _FAMILY_ALIASES.get(family, family)


def ensure_third_party(judge_model: str | None, generator_model: str | None) -> None:
    if not judge_model:
        raise JudgeConfigError("未配置 JUDGE_MODEL，入库闸门无法工作")
    judge_family = model_family(judge_model)
    if judge_family and judge_family == model_family(generator_model):
        raise JudgeConfigError(
            f"裁判模型 {judge_model} 与生成模型 {generator_model} 属于同一家族（{judge_family}），"
            f"失去了第三方复核的意义"
        )


def build_judge_llm(settings):
    """按独立配置构建裁判模型。配置不完整或与生成模型同家族时抛 JudgeConfigError。"""
    ensure_third_party(settings.judge_model, settings.generator_model)
    if not settings.judge_api_url or not settings.judge_api_key:
        raise JudgeConfigError("未配置 JUDGE_API_URL / JUDGE_API_KEY，入库闸门无法工作")
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.judge_model,
        api_key=settings.judge_api_key,
        base_url=settings.judge_api_url,
        temperature=0,
    )


class QuestionJudge:
    def __init__(self, llm, model_name: str, pass_score: float = 0.8):
        self._llm = llm
        self._model_name = model_name
        self._pass_score = pass_score

    def _reject(self, reason: str) -> JudgeResult:
        return JudgeResult(False, 0.0, [reason], judge_model=self._model_name)

    async def judge(self, item: QuestionItem) -> JudgeResult:
        prompt = (
            f"【题干】{item.stem}\n"
            f"【给定答案】{item.answer}\n"
            f"【年级】{item.grade or '未知'}\n"
            f"【标注知识点】{'、'.join(item.knowledge_points) or '无'}"
        )
        try:
            response = await self._llm.ainvoke([
                SystemMessage(content=load_skill(SKILL_NAME)),
                HumanMessage(content=prompt),
            ])
        except Exception as e:
            return self._reject(f"裁判调用失败：{e}")

        data = parse_json_object(getattr(response, "content", None))
        if data is None:
            return self._reject("裁判输出无法解析为 JSON")

        # 必须是真正的 true：字符串 "true"、1、缺失都算不通过
        failed = [key for key in REQUIRED_CHECKS if data.get(key) is not True]
        try:
            score = float(data.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        score = max(0.0, min(1.0, score))

        reasons = [str(r) for r in (data.get("reasons") or []) if str(r).strip()]
        reasons += [f"未通过检查项：{key}" for key in failed]
        if score < self._pass_score:
            reasons.append(f"评分 {score:.2f} 低于通过线 {self._pass_score}")

        return JudgeResult(
            passed=not failed and score >= self._pass_score,
            score=score,
            reasons=reasons,
            judge_answer=str(data.get("judge_answer", "")),
            judge_model=self._model_name,
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_judge.py -v`
Expected: 17 passed

- [ ] **Step 5: 提交**

```bash
git add backend/agents/skills/rag_judge/ backend/agents/rag/ingest/judge.py backend/tests/rag/test_judge.py
git commit -m "feat: RAG 第三方复核闸门（独立解题比对，失败即拒）"
```

---

## Task 9: 去重

> **这个任务做什么**：在花钱调用裁判之前，先把重复的题筛掉。分两级：① 精确去重：规范化后文本相同的题 id 也相同，直接比 id；② 近似去重：比较向量相似度，和库里已有的题、同一批里已经接受的题都比一遍，相似度不低于 0.95 就算重复。近似去重主要兜住两种情况：同一道题在两个重叠窗口里被转写得略有差别，以及模型批量出题时换汤不换药。
>
> **做完之后**：两个函数分别返回去重后的题目和被去掉的数量，供 Task 10 的流水线调用。

两级去重都放在复核之前——重复的题不值得再花一次裁判调用。近似去重用来兜住「同一道题在两个重叠窗口里被转写得略有不同」和「LLM 批量生成时换汤不换药」。

**Files:**
- Create: `backend/agents/rag/ingest/dedup.py`
- Test: `backend/tests/rag/test_dedup.py`

**Interfaces:**
- Consumes: Task 1 的 `QuestionItem`；Task 2 的 `dot`、`InMemoryVectorStore`、`HashingEmbedder`
- Produces:
  - `drop_exact_duplicates(items, exists: Callable[[str], bool]) -> (list[QuestionItem], int)`
  - `async drop_near_duplicates(items, embeddings, store, threshold) -> (list[QuestionItem], list[list[float]], int)`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/rag/test_dedup.py`：

```python
from backend.agents.rag.ingest.dedup import drop_exact_duplicates, drop_near_duplicates
from backend.agents.rag.models import QuestionItem
from backend.agents.rag.store.vector_store import InMemoryVectorStore
from backend.tests.rag.helpers import HashingEmbedder

LONG = "已知长方形的长是宽的2倍，周长是36厘米，求长和宽各是多少"


def _q(stem):
    return QuestionItem(stem=stem, answer="略")


def test_exact_duplicates_within_batch_and_against_store():
    existing = _q("解方程 2x+3=7")
    items = [_q("解方程2x+3=7"), _q("分解因式 x²-9"), _q("分解因式 x² - 9")]
    kept, duplicates = drop_exact_duplicates(items, exists=lambda i: i == existing.id)
    assert [k.stem for k in kept] == ["分解因式 x²-9"]
    assert duplicates == 2


async def test_near_duplicates_within_batch():
    embedder = HashingEmbedder()
    items = [_q(LONG), _q(LONG + "？"), _q("计算半径为3的圆的面积")]
    vectors = await embedder.embed([i.stem for i in items])
    kept, kept_vectors, duplicates = await drop_near_duplicates(items, vectors, InMemoryVectorStore(), 0.9)
    assert [k.stem for k in kept] == [LONG, "计算半径为3的圆的面积"]
    assert len(kept_vectors) == 2 and duplicates == 1


async def test_near_duplicates_against_store():
    embedder = HashingEmbedder()
    store = InMemoryVectorStore()
    [stored_vec] = await embedder.embed([LONG])
    await store.upsert(["q_old"], [stored_vec], [LONG], [{}])

    items = [_q(LONG + "？")]
    vectors = await embedder.embed([items[0].stem])
    kept, _, duplicates = await drop_near_duplicates(items, vectors, store, 0.9)
    assert kept == [] and duplicates == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_dedup.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.agents.rag.ingest.dedup'`

- [ ] **Step 3: 实现**

创建 `backend/agents/rag/ingest/dedup.py`：

```python
"""
两级去重，都放在复核之前——重复的题不值得再花一次裁判调用。

1. 精确去重：id（规范化题干的哈希）相同即重复，包括与库中已有的题、与同批次的题。
2. 近似去重：与库中已有题目、或同批次已保留题目的余弦相似度不低于阈值即重复。
   用来兜住「同一道题在两个重叠窗口里被转写得略有不同」和「LLM 批量生成时换汤不换药」。
"""
from typing import Callable, Sequence

from backend.agents.rag.models import QuestionItem
from backend.agents.rag.store.embedder import dot


def drop_exact_duplicates(items: Sequence[QuestionItem],
                          exists: Callable[[str], bool]) -> tuple[list[QuestionItem], int]:
    """返回 (保留的题目, 重复数)。exists(id) 判断库里是否已有。"""
    seen: set[str] = set()
    kept: list[QuestionItem] = []
    duplicates = 0
    for item in items:
        if item.id in seen or exists(item.id):
            duplicates += 1
            continue
        seen.add(item.id)
        kept.append(item)
    return kept, duplicates


async def drop_near_duplicates(items: Sequence[QuestionItem], embeddings: Sequence[Sequence[float]],
                               store, threshold: float
                               ) -> tuple[list[QuestionItem], list[list[float]], int]:
    """返回 (保留的题目, 对应的向量, 重复数)。向量须已归一化。"""
    kept: list[QuestionItem] = []
    kept_vectors: list[list[float]] = []
    duplicates = 0
    for item, vector in zip(items, embeddings, strict=True):
        if any(dot(vector, other) >= threshold for other in kept_vectors):
            duplicates += 1
            continue
        hits = await store.query(vector, top_k=1)
        if hits and hits[0].similarity >= threshold:
            duplicates += 1
            continue
        kept.append(item)
        kept_vectors.append(list(vector))
    return kept, kept_vectors, duplicates
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_dedup.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add backend/agents/rag/ingest/dedup.py backend/tests/rag/test_dedup.py
git commit -m "feat: RAG 精确与近似两级去重"
```

---

## Task 10: 入库服务编排

> **这个任务做什么**：把 Task 2～9 做好的零件串成完整的入库流水线，封装成 `IngestService`。对外提供几个入口：导入一个文件、按知识点生成并入库、直接入库一批题、入库教材讲解段、把隔离区里人工通过的题直接入库。内部流程是：精确去重 → 向量化 → 近似去重 → 裁判复核 → 通过的写入向量库并重建词法索引，没通过的送进隔离区。每次调用返回一份 `IngestReport`，统计抽出多少、去重多少、通过和拒绝各多少。
>
> **做完之后**：入库的全部逻辑都集中在这一个类里，命令行（Task 11）和后台接口（Task 18）都只调用它，不重复写逻辑。这是阶段二的核心任务。

命令行与后台接口共用的唯一入口，把前面的零件串成流水线：

```
精确去重 → 向量化 → 近似去重 → 第三方复核 → 写入向量库 → 重建词法索引
                                    └→ 不通过的进隔离区
```

`ingest_file` 的 `source_name` 参数是为 Task 18 准备的：后台上传的文件落盘时改用任务 id 命名，要把原始文件名单独传进来写进题目来源，否则来源里记的就是一串 id。

**Files:**
- Create: `backend/agents/rag/ingest/service.py`
- Modify: `backend/tests/rag/helpers.py`（追加一段）
- Test: `backend/tests/rag/test_ingest_service.py`

**Interfaces:**
- Consumes: Task 2-9 的全部组件
- Produces:
  - `IngestReport`（dataclass）：`extracted, exact_duplicates, near_duplicates, passed, rejected, knowledge_added, knowledge_duplicates, errors`；方法 `merge(other)`、`to_dict()`
  - 类型 `QuarantineFn = Callable[[QuestionItem, JudgeResult], Awaitable]`
  - `IngestService(*, settings, embedder, question_store, knowledge_store, lexical_index, structurer=None, generator=None, judge=None, quarantine=None)`
    - `async ingest_file(path, grade_hint=None, source_name=None) -> IngestReport`
    - `async generate_and_ingest(knowledge_point, grade, difficulty, n) -> IngestReport`
    - `async ingest_questions(items) -> IngestReport`：没有配置裁判或隔离区时抛 `JudgeConfigError`
    - `async ingest_knowledge(chunks) -> IngestReport`
    - `async approve(item)`：跳过裁判直接入库
  - 测试替身 `FakeJudge(reject_stems=(), delay=0.0)`、`ListQuarantine(fail=False)`、工厂 `make_ingest_service(settings, *, structurer_llm=None, generator_llm=None, judge=None, quarantine=None) -> (service, parts)`

- [ ] **Step 1: 追加测试替身**

在 `backend/tests/rag/helpers.py` 末尾追加：

```python
import asyncio
from types import SimpleNamespace

from backend.agents.rag.models import JudgeResult


class FakeJudge:
    """reject_stems 里的题判为不通过，其余通过；同时记录调用次数与最大并发数。"""

    def __init__(self, reject_stems=(), delay: float = 0.0):
        self.reject_stems = set(reject_stems)
        self.delay = delay
        self.calls = 0
        self.max_concurrency = 0
        self._running = 0

    async def judge(self, item):
        self.calls += 1
        self._running += 1
        self.max_concurrency = max(self.max_concurrency, self._running)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            passed = item.stem not in self.reject_stems
            reasons = [] if passed else ["答案与独立解答不一致"]
            return JudgeResult(passed, 0.9 if passed else 0.3, reasons, judge_model="fake-judge")
        finally:
            self._running -= 1


class ListQuarantine:
    """隔离区替身：把 (题目, 复核结果) 记在列表里；fail=True 时模拟数据库故障。"""

    def __init__(self, fail: bool = False):
        self.entries = []
        self.fail = fail

    async def __call__(self, item, result):
        if self.fail:
            raise RuntimeError("数据库连不上")
        self.entries.append((item, result))


def make_ingest_service(settings, *, structurer_llm=None, generator_llm=None, judge=None, quarantine=None):
    """用全套离线替身组装入库服务，返回 (service, parts)。"""
    from backend.agents.rag.ingest.generator import SeedGenerator
    from backend.agents.rag.ingest.service import IngestService
    from backend.agents.rag.ingest.structurer import Structurer
    from backend.agents.rag.store.lexical_index import LexicalIndex
    from backend.agents.rag.store.vector_store import InMemoryVectorStore

    parts = SimpleNamespace(
        embedder=HashingEmbedder(),
        questions=InMemoryVectorStore(),
        knowledge=InMemoryVectorStore(),
        index=LexicalIndex(),
        judge=judge or FakeJudge(),
        quarantine=quarantine or ListQuarantine(),
    )
    service = IngestService(
        settings=settings,
        embedder=parts.embedder,
        question_store=parts.questions,
        knowledge_store=parts.knowledge,
        lexical_index=parts.index,
        structurer=Structurer(structurer_llm) if structurer_llm else None,
        generator=SeedGenerator(generator_llm) if generator_llm else None,
        judge=parts.judge,
        quarantine=parts.quarantine,
    )
    return service, parts
```

- [ ] **Step 2: 写失败的测试**

创建 `backend/tests/rag/test_ingest_service.py`：

```python
import json

import pytest

from backend.agents.rag.ingest.judge import JudgeConfigError
from backend.agents.rag.ingest.service import IngestReport, IngestService
from backend.agents.rag.models import KnowledgeChunk, QuestionItem
from backend.tests.rag.helpers import FakeJudge, ListQuarantine, ScriptedLLM, make_ingest_service, write_docx


def _q(stem, kps=("一元一次方程",)):
    return QuestionItem(stem=stem, answer="略", knowledge_points=list(kps), grade="七年级")


GOOD = [_q("解方程 2x+3=7"), _q("解方程 5x-1=9"), _q("分解因式 x²-9", ["因式分解"])]


async def test_passed_questions_are_stored_and_indexed(rag_settings):
    service, parts = make_ingest_service(rag_settings, judge=FakeJudge(reject_stems={"解方程 5x-1=9"}))
    report = await service.ingest_questions(GOOD)

    assert (report.extracted, report.passed, report.rejected) == (3, 2, 1)
    assert await parts.questions.count() == 2
    assert parts.index.contains(GOOD[0].id)
    assert not parts.index.contains(GOOD[1].id)
    [(item, result)] = parts.quarantine.entries
    assert item.stem == "解方程 5x-1=9" and not result.passed


async def test_reingesting_is_idempotent_and_skips_the_judge(rag_settings):
    """同一批题第二次入库：全部判为精确重复，一次裁判都不调用。"""
    judge = FakeJudge()
    service, _ = make_ingest_service(rag_settings, judge=judge)
    await service.ingest_questions(GOOD)
    calls_after_first_run = judge.calls

    report = await service.ingest_questions(GOOD)
    assert report.exact_duplicates == 3 and report.passed == 0
    assert judge.calls == calls_after_first_run


async def test_near_duplicates_are_dropped_before_judging(rag_settings):
    stem = "已知长方形的长是宽的2倍，周长是36厘米，求长和宽各是多少"
    judge = FakeJudge()
    service, _ = make_ingest_service(rag_settings, judge=judge)
    report = await service.ingest_questions([_q(stem), _q(stem + "？")])
    assert report.near_duplicates == 1 and report.passed == 1
    assert judge.calls == 1, "近似重复的题不应再花一次裁判调用"


async def test_judge_concurrency_is_bounded(rag_settings):
    judge = FakeJudge(delay=0.01)
    service, _ = make_ingest_service(rag_settings, judge=judge)
    await service.ingest_questions([_q(f"第{i}题：求 {i} 的相反数") for i in range(8)])
    assert judge.calls == 8
    assert judge.max_concurrency <= rag_settings.judge_concurrency


async def test_quarantine_failure_is_reported_not_fatal(rag_settings):
    service, _ = make_ingest_service(
        rag_settings, judge=FakeJudge(reject_stems={GOOD[1].stem}), quarantine=ListQuarantine(fail=True)
    )
    report = await service.ingest_questions(GOOD)
    assert report.passed == 2 and report.rejected == 1
    assert any("写入隔离区失败" in e for e in report.errors)


async def test_service_without_judge_refuses_to_ingest(rag_settings):
    service = IngestService(settings=rag_settings, embedder=None, question_store=None,
                            knowledge_store=None, lexical_index=None)
    with pytest.raises(JudgeConfigError):
        await service.ingest_questions(GOOD)


async def test_approve_skips_the_judge(rag_settings):
    judge = FakeJudge()
    service, parts = make_ingest_service(rag_settings, judge=judge)
    await service.approve(GOOD[0])
    assert parts.index.contains(GOOD[0].id)
    assert judge.calls == 0


async def test_knowledge_chunks_are_deduplicated(rag_settings):
    service, parts = make_ingest_service(rag_settings)
    chunk = KnowledgeChunk(text="移项要变号。", knowledge_points=["一元一次方程"])

    first = await service.ingest_knowledge([chunk, chunk])
    second = await service.ingest_knowledge([chunk])
    assert (first.knowledge_added, first.knowledge_duplicates) == (1, 1)
    assert (second.knowledge_added, second.knowledge_duplicates) == (0, 1)
    assert await parts.knowledge.count() == 1


async def test_ingest_file_end_to_end(rag_settings, tmp_path):
    path = tmp_path / "练习.docx"
    write_docx(path, ["一、解方程 2x+3=7。答案：x=2", "一元一次方程：只含一个未知数且次数为 1 的方程。"])
    llm_output = json.dumps({
        "questions": [{"stem": "解方程 2x+3=7", "answer": "x=2", "knowledge_points": ["一元一次方程"]}],
        "knowledge": [{"text": "一元一次方程：只含一个未知数且次数为 1 的方程。", "knowledge_points": ["一元一次方程"]}],
    }, ensure_ascii=False)
    service, parts = make_ingest_service(rag_settings, structurer_llm=ScriptedLLM(llm_output))

    report = await service.ingest_file(path, grade_hint="七年级")
    assert (report.extracted, report.passed, report.knowledge_added) == (1, 1, 1)
    stored = parts.index.get(QuestionItem(stem="解方程 2x+3=7", answer="x").id)
    assert (stored.source_type, stored.source_ref, stored.grade) == ("docx", "练习.docx#p1", "七年级")


async def test_source_name_overrides_the_stored_file_name(rag_settings, tmp_path):
    """后台上传的文件落盘名是任务 id，来源里要记原始文件名。"""
    path = tmp_path / "3f2a9c.docx"
    write_docx(path, ["一、解方程 2x+3=7。答案：x=2"])
    llm_output = json.dumps({"questions": [{"stem": "解方程 2x+3=7", "answer": "x=2"}], "knowledge": []})
    service, parts = make_ingest_service(rag_settings, structurer_llm=ScriptedLLM(llm_output))

    await service.ingest_file(path, source_name="七年级期中卷.docx")
    stored = parts.index.get(QuestionItem(stem="解方程 2x+3=7", answer="x").id)
    assert stored.source_ref == "七年级期中卷.docx#p1"


async def test_generate_and_ingest(rag_settings):
    output = json.dumps([{"stem": "解方程 3x=12", "answer": "x=4"},
                         {"stem": "解方程 x-5=2", "answer": "x=7"}], ensure_ascii=False)
    service, parts = make_ingest_service(rag_settings, generator_llm=ScriptedLLM(output))
    report = await service.generate_and_ingest("一元一次方程", "七年级", "简单", 2)

    assert report.passed == 2
    for stem in ("解方程 3x=12", "解方程 x-5=2"):
        assert parts.index.get(QuestionItem(stem=stem, answer="x").id).source_type == "llm"


def test_report_merge():
    report = IngestReport(extracted=2, passed=1, errors=["x"])
    report.merge(IngestReport(extracted=3, rejected=1, errors=["y"]))
    assert (report.extracted, report.passed, report.rejected, report.errors) == (5, 1, 1, ["x", "y"])
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_ingest_service.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.agents.rag.ingest.service'`

- [ ] **Step 4: 实现**

创建 `backend/agents/rag/ingest/service.py`：

```python
"""
入库服务：命令行与后台接口共用的唯一入口。

题目流水线（顺序有讲究）：
    精确去重 → 向量化 → 近似去重 → 第三方复核 → 写入向量库 → 重建词法索引
- 两级去重都在复核之前：重复的题不值得花一次裁判调用
- 复核不通过的题进隔离区，等人工处理，不直接丢弃
教材讲解不经复核：来自权威原文，只做精确去重。
"""
import asyncio
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Awaitable, Callable, Sequence

from backend.agents.rag.config import RagSettings
from backend.agents.rag.ingest.dedup import drop_exact_duplicates, drop_near_duplicates
from backend.agents.rag.ingest.judge import JudgeConfigError
from backend.agents.rag.ingest.loaders import load_document
from backend.agents.rag.models import JudgeResult, KnowledgeChunk, QuestionItem
from backend.core.executors import get_vector_executor
from backend.middleware.logging import get_logger

logger = get_logger(__name__)

QuarantineFn = Callable[[QuestionItem, JudgeResult], Awaitable[object]]


@dataclass
class IngestReport:
    extracted: int = 0
    exact_duplicates: int = 0
    near_duplicates: int = 0
    passed: int = 0
    rejected: int = 0
    knowledge_added: int = 0
    knowledge_duplicates: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "IngestReport") -> None:
        for f in fields(self):
            if f.name == "errors":
                self.errors.extend(other.errors)
            else:
                setattr(self, f.name, getattr(self, f.name) + getattr(other, f.name))

    def to_dict(self) -> dict:
        return asdict(self)


class IngestService:
    def __init__(self, *, settings: RagSettings, embedder, question_store, knowledge_store,
                 lexical_index, structurer=None, generator=None, judge=None,
                 quarantine: QuarantineFn | None = None):
        self._settings = settings
        self._embedder = embedder
        self._questions = question_store
        self._knowledge = knowledge_store
        self._index = lexical_index
        self._structurer = structurer
        self._generator = generator
        self._judge = judge
        self._quarantine = quarantine

    # ---------- 入口 ----------

    async def ingest_file(self, path: Path, grade_hint: str | None = None,
                          source_name: str | None = None) -> IngestReport:
        """
        source_name 是记录在题目来源里的文件名。后台上传的文件落盘时改用「任务 id + 后缀」命名，
        这里要传入用户上传时的原始文件名，否则来源就成了一串 id，无法追溯。
        """
        loop = asyncio.get_running_loop()
        # PDF 解析是 CPU 密集的同步调用，放进专属线程池
        pages = await loop.run_in_executor(get_vector_executor(), load_document, path)
        structured = await self._structurer.structure(
            pages,
            source_ref=source_name or path.name,
            source_type=path.suffix.lower().lstrip("."),
            grade_hint=grade_hint,
        )
        report = IngestReport(errors=list(structured.errors))
        report.merge(await self.ingest_questions(structured.questions))
        report.merge(await self.ingest_knowledge(structured.knowledge))
        return report

    async def generate_and_ingest(self, knowledge_point: str, grade: str, difficulty: str,
                                  n: int) -> IngestReport:
        items, errors = await self._generator.generate(knowledge_point, grade, difficulty, n)
        report = IngestReport(errors=list(errors))
        report.merge(await self.ingest_questions(items))
        return report

    async def approve(self, item: QuestionItem) -> None:
        """人工复核通过：跳过裁判，直接入库。"""
        [vector] = await self._embedder.embed([item.stem])
        await self._questions.upsert([item.id], [vector], [item.stem], [item.to_metadata()])
        await self._index.rebuild_from_store(self._questions)

    # ---------- 流水线 ----------

    async def ingest_questions(self, items: Sequence[QuestionItem]) -> IngestReport:
        if self._judge is None or self._quarantine is None:
            raise JudgeConfigError("入库服务未配置裁判或隔离区，不能写入未经复核的题目")

        report = IngestReport(extracted=len(items))
        unique, report.exact_duplicates = drop_exact_duplicates(items, self._index.contains)
        if not unique:
            return report

        vectors = await self._embedder.embed([i.stem for i in unique])
        unique, vectors, report.near_duplicates = await drop_near_duplicates(
            unique, vectors, self._questions, self._settings.near_dup_threshold
        )
        results = await self._judge_all(unique)

        accepted: list[QuestionItem] = []
        accepted_vectors: list[list[float]] = []
        for item, vector, result in zip(unique, vectors, results):
            if result.passed:
                accepted.append(item)
                accepted_vectors.append(vector)
                continue
            report.rejected += 1
            try:
                await self._quarantine(item, result)
            except Exception as e:  # 隔离区写失败不应中断整批入库
                logger.error("写入隔离区失败 %s：%s", item.id, e, exc_info=True)
                report.errors.append(f"写入隔离区失败：{item.id}（{e}）")

        if accepted:
            await self._questions.upsert(
                [i.id for i in accepted], accepted_vectors,
                [i.stem for i in accepted], [i.to_metadata() for i in accepted],
            )
            report.passed = len(accepted)
            await self._index.rebuild_from_store(self._questions)
        return report

    async def ingest_knowledge(self, chunks: Sequence[KnowledgeChunk]) -> IngestReport:
        report = IngestReport()
        existing = {row_id for row_id, _, _ in await self._knowledge.get_all()}
        seen: set[str] = set()
        fresh: list[KnowledgeChunk] = []
        for chunk in chunks:
            if chunk.id in existing or chunk.id in seen:
                report.knowledge_duplicates += 1
                continue
            seen.add(chunk.id)
            fresh.append(chunk)
        if fresh:
            vectors = await self._embedder.embed([c.text for c in fresh])
            await self._knowledge.upsert(
                [c.id for c in fresh], vectors, [c.text for c in fresh], [c.to_metadata() for c in fresh]
            )
            report.knowledge_added = len(fresh)
        return report

    async def _judge_all(self, items: Sequence[QuestionItem]) -> list[JudgeResult]:
        semaphore = asyncio.Semaphore(self._settings.judge_concurrency)

        async def judge_one(item: QuestionItem) -> JudgeResult:
            async with semaphore:
                return await self._judge.judge(item)

        # judge() 内部失败即拒、不抛异常，所以这里不需要 return_exceptions
        return list(await asyncio.gather(*(judge_one(i) for i in items)))
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_ingest_service.py -v`
Expected: 12 passed

- [ ] **Step 6: 提交**

```bash
git add backend/agents/rag/ingest/service.py backend/tests/rag/helpers.py backend/tests/rag/test_ingest_service.py
git commit -m "feat: RAG 入库服务编排"
```

---

## Task 11: 运行时组装与入库命令行

> **这个任务做什么**：两件事。① `RagRuntime`：把真实组件组装起来（`LlamaIndexEmbedder` 向量化、经 LlamaIndex 读写 Chroma 的两个 collection、词法索引、出题模型和裁判），整个进程里只建一份，其他地方通过 `get_rag_runtime()` 拿到；测试时可以换成内存版。② 入库命令行 `python -m backend.agents.rag.ingest`：在终端里指定文件或知识点，调用 Task 10 的服务入库，并打印入库报告。
>
> **做完之后**：阶段二完成，可以真正把 PDF / Word 资料和生成的种子题灌进本地知识库了。

`RagRuntime` 负责按需组装各组件（进程内单例）；命令行是阶段二的交付物——做完这个任务，就可以把资料灌进库里了。

**Chroma 的本地库不支持多进程同时写入**：服务运行期间请改用后台接口入库（Task 18）；命令行入库完成后要重启服务，服务里的词法索引才会包含新题。

**Files:**
- Create: `backend/agents/rag/runtime.py`
- Create: `backend/agents/rag/ingest/__main__.py`
- Modify: `backend/tests/rag/helpers.py`（追加一段）
- Test: `backend/tests/rag/test_runtime_and_cli.py`

**Interfaces:**
- Consumes: Task 2-10 的全部组件
- Produces:
  - `RagRuntime(settings, *, embedder, question_store, knowledge_store, lexical_index, llm_factory, judge_factory, reranker=None)`
    - 属性 `settings, embedder, question_store, knowledge_store, lexical_index`
    - `RagRuntime.from_settings(settings)`：生产组装（`LlamaIndexEmbedder` + 两个 `LlamaIndexVectorStore.from_chroma`）
    - `async warm_up() -> int`、`ensure_judge()`（缓存；配置不对时抛 `JudgeConfigError`）
    - `ingest_service(quarantine, require_judge=True) -> IngestService`
  - `get_rag_runtime()`、`set_rag_runtime(runtime | None)`
  - 常量 `QUESTION_COLLECTION = "rag_questions"`、`KNOWLEDGE_COLLECTION = "rag_knowledge"`
  - 命令行：`build_parser()`、`expand_paths(paths)`、`format_report(report)`、`async run(args, runtime=None, quarantine=None, out=print) -> int`
  - 测试工厂 `make_test_runtime(settings, *, llm=None, judge=None, judge_error=None, reranker=None)`

- [ ] **Step 1: 追加测试工厂**

在 `backend/tests/rag/helpers.py` 末尾追加：

```python
def make_test_runtime(settings, *, llm=None, judge=None, judge_error=None, reranker=None):
    """
    用离线替身组装 RagRuntime。llm 同时充当结构化与生成用的模型；
    judge_error 不为空时，构建裁判会抛出它（模拟裁判配置错误）。
    """
    from backend.agents.rag.runtime import RagRuntime
    from backend.agents.rag.store.lexical_index import LexicalIndex
    from backend.agents.rag.store.vector_store import InMemoryVectorStore

    def judge_factory():
        if judge_error is not None:
            raise judge_error
        return judge or FakeJudge()

    return RagRuntime(
        settings,
        embedder=HashingEmbedder(),
        question_store=InMemoryVectorStore(),
        knowledge_store=InMemoryVectorStore(),
        lexical_index=LexicalIndex(),
        llm_factory=lambda: llm,
        judge_factory=judge_factory,
        reranker=reranker,
    )
```

- [ ] **Step 2: 写失败的测试**

创建 `backend/tests/rag/test_runtime_and_cli.py`：

```python
import json

import pytest

from backend.agents.rag.ingest.__main__ import build_parser, expand_paths, run
from backend.agents.rag.ingest.judge import JudgeConfigError
from backend.agents.rag.models import QuestionItem
from backend.agents.rag.runtime import RagRuntime, get_rag_runtime, set_rag_runtime
from backend.tests.rag.helpers import FakeJudge, ListQuarantine, ScriptedLLM, make_test_runtime, write_docx

STRUCTURED = json.dumps({
    "questions": [{"stem": "解方程 2x+3=7", "answer": "x=2", "knowledge_points": ["一元一次方程"]}],
    "knowledge": [],
}, ensure_ascii=False)


# ---------- 运行时 ----------

def test_judge_is_built_once_and_cached(rag_settings):
    built = []

    def factory():
        built.append(1)
        return FakeJudge()

    runtime = make_test_runtime(rag_settings)
    runtime._judge_factory = factory
    assert runtime.ensure_judge() is runtime.ensure_judge()
    assert len(built) == 1


def test_misconfigured_judge_blocks_ingest_service(rag_settings):
    runtime = make_test_runtime(rag_settings, judge_error=JudgeConfigError("同一家族"))
    with pytest.raises(JudgeConfigError):
        runtime.ingest_service(ListQuarantine())
    # 人工复核通过不需要裁判，不受影响
    assert runtime.ingest_service(None, require_judge=False) is not None


async def test_warm_up_rebuilds_index_from_store(rag_settings):
    runtime = make_test_runtime(rag_settings)
    item = QuestionItem(stem="解方程 2x+3=7", answer="x=2")
    await runtime.question_store.upsert([item.id], [[1.0]], [item.stem], [item.to_metadata()])
    assert await runtime.warm_up() == 1
    assert runtime.lexical_index.contains(item.id)


def test_runtime_singleton_can_be_injected(rag_settings):
    fake = make_test_runtime(rag_settings)
    set_rag_runtime(fake)
    try:
        assert get_rag_runtime() is fake
    finally:
        set_rag_runtime(None)


def test_production_wiring_uses_llamaindex_chroma(rag_settings):
    pytest.importorskip("chromadb", exc_type=ImportError)
    from backend.agents.rag.store.embedder import LlamaIndexEmbedder
    from backend.agents.rag.store.llama_store import LlamaIndexVectorStore

    runtime = RagRuntime.from_settings(rag_settings)
    assert isinstance(runtime.embedder, LlamaIndexEmbedder)
    assert isinstance(runtime.question_store, LlamaIndexVectorStore)
    assert isinstance(runtime.knowledge_store, LlamaIndexVectorStore)
    assert (rag_settings.db_dir / "chroma.sqlite3").exists()


# ---------- 命令行 ----------

def test_parser():
    args = build_parser().parse_args(["file", "a.pdf", "b.docx", "--grade", "七年级"])
    assert (args.command, [p.name for p in args.paths], args.grade) == ("file", ["a.pdf", "b.docx"], "七年级")
    args = build_parser().parse_args(["generate", "--kp", "一元一次方程", "--grade", "七年级", "-n", "5"])
    assert (args.kp, args.difficulty, args.n) == ("一元一次方程", "中等", 5)


def test_expand_paths_filters_and_sorts(tmp_path):
    for name in ("b.docx", "a.pdf", "notes.txt", "sub/d.docx"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    names = [p.relative_to(tmp_path).as_posix() for p in expand_paths([tmp_path])]
    assert names == ["a.pdf", "b.docx", "sub/d.docx"]


async def test_file_command_ingests_and_prints_summary(rag_settings, tmp_path):
    path = tmp_path / "练习.docx"
    write_docx(path, ["一、解方程 2x+3=7。答案：x=2"])
    runtime = make_test_runtime(rag_settings, llm=ScriptedLLM(STRUCTURED))
    lines = []

    code = await run(build_parser().parse_args(["file", str(path)]), runtime=runtime,
                     quarantine=ListQuarantine(), out=lines.append)
    assert code == 0
    assert "通过 1" in lines[-1] and lines[-1].startswith("合计")


async def test_one_bad_file_does_not_abort_the_batch(rag_settings, tmp_path):
    good, broken = tmp_path / "a.docx", tmp_path / "b.docx"
    write_docx(good, ["一、解方程 2x+3=7。答案：x=2"])
    broken.write_bytes(b"this is not a zip file")
    runtime = make_test_runtime(rag_settings, llm=ScriptedLLM(STRUCTURED))
    lines = []

    await run(build_parser().parse_args(["file", str(tmp_path)]), runtime=runtime,
              quarantine=ListQuarantine(), out=lines.append)
    assert any(line.strip().startswith("失败") for line in lines)
    assert "通过 1" in lines[-1]


async def test_misconfigured_judge_exits_with_clear_message(rag_settings):
    runtime = make_test_runtime(rag_settings, judge_error=JudgeConfigError("裁判与生成模型属于同一家族"))
    lines = []
    code = await run(build_parser().parse_args(["generate", "--kp", "一元一次方程", "--grade", "七年级"]),
                     runtime=runtime, quarantine=ListQuarantine(), out=lines.append)
    assert code == 2
    assert "同一家族" in lines[0]
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_runtime_and_cli.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.agents.rag.ingest.__main__'`

- [ ] **Step 4: 实现**

创建 `backend/agents/rag/runtime.py`：

```python
"""
RAG 运行时：按需组装各组件的进程内单例。

组装失败的影响被限制在 RAG 自身：
- 检索侧（生题）由 integration.py 吞掉异常，生题照常进行
- 入库侧抛出明确的错误（如 JudgeConfigError），让调用方拒绝执行
"""
from typing import Callable

from backend.agents.rag.config import RagSettings
from backend.agents.rag.ingest.generator import SeedGenerator
from backend.agents.rag.ingest.judge import QuestionJudge, build_judge_llm
from backend.agents.rag.ingest.service import IngestService, QuarantineFn
from backend.agents.rag.ingest.structurer import Structurer
from backend.agents.rag.store.lexical_index import LexicalIndex

QUESTION_COLLECTION = "rag_questions"
KNOWLEDGE_COLLECTION = "rag_knowledge"


class RagRuntime:
    def __init__(self, settings: RagSettings, *, embedder, question_store, knowledge_store,
                 lexical_index: LexicalIndex, llm_factory: Callable[[], object],
                 judge_factory: Callable[[], object], reranker=None):
        self.settings = settings
        self.embedder = embedder
        self.question_store = question_store
        self.knowledge_store = knowledge_store
        self.lexical_index = lexical_index
        self._llm_factory = llm_factory
        self._judge_factory = judge_factory
        self._reranker = reranker
        self._judge = None

    @classmethod
    def from_settings(cls, settings: RagSettings) -> "RagRuntime":
        """生产组装：LlamaIndex 向量化（DashScope）+ LlamaIndex 读写 Chroma + 独立配置的裁判。"""
        from backend.agents.agent.get_llm import get_llm
        from backend.agents.rag.store.embedder import LlamaIndexEmbedder
        from backend.agents.rag.store.llama_store import LlamaIndexVectorStore

        def judge_factory():
            return QuestionJudge(build_judge_llm(settings), settings.judge_model, settings.judge_pass_score)

        return cls(
            settings,
            embedder=LlamaIndexEmbedder(),
            question_store=LlamaIndexVectorStore.from_chroma(settings.db_dir, QUESTION_COLLECTION),
            knowledge_store=LlamaIndexVectorStore.from_chroma(settings.db_dir, KNOWLEDGE_COLLECTION),
            lexical_index=LexicalIndex(),
            llm_factory=get_llm,
            judge_factory=judge_factory,
        )

    async def warm_up(self) -> int:
        """从向量库重建词法索引，返回题目数。服务启动、命令行入库前各调一次。"""
        return await self.lexical_index.rebuild_from_store(self.question_store)

    def ensure_judge(self):
        """构建并缓存裁判；配置不对时抛 JudgeConfigError。后台接口在创建任务前用它做预检。"""
        if self._judge is None:
            self._judge = self._judge_factory()
        return self._judge

    def ingest_service(self, quarantine: QuarantineFn | None, require_judge: bool = True) -> IngestService:
        """require_judge=False 只用于人工复核通过（approve），它本来就不经过裁判。"""
        llm = self._llm_factory()
        return IngestService(
            settings=self.settings,
            embedder=self.embedder,
            question_store=self.question_store,
            knowledge_store=self.knowledge_store,
            lexical_index=self.lexical_index,
            structurer=Structurer(llm),
            generator=SeedGenerator(llm),
            judge=self.ensure_judge() if require_judge else None,
            quarantine=quarantine,
        )


_runtime: RagRuntime | None = None


def get_rag_runtime() -> RagRuntime:
    global _runtime
    if _runtime is None:
        _runtime = RagRuntime.from_settings(RagSettings.from_env())
    return _runtime


def set_rag_runtime(runtime: RagRuntime | None) -> None:
    """测试注入用；传 None 恢复为按配置懒加载。"""
    global _runtime
    _runtime = runtime
```

创建 `backend/agents/rag/ingest/__main__.py`：

```python
"""
命令行入库。

    python -m backend.agents.rag.ingest file <文件或目录>... [--grade 七年级]
    python -m backend.agents.rag.ingest generate --kp 一元一次方程 --grade 七年级 [--difficulty 中等] [-n 10]

注意：Chroma 的本地库不支持多进程同时写入。服务运行期间请改用后台接口入库；
命令行入库完成后要重启服务，服务里的词法索引才会包含新题。
"""
import argparse
import asyncio
import sys
from functools import partial
from pathlib import Path

from backend.agents.rag.ingest.judge import JudgeConfigError
from backend.agents.rag.ingest.loaders import SUPPORTED_SUFFIXES, UnsupportedDocumentError
from backend.agents.rag.ingest.service import IngestReport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m backend.agents.rag.ingest", description="RAG 知识库入库")
    sub = parser.add_subparsers(dest="command", required=True)

    file_cmd = sub.add_parser("file", help="导入 PDF / Word 文件或目录")
    file_cmd.add_argument("paths", nargs="+", type=Path)
    file_cmd.add_argument("--grade", default=None, help="年级提示，如 七年级")

    gen_cmd = sub.add_parser("generate", help="按知识点生成种子题")
    gen_cmd.add_argument("--kp", required=True, help="知识点，如 一元一次方程")
    gen_cmd.add_argument("--grade", required=True, help="年级，如 七年级")
    gen_cmd.add_argument("--difficulty", default="中等", choices=["简单", "中等", "困难"])
    gen_cmd.add_argument("-n", type=int, default=10, help="生成数量")
    return parser


def expand_paths(paths) -> list[Path]:
    """目录展开为其中（递归）所有支持的文件并排序；单个文件原样保留。"""
    result: list[Path] = []
    for path in paths:
        if path.is_dir():
            result.extend(sorted(p for p in path.rglob("*") if p.suffix.lower() in SUPPORTED_SUFFIXES))
        else:
            result.append(path)
    return result


def format_report(report: IngestReport) -> str:
    text = (f"抽取 {report.extracted}，精确重复 {report.exact_duplicates}，近似重复 {report.near_duplicates}，"
            f"通过 {report.passed}，进隔离区 {report.rejected}；讲解新增 {report.knowledge_added}")
    if report.errors:
        text += f"；错误 {len(report.errors)} 条"
    return text


async def run(args, runtime=None, quarantine=None, out=print) -> int:
    if runtime is None:
        from backend.agents.rag.runtime import get_rag_runtime
        runtime = get_rag_runtime()
    try:
        runtime.ensure_judge()
    except JudgeConfigError as e:
        out(f"无法入库：{e}")
        return 2

    engine = None
    if quarantine is None:
        # 命令行没有服务的启动钩子，自己建表、连库
        import backend.model.rag  # noqa: F401  注册表结构
        from backend.dao.rag_mapper import RagQuarantineMapper
        from backend.model import AsyncSessionLocal, Base, engine

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        quarantine = partial(RagQuarantineMapper(AsyncSessionLocal).put, job_id=None)

    try:
        await runtime.warm_up()
        service = runtime.ingest_service(quarantine)
        total = IngestReport()
        if args.command == "file":
            for path in expand_paths(args.paths):
                out(f"入库：{path}")
                try:
                    report = await service.ingest_file(path, grade_hint=args.grade)
                except UnsupportedDocumentError as e:
                    out(f"  跳过：{e}")
                    continue
                except Exception as e:  # 一个文件出错不应中断整批
                    out(f"  失败：{type(e).__name__}: {e}")
                    continue
                out("  " + format_report(report))
                for err in report.errors:
                    out(f"  ! {err}")
                total.merge(report)
        else:
            total = await service.generate_and_ingest(args.kp, args.grade, args.difficulty, args.n)
            for err in total.errors:
                out(f"  ! {err}")
        out("合计：" + format_report(total))
        return 0
    finally:
        if engine is not None:
            await engine.dispose()


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_runtime_and_cli.py -v -rs`
Expected: chromadb 可用时 10 passed；不可用时 9 passed、1 skipped

- [ ] **Step 6: 用真实资料试一次（可选，需要 chromadb 与裁判配置都已就绪）**

先停掉服务，然后在仓库根目录运行：

```bash
python -m backend.agents.rag.ingest generate --kp 一元一次方程 --grade 七年级 -n 5
python -m backend.agents.rag.ingest file 你的资料目录 --grade 七年级
```

每个文件打印一行报告，最后打印合计；未通过复核的题可以在 MySQL 的 `rag_quarantine` 表里看到。

- [ ] **Step 7: 提交**

```bash
git add backend/agents/rag/runtime.py backend/agents/rag/ingest/__main__.py backend/tests/rag/helpers.py backend/tests/rag/test_runtime_and_cli.py
git commit -m "feat: RAG 运行时组装与入库命令行"
```

---

## Task 12: 三路召回

> **这个任务做什么**：检索链路的第一步——从题库里各自粗筛出一批候选题。三路互相独立：① 向量召回：按语义相似度找（用 Task 2）；② BM25 召回：按关键词匹配找（用 Task 3）；③ 知识点召回：按知识点重合数找，难度一致的加分，过滤掉高于用户年级的题（用 Task 3）。每一路都只返回「题目 id + 本路分数」的列表。
>
> **做完之后**：得到三个召回函数。拆成三个独立函数，是为了某一路失败时能单独跳过，评测时也能单独跑某一路做对比。

每一路都是独立的函数，只返回 `[(id, score)]`，彼此没有依赖：单独失败时由编排层跳过；离线评测可以逐路单独运行，做消融对比。各路分数量纲不同，不直接比较，由 Task 13 按名次融合。

**Files:**
- Create: `backend/agents/rag/retrieval/__init__.py`（空文件）
- Create: `backend/agents/rag/retrieval/routes.py`
- Test: `backend/tests/rag/test_routes.py`

**Interfaces:**
- Consumes: Task 1 的 `RagQuery`、`grade_rank`；Task 2 的 `VectorStore`；Task 3 的 `LexicalIndex`
- Produces:
  - 常量 `ROUTE_DENSE = "dense"`、`ROUTE_LEXICAL = "lexical"`、`ROUTE_METADATA = "metadata"`、`ALL_ROUTES`
  - `async dense_route(store, query_vector, top_k)`、`async lexical_route(index, text, top_k)`、`async metadata_route(index, query, top_k)`，都返回 `list[(id, score)]`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/rag/test_routes.py`：

```python
import threading

from backend.agents.rag.models import QuestionItem, RagQuery
from backend.agents.rag.retrieval.routes import dense_route, lexical_route, metadata_route
from backend.agents.rag.store.lexical_index import LexicalIndex
from backend.agents.rag.store.vector_store import InMemoryVectorStore


async def test_dense_route_returns_store_order():
    store = InMemoryVectorStore()
    await store.upsert(["a", "b"], [[1.0, 0.0], [0.0, 1.0]], ["A", "B"], [{}, {}])
    assert [i for i, _ in await dense_route(store, [0.0, 1.0], top_k=2)] == ["b", "a"]


async def test_lexical_route_runs_in_the_vector_pool():
    """BM25 是 CPU 计算，必须跑在专属线程池里，不能占着事件循环。"""
    seen_threads = []

    class RecordingIndex:
        def search_bm25(self, text, top_k):
            seen_threads.append(threading.current_thread().name)
            return [("q_1", 1.0)]

    assert await lexical_route(RecordingIndex(), "方程", 5) == [("q_1", 1.0)]
    assert seen_threads[0].startswith("vec")


async def test_metadata_route_applies_grade_and_difficulty():
    low = QuestionItem(stem="七年级的题", answer="略", knowledge_points=["一元一次方程"], grade="七年级")
    high = QuestionItem(stem="九年级的题", answer="略", knowledge_points=["一元一次方程"], grade="九年级")
    index = LexicalIndex()
    index.rebuild([low, high])
    query = RagQuery(text="x", knowledge_points=["一元一次方程"], grade="七年级")
    assert [i for i, _ in await metadata_route(index, query, top_k=5)] == [low.id]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_routes.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.agents.rag.retrieval'`

- [ ] **Step 3: 实现**

创建空文件 `backend/agents/rag/retrieval/__init__.py`，然后创建 `backend/agents/rag/retrieval/routes.py`：

```python
"""
三路召回。每一路都是独立的函数，只返回 [(id, score)]，彼此没有依赖：
- 单独失败时由编排层跳过，不影响其他路
- 离线评测可以逐路单独运行，做消融对比
各路 score 的量纲不同（余弦相似度 / BM25 分 / 知识点重合数），不要直接比较，由 RRF 按名次融合。
"""
import asyncio
from typing import Sequence

from backend.agents.rag.models import RagQuery, grade_rank
from backend.core.executors import get_vector_executor

ROUTE_DENSE = "dense"
ROUTE_LEXICAL = "lexical"
ROUTE_METADATA = "metadata"
ALL_ROUTES = (ROUTE_DENSE, ROUTE_LEXICAL, ROUTE_METADATA)


async def dense_route(store, query_vector: Sequence[float], top_k: int) -> list[tuple[str, float]]:
    hits = await store.query(query_vector, top_k)
    return [(hit.id, hit.similarity) for hit in hits]


async def lexical_route(index, text: str, top_k: int) -> list[tuple[str, float]]:
    # BM25 打分是 CPU 计算，放进向量库专属线程池
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(get_vector_executor(), index.search_bm25, text, top_k)


async def metadata_route(index, query: RagQuery, top_k: int) -> list[tuple[str, float]]:
    # 只是几次字典查找，直接在事件循环里做
    return index.search_knowledge_points(
        query.knowledge_points,
        top_k,
        max_grade_rank=grade_rank(query.grade),
        difficulty=query.difficulty,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_routes.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add backend/agents/rag/retrieval/__init__.py backend/agents/rag/retrieval/routes.py backend/tests/rag/test_routes.py
git commit -m "feat: RAG 三路召回（向量 / BM25 / 知识点）"
```

---

## Task 13: RRF 融合

> **这个任务做什么**：把三路召回的结果合并成一个排名。三路的分数单位完全不同（BM25 分、余弦相似度、知识点重合数），不能直接相加，所以 RRF（倒数排名融合）只看每道题在各路里排第几：`score = Σ 1 / (60 + 名次)`。一道题在越多路里排得越靠前，总分就越高。
>
> **做完之后**：`rrf_fuse()` 输出融合后的前 30 名候选，每个候选都记录它来自哪几路。这是个几十行的纯函数、没有外部依赖，也很适合入门。

`score(d) = Σ 1 / (k + rank)`。只看名次、不看原始分数：BM25 分、余弦相似度、知识点重合数三者量纲完全不同，没法直接相加。

**Files:**
- Create: `backend/agents/rag/retrieval/fusion.py`
- Test: `backend/tests/rag/test_fusion.py`

**Interfaces:**
- Consumes: 无
- Produces: `FusedCandidate(id, score, routes)`；`rrf_fuse(ranked: dict[str, list[str]], k=60, top_n=30) -> list[FusedCandidate]`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/rag/test_fusion.py`：

```python
import pytest

from backend.agents.rag.retrieval.fusion import rrf_fuse


def test_agreement_across_routes_beats_a_single_top_hit():
    """B 在两路都靠前，胜过只在一路排第一的 A：1/62 + 1/61 > 1/61。"""
    fused = rrf_fuse({"dense": ["A", "B"], "lexical": ["B"]})
    assert [c.id for c in fused] == ["B", "A"]
    assert fused[0].routes == ["dense", "lexical"]
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 61)


def test_ties_are_broken_by_id_and_top_n_is_applied():
    fused = rrf_fuse({"dense": ["z"], "lexical": ["a"]}, top_n=1)
    assert [c.id for c in fused] == ["a"]


def test_empty_input():
    assert rrf_fuse({}) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_fusion.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.agents.rag.retrieval.fusion'`

- [ ] **Step 3: 实现**

创建 `backend/agents/rag/retrieval/fusion.py`：

```python
"""
RRF（Reciprocal Rank Fusion）融合多路召回结果。

    score(d) = Σ_route 1 / (k + rank_route(d))，rank 从 1 开始

只看名次、不看原始分数：BM25 分、余弦相似度、知识点重合数三者量纲完全不同，没法直接相加。
k 越大，排在后面的结果占的权重越接近前面的；60 是论文与业界的常用值。
"""
from dataclasses import dataclass


@dataclass
class FusedCandidate:
    id: str
    score: float
    routes: list[str]  # 按出现顺序记录命中了它的召回路


def rrf_fuse(ranked: dict[str, list[str]], k: int = 60, top_n: int = 30) -> list[FusedCandidate]:
    scores: dict[str, float] = {}
    routes: dict[str, list[str]] = {}
    for route, ids in ranked.items():
        for rank, doc_id in enumerate(ids, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            routes.setdefault(doc_id, []).append(route)
    fused = [FusedCandidate(doc_id, score, routes[doc_id]) for doc_id, score in scores.items()]
    fused.sort(key=lambda c: (-c.score, c.id))
    return fused[:top_n]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_fusion.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add backend/agents/rag/retrieval/fusion.py backend/tests/rag/test_fusion.py
git commit -m "feat: RAG 多路召回的 RRF 融合"
```

---

## Task 14: 重排

> **这个任务做什么**：对融合后的候选再精排一次。召回和融合都比较粗糙，重排模型会把「查询」和「每个候选」成对地看一遍，给出更准确的相关度分数。这里调用 DashScope 的文本排序服务（默认 `gte-rerank-v2`），并定义 `Reranker` 协议，将来想换成本地模型只要另写一个实现。另外提供 `NoopReranker`（不重排，保持原顺序），没配重排 key 时用它。
>
> **做完之后**：给一个查询和一组文档，返回按相关度排好序的结果。动手前要先对照 Task 0 探针打印出的真实响应结构。

检索编排只依赖 `Reranker` 协议，将来换本地 cross-encoder 只需另写一个实现。

**先回头看 Task 0 探针的输出。** 下面的 `parse_rerank_response` 按 `output.results[].index / relevance_score` 解析。如果探针显示的结构不同，按实际结构改这个函数，同时改测试里的模拟响应。解析逻辑集中在这一个函数里，就是为了方便这种调整。

**Files:**
- Create: `backend/agents/rag/retrieval/reranker.py`
- Test: `backend/tests/rag/test_reranker.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `RerankResult(index, score: float | None)`
  - `Reranker` 协议：`async rerank(query, documents, top_n) -> list[RerankResult]`（按分数从高到低）
  - `RerankError(RuntimeError)`、`parse_rerank_response(data, n_documents)`
  - `NoopReranker()`：保持顺序、分数为 None
  - `DashScopeReranker(api_key, url, model, timeout=10.0, transport=None)`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/rag/test_reranker.py`：

```python
import json

import httpx
import pytest

from backend.agents.rag.retrieval.reranker import (
    DashScopeReranker,
    NoopReranker,
    RerankError,
    parse_rerank_response,
)


def _mock(handler):
    return httpx.MockTransport(handler)


async def test_dashscope_reranker_sends_query_and_sorts_results():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers["Authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"output": {"results": [
            {"index": 0, "relevance_score": 0.2}, {"index": 1, "relevance_score": 0.9},
        ]}})

    reranker = DashScopeReranker("k-1", "https://rerank.example/api", "gte-rerank-v2", transport=_mock(handler))
    results = await reranker.rerank("解方程", ["doc0", "doc1"], top_n=5)

    assert [(r.index, r.score) for r in results] == [(1, 0.9), (0, 0.2)]
    assert captured["auth"] == "Bearer k-1"
    assert captured["body"]["input"] == {"query": "解方程", "documents": ["doc0", "doc1"]}
    assert captured["body"]["parameters"]["top_n"] == 2, "top_n 不应超过文档数"


async def test_http_error_raises():
    reranker = DashScopeReranker("k", "https://x", "m", transport=_mock(lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(RerankError, match="500"):
        await reranker.rerank("q", ["d"], top_n=1)


async def test_empty_documents_make_no_request():
    def handler(request):
        raise AssertionError("不应发出请求")

    assert await DashScopeReranker("k", "https://x", "m", transport=_mock(handler)).rerank("q", [], 3) == []


def test_malformed_response_raises():
    with pytest.raises(RerankError, match="无法识别"):
        parse_rerank_response({"data": []}, 3)


def test_out_of_range_indices_are_dropped():
    data = {"output": {"results": [{"index": 5, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.5}]}}
    assert [r.index for r in parse_rerank_response(data, 2)] == [0]


async def test_noop_reranker_keeps_order_without_scores():
    results = await NoopReranker().rerank("q", ["a", "b", "c"], top_n=2)
    assert [(r.index, r.score) for r in results] == [(0, None), (1, None)]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_reranker.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.agents.rag.retrieval.reranker'`

- [ ] **Step 3: 实现**

创建 `backend/agents/rag/retrieval/reranker.py`：

```python
"""
重排。检索编排只依赖 Reranker 协议，将来换本地 cross-encoder 只需另写一个实现。

- DashScopeReranker：调用 DashScope 文本排序服务
- NoopReranker：不重排，保持输入顺序；分数为 None，编排层据此跳过分数阈值
"""
from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass
class RerankResult:
    index: int            # 在输入 documents 中的下标
    score: float | None   # 相关度；None 表示没有可用的分数


class Reranker(Protocol):
    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[RerankResult]: ...


class RerankError(RuntimeError):
    pass


class NoopReranker:
    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[RerankResult]:
        return [RerankResult(i, None) for i in range(min(top_n, len(documents)))]


def parse_rerank_response(data, n_documents: int) -> list[RerankResult]:
    """
    解析响应，按相关度从高到低返回。结构以 Task 0 探针的实际结果为准：
    DashScope 文本排序接口把结果放在 output.results，每项含 index 与 relevance_score。
    越界的 index 直接丢弃。
    """
    try:
        raw_results = data["output"]["results"]
        parsed = [RerankResult(int(r["index"]), float(r["relevance_score"])) for r in raw_results]
    except (KeyError, TypeError, ValueError) as e:
        raise RerankError(f"无法识别的重排响应结构：{str(data)[:200]}") from e
    parsed = [r for r in parsed if 0 <= r.index < n_documents]
    parsed.sort(key=lambda r: -r.score)
    return parsed


class DashScopeReranker:
    def __init__(self, api_key: str, url: str, model: str, timeout: float = 10.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._api_key = api_key
        self._url = url
        self._model = model
        self._timeout = timeout
        self._transport = transport  # 测试时注入 httpx.MockTransport

    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[RerankResult]:
        if not documents:
            return []
        payload = {
            "model": self._model,
            "input": {"query": query, "documents": documents},
            "parameters": {"top_n": min(top_n, len(documents)), "return_documents": False},
        }
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.post(
                self._url, json=payload, headers={"Authorization": f"Bearer {self._api_key}"}
            )
        if response.status_code != 200:
            raise RerankError(f"重排接口返回 HTTP {response.status_code}：{response.text[:200]}")
        return parse_rerank_response(response.json(), len(documents))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_reranker.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add backend/agents/rag/retrieval/reranker.py backend/tests/rag/test_reranker.py
git commit -m "feat: RAG 重排（DashScope 实现与不重排的兜底）"
```

---

## Task 15: 检索编排

> **这个任务做什么**：把 Task 12～14 串成完整的检索器 `RagRetriever`：查询向量化 → 三路并行召回（同时去教材库找相关讲解）→ RRF 融合 → 防泄漏过滤（排除用户问的这道原题本身）→ 重排 → 取前 3 道。重点是「逐级降级」：任何环节出错都只是少一些结果，不抛异常，并在结果里记下哪里降级了。同时给 `RagRuntime` 加上 `retriever` 属性。
>
> **做完之后**：阶段三完成，给一道题就能拿到参考题和相关知识点。此时检索还没接进生题链路，只能在测试或评测里调用。

把前面的组件串起来：

```
向量化 → 三路并行召回（同时查教材库）→ RRF 融合 → 防泄漏过滤 → 重排 → Top-K
```

**降级原则：RAG 是增强，不是依赖。** 向量化失败只剩 BM25 与知识点两路；某一路失败就跳过；重排失败退回融合顺序；全部失败返回空结果。每次降级都记进 `ctx.degraded`，`retrieve()` 本身不抛异常。

**防泄漏**：用户问的就是库里这道题时，不能把它当参考题交给模型照抄。

`retrieve()` 的 `routes / use_rerank / top_k / use_threshold` 四个参数是给 Task 21 的消融评测用的，线上调用保持默认值。

**Files:**
- Create: `backend/agents/rag/retrieval/retriever.py`
- Modify: `backend/agents/rag/runtime.py`（加 `retriever` 属性）
- Modify: `backend/tests/rag/helpers.py`（追加一段）
- Test: `backend/tests/rag/test_retriever.py`

**Interfaces:**
- Consumes: Task 12-14 的全部组件；Task 11 的 `RagRuntime`
- Produces:
  - `RagRetriever(*, settings, embedder, question_store, knowledge_store, lexical_index, reranker)`：`async retrieve(query, routes=ALL_ROUTES, use_rerank=True, top_k=None, use_threshold=True) -> RagContext`
  - `RagRuntime.retriever`（`cached_property`）：有 `rerank_api_key` 时用 `DashScopeReranker`，否则 `NoopReranker`
  - 测试替身 `FakeReranker(score_fn=None, error=None)`、`BrokenEmbedder()`；工具 `async seed_runtime(runtime, questions, knowledge=())`

- [ ] **Step 1: 追加测试替身**

在 `backend/tests/rag/helpers.py` 末尾追加：

```python
class FakeReranker:
    """
    用 score_fn(文档) 给每个候选打分并按分数排序；默认一律 0.9（保持融合顺序）。
    error 不为空时抛出它，模拟重排服务故障。calls 记录每次收到的 (query, documents)。
    """

    def __init__(self, score_fn=None, error=None):
        self.score_fn = score_fn or (lambda doc: 0.9)
        self.error = error
        self.calls = []

    async def rerank(self, query, documents, top_n):
        from backend.agents.rag.retrieval.reranker import RerankResult

        self.calls.append((query, list(documents)))
        if self.error is not None:
            raise self.error
        results = [RerankResult(i, self.score_fn(doc)) for i, doc in enumerate(documents)]
        results.sort(key=lambda r: -r.score)
        return results[:top_n]


class BrokenEmbedder:
    async def embed(self, texts):
        raise RuntimeError("embedding 服务不可用")


async def seed_runtime(runtime, questions, knowledge=()):
    """把题目与讲解写进运行时的替身存储，并重建词法索引。"""
    embedder = HashingEmbedder()
    if questions:
        vectors = await embedder.embed([q.stem for q in questions])
        await runtime.question_store.upsert(
            [q.id for q in questions], vectors, [q.stem for q in questions], [q.to_metadata() for q in questions]
        )
    if knowledge:
        vectors = await embedder.embed([k.text for k in knowledge])
        await runtime.knowledge_store.upsert(
            [k.id for k in knowledge], vectors, [k.text for k in knowledge], [k.to_metadata() for k in knowledge]
        )
    await runtime.warm_up()
```

- [ ] **Step 2: 写失败的测试**

创建 `backend/tests/rag/test_retriever.py`：

```python
import dataclasses

import pytest

import backend.agents.rag.retrieval.retriever as retriever_module
from backend.agents.rag.models import KnowledgeChunk, QuestionItem, RagQuery
from backend.agents.rag.retrieval.reranker import DashScopeReranker, NoopReranker
from backend.agents.rag.retrieval.retriever import RagRetriever
from backend.tests.rag.helpers import BrokenEmbedder, FakeReranker, make_test_runtime, seed_runtime


def _q(stem, kps, grade="七年级"):
    return QuestionItem(stem=stem, answer="略", knowledge_points=kps, grade=grade)


CORPUS = [
    _q("解方程 2x+3=7", ["一元一次方程"]),
    _q("解方程 5x-4=11，并检验", ["一元一次方程"]),
    _q("分解因式 x²-9", ["因式分解"], grade="八年级"),
    _q("鸡兔同笼，头 35 个，脚 94 只，鸡兔各几只", ["鸡兔同笼问题"], grade="四年级"),
    _q("长方形长 5 厘米、宽 3 厘米，求周长", ["长方形周长"], grade="三年级"),
    _q("解一元二次方程 x²-5x+6=0", ["一元二次方程"], grade="九年级"),
]
STEPS = "解一元一次方程的一般步骤：去分母、去括号、移项、合并同类项、系数化为 1。"
KNOWLEDGE = [KnowledgeChunk(text=STEPS, knowledge_points=["一元一次方程"])]
QUERY = RagQuery(text="解方程 3x+2=11", knowledge_points=["一元一次方程"], grade="七年级")


async def _retriever(settings, *, reranker=None, embedder=None):
    runtime = make_test_runtime(settings, reranker=reranker or FakeReranker())
    await seed_runtime(runtime, CORPUS, KNOWLEDGE)
    if embedder is not None:
        runtime.embedder = embedder
    return runtime.retriever


async def test_returns_relevant_questions_with_routes(rag_settings):
    ctx = await (await _retriever(rag_settings)).retrieve(QUERY)
    assert 0 < len(ctx.questions) <= rag_settings.final_top_k
    top = ctx.questions[0]
    assert "一元一次方程" in top.item.knowledge_points
    assert "metadata" in top.routes
    assert ctx.degraded == []
    assert {"embed", "recall", "rerank", "total"} <= ctx.timings_ms.keys()


async def test_the_users_own_question_is_never_returned(rag_settings):
    """防泄漏：用户问的就是库里这道题时，不能把它当参考题交给模型照抄。"""
    asked = CORPUS[3]
    ctx = await (await _retriever(rag_settings)).retrieve(RagQuery(asked.stem, asked.knowledge_points))
    assert asked.id not in [q.item.id for q in ctx.questions]


async def test_a_failing_route_is_skipped(rag_settings, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("BM25 挂了")

    monkeypatch.setattr(retriever_module, "lexical_route", boom)
    ctx = await (await _retriever(rag_settings)).retrieve(QUERY)
    assert ctx.degraded == ["lexical"]
    assert ctx.questions


async def test_embedding_failure_keeps_the_non_vector_routes(rag_settings):
    ctx = await (await _retriever(rag_settings, embedder=BrokenEmbedder())).retrieve(QUERY)
    assert "embed" in ctx.degraded
    assert ctx.questions, "BM25 与知识点两路不依赖向量，仍应有结果"
    assert ctx.knowledge == []


async def test_total_failure_returns_an_empty_context(rag_settings, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("挂了")

    monkeypatch.setattr(retriever_module, "lexical_route", boom)
    monkeypatch.setattr(retriever_module, "metadata_route", boom)
    ctx = await (await _retriever(rag_settings, embedder=BrokenEmbedder())).retrieve(QUERY)
    assert ctx.empty
    assert set(ctx.degraded) == {"embed", "lexical", "metadata"}


async def test_rerank_failure_falls_back_to_fusion_order(rag_settings):
    retriever = await _retriever(rag_settings, reranker=FakeReranker(error=RuntimeError("重排超时")))
    ctx = await retriever.retrieve(QUERY)
    assert ctx.degraded == ["rerank"]
    assert ctx.questions


async def test_reranker_decides_the_final_order(rag_settings):
    reranker = FakeReranker(score_fn=lambda doc: 0.95 if "5x-4" in doc else 0.5)
    ctx = await (await _retriever(rag_settings, reranker=reranker)).retrieve(QUERY)
    assert "5x-4" in ctx.questions[0].item.stem
    assert ctx.questions[0].score == 0.95


async def test_low_rerank_scores_are_dropped(rag_settings):
    reranker = FakeReranker(score_fn=lambda doc: 0.9 if "2x+3" in doc else 0.1)
    ctx = await (await _retriever(rag_settings, reranker=reranker)).retrieve(QUERY)
    assert [q.item.stem for q in ctx.questions] == ["解方程 2x+3=7"]


async def test_ablation_parameters(rag_settings):
    """离线评测用：只跑指定的召回路、不重排、不设阈值、取更多结果。"""
    reranker = FakeReranker()
    ctx = await (await _retriever(rag_settings, reranker=reranker)).retrieve(
        QUERY, routes=("lexical",), use_rerank=False, top_k=5
    )
    assert ctx.questions and all(q.routes == ["lexical"] for q in ctx.questions)
    assert reranker.calls == []


async def test_similar_knowledge_is_returned(rag_settings):
    ctx = await (await _retriever(rag_settings)).retrieve(
        RagQuery(text="解一元一次方程的一般步骤：去分母、去括号、移项", knowledge_points=["一元一次方程"])
    )
    assert [k.text for k in ctx.knowledge] == [STEPS]


async def test_unrelated_knowledge_is_filtered_out(rag_settings):
    ctx = await (await _retriever(rag_settings)).retrieve(RagQuery(text="鸡兔同笼，头 20 个脚 54 只"))
    assert ctx.knowledge == []


def test_runtime_builds_the_retriever_once(rag_settings):
    runtime = make_test_runtime(rag_settings, reranker=FakeReranker())
    assert isinstance(runtime.retriever, RagRetriever)
    assert runtime.retriever is runtime.retriever


@pytest.mark.parametrize("api_key, expected", [("k", DashScopeReranker), (None, NoopReranker)])
def test_runtime_picks_the_reranker_from_config(rag_settings, api_key, expected):
    runtime = make_test_runtime(dataclasses.replace(rag_settings, rerank_api_key=api_key))
    assert isinstance(runtime.retriever._reranker, expected)
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_retriever.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.agents.rag.retrieval.retriever'`

- [ ] **Step 4: 实现检索编排**

创建 `backend/agents/rag/retrieval/retriever.py`：

```python
"""
检索编排：向量化 → 三路并行召回（同时查教材库）→ RRF 融合 → 防泄漏过滤 → 重排 → Top-K。

降级原则——RAG 是增强，不是依赖：
- 向量化失败：只剩不需要向量的 BM25 与知识点两路，教材库跳过
- 某一路召回失败：跳过该路
- 重排失败：退回融合顺序
- 全部失败：返回空的 RagContext
每次降级都记进 ctx.degraded 并写日志，retrieve() 本身不抛异常。
"""
import asyncio
import time
from typing import Sequence

from backend.agents.rag.models import KnowledgeChunk, RagContext, RagQuery, RetrievedQuestion, content_id
from backend.agents.rag.retrieval.fusion import rrf_fuse
from backend.agents.rag.retrieval.routes import (
    ALL_ROUTES,
    ROUTE_DENSE,
    ROUTE_LEXICAL,
    ROUTE_METADATA,
    dense_route,
    lexical_route,
    metadata_route,
)
from backend.middleware.logging import get_logger

logger = get_logger(__name__)


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


async def _no_knowledge() -> list[KnowledgeChunk]:
    return []


class RagRetriever:
    def __init__(self, *, settings, embedder, question_store, knowledge_store, lexical_index, reranker):
        self._settings = settings
        self._embedder = embedder
        self._questions = question_store
        self._knowledge = knowledge_store
        self._index = lexical_index
        self._reranker = reranker

    async def retrieve(self, query: RagQuery, routes: Sequence[str] = ALL_ROUTES, use_rerank: bool = True,
                       top_k: int | None = None, use_threshold: bool = True) -> RagContext:
        """routes / use_rerank / top_k / use_threshold 供离线评测做消融，线上调用保持默认值。"""
        s = self._settings
        top_k = top_k or s.final_top_k
        ctx = RagContext()
        started = time.perf_counter()

        # 1. 向量化：查询只算一次，题库与教材库共用
        t = time.perf_counter()
        query_vector = None
        try:
            [query_vector] = await self._embedder.embed([query.text])
        except Exception as e:
            ctx.degraded.append("embed")
            logger.warning("RAG 查询向量化失败，跳过向量召回与教材库：%s", e)
        ctx.timings_ms["embed"] = _elapsed_ms(t)

        # 2. 三路召回与教材库并行
        t = time.perf_counter()
        route_calls = {}
        if ROUTE_DENSE in routes and query_vector is not None:
            route_calls[ROUTE_DENSE] = dense_route(self._questions, query_vector, s.route_top_k)
        if ROUTE_LEXICAL in routes:
            route_calls[ROUTE_LEXICAL] = lexical_route(self._index, query.text, s.route_top_k)
        if ROUTE_METADATA in routes and query.knowledge_points:
            route_calls[ROUTE_METADATA] = metadata_route(self._index, query, s.route_top_k)
        knowledge_call = self._search_knowledge(query_vector) if query_vector is not None else _no_knowledge()

        *route_results, knowledge_result = await asyncio.gather(
            *route_calls.values(), knowledge_call, return_exceptions=True
        )
        ranked: dict[str, list[str]] = {}
        dense_similarity: dict[str, float] = {}
        for name, result in zip(route_calls, route_results):
            if isinstance(result, BaseException):
                ctx.degraded.append(name)
                logger.warning("RAG 召回路 %s 失败，已跳过：%s", name, result)
                continue
            ranked[name] = [doc_id for doc_id, _ in result]
            if name == ROUTE_DENSE:
                dense_similarity = dict(result)
        if isinstance(knowledge_result, BaseException):
            ctx.degraded.append("knowledge")
            logger.warning("RAG 教材库检索失败，已跳过：%s", knowledge_result)
        else:
            ctx.knowledge = knowledge_result
        ctx.timings_ms["recall"] = _elapsed_ms(t)

        # 3. 融合 + 防泄漏
        query_id = content_id(query.text, "q_")
        candidates = []
        for cand in rrf_fuse(ranked, k=s.rrf_k, top_n=s.fusion_top_n):
            if cand.id == query_id or dense_similarity.get(cand.id, 0.0) >= s.leak_threshold:
                continue  # 用户问的就是库里这道题：不能把它当参考题交给模型照抄
            item = self._index.get(cand.id)
            if item is None:
                continue  # 向量库里有、词法索引尚未重建进来的题，下次重建后可用
            candidates.append((cand, item))

        # 4. 重排
        t = time.perf_counter()
        ctx.questions = await self._rerank(query, candidates, top_k, use_rerank, use_threshold, ctx)
        ctx.timings_ms["rerank"] = _elapsed_ms(t)
        ctx.timings_ms["total"] = _elapsed_ms(started)

        logger.info("RAG 检索：参考题 %s 道，讲解 %s 段，耗时 %s，降级 %s",
                    len(ctx.questions), len(ctx.knowledge), ctx.timings_ms, ctx.degraded or "无")
        return ctx

    async def _rerank(self, query, candidates, top_k, use_rerank, use_threshold, ctx) -> list[RetrievedQuestion]:
        if not candidates:
            return []
        fusion_order = [RetrievedQuestion(item, cand.score, cand.routes) for cand, item in candidates[:top_k]]
        if not use_rerank:
            return fusion_order
        try:
            results = await self._reranker.rerank(query.text, [item.stem for _, item in candidates], top_k)
        except Exception as e:
            ctx.degraded.append("rerank")
            logger.warning("RAG 重排失败，退回融合顺序：%s", e)
            return fusion_order

        selected: list[RetrievedQuestion] = []
        for result in results:
            if use_threshold and result.score is not None and result.score < self._settings.rerank_min_score:
                continue
            cand, item = candidates[result.index]
            score = result.score if result.score is not None else cand.score
            selected.append(RetrievedQuestion(item, score, cand.routes))
        return selected[:top_k]

    async def _search_knowledge(self, query_vector) -> list[KnowledgeChunk]:
        hits = await self._knowledge.query(query_vector, self._settings.knowledge_top_k)
        return [
            KnowledgeChunk.from_record(hit.document, hit.metadata)
            for hit in hits
            if hit.similarity >= self._settings.knowledge_min_similarity
        ]
```

- [ ] **Step 5: 给运行时加上检索器**

在 `backend/agents/rag/runtime.py` 顶部 import 区加入：

```python
from functools import cached_property
```

在 `RagRuntime` 类里、`ingest_service` 方法之后加入：

```python
    @cached_property
    def retriever(self):
        """第一次使用时组装。没有配置重排的 API key 时退化为不重排。"""
        from backend.agents.rag.retrieval.reranker import DashScopeReranker, NoopReranker
        from backend.agents.rag.retrieval.retriever import RagRetriever

        reranker = self._reranker
        if reranker is None:
            s = self.settings
            reranker = (DashScopeReranker(s.rerank_api_key, s.rerank_url, s.rerank_model)
                        if s.rerank_api_key else NoopReranker())
        return RagRetriever(
            settings=self.settings,
            embedder=self.embedder,
            question_store=self.question_store,
            knowledge_store=self.knowledge_store,
            lexical_index=self.lexical_index,
            reranker=reranker,
        )
```

检索器用到的模块在属性内部导入，所以 `runtime.py` 本身不依赖检索层。

- [ ] **Step 6: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_retriever.py -v`
Expected: 14 passed

- [ ] **Step 7: 提交**

```bash
git add backend/agents/rag/retrieval/retriever.py backend/agents/rag/runtime.py backend/tests/rag/helpers.py backend/tests/rag/test_retriever.py
git commit -m "feat: RAG 检索编排（并行召回、防泄漏、逐级降级）"
```

---

## Task 16: 接入生题

> **这个任务做什么**：让线上的「生成变式题」真正用上 RAG。`question_set_tool` 在抽取知识点之后调用检索，把结果整理成「参考真题」「相关知识点」两段文字（总长不超过 1200 字），拼进生题模型的 system prompt，并附上使用规则（参考题型和难度，不许照抄）。生成之后做防照抄检查：和参考题太像（相似度不低于 0.9）就带上提示重试一次。服务启动时加载词法索引。RAG 任何环节失败，生题都照常进行。
>
> **前置**：依赖记忆计划的 Task 7 和 Task 12，开工前先确认它们已经完成。
>
> **做完之后**：用户调用生题接口时，背后会自动检索参考材料。这是 RAG 第一次对线上用户产生效果。

生题前先检索，把结果整理成「参考真题」与「相关知识点」两段拼进 system prompt，交给模型自己决定怎么用。

**前置：记忆计划 Task 7 与 Task 12 已完成。** Task 12 为 `question_set_tool` 注入了 `user_id` 并加入记忆召回 `recall`，这里在它的基础上扩展。system prompt 中各段的顺序是：生题剧本 → 参考材料 → 用户偏好。偏好放最后，作为最终的风格约束。

**防照抄的阈值是 0.9，是实测定下来的**：

| 生成结果（参考题是「鸡兔同笼，头35个，脚94只」） | 相似度 | 0.8 | 0.9 |
|---|---|---|---|
| 原样照搬 | 1.00 | 拦截 | 拦截 |
| 只改了一个数字 | 0.95 | 拦截 | 拦截 |
| 同题型、数字全换（正常变式） | 0.85 | **误伤** | 放行 |
| 换了情境和数字 | 0.25 | 放行 | 放行 |

参考题与用户的原题经常是同一类题型，正常的变式题与参考题相似度本来就高，阈值再低就会误伤。

另外，启动时加载词法索引的逻辑单独放进 `lifecycle.py`：它可以单独测试，`hooks.py` 只负责在正确的位置调用它。

**Files:**
- Create: `backend/agents/rag/retrieval/context.py`
- Create: `backend/agents/rag/integration.py`
- Create: `backend/agents/rag/lifecycle.py`
- Modify: `backend/agents/agent/question_set_agent.py`
- Modify: `backend/agents/tools/question_set_tool.py`
- Modify: `backend/core/hooks.py`
- Test: `backend/tests/rag/test_integration.py`

**Interfaces:**
- Consumes: Task 11 的 `get_rag_runtime`；Task 15 的 `retriever`；记忆计划 Task 12 的 `recall_context`
- Produces:
  - `format_rag_context(ctx, max_chars=1200) -> str`、`copy_ratio(generated, reference) -> float`、`too_similar(generated, references, threshold=0.9) -> bool`
  - 常量 `QUESTIONS_HEADER`、`KNOWLEDGE_HEADER`、`USAGE_RULE`、`COPY_THRESHOLD`
  - `async rag_references(query_text, extract, user_id) -> RagContext | None`（任何失败都返回 None）、`async user_grade(user_id) -> str | None`
  - `async rag_startup(runtime_provider=None)`
  - `async_question_set_tool` 新增读取 `text['references']`（`RagContext | None`）

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/rag/test_integration.py`：

```python
import pytest

import backend.agents.agent.question_set_agent as qs_agent
import backend.agents.rag.integration as integration
from backend.agents.rag.lifecycle import rag_startup
from backend.agents.rag.models import KnowledgeChunk, QuestionItem, RagContext, RagQuery, RetrievedQuestion
from backend.agents.rag.retrieval.context import (
    KNOWLEDGE_HEADER,
    QUESTIONS_HEADER,
    USAGE_RULE,
    copy_ratio,
    format_rag_context,
    too_similar,
)
from backend.agents.rag.runtime import set_rag_runtime
from backend.tests.rag.helpers import FakeReranker, ScriptedLLM, make_test_runtime, seed_runtime

REF = QuestionItem(stem="鸡兔同笼，头35个，脚94只，鸡兔各几只", answer="鸡23只，兔12只",
                   question_type="应用题", difficulty="中等", knowledge_points=["鸡兔同笼问题"])
CHUNK = KnowledgeChunk(text="鸡兔同笼问题常用假设法：先假设全是鸡。", knowledge_points=["鸡兔同笼问题"])


def _ctx(questions=(REF,), knowledge=(CHUNK,)):
    return RagContext(questions=[RetrievedQuestion(q, 0.9, ["dense"]) for q in questions], knowledge=list(knowledge))


# ---------- 上下文格式化 ----------

def test_empty_context_produces_nothing():
    assert format_rag_context(None) == ""
    assert format_rag_context(RagContext()) == ""


def test_context_lists_references_then_knowledge_then_rule():
    text = format_rag_context(_ctx())
    assert text.index(QUESTIONS_HEADER) < text.index(REF.stem) < text.index(KNOWLEDGE_HEADER) < text.index(CHUNK.text)
    assert "答案：鸡23只，兔12只" in text
    assert text.endswith(USAGE_RULE)


def test_budget_drops_whole_items_and_never_leaves_an_empty_header():
    many = [QuestionItem(stem=f"第{i}题：" + "题干" * 60, answer="略") for i in range(5)]
    long_chunk = KnowledgeChunk(text="讲解" * 100)
    text = format_rag_context(_ctx(questions=many, knowledge=[long_chunk]), max_chars=500)
    assert len(text) <= 500
    assert "1. [" in text and "5. [" not in text
    assert KNOWLEDGE_HEADER not in text, "放不下第一条讲解时，连标题一起省略"
    assert text.endswith(USAGE_RULE)


# ---------- 防照抄 ----------

@pytest.mark.parametrize("generated, flagged", [
    ("应用题\n鸡兔同笼，头35个，脚94只，鸡兔各几只\n答案：鸡23只，兔12只", True),   # 原样照搬
    ("应用题\n鸡兔同笼，头36个，脚94只，鸡兔各几只\n答案：鸡25只，兔11只", True),   # 只改一个数字
    ("应用题\n鸡兔同笼，头30个，脚80只，鸡兔各几只\n答案：鸡20只，兔10只", False),  # 同题型的正常变式
    ("应用题\n停车场有汽车和摩托车共20辆，轮子共56个，各有几辆\n答案：汽车8辆", False),
])
def test_copy_detection_only_catches_near_verbatim_copies(generated, flagged):
    assert too_similar(generated, [REF.stem]) is flagged


def test_copy_ratio_bounds():
    assert copy_ratio("任何内容", "") == 0.0
    assert copy_ratio(REF.stem, REF.stem) == 1.0


# ---------- rag_references ----------

async def test_rag_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("RAG_ENABLED", "false")
    assert await integration.rag_references("解方程 2x+3=7", {}, 1) is None


async def test_any_failure_returns_none(monkeypatch):
    monkeypatch.setenv("RAG_ENABLED", "true")

    def broken_runtime():
        raise RuntimeError("chromadb 导入失败")

    monkeypatch.setattr(integration, "get_rag_runtime", broken_runtime)
    assert await integration.rag_references("解方程 2x+3=7", {}, 1) is None


async def test_query_is_built_from_extract_and_profile(monkeypatch, rag_settings):
    monkeypatch.setenv("RAG_ENABLED", "true")
    captured = {}

    class RecordingRetriever:
        async def retrieve(self, query: RagQuery):
            captured["query"] = query
            return RagContext()

    async def grade_of(user_id):
        return "四年级"

    runtime = make_test_runtime(rag_settings)
    runtime.__dict__["retriever"] = RecordingRetriever()  # 覆盖 cached_property
    monkeypatch.setattr(integration, "get_rag_runtime", lambda: runtime)
    monkeypatch.setattr(integration, "user_grade", grade_of)

    await integration.rag_references("鸡兔同笼……", {"knowledge_points": ["鸡兔同笼问题"], "difficulty": "未知"}, 1)
    query = captured["query"]
    assert (query.knowledge_points, query.grade) == (["鸡兔同笼问题"], "四年级")
    assert query.difficulty is None, "extract 失败时的「未知」不应当作难度条件"


async def test_end_to_end_with_real_retriever(monkeypatch, rag_settings):
    monkeypatch.setenv("RAG_ENABLED", "true")
    runtime = make_test_runtime(rag_settings, reranker=FakeReranker())
    await seed_runtime(runtime, [REF], [CHUNK])
    set_rag_runtime(runtime)
    try:
        ctx = await integration.rag_references("鸡兔同笼，头20个，脚54只", {"knowledge_points": ["鸡兔同笼问题"]}, None)
    finally:
        set_rag_runtime(None)
    assert [q.item.stem for q in ctx.questions] == [REF.stem]


# ---------- 接入生题 ----------

VARIANT = "应用题\n停车场有汽车和摩托车共20辆，轮子共56个，各有几辆\n答案：汽车8辆，摩托车12辆"
TEXT = {"input": "鸡兔同笼，头20个，脚54只", "extract": {"difficulty": "中等", "knowledge_points": ["鸡兔同笼问题"]}}


async def test_prompt_order_is_skill_then_references_then_preferences(monkeypatch):
    llm = ScriptedLLM(VARIANT)
    monkeypatch.setattr(qs_agent, "build_question_set_agent", lambda **kw: llm)
    result = await qs_agent.async_question_set_tool({**TEXT, "references": _ctx(), "recall": "【该学生的历史偏好】不要雷同"})

    assert result["result"] == VARIANT
    system = llm.calls[0][0].content
    assert system.index("变式") < system.index(QUESTIONS_HEADER) < system.index("【该学生的历史偏好】")


async def test_no_references_means_no_rag_section(monkeypatch):
    llm = ScriptedLLM(VARIANT)
    monkeypatch.setattr(qs_agent, "build_question_set_agent", lambda **kw: llm)
    await qs_agent.async_question_set_tool({**TEXT, "references": None})
    assert QUESTIONS_HEADER not in llm.calls[0][0].content


async def test_copying_a_reference_triggers_one_retry(monkeypatch):
    copied = f"应用题\n{REF.stem}\n答案：{REF.answer}"
    llm = ScriptedLLM(copied, VARIANT)
    monkeypatch.setattr(qs_agent, "build_question_set_agent", lambda **kw: llm)
    result = await qs_agent.async_question_set_tool({**TEXT, "references": _ctx()})

    assert len(llm.calls) == 2
    assert "过于相似" in llm.calls[1][-1].content
    assert result["result"] == VARIANT


async def test_tool_passes_references_and_user_id(monkeypatch):
    # 工具模块会连带导入记忆计划 Task 12 的召回模块，它在导入时就要连 chromadb；
    # 所以只在这一条测试里、先确认 chromadb 可用再导入，不拖累其余测试
    pytest.importorskip("chromadb", exc_type=ImportError)
    import backend.agents.tools.question_set_tool as qs_tool

    captured = {}

    async def fake_extract(query):
        return {"difficulty": "中等", "knowledge_points": ["鸡兔同笼问题"]}

    async def fake_references(query, extract, user_id):
        captured["user_id"] = user_id
        return _ctx()

    async def fake_generate(payload):
        captured["payload"] = payload
        return {"result": VARIANT}

    monkeypatch.setattr(qs_tool, "async_extract_tool", fake_extract)
    monkeypatch.setattr(qs_tool, "rag_references", fake_references)
    monkeypatch.setattr(qs_tool, "async_question_set_tool", fake_generate)

    out = await qs_tool.QuestionSetTool()._arun(query="鸡兔同笼，头20个，脚54只", user_id=7)
    assert captured["user_id"] == 7
    assert captured["payload"]["references"].questions[0].item.stem == REF.stem
    assert "已生成变式题" in out


# ---------- 启动钩子 ----------

async def test_startup_warms_up_the_index(monkeypatch, rag_settings):
    monkeypatch.setenv("RAG_ENABLED", "true")
    runtime = make_test_runtime(rag_settings)
    await seed_runtime(runtime, [REF])
    runtime.lexical_index.rebuild([])  # 模拟刚启动：索引为空
    await rag_startup(runtime_provider=lambda: runtime)
    assert runtime.lexical_index.contains(REF.id)


async def test_startup_failure_does_not_raise(monkeypatch):
    monkeypatch.setenv("RAG_ENABLED", "true")

    def broken():
        raise RuntimeError("chromadb 导入失败")

    await rag_startup(runtime_provider=broken)  # 不抛异常即通过


async def test_startup_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("RAG_ENABLED", "false")

    def must_not_be_called():
        raise AssertionError("RAG 关闭时不应组装运行时")

    await rag_startup(runtime_provider=must_not_be_called)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_integration.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.agents.rag.integration'`

- [ ] **Step 3: 参考材料格式化与防照抄**

创建 `backend/agents/rag/retrieval/context.py`：

```python
"""
把检索结果整理成拼进生题 system prompt 的文本，以及防照抄检查。
"""
import difflib

from backend.agents.rag.models import RagContext, RetrievedQuestion, normalize_text

QUESTIONS_HEADER = "【参考真题】（检索自题库，按相关度排序）"
KNOWLEDGE_HEADER = "【相关知识点】"
USAGE_RULE = "使用规则：参考上面题目的题型、难度与考法；不得照抄题干或数字；若参考题与原题考查的知识点不一致，忽略它。"

# 0.9 而不是更低：参考题与原题常是同一类题型，正常换数字的变式题与参考题的相似度本来就在 0.85 左右，
# 阈值再低就会误伤；这里只拦截「几乎原样照搬」（原样 1.0，只改一个数字约 0.95）
COPY_THRESHOLD = 0.9


def _question_block(number: int, rq: RetrievedQuestion) -> str:
    item = rq.item
    meta = f"{item.question_type}｜{item.difficulty}｜{'、'.join(item.knowledge_points) or '未标注知识点'}"
    return f"{number}. [{meta}] {item.stem}\n   答案：{item.answer}"


def format_rag_context(ctx: RagContext | None, max_chars: int = 1200) -> str:
    """
    总长度不超过 max_chars。放不下时整条丢弃靠后的条目，不截断半条；
    某一节连第一条都放不下时，连标题一起省略，不留空标题。没有任何内容时返回空串。
    """
    if ctx is None or ctx.empty:
        return ""
    budget = max_chars - len(USAGE_RULE) - 1
    lines: list[str] = []
    used = 0

    sections = [
        (QUESTIONS_HEADER, [_question_block(i, q) for i, q in enumerate(ctx.questions, start=1)]),
        (KNOWLEDGE_HEADER, [f"- {k.text}" for k in ctx.knowledge]),
    ]
    for header, blocks in sections:
        if not blocks or used + len(header) + len(blocks[0]) + 2 > budget:
            continue
        lines.append(header)
        used += len(header) + 1
        for block in blocks:
            if used + len(block) + 1 > budget:
                break  # 保持编号连续：放不下就停，不跳过去塞后面更短的
            lines.append(block)
            used += len(block) + 1

    return "\n".join(lines + [USAGE_RULE]) if lines else ""


def copy_ratio(generated: str, reference: str) -> float:
    """reference 的字符有多大比例按顺序出现在 generated 中，取值 0~1。"""
    g, r = normalize_text(generated), normalize_text(reference)
    if not r:
        return 0.0
    matcher = difflib.SequenceMatcher(None, r, g, autojunk=False)
    return sum(block.size for block in matcher.get_matching_blocks()) / len(r)


def too_similar(generated: str, references: list[str], threshold: float = COPY_THRESHOLD) -> bool:
    return any(copy_ratio(generated, ref) >= threshold for ref in references)
```

- [ ] **Step 4: 检索入口与启动钩子**

创建 `backend/agents/rag/integration.py`：

```python
"""
生题链路接入 RAG 的唯一入口。任何失败都返回 None——RAG 是增强，不是依赖。
"""
from backend.agents.rag.config import RagSettings
from backend.agents.rag.models import DIFFICULTIES, RagContext, RagQuery
from backend.agents.rag.runtime import get_rag_runtime
from backend.middleware.logging import get_logger

logger = get_logger(__name__)


async def user_grade(user_id: int | None) -> str | None:
    """从长期画像取年级，用来过滤超纲的参考题。取不到就不过滤。"""
    if user_id is None:
        return None
    try:
        from backend.dao.user_profile_mapper import get_user_profile_mapper

        profile = await (await get_user_profile_mapper()).get_by_user_id(user_id)
        return profile.grade if profile and profile.grade else None
    except Exception as e:
        logger.warning("读取用户 %s 的年级失败，本次检索不按年级过滤：%s", user_id, e)
        return None


async def rag_references(query_text: str, extract: dict | None, user_id: int | None) -> RagContext | None:
    """
    生题前的检索。query_text 是用户的原题，extract 是 extract_tool 的结果
    （含 knowledge_points 与 difficulty）。
    """
    if not RagSettings.from_env().enabled:
        return None
    extract = extract or {}
    difficulty = extract.get("difficulty")
    try:
        query = RagQuery(
            text=query_text,
            knowledge_points=list(extract.get("knowledge_points") or []),
            difficulty=difficulty if difficulty in DIFFICULTIES else None,  # extract 失败时是「未知」
            grade=await user_grade(user_id),
        )
        return await get_rag_runtime().retriever.retrieve(query)
    except Exception as e:
        logger.warning("RAG 检索不可用，本次生题不使用参考题：%s", e, exc_info=True)
        return None
```

创建 `backend/agents/rag/lifecycle.py`：

```python
"""
RAG 在服务启动与关停时要做的事集中在这里，core/hooks.py 只负责在正确的位置调用。
每个函数都自己吞掉异常：RAG 出问题不能阻止服务启动或正常关停。
"""
from backend.agents.rag.config import RagSettings
from backend.middleware.logging import get_logger

logger = get_logger(__name__)


async def rag_startup(runtime_provider=None) -> None:
    """从向量库加载词法索引。runtime_provider 仅供测试注入。"""
    if not RagSettings.from_env().enabled:
        logger.info("RAG 已关闭（RAG_ENABLED=false），生题不做检索")
        return
    try:
        if runtime_provider is None:
            from backend.agents.rag.runtime import get_rag_runtime
            runtime_provider = get_rag_runtime
        count = await runtime_provider().warm_up()
        logger.info("RAG 词法索引已加载 %s 道题", count)
    except Exception as e:
        logger.error("RAG 初始化失败，生题将不使用参考题：%s", e, exc_info=True)
```

- [ ] **Step 5: 生题时注入参考材料并防照抄**

`backend/agents/agent/question_set_agent.py` 顶部的 import 区调整为：

```python
import os

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph.state import CompiledStateGraph

from backend.agents.agent.tools import GraphState
from backend.agents.agent.get_llm import get_llm
from backend.agents.rag.retrieval.context import format_rag_context, too_similar
from backend.agents.skills import load_skill
from backend.agents.skills.skill_runner import run_validator
from backend.core.single_tool import singleton_method
from backend.middleware.logging import get_logger

from backend.core.config import load_env

load_env()
logger = get_logger(__name__)

COPY_RETRY_PROMPT = (
    "你的题目与【参考真题】过于相似。请以原题为基础重新出一道变式题，"
    "参考题只用于把握题型和难度，不要照搬它的题干和数字。"
)
```

`async_question_set_tool` 整体替换为：

```python
async def async_question_set_tool(text: dict) -> dict:
    try:
        user_input = text['input']
        difficulty = text['extract']['difficulty']
        knowledge_points = text['extract']['knowledge_points']

        enhanced_input = (
            f"请基于以下参考题目生成一道变式题：{user_input}\n"
            f"知识点要求：{knowledge_points}\n"
            f"难度要求：{difficulty}"
        )

        # system prompt 的顺序：生题剧本 → 参考材料（RAG）→ 用户偏好（记忆召回）
        # 偏好放最后，作为最终的风格约束
        system_body = load_skill("question_variant")
        references = text.get('references')
        rag_section = format_rag_context(references)
        if rag_section:
            system_body = f"{system_body}\n\n{rag_section}"
        recall = text.get('recall', '')
        if recall:
            system_body = f"{system_body}\n\n{recall}"

        llm = build_question_set_agent(streaming=False)
        messages = [SystemMessage(content=system_body), HumanMessage(content=enhanced_input)]
        response = await llm.ainvoke(messages)
        result_text = response.content

        # 防照抄：几乎原样照搬了某道参考题时，带上提示重试一次
        reference_stems = [q.item.stem for q in references.questions] if references else []
        if reference_stems and too_similar(result_text, reference_stems):
            logger.info("生成结果与参考题过于相似，重试一次")
            retry = await llm.ainvoke(messages + [AIMessage(content=result_text),
                                                  HumanMessage(content=COPY_RETRY_PROMPT)])
            result_text = retry.content
            if too_similar(result_text, reference_stems):
                logger.warning("重试后仍与参考题过于相似，按原样返回")

        is_valid, reason = run_validator("question_variant", result_text)
        if not is_valid:
            return {'error': f"生成题目未通过校验：{reason}"}

        return {
            'result': result_text,
            'difficulty': difficulty,
            'knowledge_points': knowledge_points,
        }
    except Exception as e:
        return {'error': str(e)}
```

- [ ] **Step 6: 工具调用检索**

`backend/agents/tools/question_set_tool.py` 的最终内容如下。与记忆计划 Task 12 完成后的版本相比，只多了两处：导入 `rag_references`，以及 `new_input` 里的 `references` 一行。

```python
from typing import Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from backend.agents.agent.extract_agent import async_extract_tool
from backend.agents.agent.question_set_agent import async_question_set_tool
from backend.agents.memory.recall import recall_context
from backend.agents.rag.integration import rag_references


class QuestionSetInput(BaseModel):
    query: str = Field(description="用户的题目请求")


class QuestionSetTool(BaseTool):
    name: str = "question_set_tool"
    description: str = (
        "根据已有的题目生成变式题"
        "不要连续多次调用该工具，否则会导致最终结果无法返回"
    )
    args_schema: Type[BaseModel] = QuestionSetInput

    def _run(self, *args, **kwargs):
        raise NotImplementedError("QuestionSetTool 仅支持异步调用，请使用 _arun")

    async def _arun(self, query: str, user_id: Optional[int] = None) -> str:
        """执行题目生成工具"""
        try:
            extract = await async_extract_tool(query)
            new_input = {
                'input': query,
                'extract': extract,
                'references': await rag_references(query, extract, user_id),  # RAG 参考材料
                'recall': await recall_context(query, user_id),                # 记忆计划 Task 12
            }
            result = await async_question_set_tool(new_input)
            if 'error' in result:
                return f"【题目生成】生成变式题失败：{result['error']}"
            return f"【题目生成】已生成变式题：\n{result['result']}"
        except Exception as e:
            return f"【题目生成】生成变式题失败：{str(e)}"
```

- [ ] **Step 7: 启动时加载词法索引**

在 `backend/core/hooks.py` 的 import 区加入：

```python
from backend.agents.rag.lifecycle import rag_startup
```

在 `startup_event()` 的末尾（记忆归档恢复之后）加入：

```python
    await rag_startup()  # 内部吞掉异常：RAG 出问题不能阻止服务启动
```

- [ ] **Step 8: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_integration.py -v -rs`
Expected: chromadb 可用时 19 passed；不可用时 18 passed、1 skipped——被跳过的是测工具层的那一条，它导入的工具模块会连带导入记忆计划 Task 12 的召回模块

- [ ] **Step 9: 端到端试一次（需要 chromadb、裁判配置与已入库的资料）**

启动服务后，用普通账号调用 `/agent/analyse`，请它生成一道变式题。日志里应该能看到一行 `RAG 检索：参考题 N 道……`，里面有每个阶段的耗时，降级一项应为「无」。

- [ ] **Step 10: 提交**

```bash
git add backend/agents/rag/retrieval/context.py backend/agents/rag/integration.py backend/agents/rag/lifecycle.py backend/agents/agent/question_set_agent.py backend/agents/tools/question_set_tool.py backend/core/hooks.py backend/tests/rag/test_integration.py
git commit -m "feat: 生题接入 RAG 参考材料，附防照抄重试"
```

---

## Task 17: 管理员鉴权、后台任务执行器与生命周期

> **这个任务做什么**：为后台管理接口（Task 18）准备三样基础设施。① `require_admin`：FastAPI 依赖，非管理员（`user_privilege < 1`）直接返回 403；② `RagJobRunner`：进程内的后台任务执行器，入库任务提交后在后台运行，同一时间只跑一个，并把状态写进 Task 4 的任务表；③ 生命周期：服务启动时把上次没跑完的任务标记为失败，关停时最多等 10 秒让在途任务结束，再关数据库连接和线程池。
>
> **前置**：依赖记忆计划 Task 7 改造后的 `core/hooks.py`，开工前先确认它已经完成。
>
> **做完之后**：接口层只需要提交任务、查询状态，不用自己处理并发和关停收尾。

入库任务在进程内后台运行，**同一时间只跑一个**：入库会密集调用 LLM，词法索引的重建也需要串行。服务重启时，没跑完的任务一律标记为失败，不做断点续传——id 是哈希、入库是幂等的，重新提交即可，已入库的部分会被自动跳过。

**关于测试**：`backend/api/__init__.py` 在包初始化时会导入全部路由，`agent_api` 导入时就要实例化向量库，所以**导入 `backend.api` 下的任何模块都会连带导入 chromadb**。这是项目原有的结构，本次不改。鉴权的测试因此单独放一个文件、开头用 `importorskip` 守住；执行器的测试不碰 `backend.api`，照常运行。

**Files:**
- Create: `backend/api/manage_api/dependencies.py`
- Create: `backend/agents/rag/jobs.py`
- Modify: `backend/agents/rag/lifecycle.py`（追加一段）
- Modify: `backend/core/hooks.py`
- Test: `backend/tests/rag/test_jobs.py`、`backend/tests/rag/test_admin_dependency.py`

**Interfaces:**
- Consumes: Task 4 的 `RagJobMapper`；Task 10 的 `IngestReport`
- Produces:
  - `async require_admin(user=Depends(get_current_user)) -> User`：`user_privilege < 1` 时 403
  - `RagJobRunner(job_mapper, max_concurrency=1)`：`submit(job_id, work) -> asyncio.Task`、`async shutdown(timeout=10.0)`、属性 `in_flight`
  - `get_job_runner()`
  - `async recover_interrupted_jobs(job_mapper=None)`、`async rag_shutdown(runner=None, timeout=10.0)`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/rag/test_jobs.py`：

```python
import asyncio

from backend.agents.rag.ingest.service import IngestReport
from backend.agents.rag.jobs import RagJobRunner
from backend.agents.rag.lifecycle import rag_shutdown, recover_interrupted_jobs
from backend.dao.rag_mapper import RagJobMapper


def _work(report=None, error=None, delay=0.0, log=None, name=""):
    async def work():
        if log is not None:
            log.append(f"开始 {name}")
        await asyncio.sleep(delay)
        if log is not None:
            log.append(f"结束 {name}")
        if error is not None:
            raise error
        return report or IngestReport()
    return work


# ---------- 任务执行器 ----------

async def test_successful_job_records_its_report(sqlite_session_factory):
    jobs = RagJobMapper(sqlite_session_factory)
    runner = RagJobRunner(jobs)
    job_id = await jobs.create("file", source="a.pdf")

    await runner.submit(job_id, _work(IngestReport(passed=3)))
    job = await jobs.get(job_id)
    assert job["status"] == "succeeded" and job["report"]["passed"] == 3


async def test_failed_job_records_the_error(sqlite_session_factory):
    jobs = RagJobMapper(sqlite_session_factory)
    runner = RagJobRunner(jobs)
    job_id = await jobs.create("file", source="a.pdf")

    await runner.submit(job_id, _work(error=ValueError("疑似扫描件")))
    job = await jobs.get(job_id)
    assert job["status"] == "failed" and "疑似扫描件" in job["error"]


async def test_jobs_run_one_at_a_time(sqlite_session_factory):
    jobs = RagJobMapper(sqlite_session_factory)
    runner = RagJobRunner(jobs)
    log = []
    first = await jobs.create("file", source="a")
    second = await jobs.create("file", source="b")

    await asyncio.gather(
        runner.submit(first, _work(delay=0.05, log=log, name="a")),
        runner.submit(second, _work(delay=0.05, log=log, name="b")),
    )
    assert log == ["开始 a", "结束 a", "开始 b", "结束 b"]


async def test_shutdown_waits_for_in_flight_jobs(sqlite_session_factory):
    jobs = RagJobMapper(sqlite_session_factory)
    runner = RagJobRunner(jobs)
    job_id = await jobs.create("file", source="a")

    runner.submit(job_id, _work(delay=0.05))
    await runner.shutdown(timeout=5)
    assert runner.in_flight == 0
    assert (await jobs.get(job_id))["status"] == "succeeded"


async def test_jobs_exceeding_the_shutdown_timeout_are_left_for_recovery(sqlite_session_factory):
    jobs = RagJobMapper(sqlite_session_factory)
    runner = RagJobRunner(jobs)
    job_id = await jobs.create("file", source="a")

    runner.submit(job_id, _work(delay=5))
    await asyncio.sleep(0.05)  # 让任务开始运行
    await runner.shutdown(timeout=0.05)
    assert (await jobs.get(job_id))["status"] == "running"

    await recover_interrupted_jobs(job_mapper=jobs)  # 模拟下次启动
    assert (await jobs.get(job_id))["status"] == "failed"


# ---------- 生命周期钩子 ----------

async def test_lifecycle_hooks_swallow_errors():
    class Broken:
        async def fail_interrupted(self):
            raise RuntimeError("数据库连不上")

        async def shutdown(self, timeout):
            raise RuntimeError("关停出错")

    await recover_interrupted_jobs(job_mapper=Broken())
    await rag_shutdown(runner=Broken())
```

创建 `backend/tests/rag/test_admin_dependency.py`：

```python
from types import SimpleNamespace

import pytest

# backend/api/__init__.py 在包初始化时会导入全部路由，agent_api 导入时就要连 chromadb。
# chromadb 不可用（例如编译扩展缺失）时明确跳过，而不是在收集阶段报错。
pytest.importorskip("chromadb", exc_type=ImportError)

from fastapi import HTTPException  # noqa: E402

from backend.api.manage_api.dependencies import require_admin  # noqa: E402


async def test_non_admin_is_forbidden():
    with pytest.raises(HTTPException) as exc:
        await require_admin(user=SimpleNamespace(user_privilege=0))
    assert exc.value.status_code == 403


async def test_missing_privilege_counts_as_normal_user():
    with pytest.raises(HTTPException):
        await require_admin(user=SimpleNamespace(user_privilege=None))


@pytest.mark.parametrize("privilege", [1, 2])
async def test_admins_pass(privilege):
    user = SimpleNamespace(user_privilege=privilege)
    assert await require_admin(user=user) is user
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_jobs.py tests/rag/test_admin_dependency.py -v -rs`
Expected: `test_jobs.py` 在收集阶段报 `ModuleNotFoundError: No module named 'backend.agents.rag.jobs'`；chromadb 可用时 `test_admin_dependency.py` 报找不到 `backend.api.manage_api.dependencies`，不可用时整个文件被跳过

- [ ] **Step 3: 实现**

创建 `backend/api/manage_api/dependencies.py`：

```python
from fastapi import Depends, HTTPException, status

from backend.api.dependencies import get_current_user
from backend.model.user import User

ADMIN_PRIVILEGE = 1  # user_privilege：0 普通用户，1 管理员，2 超级管理员


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if (user.user_privilege or 0) < ADMIN_PRIVILEGE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user
```

创建 `backend/agents/rag/jobs.py`：

```python
"""
后台入库任务执行器（进程内）。

- 同一时间只跑一个任务：入库会密集调用 LLM，词法索引的重建也需要串行
- 持有 task 的强引用：事件循环只弱引用运行中的 task，不持有的话可能在完成前被垃圾回收
- 关停时等待在途任务；超时的会被取消，任务状态停在「运行中」，
  下次启动时由 lifecycle.recover_interrupted_jobs 标记为失败
"""
import asyncio
from typing import Awaitable, Callable

from backend.middleware.logging import get_logger

logger = get_logger(__name__)


class RagJobRunner:
    def __init__(self, job_mapper, max_concurrency: int = 1):
        self._jobs = job_mapper
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._tasks: set[asyncio.Task] = set()

    @property
    def in_flight(self) -> int:
        return len(self._tasks)

    def submit(self, job_id: str, work: Callable[[], Awaitable]) -> asyncio.Task:
        """work 是一个返回 IngestReport 的协程工厂；立即返回，不等任务执行。"""
        task = asyncio.create_task(self._run(job_id, work), name=f"rag-job-{job_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _run(self, job_id: str, work: Callable[[], Awaitable]) -> None:
        async with self._semaphore:
            await self._jobs.mark_running(job_id)
            try:
                report = await work()
            except Exception as e:
                logger.error("RAG 入库任务 %s 失败：%s", job_id, e, exc_info=True)
                await self._jobs.mark_failed(job_id, f"{type(e).__name__}: {e}")
                return
            await self._jobs.mark_succeeded(job_id, report.to_dict())
            logger.info("RAG 入库任务 %s 完成：%s", job_id, report.to_dict())

    async def shutdown(self, timeout: float = 10.0) -> None:
        if not self._tasks:
            return
        logger.info("等待 %s 个 RAG 入库任务结束……", len(self._tasks))
        _, pending = await asyncio.wait(list(self._tasks), timeout=timeout)
        for task in pending:
            task.cancel()
        if pending:
            # 让被取消的任务把自己的数据库会话等收拾干净，再继续关停流程
            await asyncio.gather(*pending, return_exceptions=True)
            logger.warning("%s 个 RAG 入库任务超时被取消，下次启动时会标记为失败", len(pending))


_runner: RagJobRunner | None = None


def get_job_runner() -> RagJobRunner:
    global _runner
    if _runner is None:
        from backend.dao.rag_mapper import RagJobMapper
        from backend.model import AsyncSessionLocal

        _runner = RagJobRunner(RagJobMapper(AsyncSessionLocal))
    return _runner
```

在 `backend/agents/rag/lifecycle.py` 末尾追加：

```python
async def recover_interrupted_jobs(job_mapper=None) -> None:
    """
    服务启动时（建表之后）调用：把上次没跑完的入库任务标记为失败。
    不做断点续传——入库是幂等的，重新提交即可，已入库的部分会被自动跳过。
    """
    try:
        if job_mapper is None:
            from backend.dao.rag_mapper import RagJobMapper
            from backend.model import AsyncSessionLocal

            job_mapper = RagJobMapper(AsyncSessionLocal)
        count = await job_mapper.fail_interrupted()
        if count:
            logger.warning("有 %s 个 RAG 入库任务因服务重启而中断，已标记为失败", count)
    except Exception as e:
        logger.error("恢复 RAG 入库任务状态失败：%s", e, exc_info=True)


async def rag_shutdown(runner=None, timeout: float = 10.0) -> None:
    """关停时等待在途的入库任务。必须在关闭数据库、Redis 与线程池之前调用——任务还要用它们。"""
    try:
        if runner is None:
            from backend.agents.rag.jobs import get_job_runner

            runner = get_job_runner()
        await runner.shutdown(timeout)
    except Exception as e:
        logger.error("等待 RAG 入库任务结束时出错：%s", e, exc_info=True)
```

- [ ] **Step 4: 接入启动与关停钩子**

`backend/core/hooks.py` 做三处修改：

1. import 区最前面加一行，注册 RAG 的表结构，`create_all` 才会建出它们：

```python
import backend.model.rag  # noqa: F401  注册 RAG 的表结构，create_all 才会建出它们
```

2. 把 Task 16 加的那行 import 扩展为：

```python
from backend.agents.rag.lifecycle import rag_shutdown, rag_startup, recover_interrupted_jobs
```

3. `startup_event()` 里，在 `await rag_startup()` **之前**加 `await recover_interrupted_jobs()`（它必须在建表之后）；`shutdown_event()` 里，在等待记忆归档之后、释放线程池与连接之前加 `await rag_shutdown(timeout=10.0)`——任务还要用这些资源。

改完后的完整 `hooks.py`（含记忆计划 Task 7 的部分）：

```python
import backend.model.rag  # noqa: F401  注册 RAG 的表结构，create_all 才会建出它们
from backend.agents.rag.lifecycle import rag_shutdown, rag_startup, recover_interrupted_jobs
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
    """应用启动：建表 → 补做上次未完成的记忆归档 → 恢复 RAG 任务状态 → 加载 RAG 索引"""
    logger.info("应用启动中，正在创建数据库表...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库表创建完成")

    try:
        recovered = await _get_memory_manager().drain_pending()
        logger.info("记忆归档恢复完成，补做 %s 条", recovered)
    except Exception as e:
        logger.error("记忆归档恢复失败：%s", e, exc_info=True)

    # RAG：两个函数都自己吞掉异常，不会阻止服务启动
    await recover_interrupted_jobs()  # 必须在建表之后
    await rag_startup()


async def shutdown_event():
    """应用关闭：先等在途的后台任务（它们还要用数据库、Redis、线程池），再释放资源"""
    logger.info("应用关闭中，等待在途记忆归档...")
    try:
        await _get_memory_manager().shutdown(timeout=10.0)
    except Exception as e:
        logger.error("等待归档任务时出错：%s", e, exc_info=True)
    await rag_shutdown(timeout=10.0)

    logger.info("正在释放线程池与数据库连接...")
    shutdown_executors(wait=True)
    await engine.dispose()
    await close_redis()
    logger.info("资源已全部释放")
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_jobs.py tests/rag/test_admin_dependency.py -v -rs`
Expected: chromadb 可用时 10 passed；不可用时 6 passed，`test_admin_dependency.py` 整个文件跳过

- [ ] **Step 6: 提交**

```bash
git add backend/api/manage_api/dependencies.py backend/agents/rag/jobs.py backend/agents/rag/lifecycle.py backend/core/hooks.py backend/tests/rag/test_jobs.py backend/tests/rag/test_admin_dependency.py
git commit -m "feat: RAG 后台任务执行器、管理员鉴权与生命周期钩子"
```

---

## Task 18: 上传、生成与任务查询接口

> **这个任务做什么**：提供管理后台调用的 HTTP 接口，路径都在 `/manage/rag/` 下：上传 PDF / Word 入库（`/upload`）、按知识点生成种子题（`/generate`）、查询任务进度和入库报告（`/jobs/{job_id}`）。上传和生成都是异步的：立即返回任务 id，实际入库交给 Task 17 的执行器。安全方面：落盘文件只用「任务 id + 后缀」命名以防路径穿越，大小限制 20MB，裁判没配好时直接返回 503。同时把一直没接进来的 `manage_api` 挂到 `main.py` 上。
>
> **做完之后**：不用命令行、也不用停服务就能入库，并能在 `/api` 接口文档里看到这几个接口。

路由挂在项目里已有的 `manage_api`（前缀 `/manage`）之下，自身前缀 `/rag`，最终路径 `/manage/rag/...`。`manage_api` 之前定义了但没接进 `main.py`，这次一并接入。

三个安全与体验上的细节：
- 落盘文件名只用「任务 id + 已校验的后缀」，**不用用户提供的文件名**，杜绝路径穿越；原始文件名单独传给入库服务，写进题目来源
- 裁判配置不对时，在**创建任务之前**就返回 503，不留下一个注定失败的任务
- 测试覆盖的是 `get_current_user` 而不是 `require_admin`，这样管理员校验的真实逻辑也在测试范围内

**Files:**
- Create: `backend/api/manage_api/rag_api.py`
- Modify: `backend/api/manage_api/__init__.py`
- Modify: `backend/main.py`
- Modify: `backend/tests/rag/conftest.py`（追加一段）
- Test: `backend/tests/rag/test_rag_api.py`

**Interfaces:**
- Consumes: Task 11 的 `RagRuntime`（`ensure_judge`、`ingest_service`）；Task 17 的 `RagJobRunner`、`require_admin`；Task 4 的两个 mapper
- Produces:
  - 路由 `rag_router`（前缀 `/rag`），经 `manage_api` 挂载为 `/manage/rag`
  - `POST /manage/rag/upload`（表单：`file`、可选 `grade`）→ `{"job_id", "status"}`
  - `POST /manage/rag/generate`（JSON：`knowledge_point, grade, difficulty, n`）→ `{"job_id", "status"}`
  - `GET /manage/rag/jobs/{job_id}` → 任务详情
  - 可被测试替换的依赖：`get_settings`、`get_job_mapper`、`get_quarantine_mapper`、`get_runner`、`get_runtime`
  - fixture `api`：属性 `client, settings, jobs, quarantine, runner, runtime, user`，均可在测试中替换

- [ ] **Step 1: 追加接口测试的 fixture**

在 `backend/tests/rag/conftest.py` 末尾追加。注意 `backend.api` 的导入写在函数内部：写在 conftest 顶层的话，chromadb 不可用时**所有** RAG 测试都会在收集阶段失败。

```python
from types import SimpleNamespace


@pytest_asyncio.fixture
async def api(rag_settings, sqlite_session_factory):
    """
    只挂载 manage_api 的最小 FastAPI 应用。依赖全部替换成离线替身；
    覆盖的是 get_current_user 而不是 require_admin，这样管理员校验的真实逻辑也在测试范围内。
    backend.api 的导入放在函数里：放在 conftest 顶层的话，chromadb 不可用时所有 RAG 测试都会在收集阶段失败。
    """
    import httpx
    from fastapi import FastAPI

    from backend.agents.rag.jobs import RagJobRunner
    from backend.api.dependencies import get_current_user
    from backend.api.manage_api import manage_api, rag_api
    from backend.dao.rag_mapper import RagJobMapper, RagQuarantineMapper
    from backend.tests.rag.helpers import make_test_runtime

    jobs = RagJobMapper(sqlite_session_factory)
    state = SimpleNamespace(
        settings=rag_settings,
        jobs=jobs,
        quarantine=RagQuarantineMapper(sqlite_session_factory),
        runner=RagJobRunner(jobs),
        runtime=make_test_runtime(rag_settings),
        user=SimpleNamespace(id=1, user_privilege=1),
    )
    app = FastAPI()
    app.include_router(manage_api)
    app.dependency_overrides.update({
        get_current_user: lambda: state.user,
        rag_api.get_settings: lambda: state.settings,
        rag_api.get_job_mapper: lambda: state.jobs,
        rag_api.get_quarantine_mapper: lambda: state.quarantine,
        rag_api.get_runner: lambda: state.runner,
        rag_api.get_runtime: lambda: state.runtime,
    })
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        state.client = client
        yield state
    await state.runner.shutdown(timeout=5)
```

- [ ] **Step 2: 写失败的测试**

创建 `backend/tests/rag/test_rag_api.py`：

```python
import dataclasses
import json
from types import SimpleNamespace

import pytest

# 见 test_admin_dependency.py：backend.api 包在初始化时会连带导入 chromadb
pytest.importorskip("chromadb", exc_type=ImportError)

from backend.agents.rag.ingest.judge import JudgeConfigError  # noqa: E402
from backend.agents.rag.models import QuestionItem  # noqa: E402
from backend.tests.rag.helpers import ScriptedLLM, make_test_runtime, write_docx  # noqa: E402

STRUCTURED = json.dumps({
    "questions": [{"stem": "解方程 2x+3=7", "answer": "x=2", "knowledge_points": ["一元一次方程"]}],
    "knowledge": [],
}, ensure_ascii=False)
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _docx_bytes(tmp_path) -> bytes:
    path = tmp_path / "source.docx"
    write_docx(path, ["一、解方程 2x+3=7。答案：x=2"])
    return path.read_bytes()


async def _upload(api, name, content, grade=None):
    data = {"grade": grade} if grade else {}
    return await api.client.post("/manage/rag/upload", files={"file": (name, content, DOCX_MIME)}, data=data)


async def test_non_admin_is_forbidden(api):
    api.user = SimpleNamespace(id=2, user_privilege=0)
    response = await _upload(api, "a.docx", b"x")
    assert response.status_code == 403


async def test_unsupported_file_type_is_rejected(api):
    response = await _upload(api, "notes.txt", b"hello")
    assert response.status_code == 400


async def test_empty_file_is_rejected(api):
    assert (await _upload(api, "a.docx", b"")).status_code == 400


async def test_oversized_file_is_rejected(api):
    api.settings = dataclasses.replace(api.settings, max_upload_mb=1)
    response = await _upload(api, "big.docx", b"0" * (1024 * 1024 + 1))
    assert response.status_code == 413


async def test_misconfigured_judge_returns_503_without_creating_a_job(api, tmp_path):
    api.runtime = make_test_runtime(api.settings, judge_error=JudgeConfigError("与生成模型属于同一家族"))
    response = await _upload(api, "a.docx", _docx_bytes(tmp_path))
    assert response.status_code == 503 and "同一家族" in response.json()["detail"]
    assert api.runner.in_flight == 0
    assert not api.settings.upload_dir.exists() or not any(api.settings.upload_dir.iterdir())


async def test_upload_ingests_in_the_background(api, tmp_path):
    api.runtime = make_test_runtime(api.settings, llm=ScriptedLLM(STRUCTURED))
    response = await _upload(api, "七年级期中卷.docx", _docx_bytes(tmp_path), grade="七年级")
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    await api.runner.shutdown(timeout=5)  # 等后台任务跑完
    job = (await api.client.get(f"/manage/rag/jobs/{job_id}")).json()
    assert job["status"] == "succeeded" and job["report"]["passed"] == 1
    assert job["source"] == "七年级期中卷.docx"

    stored = api.runtime.lexical_index.get(QuestionItem(stem="解方程 2x+3=7", answer="x").id)
    assert stored.source_ref == "七年级期中卷.docx#p1", "来源里记的是原始文件名，而不是落盘用的任务 id"
    assert stored.grade == "七年级"


async def test_uploaded_file_name_cannot_escape_the_upload_dir(api, tmp_path):
    api.runtime = make_test_runtime(api.settings, llm=ScriptedLLM(STRUCTURED))
    response = await _upload(api, "../../evil.docx", _docx_bytes(tmp_path))
    job_id = response.json()["job_id"]
    await api.runner.shutdown(timeout=5)

    assert list(api.settings.upload_dir.iterdir()) == [api.settings.upload_dir / f"{job_id}.docx"]
    assert not (api.settings.upload_dir.parent.parent / "evil.docx").exists()


async def test_generate_ingests_seed_questions(api):
    output = json.dumps([{"stem": "解方程 3x=12", "answer": "x=4"}], ensure_ascii=False)
    api.runtime = make_test_runtime(api.settings, llm=ScriptedLLM(output))
    response = await api.client.post("/manage/rag/generate", json={
        "knowledge_point": "一元一次方程", "grade": "七年级", "difficulty": "简单", "n": 1,
    })
    job_id = response.json()["job_id"]
    await api.runner.shutdown(timeout=5)

    job = (await api.client.get(f"/manage/rag/jobs/{job_id}")).json()
    assert job["status"] == "succeeded" and job["report"]["passed"] == 1
    assert job["kind"] == "generate"


async def test_generate_validates_input(api):
    response = await api.client.post("/manage/rag/generate", json={
        "knowledge_point": "一元一次方程", "grade": "七年级", "n": 0,
    })
    assert response.status_code == 422


async def test_unknown_job_is_404(api):
    assert (await api.client.get("/manage/rag/jobs/nope")).status_code == 404
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_rag_api.py -v -rs`
Expected: chromadb 可用时报 `ImportError: cannot import name 'rag_api' from 'backend.api.manage_api'`；不可用时整个文件跳过

- [ ] **Step 4: 实现接口**

创建 `backend/api/manage_api/rag_api.py`：

```python
"""
RAG 管理后台接口，挂在 manage_api（前缀 /manage）下，最终路径为 /manage/rag/...。
所有接口都要求管理员权限（user_privilege >= 1）。
"""
import asyncio
from functools import partial
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from backend.agents.rag.config import RagSettings
from backend.agents.rag.ingest.judge import JudgeConfigError
from backend.agents.rag.ingest.loaders import SUPPORTED_SUFFIXES
from backend.api.manage_api.dependencies import require_admin
from backend.core.executors import get_vector_executor

rag_router = APIRouter(prefix="/rag", tags=["manage-rag"])


# ---------- 依赖：测试里用 app.dependency_overrides 替换 ----------

def get_settings() -> RagSettings:
    return RagSettings.from_env()


def get_job_mapper():
    from backend.dao.rag_mapper import RagJobMapper
    from backend.model import AsyncSessionLocal

    return RagJobMapper(AsyncSessionLocal)


def get_quarantine_mapper():
    from backend.dao.rag_mapper import RagQuarantineMapper
    from backend.model import AsyncSessionLocal

    return RagQuarantineMapper(AsyncSessionLocal)


def get_runner():
    from backend.agents.rag.jobs import get_job_runner

    return get_job_runner()


def get_runtime():
    from backend.agents.rag.runtime import get_rag_runtime

    return get_rag_runtime()


def _ensure_judge(runtime) -> None:
    """在创建任务之前预检裁判配置，不留下一个注定失败的任务。"""
    try:
        runtime.ensure_judge()
    except JudgeConfigError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))


def _save(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


class GenerateRequest(BaseModel):
    knowledge_point: str = Field(min_length=1, max_length=50)
    grade: str = Field(min_length=1, max_length=16)
    difficulty: Literal["简单", "中等", "困难"] = "中等"
    n: int = Field(default=10, ge=1, le=50)


# ---------- 接口 ----------

@rag_router.post("/upload")
async def upload(
    file: UploadFile = File(...),
    grade: str | None = Form(default=None),
    admin=Depends(require_admin),
    settings: RagSettings = Depends(get_settings),
    jobs=Depends(get_job_mapper),
    quarantine=Depends(get_quarantine_mapper),
    runner=Depends(get_runner),
    runtime=Depends(get_runtime),
) -> dict:
    original_name = Path(file.filename or "").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"只支持 {'、'.join(SUPPORTED_SUFFIXES)} 文件")

    limit = settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"文件超过 {settings.max_upload_mb}MB 上限")
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    _ensure_judge(runtime)

    job_id = await jobs.create("file", source=original_name, params={"grade": grade}, created_by=admin.id)
    # 落盘只用「任务 id + 已校验的后缀」，不用用户提供的文件名：杜绝路径穿越
    dest = settings.upload_dir / f"{job_id}{suffix}"
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(get_vector_executor(), _save, dest, data)

    service = runtime.ingest_service(partial(quarantine.put, job_id=job_id))
    runner.submit(job_id, lambda: service.ingest_file(dest, grade_hint=grade, source_name=original_name))
    return {"job_id": job_id, "status": "pending"}


@rag_router.post("/generate")
async def generate(
    body: GenerateRequest,
    admin=Depends(require_admin),
    jobs=Depends(get_job_mapper),
    quarantine=Depends(get_quarantine_mapper),
    runner=Depends(get_runner),
    runtime=Depends(get_runtime),
) -> dict:
    _ensure_judge(runtime)
    job_id = await jobs.create(
        "generate",
        source=f"{body.knowledge_point} / {body.grade} / {body.difficulty} × {body.n}",
        params=body.model_dump(),
        created_by=admin.id,
    )
    service = runtime.ingest_service(partial(quarantine.put, job_id=job_id))
    runner.submit(job_id, lambda: service.generate_and_ingest(
        body.knowledge_point, body.grade, body.difficulty, body.n
    ))
    return {"job_id": job_id, "status": "pending"}


@rag_router.get("/jobs/{job_id}")
async def get_job(job_id: str, admin=Depends(require_admin), jobs=Depends(get_job_mapper)) -> dict:
    job = await jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job
```

`backend/api/manage_api/__init__.py` 改为：

```python
#定义manage_api的父层api，便于管理所有管理端api，降低耦合度
from fastapi import APIRouter

from backend.api.manage_api.rag_api import rag_router

manage_api=APIRouter(prefix='/manage',tags=['manage'])
manage_api.include_router(rag_router)
```

`backend/main.py` 的 import 区加入：

```python
from backend.api.manage_api import manage_api
```

在 `app.include_router(login_router)` 之后加入：

```python
app.include_router(manage_api)  # /manage/...，目前包含 RAG 管理接口
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_rag_api.py -v -rs`
Expected: chromadb 可用时 10 passed；不可用时整个文件跳过

- [ ] **Step 6: 在接口文档里确认路由**

启动服务，打开 `http://127.0.0.1:8000/docs`，应能看到 `manage-rag` 分组下的 `/manage/rag/upload`、`/manage/rag/generate`、`/manage/rag/jobs/{job_id}`。用管理员账号的 token 上传一份资料，再轮询任务接口，直到状态变为 `succeeded`。

- [ ] **Step 7: 提交**

```bash
git add backend/api/manage_api/rag_api.py backend/api/manage_api/__init__.py backend/main.py backend/tests/rag/conftest.py backend/tests/rag/test_rag_api.py
git commit -m "feat: RAG 管理接口（上传、生成、任务查询）"
```

---

## Task 19: 隔离区复核接口

> **这个任务做什么**：给管理员处理隔离区的接口：列出隔离区里的题（可按待处理 / 已通过 / 已丢弃筛选，支持分页）、人工通过（跳过裁判直接入库）、人工丢弃。对已经处理过的题再操作返回 409，条目不存在返回 404；并发操作时，同一道题也只能被处理一次。
>
> **做完之后**：被裁判误拒的好题可以捞回来，入库流程形成闭环。阶段五完成。

人工通过的题**跳过裁判直接入库**。实现上先入库再改状态：入库是幂等的，即使并发下被别人抢先处理，重复入库也不会产生脏数据；而状态更新是条件更新（Task 4），保证每个条目只能被处理一次。

**Files:**
- Modify: `backend/api/manage_api/rag_api.py`
- Test: `backend/tests/rag/test_rag_quarantine_api.py`

**Interfaces:**
- Consumes: Task 18 的 `rag_router` 与依赖；Task 10 的 `IngestService.approve`
- Produces:
  - `GET /manage/rag/quarantine?status=pending|approved|discarded&limit=&offset=` → `{"items": [...]}`
  - `POST /manage/rag/quarantine/{id}/approve` → `{"id", "status", "question_id"}`；已处理 409，不存在 404
  - `POST /manage/rag/quarantine/{id}/discard` → `{"id", "status"}`；已处理 409，不存在 404

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/rag/test_rag_quarantine_api.py`：

```python
from types import SimpleNamespace

import pytest

# 见 test_admin_dependency.py：backend.api 包在初始化时会连带导入 chromadb
pytest.importorskip("chromadb", exc_type=ImportError)

from backend.agents.rag.models import JudgeResult, QuestionItem  # noqa: E402

ITEM = QuestionItem(stem="解方程 2x+3=7", answer="x=2", knowledge_points=["一元一次方程"])
VERDICT = JudgeResult(False, 0.3, ["答案与独立解答不一致"], judge_answer="x=5", judge_model="deepseek-v3")


async def _quarantined(api) -> int:
    return await api.quarantine.put(ITEM, VERDICT, job_id="job1")


async def test_list_pending_entries(api):
    entry_id = await _quarantined(api)
    items = (await api.client.get("/manage/rag/quarantine")).json()["items"]
    assert [i["id"] for i in items] == [entry_id]
    assert items[0]["judge"]["reasons"] == ["答案与独立解答不一致"]


async def test_list_filters_by_status(api):
    entry_id = await _quarantined(api)
    await api.client.post(f"/manage/rag/quarantine/{entry_id}/discard")
    assert (await api.client.get("/manage/rag/quarantine")).json()["items"] == []
    discarded = (await api.client.get("/manage/rag/quarantine", params={"status": "discarded"})).json()["items"]
    assert [i["id"] for i in discarded] == [entry_id]


async def test_approve_ingests_without_the_judge(api):
    entry_id = await _quarantined(api)
    response = await api.client.post(f"/manage/rag/quarantine/{entry_id}/approve")
    assert response.status_code == 200 and response.json()["question_id"] == ITEM.id
    assert api.runtime.lexical_index.contains(ITEM.id)
    assert (await api.quarantine.get(entry_id))["status"] == "approved"


async def test_each_entry_can_only_be_handled_once(api):
    entry_id = await _quarantined(api)
    assert (await api.client.post(f"/manage/rag/quarantine/{entry_id}/discard")).status_code == 200
    assert (await api.client.post(f"/manage/rag/quarantine/{entry_id}/approve")).status_code == 409
    assert (await api.client.post(f"/manage/rag/quarantine/{entry_id}/discard")).status_code == 409


async def test_unknown_entry_is_404(api):
    assert (await api.client.post("/manage/rag/quarantine/999/approve")).status_code == 404
    assert (await api.client.post("/manage/rag/quarantine/999/discard")).status_code == 404


async def test_non_admin_cannot_review(api):
    entry_id = await _quarantined(api)
    api.user = SimpleNamespace(id=2, user_privilege=0)
    assert (await api.client.get("/manage/rag/quarantine")).status_code == 403
    assert (await api.client.post(f"/manage/rag/quarantine/{entry_id}/approve")).status_code == 403
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_rag_quarantine_api.py -v -rs`
Expected: chromadb 可用时各接口返回 404 或 405（路由不存在）；不可用时整个文件跳过

- [ ] **Step 3: 实现**

在 `backend/api/manage_api/rag_api.py` 顶部 import 区加入：

```python
from fastapi import Query
from backend.agents.rag.models import QuestionItem
```

在文件末尾追加：

```python
@rag_router.get("/quarantine")
async def list_quarantine(
    status_filter: Literal["pending", "approved", "discarded"] = Query("pending", alias="status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    admin=Depends(require_admin),
    quarantine=Depends(get_quarantine_mapper),
) -> dict:
    return {"items": await quarantine.list(status=status_filter, limit=limit, offset=offset)}


@rag_router.post("/quarantine/{entry_id}/approve")
async def approve(
    entry_id: int,
    admin=Depends(require_admin),
    quarantine=Depends(get_quarantine_mapper),
    runtime=Depends(get_runtime),
) -> dict:
    entry = await quarantine.get(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="隔离区条目不存在")
    if entry["status"] != "pending":
        raise HTTPException(status_code=409, detail="该条目已经处理过")

    item = QuestionItem(**entry["item"])
    # 人工复核通过：跳过裁判直接入库。先入库再改状态——入库是幂等的，
    # 即使并发下被别人抢先处理，重复入库也不会产生脏数据
    await runtime.ingest_service(None, require_judge=False).approve(item)
    if not await quarantine.set_status(entry_id, "approved", reviewer_id=admin.id):
        raise HTTPException(status_code=409, detail="该条目已被其他人处理")
    return {"id": entry_id, "status": "approved", "question_id": item.id}


@rag_router.post("/quarantine/{entry_id}/discard")
async def discard(entry_id: int, admin=Depends(require_admin), quarantine=Depends(get_quarantine_mapper)) -> dict:
    if await quarantine.get(entry_id) is None:
        raise HTTPException(status_code=404, detail="隔离区条目不存在")
    if not await quarantine.set_status(entry_id, "discarded", reviewer_id=admin.id):
        raise HTTPException(status_code=409, detail="该条目已经处理过")
    return {"id": entry_id, "status": "discarded"}
```

查询参数用 `status_filter` 做变量名、`alias="status"` 暴露给前端：直接叫 `status` 会遮住文件顶部导入的 `fastapi.status`。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_rag_quarantine_api.py -v -rs`
Expected: chromadb 可用时 6 passed；不可用时整个文件跳过

- [ ] **Step 5: 提交**

```bash
git add backend/api/manage_api/rag_api.py backend/tests/rag/test_rag_quarantine_api.py
git commit -m "feat: RAG 隔离区人工复核接口"
```

---

## Task 20: 评测指标与评测集

> **这个任务做什么**：为回答「RAG 到底有没有用」准备量尺和考题。① 指标：Recall@K（前 K 个结果里有没有找到正确答案）、MRR（正确答案平均排第几）、nDCG（整体排序质量），以及耗时的分位数。② 评测集：从题库抽题，让大模型改写成「考法相同、措辞和数字不同」的新题当查询，原题就是这条查询的标准答案（known-item 方法），不需要人工标注。改写失败、或改写后和原题太像的样本会被剔除。
>
> **做完之后**：能自动生成评测集并保存成 JSONL 文件，供 Task 21、22 使用。

**known-item 方法**：从库里抽样题目，让 LLM 改写成「考法相同、措辞和数字都不同」的新题作为查询，原题就是这条查询的标准答案。

构建时剔除三类样本：改写失败、原样返回、以及改写后与原题相似度不低于防泄漏阈值的——最后一类会被检索主动排除原题（Task 15 的防泄漏），留着只会把召回率算低。

**已知偏差**：查询的知识点直接沿用原题的标注，而线上是由 extract_tool 从用户的题目里抽取的，可能不准。因此「只用知识点」这一路的评测结果偏乐观，报告里会注明。

**Files:**
- Create: `backend/agents/rag/eval/__init__.py`（空文件）
- Create: `backend/agents/rag/eval/metrics.py`
- Create: `backend/agents/rag/eval/dataset.py`
- Test: `backend/tests/rag/test_eval_dataset.py`

**Interfaces:**
- Consumes: Task 1 的 `QuestionItem`、`RagQuery`、`normalize_text`；Task 2 的 `dot`
- Produces:
  - `recall_at_k(ranked, relevant, k)`、`reciprocal_rank(ranked, relevant)`、`ndcg_at_k(ranked, relevant, k)`、`mean(values)`、`percentile(values, p)`
  - `EvalCase(query, relevant_ids, knowledge_points, difficulty, grade, source_id)`，方法 `to_query() -> RagQuery`
  - `async build_known_item_cases(items, llm, embedder, n, leak_threshold=0.95, seed=0) -> (list[EvalCase], dict)`
  - `save_cases(path, cases)`、`load_cases(path)`（JSONL）

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/rag/test_eval_dataset.py`：

```python
import math

import pytest

from backend.agents.rag.eval.dataset import EvalCase, build_known_item_cases, load_cases, save_cases
from backend.agents.rag.eval.metrics import mean, ndcg_at_k, percentile, recall_at_k, reciprocal_rank
from backend.agents.rag.models import QuestionItem
from backend.tests.rag.helpers import FunctionLLM, HashingEmbedder


# ---------- 指标 ----------

def test_recall_at_k():
    assert recall_at_k(["a", "b", "c"], {"b"}, 1) == 0.0
    assert recall_at_k(["a", "b", "c"], {"b"}, 2) == 1.0
    assert recall_at_k(["a", "b"], {"b", "z"}, 2) == 0.5
    assert recall_at_k(["a"], set(), 3) == 0.0


def test_reciprocal_rank():
    assert reciprocal_rank(["a", "b", "c"], {"b"}) == 0.5
    assert reciprocal_rank(["a", "b"], {"z"}) == 0.0


def test_ndcg_at_k():
    assert ndcg_at_k(["b", "a"], {"b"}, 5) == 1.0
    assert ndcg_at_k(["a", "b"], {"b"}, 5) == pytest.approx(1 / math.log2(3))
    assert ndcg_at_k(["a"], {"b"}, 5) == 0.0


def test_mean_and_percentile():
    assert mean([]) == 0.0 and mean([1.0, 3.0]) == 2.0
    assert percentile([], 95) == 0.0
    assert percentile(list(range(1, 101)), 95) == 95
    assert percentile([7.0], 95) == 7.0


# ---------- 评测集构建 ----------

ITEMS = [
    QuestionItem(stem="解方程 2x+3=7", answer="x=2", knowledge_points=["一元一次方程"]),
    QuestionItem(stem="鸡兔同笼，头35个，脚94只，鸡兔各几只", answer="鸡23兔12", knowledge_points=["鸡兔同笼问题"]),
    QuestionItem(stem="长方形长5厘米宽3厘米，求周长", answer="16厘米"),
    QuestionItem(stem="分解因式 x²-9", answer="(x+3)(x-3)"),
]
REWRITES = {
    "解方程 2x+3=7": "求方程 5x-4=11 的解",                       # 正常改写
    "鸡兔同笼，头35个，脚94只，鸡兔各几只": "鸡兔同笼，头35个，脚94只，鸡兔各几只？",  # 几乎没改
    "长方形长5厘米宽3厘米，求周长": "长方形长5厘米宽3厘米，求周长",   # 原样返回
    "分解因式 x²-9": "",                                          # 改写失败
}


def _rewriter():
    def fn(messages):
        stem = messages[0].content.split("题目：", 1)[1]
        return REWRITES[stem]
    return FunctionLLM(fn)


async def test_bad_rewrites_are_dropped():
    cases, dropped = await build_known_item_cases(ITEMS, _rewriter(), HashingEmbedder(), n=4)
    assert [c.query for c in cases] == ["求方程 5x-4=11 的解"]
    assert cases[0].relevant_ids == [ITEMS[0].id]
    assert cases[0].knowledge_points == ["一元一次方程"]
    assert dropped == {"failed": 1, "unchanged": 1, "too_similar": 1}


async def test_sampling_is_reproducible_with_a_seed():
    first, _ = await build_known_item_cases(ITEMS, _rewriter(), HashingEmbedder(), n=2, seed=42)
    second, _ = await build_known_item_cases(ITEMS, _rewriter(), HashingEmbedder(), n=2, seed=42)
    assert [c.source_id for c in first] == [c.source_id for c in second]


def test_cases_roundtrip_through_jsonl(tmp_path):
    cases = [EvalCase(query="求方程 5x-4=11 的解", relevant_ids=["q_1"], knowledge_points=["一元一次方程"],
                      difficulty="简单", grade="七年级", source_id="q_1")]
    path = tmp_path / "eval" / "cases.jsonl"
    save_cases(path, cases)
    assert load_cases(path) == cases
    assert "一元一次方程" in path.read_text(encoding="utf-8"), "中文不应被转义成 \\uXXXX"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_eval_dataset.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.agents.rag.eval'`

- [ ] **Step 3: 实现**

创建空文件 `backend/agents/rag/eval/__init__.py`。

创建 `backend/agents/rag/eval/metrics.py`：

```python
"""检索评测指标。relevant 为相关文档 id 的集合；ranked 为检索结果 id 列表（按名次）。"""
import math
from typing import Iterable, Sequence


def recall_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """前 k 个结果覆盖了多少比例的相关文档。"""
    relevant = set(relevant)
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def reciprocal_rank(ranked: Sequence[str], relevant: Iterable[str]) -> float:
    """第一个相关结果名次的倒数；没有相关结果时为 0。对所有查询取平均即 MRR。"""
    relevant = set(relevant)
    for rank, doc_id in enumerate(ranked, start=1):
        if doc_id in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """二值相关性的 nDCG：排得越靠前的相关结果贡献越大，理想排序时为 1。"""
    relevant = set(relevant)
    dcg = sum(1.0 / math.log2(i + 2) for i, doc_id in enumerate(ranked[:k]) if doc_id in relevant)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(k, len(relevant))))
    return dcg / ideal if ideal else 0.0


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def percentile(values: Sequence[float], p: float) -> float:
    """最近秩法（nearest-rank）的分位数；空列表返回 0。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(p / 100 * len(ordered)) - 1)
    return ordered[index]
```

创建 `backend/agents/rag/eval/dataset.py`：

```python
"""
评测集构建（known-item 方法）：从库中抽样题目，让 LLM 改写成「考法相同、措辞和数字都不同」的新题
作为查询，原题就是这条查询的标准答案。

剔除三类样本：
- 改写失败（调用出错或返回空）
- 原样返回
- 改写后与原题的相似度不低于防泄漏阈值：这类查询会被检索主动排除原题（见 retriever 的防泄漏），
  留着只会把召回率算低

已知偏差：查询的知识点直接沿用原题的标注，而线上是由 extract_tool 从用户的题目里抽取的，
可能不准。因此「只用知识点」这一路的评测结果偏乐观。
"""
import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

from langchain_core.messages import HumanMessage

from backend.agents.rag.models import QuestionItem, RagQuery, normalize_text
from backend.agents.rag.store.embedder import dot

REWRITE_PROMPT = (
    "把下面这道题改写成一道考法相同、但措辞和数字都不同的新题。"
    "只输出新题的题干，不要答案，不要任何解释。\n题目：{stem}"
)


@dataclass
class EvalCase:
    query: str
    relevant_ids: list[str]
    knowledge_points: list[str] = field(default_factory=list)
    difficulty: str | None = None
    grade: str | None = None
    source_id: str = ""

    def to_query(self) -> RagQuery:
        return RagQuery(self.query, list(self.knowledge_points), self.difficulty, self.grade)


async def build_known_item_cases(items: Sequence[QuestionItem], llm, embedder, n: int,
                                 leak_threshold: float = 0.95, seed: int = 0
                                 ) -> tuple[list[EvalCase], dict[str, int]]:
    """返回 (评测样本, 各类剔除数)。seed 固定时抽样结果可复现。"""
    sample = random.Random(seed).sample(list(items), min(n, len(items)))
    cases: list[EvalCase] = []
    dropped = {"failed": 0, "unchanged": 0, "too_similar": 0}

    for item in sample:
        try:
            response = await llm.ainvoke([HumanMessage(content=REWRITE_PROMPT.format(stem=item.stem))])
            query = (getattr(response, "content", "") or "").strip()
        except Exception:
            query = ""
        if not query:
            dropped["failed"] += 1
            continue
        if normalize_text(query) == normalize_text(item.stem):
            dropped["unchanged"] += 1
            continue
        query_vec, stem_vec = await embedder.embed([query, item.stem])
        if dot(query_vec, stem_vec) >= leak_threshold:
            dropped["too_similar"] += 1
            continue
        cases.append(EvalCase(
            query=query,
            relevant_ids=[item.id],
            knowledge_points=list(item.knowledge_points),
            difficulty=item.difficulty,
            grade=item.grade,
            source_id=item.id,
        ))
    return cases, dropped


def save_cases(path: Path, cases: Sequence[EvalCase]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for case in cases:
            f.write(json.dumps(asdict(case), ensure_ascii=False) + "\n")


def load_cases(path: Path) -> list[EvalCase]:
    with path.open(encoding="utf-8") as f:
        return [EvalCase(**json.loads(line)) for line in f if line.strip()]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_eval_dataset.py -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add backend/agents/rag/eval/__init__.py backend/agents/rag/eval/metrics.py backend/agents/rag/eval/dataset.py backend/tests/rag/test_eval_dataset.py
git commit -m "feat: RAG 评测指标与 known-item 评测集构建"
```

---

## Task 21: 检索消融评测

> **这个任务做什么**：用 Task 20 的评测集，让同一批查询在五种配置下各跑一遍检索：只用向量、只用 BM25、只用知识点、三路融合、融合加重排；算出每种配置的 Recall@3/5/10、MRR、nDCG@5、p95 耗时和降级率，输出对比表。「消融」就是逐项去掉某种技术，看效果下降多少。
>
> **做完之后**：得到一张对比表，用数字说明多路召回和重排各自带来多少提升。

同一批查询分别用五种配置跑一遍：只用向量、只用 BM25、只用知识点、三路融合、融合加重排。**「多路召回和重排到底有没有用」直接用数字回答。**

评测时关闭重排分数阈值并取前 10 个结果：衡量的是排序质量本身，阈值会把后面的结果截掉，让 Recall@10 失去意义。

**Files:**
- Create: `backend/agents/rag/eval/retrieval_eval.py`
- Test: `backend/tests/rag/test_retrieval_eval.py`

**Interfaces:**
- Consumes: Task 15 的 `RagRetriever.retrieve(query, routes, use_rerank, top_k, use_threshold)`；Task 20 的指标与 `EvalCase`
- Produces:
  - 常量 `KS = (3, 5, 10)`、`ABLATIONS`（配置名 → `{"routes", "use_rerank"}`）
  - `AblationResult(name, recall, mrr, ndcg5, p95_ms, degraded_rate, cases)`
  - `async evaluate_retrieval(retriever, cases, ks=KS, ablations=ABLATIONS) -> list[AblationResult]`
  - `render_retrieval_report(results, title=...) -> str`、`results_to_json(results) -> list[dict]`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/rag/test_retrieval_eval.py`：

```python
import pytest

from backend.agents.rag.eval.dataset import EvalCase
from backend.agents.rag.eval.retrieval_eval import ABLATIONS, evaluate_retrieval, render_retrieval_report
from backend.agents.rag.models import QuestionItem, RagContext, RetrievedQuestion
from backend.tests.rag.helpers import FakeReranker, make_test_runtime, seed_runtime


def _id(stem):
    return QuestionItem(stem=stem, answer="x").id


class ScriptedRetriever:
    """按 (召回路, 是否重排) 返回预设的题干排序，用来单独验证指标的汇总逻辑。"""

    def __init__(self, rankings):
        self.rankings = rankings
        self.calls = []

    async def retrieve(self, query, routes, use_rerank, top_k, use_threshold):
        self.calls.append((tuple(routes), use_rerank, top_k, use_threshold))
        stems = self.rankings.get((tuple(routes), use_rerank), [])
        ctx = RagContext(questions=[RetrievedQuestion(QuestionItem(stem=s, answer="x"), 1.0, []) for s in stems])
        ctx.timings_ms["total"] = 12.0
        return ctx


async def test_metrics_are_aggregated_per_configuration():
    cases = [EvalCase(query="q1", relevant_ids=[_id("目标题")])]
    rankings = {
        (("dense",), False): ["干扰1", "目标题"],     # 第 2 名
        (("lexical",), False): ["目标题"],            # 第 1 名
        (("metadata",), False): [],                   # 没找到
        (("dense", "lexical", "metadata"), False): ["目标题"],
        (("dense", "lexical", "metadata"), True): ["目标题"],
    }
    results = {r.name: r for r in await evaluate_retrieval(ScriptedRetriever(rankings), cases)}

    assert results["只用向量"].mrr == 0.5 and results["只用向量"].recall[3] == 1.0
    assert results["只用 BM25"].mrr == 1.0
    assert results["只用知识点"].recall[10] == 0.0
    assert results["融合 + 重排"].p95_ms == 12.0


async def test_every_configuration_disables_the_threshold_and_looks_deeper():
    retriever = ScriptedRetriever({})
    await evaluate_retrieval(retriever, [EvalCase(query="q", relevant_ids=["x"])])
    assert len(retriever.calls) == len(ABLATIONS)
    assert all(top_k == 10 and use_threshold is False for _, _, top_k, use_threshold in retriever.calls)


async def test_empty_case_list_is_safe():
    results = await evaluate_retrieval(ScriptedRetriever({}), [])
    assert all(r.cases == 0 and r.degraded_rate == 0.0 for r in results)


async def test_report_lists_every_configuration():
    results = await evaluate_retrieval(ScriptedRetriever({}), [EvalCase(query="q", relevant_ids=["x"])])
    report = render_retrieval_report(results)
    assert "| 配置 | Recall@3 | Recall@5 | Recall@10 | MRR | nDCG@5 |" in report
    assert all(f"| {name} |" in report for name in ABLATIONS)


async def test_against_the_real_retriever(rag_settings):
    """用真实的检索编排跑一遍：改写后的查询要能把原题找回来。"""
    corpus = [
        QuestionItem(stem="鸡兔同笼，头35个，脚94只，鸡兔各几只", answer="略", knowledge_points=["鸡兔同笼问题"]),
        QuestionItem(stem="解方程 2x+3=7", answer="x=2", knowledge_points=["一元一次方程"]),
        QuestionItem(stem="分解因式 x²-9", answer="略", knowledge_points=["因式分解"]),
        QuestionItem(stem="长方形长5厘米宽3厘米，求周长", answer="16", knowledge_points=["长方形周长"]),
    ]
    runtime = make_test_runtime(rag_settings, reranker=FakeReranker())
    await seed_runtime(runtime, corpus)
    case = EvalCase(query="鸡兔同笼问题：头20个，脚54只，各有几只", relevant_ids=[corpus[0].id],
                    knowledge_points=["鸡兔同笼问题"])

    results = {r.name: r for r in await evaluate_retrieval(runtime.retriever, [case])}
    assert results["三路融合"].recall[3] == pytest.approx(1.0)
    assert results["只用知识点"].mrr == pytest.approx(1.0)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_retrieval_eval.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.agents.rag.eval.retrieval_eval'`

- [ ] **Step 3: 实现**

创建 `backend/agents/rag/eval/retrieval_eval.py`：

```python
"""
检索消融评测：同一批查询分别用五种配置跑一遍，对比每项技术的实际收益。
「多路召回和重排到底有没有用」用数字回答。

评测时关闭重排分数阈值（use_threshold=False）并取前 10 个结果：
这里衡量的是排序质量本身，阈值会把排在后面的结果截掉，让 Recall@10 失去意义。
"""
from dataclasses import asdict, dataclass
from typing import Sequence

from backend.agents.rag.eval.dataset import EvalCase
from backend.agents.rag.eval.metrics import mean, ndcg_at_k, percentile, recall_at_k, reciprocal_rank
from backend.agents.rag.retrieval.routes import ALL_ROUTES, ROUTE_DENSE, ROUTE_LEXICAL, ROUTE_METADATA

KS = (3, 5, 10)
ABLATIONS = {
    "只用向量": {"routes": (ROUTE_DENSE,), "use_rerank": False},
    "只用 BM25": {"routes": (ROUTE_LEXICAL,), "use_rerank": False},
    "只用知识点": {"routes": (ROUTE_METADATA,), "use_rerank": False},
    "三路融合": {"routes": ALL_ROUTES, "use_rerank": False},
    "融合 + 重排": {"routes": ALL_ROUTES, "use_rerank": True},
}


@dataclass
class AblationResult:
    name: str
    recall: dict[int, float]
    mrr: float
    ndcg5: float
    p95_ms: float
    degraded_rate: float
    cases: int


async def evaluate_retrieval(retriever, cases: Sequence[EvalCase], ks=KS,
                             ablations=ABLATIONS) -> list[AblationResult]:
    depth = max(ks)
    results: list[AblationResult] = []
    for name, config in ablations.items():
        recalls: dict[int, list[float]] = {k: [] for k in ks}
        rrs, ndcgs, latencies = [], [], []
        degraded = 0
        for case in cases:
            ctx = await retriever.retrieve(case.to_query(), routes=config["routes"],
                                           use_rerank=config["use_rerank"], top_k=depth, use_threshold=False)
            ranked = [q.item.id for q in ctx.questions]
            for k in ks:
                recalls[k].append(recall_at_k(ranked, case.relevant_ids, k))
            rrs.append(reciprocal_rank(ranked, case.relevant_ids))
            ndcgs.append(ndcg_at_k(ranked, case.relevant_ids, 5))
            latencies.append(ctx.timings_ms.get("total", 0.0))
            degraded += bool(ctx.degraded)
        results.append(AblationResult(
            name=name,
            recall={k: mean(v) for k, v in recalls.items()},
            mrr=mean(rrs),
            ndcg5=mean(ndcgs),
            p95_ms=percentile(latencies, 95),
            degraded_rate=degraded / len(cases) if cases else 0.0,
            cases=len(cases),
        ))
    return results


def render_retrieval_report(results: Sequence[AblationResult], title: str = "RAG 检索消融评测") -> str:
    ks = sorted(results[0].recall) if results else list(KS)
    header = ["配置", *[f"Recall@{k}" for k in ks], "MRR", "nDCG@5", "p95 耗时(ms)", "降级率"]
    lines = [
        f"# {title}",
        "",
        f"评测样本数：{results[0].cases if results else 0}",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
    ]
    for r in results:
        cells = [r.name, *[f"{r.recall[k]:.3f}" for k in ks], f"{r.mrr:.3f}", f"{r.ndcg5:.3f}",
                 f"{r.p95_ms:.0f}", f"{r.degraded_rate:.1%}"]
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "说明：评测时关闭重排分数阈值；「只用知识点」使用原题的知识点标注，结果偏乐观。",
    ]
    return "\n".join(lines)


def results_to_json(results: Sequence[AblationResult]) -> list[dict]:
    return [asdict(r) for r in results]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_retrieval_eval.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add backend/agents/rag/eval/retrieval_eval.py backend/tests/rag/test_retrieval_eval.py
git commit -m "feat: RAG 检索消融评测"
```

---

## Task 22: 生题 A/B 评测与评测命令行

> **这个任务做什么**：检索指标好，不代表题出得更好，这个任务直接比较最终产出：同一道题，开 RAG 和不开 RAG 各生成一次，让裁判模型判断哪个更好。每对结果比较两次、交换前后位置，两次结论一致才算一方胜出，否则记为平局，以抵消大模型当裁判时偏爱某个位置的毛病。另外提供评测命令行 `python -m backend.agents.rag.eval`，包含 `build`（建评测集）、`retrieval`（Task 21 的消融评测）、`generation`（本任务的 A/B 评测）三个子命令，报告以 Markdown 和 JSON 两种格式写进 `rag_eval` 目录。
>
> **做完之后**：阶段六完成，能得到有 RAG 与无 RAG 的胜 / 平 / 负比例。真实运行会产生 API 费用。

同一道题，有 RAG 与无 RAG 各生成一次，由裁判成对比较。**每对比较两次、交换先后位置**：两次结论一致才算一方胜出，否则记为平局。位置一换结论就变，说明裁判只是偏向某个位置——这是大模型当裁判时常见的位置偏差，不做交换就会被它系统性地带偏。

两边生成时都不带记忆召回，保证只比较 RAG 这一个变量。

**Files:**
- Create: `backend/agents/rag/eval/generation_eval.py`
- Create: `backend/agents/rag/eval/__main__.py`
- Test: `backend/tests/rag/test_generation_eval.py`

**Interfaces:**
- Consumes: Task 8 的 `build_judge_llm`；Task 11 的 `get_rag_runtime`；Task 16 的 `async_question_set_tool`（读 `references`）；Task 20、21 的全部
- Produces:
  - `compare_once(judge_llm, original, first, second) -> "A" | "B" | "tie"`
  - `PairOutcome(query, with_rag, without_rag, verdict, votes)`、`ABReport(outcomes)`：`rate(verdict)`、`to_dict()`
  - `async pairwise_ab(cases, generate_with_rag, generate_without_rag, judge_llm) -> ABReport`
  - `render_ab_report(report, title=...) -> str`
  - 命令行：`build_parser()`、`write_report(eval_dir, kind, markdown, payload) -> Path`、`default_generators(runtime)`、`async run(args, runtime=None, llm=None, judge_llm=None, generators=None, out=print) -> int`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/rag/test_generation_eval.py`：

```python
import json

from backend.agents.rag.eval.__main__ import build_parser, run
from backend.agents.rag.eval.dataset import EvalCase, load_cases, save_cases
from backend.agents.rag.eval.generation_eval import compare_once, pairwise_ab, render_ab_report
from backend.agents.rag.models import QuestionItem
from backend.tests.rag.helpers import FakeReranker, FunctionLLM, ScriptedLLM, make_test_runtime, seed_runtime


def _verdict(winner):
    return json.dumps({"winner": winner, "reason": "理由"}, ensure_ascii=False)


# ---------- 单次比较 ----------

async def test_compare_once_parses_the_winner():
    assert await compare_once(ScriptedLLM(_verdict("A")), "原题", "a", "b") == "A"
    assert await compare_once(ScriptedLLM(_verdict("b")), "原题", "a", "b") == "B"
    assert await compare_once(ScriptedLLM(_verdict("tie")), "原题", "a", "b") == "tie"


async def test_unusable_judgements_count_as_tie():
    assert await compare_once(ScriptedLLM("我觉得都不错"), "原题", "a", "b") == "tie"
    assert await compare_once(ScriptedLLM(TimeoutError("超时")), "原题", "a", "b") == "tie"


# ---------- 成对比较 ----------

async def _gen_with(query):
    return f"【RAG】{query} 的变式题"


async def _gen_without(query):
    return f"【无】{query} 的变式题"


CASES = [EvalCase(query="解方程 2x+3=7", relevant_ids=[]), EvalCase(query="分解因式 x²-9", relevant_ids=[])]


async def test_consistent_preference_counts_as_a_win():
    """裁判不管 RAG 的结果在 A 位还是 B 位都选它：这才算 RAG 胜。"""
    def prefers_rag(messages):
        human = messages[1].content
        a = human.split("【A】", 1)[1].split("【B】", 1)[0]
        return _verdict("A" if "【RAG】" in a else "B")

    report = await pairwise_ab(CASES, _gen_with, _gen_without, FunctionLLM(prefers_rag))
    assert report.rate("rag") == 1.0
    assert [o.votes for o in report.outcomes] == [("A", "B"), ("A", "B")]


async def test_position_bias_is_neutralised():
    """裁判永远选 A：交换位置后结论跟着变，说明它只是偏向位置，应记为平局。"""
    report = await pairwise_ab(CASES, _gen_with, _gen_without, FunctionLLM(lambda m: _verdict("A")))
    assert report.rate("tie") == 1.0 and report.rate("rag") == 0.0


async def test_report_rendering():
    report = await pairwise_ab(CASES[:1], _gen_with, _gen_without, FunctionLLM(lambda m: _verdict("A")))
    text = render_ab_report(report)
    assert "有 RAG 胜" in text and "平局" in text and "解方程 2x+3=7" in text
    assert report.to_dict()["total"] == 1


# ---------- 命令行 ----------

CORPUS = [
    QuestionItem(stem="鸡兔同笼，头35个，脚94只，鸡兔各几只", answer="略", knowledge_points=["鸡兔同笼问题"]),
    QuestionItem(stem="解方程 2x+3=7", answer="x=2", knowledge_points=["一元一次方程"]),
    QuestionItem(stem="分解因式 x²-9", answer="略", knowledge_points=["因式分解"]),
]


async def _runtime(rag_settings):
    runtime = make_test_runtime(rag_settings, reranker=FakeReranker())
    await seed_runtime(runtime, CORPUS)
    return runtime


def test_parser_defaults():
    args = build_parser().parse_args(["generation"])
    assert (args.command, args.limit, args.cases) == ("generation", 30, None)


async def test_build_writes_cases(rag_settings):
    runtime = await _runtime(rag_settings)
    rewriter = FunctionLLM(lambda m: "改写后的新题：" + m[0].content.split("题目：", 1)[1][::-1])
    lines = []
    code = await run(build_parser().parse_args(["build", "--n", "3"]), runtime=runtime, llm=rewriter,
                     out=lines.append)
    assert code == 0
    assert len(load_cases(rag_settings.eval_dir / "cases.jsonl")) == 3


async def test_build_on_empty_library_fails_clearly(rag_settings):
    lines = []
    code = await run(build_parser().parse_args(["build"]), runtime=make_test_runtime(rag_settings), out=lines.append)
    assert code == 1 and "题库为空" in lines[0]


async def test_retrieval_writes_markdown_and_json_reports(rag_settings):
    runtime = await _runtime(rag_settings)
    save_cases(rag_settings.eval_dir / "cases.jsonl", [
        EvalCase(query="鸡兔同笼问题：头20个脚54只", relevant_ids=[CORPUS[0].id], knowledge_points=["鸡兔同笼问题"]),
    ])
    lines = []
    assert await run(build_parser().parse_args(["retrieval"]), runtime=runtime, out=lines.append) == 0
    reports = sorted(p.suffix for p in rag_settings.eval_dir.glob("retrieval-*"))
    assert reports == [".json", ".md"]


async def test_generation_uses_injected_generators_and_judge(rag_settings):
    runtime = await _runtime(rag_settings)
    save_cases(rag_settings.eval_dir / "cases.jsonl", CASES)
    lines = []
    code = await run(build_parser().parse_args(["generation", "--limit", "1"]), runtime=runtime,
                     judge_llm=FunctionLLM(lambda m: _verdict("tie")), generators=(_gen_with, _gen_without),
                     out=lines.append)
    assert code == 0
    [json_report] = rag_settings.eval_dir.glob("generation-*.json")
    assert json.loads(json_report.read_text(encoding="utf-8"))["total"] == 1


async def test_missing_cases_file_fails_clearly(rag_settings):
    lines = []
    code = await run(build_parser().parse_args(["retrieval"]), runtime=make_test_runtime(rag_settings),
                     out=lines.append)
    assert code == 1 and "请先运行 build" in lines[0]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/rag/test_generation_eval.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'backend.agents.rag.eval.__main__'`

- [ ] **Step 3: 实现**

创建 `backend/agents/rag/eval/generation_eval.py`：

```python
"""
生题 A/B 评测：同一道题，有 RAG 与无 RAG 各生成一次，由裁判成对比较。

每对比较两次，并交换两者的先后位置：两次结论一致才算一方胜出，否则记为平局。
位置一换结论就变，说明裁判没有真正的偏好，只是偏向某个位置——
这是大模型当裁判时常见的位置偏差，不做交换就会被它系统性地带偏。
"""
from dataclasses import asdict, dataclass
from typing import Awaitable, Callable, Sequence

from langchain_core.messages import HumanMessage, SystemMessage

from backend.agents.rag.eval.dataset import EvalCase
from backend.agents.rag.llm_json import parse_json_object

PAIRWISE_SYSTEM = """你是数学教研员。下面是由同一道原题改编出的两道变式题 A 和 B，请判断哪一道更好。
评判标准（按重要性排序）：
1. 答案正确
2. 与原题考查相同的知识点
3. 难度与原题相当
4. 与原题相似但不雷同
5. 题干完整、无歧义
只输出一个 JSON 对象：{"winner": "A" 或 "B" 或 "tie", "reason": "一句话理由"}"""

Generate = Callable[[str], Awaitable[str]]


@dataclass
class PairOutcome:
    query: str
    with_rag: str
    without_rag: str
    verdict: str              # rag / baseline / tie
    votes: tuple[str, str]    # (RAG 在 A 位时的结论, RAG 在 B 位时的结论)


@dataclass
class ABReport:
    outcomes: list[PairOutcome]

    def _count(self, verdict: str) -> int:
        return sum(o.verdict == verdict for o in self.outcomes)

    def rate(self, verdict: str) -> float:
        return self._count(verdict) / len(self.outcomes) if self.outcomes else 0.0

    def to_dict(self) -> dict:
        return {
            "total": len(self.outcomes),
            "rag_win_rate": self.rate("rag"),
            "baseline_win_rate": self.rate("baseline"),
            "tie_rate": self.rate("tie"),
            "outcomes": [asdict(o) for o in self.outcomes],
        }


async def compare_once(judge_llm, original: str, first: str, second: str) -> str:
    """返回 "A" / "B" / "tie"。调用失败或输出无法识别时记为 tie。"""
    try:
        response = await judge_llm.ainvoke([
            SystemMessage(content=PAIRWISE_SYSTEM),
            HumanMessage(content=f"【原题】{original}\n【A】{first}\n【B】{second}"),
        ])
    except Exception:
        return "tie"
    data = parse_json_object(getattr(response, "content", None)) or {}
    winner = str(data.get("winner", "")).strip().upper()
    return winner if winner in ("A", "B") else "tie"


async def pairwise_ab(cases: Sequence[EvalCase], generate_with_rag: Generate,
                      generate_without_rag: Generate, judge_llm) -> ABReport:
    outcomes: list[PairOutcome] = []
    for case in cases:
        with_rag = await generate_with_rag(case.query)
        without_rag = await generate_without_rag(case.query)
        first = await compare_once(judge_llm, case.query, with_rag, without_rag)   # RAG 在 A 位
        second = await compare_once(judge_llm, case.query, without_rag, with_rag)  # RAG 在 B 位

        rag_votes = (first == "A") + (second == "B")
        baseline_votes = (first == "B") + (second == "A")
        verdict = "rag" if rag_votes == 2 else "baseline" if baseline_votes == 2 else "tie"
        outcomes.append(PairOutcome(case.query, with_rag, without_rag, verdict, (first, second)))
    return ABReport(outcomes)


def render_ab_report(report: ABReport, title: str = "RAG 生题 A/B 评测") -> str:
    lines = [
        f"# {title}",
        "",
        f"样本数：{len(report.outcomes)}",
        "",
        "| 结果 | 比例 |",
        "|---|---|",
        f"| 有 RAG 胜 | {report.rate('rag'):.1%} |",
        f"| 无 RAG 胜 | {report.rate('baseline'):.1%} |",
        f"| 平局（含位置一换结论就变） | {report.rate('tie'):.1%} |",
        "",
        "## 逐条结果",
        "",
    ]
    for i, o in enumerate(report.outcomes, start=1):
        lines += [
            f"### {i}. {o.query}",
            f"- 结论：{o.verdict}（两次投票：{o.votes[0]} / {o.votes[1]}）",
            f"- 有 RAG：{o.with_rag}",
            f"- 无 RAG：{o.without_rag}",
            "",
        ]
    return "\n".join(lines)
```

创建 `backend/agents/rag/eval/__main__.py`：

```python
"""
离线评测命令行。

    python -m backend.agents.rag.eval build      [--n 100] [--seed 0] [--cases 文件]
    python -m backend.agents.rag.eval retrieval  [--cases 文件]
    python -m backend.agents.rag.eval generation [--cases 文件] [--limit 30]

评测集默认存放在 RAG_EVAL_DIR/cases.jsonl；报告写入 RAG_EVAL_DIR，Markdown 与 JSON 各一份。
build 与 generation 会调用真实的大模型，产生费用。
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from backend.agents.rag.eval.dataset import build_known_item_cases, load_cases, save_cases
from backend.agents.rag.eval.generation_eval import pairwise_ab, render_ab_report
from backend.agents.rag.eval.retrieval_eval import evaluate_retrieval, render_retrieval_report, results_to_json
from backend.agents.rag.models import QuestionItem, RagQuery


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m backend.agents.rag.eval", description="RAG 离线评测")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="从题库抽样构建 known-item 评测集")
    build.add_argument("--n", type=int, default=100)
    build.add_argument("--seed", type=int, default=0)
    build.add_argument("--cases", type=Path, default=None)

    retrieval = sub.add_parser("retrieval", help="检索消融评测")
    retrieval.add_argument("--cases", type=Path, default=None)

    generation = sub.add_parser("generation", help="有 / 无 RAG 的生题 A/B 评测")
    generation.add_argument("--cases", type=Path, default=None)
    generation.add_argument("--limit", type=int, default=30)
    return parser


def write_report(eval_dir: Path, kind: str, markdown: str, payload) -> Path:
    eval_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    md_path = eval_dir / f"{kind}-{stamp}.md"
    md_path.write_text(markdown, encoding="utf-8")
    (eval_dir / f"{kind}-{stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return md_path


def default_generators(runtime):
    """真实的生题链路：extract → （可选）检索 → 生题。两边都不带记忆召回，保证只比较 RAG 这一个变量。"""
    from backend.agents.agent.extract_agent import async_extract_tool
    from backend.agents.agent.question_set_agent import async_question_set_tool

    async def generate(query_text: str, use_rag: bool) -> str:
        extract = await async_extract_tool(query_text)
        references = None
        if use_rag:
            references = await runtime.retriever.retrieve(
                RagQuery(query_text, list(extract.get("knowledge_points") or []))
            )
        result = await async_question_set_tool({"input": query_text, "extract": extract, "references": references})
        return result.get("result") or f"（生成失败：{result.get('error')}）"

    async def with_rag(query_text: str) -> str:
        return await generate(query_text, True)

    async def without_rag(query_text: str) -> str:
        return await generate(query_text, False)

    return with_rag, without_rag


async def run(args, runtime=None, llm=None, judge_llm=None, generators=None, out=print) -> int:
    if runtime is None:
        from backend.agents.rag.runtime import get_rag_runtime
        runtime = get_rag_runtime()
    settings = runtime.settings
    cases_path = args.cases or settings.eval_dir / "cases.jsonl"
    await runtime.warm_up()

    if args.command == "build":
        items = []
        for _, document, metadata in await runtime.question_store.get_all():
            try:
                items.append(QuestionItem.from_record(document, metadata))
            except ValidationError:
                continue
        if not items:
            out("题库为空，请先入库再构建评测集")
            return 1
        if llm is None:
            from backend.agents.agent.get_llm import get_llm
            llm = get_llm()
        cases, dropped = await build_known_item_cases(
            items, llm, runtime.embedder, args.n, settings.leak_threshold, args.seed
        )
        save_cases(cases_path, cases)
        out(f"评测集已写入 {cases_path}：{len(cases)} 条；剔除 {dropped}")
        return 0

    if not cases_path.exists():
        out(f"找不到评测集 {cases_path}，请先运行 build")
        return 1
    cases = load_cases(cases_path)

    if args.command == "retrieval":
        results = await evaluate_retrieval(runtime.retriever, cases)
        path = write_report(settings.eval_dir, "retrieval", render_retrieval_report(results), results_to_json(results))
    else:
        if judge_llm is None:
            from backend.agents.rag.ingest.judge import build_judge_llm
            judge_llm = build_judge_llm(settings)
        with_rag, without_rag = generators or default_generators(runtime)
        report = await pairwise_ab(cases[:args.limit], with_rag, without_rag, judge_llm)
        path = write_report(settings.eval_dir, "generation", render_ab_report(report), report.to_dict())
    out(f"报告已写入 {path}")
    return 0


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/rag/test_generation_eval.py -v`
Expected: 11 passed

- [ ] **Step 5: 跑一次真实评测（需要已入库的资料；build 与 generation 会产生 API 费用）**

在仓库根目录运行：

```bash
python -m backend.agents.rag.eval build --n 100
python -m backend.agents.rag.eval retrieval
python -m backend.agents.rag.eval generation --limit 30
```

报告在 `backend/rag_eval/` 下，Markdown 与 JSON 各一份。拿到数字之后再回头调参：重排阈值（`rerank_min_score`）、每路召回数（`route_top_k`）、RRF 的 `k`。

- [ ] **Step 6: 提交**

```bash
git add backend/agents/rag/eval/generation_eval.py backend/agents/rag/eval/__main__.py backend/tests/rag/test_generation_eval.py
git commit -m "feat: RAG 生题 A/B 评测与评测命令行"
```

---

## Task 23: 更新 CLAUDE.md

> **这个任务做什么**：全部功能完成后，在 `CLAUDE.md` 里补一节 RAG 说明：目录结构和各文件的职责、命令行用法、管理接口的位置，以及必须遵守的约定（RAG 失败只能降级、入库必须经过复核、词法索引要整体重建、分词模式不能改回普通模式等）。这是写给之后的开发者和 AI 助手看的，不涉及代码。
>
> **做完之后**：之后接手的人读一遍 `CLAUDE.md`，就知道 RAG 模块的结构和规矩。

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: 新增一节**

在 `CLAUDE.md` 中加入：

```markdown
## RAG 知识库（`backend/agents/rag/`）

生成变式题前先检索参考材料。设计见 `docs/superpowers/specs/2026-09-11-rag-knowledge-base-design.md`。

| 目录 / 文件 | 职责 |
|---|---|
| `store/` | 向量化与向量存储（经 LlamaIndex 读写 Chroma / 内存实现）、词法索引（BM25 + 知识点倒排 + 题目目录） |
| `ingest/` | 加载 → 结构化 → 去重 → 第三方复核 → 入库；命令行 `python -m backend.agents.rag.ingest` |
| `retrieval/` | 三路召回 → RRF → 防泄漏 → 重排；参考材料格式化与防照抄 |
| `eval/` | 离线评测：`python -m backend.agents.rag.eval build / retrieval / generation` |
| `runtime.py` | 组件组装（进程内单例） |
| `integration.py` | 生题链路唯一的检索入口，任何失败都返回 None |
| `lifecycle.py` | 启动与关停时要做的事，由 `core/hooks.py` 调用 |

管理接口挂在 `/manage/rag/*`（`api/manage_api/rag_api.py`），要求 `user_privilege >= 1`。

约定：
- **RAG 是增强不是依赖**：检索的任何环节失败都只能降级，不能让生题失败
- 入库必须经过第三方复核，裁判与生成模型不同家族，失败即拒；只有隔离区的人工通过可以跳过复核
- 题目 id 是规范化题干的哈希：入库幂等，精确去重只比较 id
- 词法索引整体重建、替换快照；不要原地修改快照
- RAG 的向量操作统一经 `store/llama_store.py`，这一层的业务代码拿到的相似度一律是余弦；LlamaIndex 的 Chroma 集成返回 exp(-距离)，换算只在适配层做。不要改用 `VectorStoreIndex`（向量由 `Embedder` 事先算好）
- **记忆层（`agents/memory/vector_store_manager.py`）是另一套封装**：它用 `VectorStoreIndex` + 切分器 + 同步 embedding，collection 用 chromadb 默认的 l2 空间，`min_score` 的口径是 `exp(-平方欧氏距离)`。两层的需求不同（RAG 要先拿到向量做去重、查询向量还要在两个库之间复用），所以没有合并；写新代码时先确认自己在哪一层，阈值不要互相套用
- BM25 分词用 jieba 的 `cut_for_search`，不要改回普通 `cut`（同一知识点的不同说法会互相召回不到）
- RAG 的 prompt 放在 `agents/skills/` 下，frontmatter 必须带 `visibility: internal`
- 命令行入库前要停服务（Chroma 本地库不支持多进程写入）；入库后重启服务才会加载新题
- 导入 `backend.api` 下的任何模块都会连带导入 chromadb（`api/__init__.py` 会导入全部路由）；依赖它的测试用 `pytest.importorskip("chromadb", exc_type=ImportError)` 守住
- 新增配置项见 spec 的「配置项」表，裁判模型必须单独配置 `JUDGE_API_URL / JUDGE_API_KEY / JUDGE_MODEL`
```

- [ ] **Step 2: 提交**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 补充 RAG 知识库的结构与约定"
```

---

## 验收对照表

| # | 验收标准（见 spec） | 覆盖它的测试 |
|---|---|---|
| 1 | PDF / Word 可通过命令行和后台接口入库；重复入库新增为 0 | `test_runtime_and_cli.py::test_file_command_ingests_and_prints_summary`、`test_rag_api.py::test_upload_ingests_in_the_background`、`test_ingest_service.py::test_reingesting_is_idempotent_and_skips_the_judge` |
| 2 | 生成与抽取的题都经过复核；未通过的进隔离区，可人工处理 | `test_ingest_service.py::test_passed_questions_are_stored_and_indexed`、`test_rag_quarantine_api.py` 全部 |
| 3 | 裁判同家族或未配置时拒绝入库并说明原因 | `test_judge.py::test_same_family_judge_is_refused`、`::test_missing_judge_model_is_refused`、`test_rag_api.py::test_misconfigured_judge_returns_503_without_creating_a_job`、`test_runtime_and_cli.py::test_misconfigured_judge_exits_with_clear_message` |
| 4 | 裁判失败或输出无法解析时失败即拒 | `test_judge.py::test_unparseable_output_fails_closed`、`::test_llm_failure_fails_closed`、`::test_string_true_is_not_true` |
| 5 | 生题会注入参考题；用户问的原题不会作为参考题 | `test_integration.py::test_prompt_order_is_skill_then_references_then_preferences`、`test_retriever.py::test_the_users_own_question_is_never_returned` |
| 6 | 任一路召回、重排或向量化失败，生题照常完成 | `test_retriever.py::test_a_failing_route_is_skipped`、`::test_embedding_failure_keeps_the_non_vector_routes`、`::test_total_failure_returns_an_empty_context`、`::test_rerank_failure_falls_back_to_fusion_order`、`test_integration.py::test_any_failure_returns_none` |
| 7 | 照抄参考题会触发一次重试，正常变式不被误伤 | `test_integration.py::test_copying_a_reference_triggers_one_retry`、`::test_copy_detection_only_catches_near_verbatim_copies` |
| 8 | 输出五种配置的检索对比表与生题胜率 | `test_retrieval_eval.py::test_report_lists_every_configuration`、`test_generation_eval.py::test_retrieval_writes_markdown_and_json_reports`、`::test_position_bias_is_neutralised` |
| 9 | 非管理员 403；上传文件不会写到上传目录之外 | `test_admin_dependency.py::test_non_admin_is_forbidden`、`test_rag_api.py::test_uploaded_file_name_cannot_escape_the_upload_dir` |
| 10 | RAG 的 skill 不出现在 ReAct 主提示词中 | `test_structurer.py::test_internal_skills_are_hidden_from_the_agent` |

全部完成后跑一遍：

```bash
cd backend && python -m pytest tests/ -v -rs
```

chromadb 可用时，RAG 部分应为 218 passed；不可用时应为 191 passed、10 skipped——3 个接口测试文件整体跳过，另有 7 条单独跳过，原因都写明 chromadb 导入失败。
