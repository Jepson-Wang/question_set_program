# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**学生学情分析系统** — An AI-powered tutoring backend. Users submit questions or requests; a ReAct agent routes to the appropriate skill (knowledge extraction, variant question generation, general Q&A, or memory retrieval), while a three-tier memory system maintains conversational continuity.

Backend code lives under `backend/`. A React 19 + Vite + TypeScript frontend lives under `frontend/`; it is a fresh scaffold (Ant Design, React Router, Zustand, axios) and is not covered by these notes. It replaced a Vue 3 client that was deleted in `3bd21d6` — check git history if you need to see what that did.

## Running the Server

```bash
cd backend
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Or directly: `python main.py`

The startup hook (`core/hooks.py`) auto-creates all MySQL tables on first run.

## Environment Setup

Copy `backend/.env` and fill in real credentials. Required variables:

```
API_KEY=          # Alibaba DashScope API key
MODEL_NAME=       # Default LLM, e.g. qwen-plus
API_URL=          # https://dashscope.aliyuncs.com/compatible-mode/v1
PLANNER_MODEL=    # Intent routing model
EXTRACT_MODEL=    # Knowledge extraction model
EMBEDDING_MODEL=  # Vector embedding model, e.g. text-embedding-v4
SQL_DATABASE_URL= # mysql+asyncmy://user:pass@host:3306/dbname
REDIS_HOST=
REDIS_PORT=
REDIS_PASSWORD=
REDIS_USERNAME=
```

Install dependencies: `pip install -r backend/requirements.txt`

## Architecture

### Agent / Skill Layer (`backend/agents/`)

**`agents/agent/react_agent.py`** — The active graph. `react_think_node` calls the LLM with all 4 bound skills and receives JSON `{thought, action, action_args, final_result}`. `skill_exec_node` dispatches via `SKILL_MAP[action]._run(**args)`. Max 5 ReAct rounds.

**`agents/skills/`** — All skills extend LangChain `BaseTool`; the registry is in `__init__.py`. Gotcha: `query_memory_skill` receives `user_id`/`session_id` injected from GraphState by `skill_exec_node`, not passed by the LLM.

`SKILL_MAP` (keyed by `skill.name`) is the single dispatch table; add new skills here.

### Memory System (`backend/agents/memory/`)

Three-tier architecture:

| Tier | Storage | Contents | Lifecycle |
|---|---|---|---|
| Short-term | Redis LIST `stm:{user_id}:{session_id}` | Raw `{user_memory, model_memory}` pairs | 24h TTL, max 10 entries |
| Archive | MySQL `memory_record` / `memory_digest` | Full dialogue text (append-only) + a per-session summary | Permanent |
| Long-term | MySQL `user_profile` | Grade, subject, weak_points, preferences | Persistent per user |

**Overflow flow**: `add_memory` pushes to the Redis LIST; when the window exceeds `max_memory_size`, a single Lua `EVAL` atomically moves the oldest entries into a pending queue. A background `asyncio.Task` writes each one into `memory_record` (deduplicated by a sha1 of the raw entry, so an archive retry cannot produce a second row), then acks it out of pending. Anything left in pending is redone by `drain_pending()` on the next startup.

**Session digest**: every `DIGEST_EVERY` (5) newly archived records, `_refresh_digest` rebuilds the session summary by calling `session_digest_agent` — **always over the raw records, never over the previous summary**, so the text cannot drift across successive compressions. A digest failure is logged and retried on the next batch; the raw records are already durable.

`MemoryManager.get_memory_for_planner()` returns `short_memory`, `long_memory` and `session_digest`. The API injects the digest plus the last 3 raw turns into `user_input`; `query_memory_skill` uses `get_latest_memories()` directly.

There is **no vector store in the memory layer**. It used to refine memories with an LLM and write them to Chroma; that was replaced because durable facts belong in `user_profile`, per-session volume is small, and Chroma's data was outside `mysqldump`. Chroma remains a dependency for the RAG knowledge base only.

### LLM Configuration (`agents/agent/get_llm.py`)

`get_llm(model, streaming)` returns a `ChatOpenAI` instance pointed at the DashScope OpenAI-compatible endpoint. Results are cached by argument via `@singleton_method`. Per-agent model overrides: `PLANNER_MODEL`, `EXTRACT_MODEL`, `DIGEST_MODEL` env vars.

`get_embedding_model()` lives in `agents/agent/embedding.py`, deliberately **not** in `get_llm.py`: importing llama-index costs ~1.5s and 1400+ modules, and nothing on the request path needs embeddings. Only the RAG knowledge base imports it.

## Key Conventions

- All I/O (DB, Redis, LLM calls) is **async throughout**. Keep new code async.
- **Adding a new Skill**: create a `BaseTool` subclass in `agents/skills/`, add an instance to `SKILLS` list in `agents/skills/__init__.py`. It will be auto-registered in `SKILL_MAP` and bound to the LLM.
- **Memory writes always use the original user text**, not the memory-augmented `user_input`, to prevent context pollution across sessions.
- The old linear planner architecture (`graph_build.py`, `planner_agent.py`, `analyse_agent.py`, `image_gene_agent.py`, `prompt.py`) was deleted in favour of `react_agent.py`. Do not restore it; check git history if you need to see what it did.
