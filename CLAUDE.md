# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**学生学情分析系统** — An AI-powered tutoring backend. Users submit questions or requests; a ReAct agent routes to the appropriate skill (knowledge extraction, variant question generation, general Q&A, or memory retrieval), while a three-tier memory system maintains conversational continuity.

Backend code lives under `backend/`. A React 19 + Vite + TypeScript + Tailwind v4 frontend lives under `frontend/` (see "Frontend" below). It replaced a Vue 3 client that was deleted in `3bd21d6` — check git history if you need to see what that did.

## Running the Server

Run from the **repository root**, not from `backend/`:

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

The code uses absolute `backend.*` imports, so `cd backend && python -m uvicorn main:app` and `python main.py` both fail with `No module named 'backend'` unless the repo root is on `PYTHONPATH` (PyCharm adds it for you; a plain shell does not). `backend/.env` is loaded by absolute path, so the working directory does not matter for config.

The startup hook (`core/hooks.py`) first checks the Redis version (see "Redis 8.0+" below), then auto-creates all MySQL tables, then replays any pending memory archive. It creates tables, not the database: `CREATE DATABASE` is on you.

`docs/` (plans, specs, reviews) is gitignored and kept local only. Never commit anything under it, and do not point code comments at files in it.

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

Optional tuning knobs (all have in-code defaults; a malformed value falls back and logs an error rather than blocking startup):

```
APP_DEBUG=                   # FastAPI debug mode. Default off — leave unset in production
CORS_ALLOW_ORIGINS=          # Comma-separated origins. Default *
PROFILE_PROMOTE_THRESHOLD=   # Times a preference must recur before it enters the profile. Default 2;
                             # below 2 disables frequency confirmation and logs an error at startup
```

Every new environment variable must also be added to `CONFIG_KEYS` in `backend/tests/isolation.py`, or the subprocess tests will silently read the host machine's value instead of the one under test.

Install dependencies: `pip install -r backend/requirements.txt`

## Architecture

### Agent Layer (`backend/agents/`)

Tools and Skills are two different things here; do not mix them up.

**`agents/agent/react_agent.py`** — The active graph, two nodes: `react_think` (function `react_think_node`) and `execute_tool` (function `tool_exec_node`). `react_think_node` calls the LLM, which answers with JSON `{thought, action, action_args, final_result}`; `tool_exec_node` dispatches via `await TOOL_MAP[action]._arun(**args)`. Max 5 ReAct rounds.

**`agents/tools/`** — Executable tools, all LangChain `BaseTool` subclasses and all async (`_arun`). The registry is `TOOLS` / `TOOL_MAP` (keyed by `tool.name`) in `agents/tools/__init__.py`; it is the single dispatch table. Eight tools: `common_tool`, `extract_tool`, `question_set_tool`, `query_memory_tool`, `user_profile_save_tool`, `user_profile_query_tool`, `user_profile_delete_tool`, `load_skill_tool`.

Gotcha: the LLM never supplies identity. `tool_exec_node` injects `user_id` + `session_id` into `query_memory_tool`, and `user_id` into the three `user_profile_*_tool`s, from `GraphState`. A new tool that needs them must be added to that injection block.

**`agents/skills/`** — Markdown playbooks, not code the graph can call. Each skill is a directory with a `SKILL.md` (frontmatter: `name`, `description`, `triggers`). `loader.py` reads them with an mtime cache, so editing a `SKILL.md` takes effect on the next call without a restart. The agent pulls a playbook into context with `load_skill_tool`; `match_triggers` does literal substring matching on the user input and adds a hint to the system prompt as a fallback. A `SKILL.md` may carry fenced `skill:<tag>` code blocks that `skill_runner.py` executes (`run_validator`, `load_vocab`) — these files are trusted code, treat edits to them like edits to Python. Current skills: `question_variant`, `profile_schema`, `session_digest` (internal, empty `triggers`).

**Initial state** is built in exactly one place, `_build_state` in `api/user_api/agent_api.py`, used by both `/agent/analyse` and the streaming endpoint. Add new `GraphState` keys there; nodes read optional keys with `state.get(...)` because `GraphState` is a `TypedDict` with no runtime checking.

### Memory System (`backend/agents/memory/`)

Three-tier architecture:

| Tier | Storage | Contents | Lifecycle |
|---|---|---|---|
| Short-term | Redis LIST `stm:{user_id}:{session_id}` | Raw `{user_memory, model_memory}` pairs | 24h TTL, max 10 entries |
| Archive | MySQL `memory_record` / `memory_digest` | Full dialogue text (append-only) + a per-session summary | Permanent |
| Long-term | MySQL `user_profile` | Grade, subject, weak_points, preferences, notes | Persistent per user |
| Candidates | Redis HASH `profile:candidates:{user_id}` | `{value, times}` per `field.sub_key` | Per-field TTL: 1 day, +12h per repeat |

**Overflow flow**: `add_memory` pushes to the Redis LIST; when the window exceeds `max_memory_size`, a single Lua `EVAL` atomically moves the oldest entries into a pending queue. A background `asyncio.Task` writes each one into `memory_record` (deduplicated by a sha1 of the raw entry, so an archive retry cannot produce a second row), then acks it out of pending. Anything left in pending is redone by `drain_pending()` on the next startup.

**Session digest**: every `DIGEST_EVERY` (5) newly archived records, `_refresh_digest` rebuilds the session summary by calling `session_digest_agent` — **always over the raw records, never over the previous summary**, so the text cannot drift across successive compressions. A digest failure is logged and retried on the next batch; the raw records are already durable.

`MemoryManager.get_memory_for_planner()` returns `short_memory`, `long_memory` and `session_digest`. `_build_state` puts the digest plus the last 3 raw turns into `user_input`, and the long-term profile into the **system prompt** (`GraphState.profile_text`, formatted once per request by `format_profile`). The profile is background, not the current request — in `user_input` the model would read "函数=薄弱" as "the user is asking about functions". `weak_points` is rendered with its mastery level; `notes` is never injected and only comes back through `user_profile_query_tool`. A profile written during a request is not visible until the next request. `query_memory_tool` uses `get_latest_memories()` directly.

**Profile writes** (`user_profile_save_tool`, `agents/memory/profile_candidates.py`):

- `grade`, `subject` and `notes` are written straight to MySQL. `weak_points` / `preferences` go through the candidate pool and are only written once the same value has been submitted `PROFILE_PROMOTE_THRESHOLD` times, to tell a stable preference from a one-off request.
- Keys **and values** of `weak_points` / `preferences` are enums defined in the `skill:vocab` block of `agents/skills/profile_schema/SKILL.md`, not in code. Adding a profile dimension means editing that Markdown. Anything outside the vocabulary is stored in `notes` instead of the pool. The counting below is only correct because values are enums (string equality = same claim).
- Counting is one Lua script: same value → `times + 1`; a different value → reset to 1 with the new value. The script reads first and issues exactly **one write, last** — Lua has no rollback, so this is what keeps a mid-script error from leaving half-written state. Keep that shape when changing it. Do not reintroduce a Python-side `HGET` → compute → write.
- Order across stores: write MySQL first, then delete the candidate. The delete is a compare-and-delete (only if the stored value still equals the promoted value). A failure in between means the preference is promoted again later, and the JSON merge is idempotent — duplicate, never lost.
- MySQL side: `update_user_profile` reads with `SELECT ... FOR UPDATE` (JSON columns are rewritten whole, so a plain read loses concurrent updates); first-time creation relies on the `user_id` unique constraint and falls back to an update on conflict.

**Redis 8.0+ is required**: the candidate pool uses `HSETEX`. `ensure_redis_version` runs at startup and refuses to start below 8.0, because an older server would otherwise fail later with a swallowed `unknown command`.

There is **no vector store in the memory layer**. It used to refine memories with an LLM and write them to Chroma; that was replaced because durable facts belong in `user_profile`, per-session volume is small, and Chroma's data was outside `mysqldump`. Chroma remains a dependency for the RAG knowledge base only.

### Chat Sessions (`model/chat.py`, `dao/chat_mapper.py`, `api/user_api/session_api.py`)

`chat_session` / `chat_message` hold the full conversation **for display** (sidebar + history); the memory system above holds what the **model** sees and is trimmed/summarised. They are written independently: the user message is saved before the agent runs, the assistant message after `final_result`, then memory. `session_id` is generated by `POST /sessions`; `/agent/analyse*` takes the user from the token only and 404s on a session the user does not own (404, not 403, so ids cannot be enumerated).

Gotcha: `backend/api/__init__.py` binds an `APIRouter` named `user_api`, shadowing the subpackage attribute, so `import backend.api.user_api.agent_api as x` fails. Use `from backend.api.user_api import agent_api`.

### LLM Configuration (`agents/agent/get_llm.py`)

`get_llm(model, streaming)` returns a `ChatOpenAI` instance pointed at the DashScope OpenAI-compatible endpoint. Results are cached by argument via `@singleton_method`. Per-agent model overrides: `PLANNER_MODEL`, `EXTRACT_MODEL`, `DIGEST_MODEL` env vars.

`get_embedding_model()` lives in `agents/agent/embedding.py`, deliberately **not** in `get_llm.py`: importing llama-index costs ~1.5s and 1400+ modules, and nothing on the request path needs embeddings. Only the RAG knowledge base imports it.

## Frontend (`frontend/`)

`npm run dev` (Vite on 5173, proxies `/login`, `/agent`, `/sessions`, `/health` to uvicorn on 8000). Two pages: `/auth` and the chat page at `/` (new conversation) or `/c/:sessionId`.

- **Route prefixes**: SPA routes must not start with a proxied prefix, or a page refresh hits the backend.
- **State**: `store/auth.ts` (token in localStorage), `store/chat.ts` (session list + messages cached per session; a streaming answer always writes back to the session it was sent from).
- **Streaming**: `api/stream.ts` wraps `fetch-event-source` with `openWhenHidden: true` and no retry — both defaults would re-POST the question. Only `thinking` events become margin-note steps.
- **KaTeX**: the top-level `katex` must match the version `rehype-katex` bundles, or the CSS class names differ and sub/superscripts render full size. Font/KaTeX CSS is imported from `main.tsx`, not `@import`ed in `index.css` (Tailwind inlines it and breaks the relative font URLs).
- Visual direction: graph-paper notebook, blue-black ink, red-pen margin notes (`font-hand`, LXGW WenKai). Theme tokens live in `@theme` in `src/index.css`.

## Key Conventions

- All I/O (DB, Redis, LLM calls) is **async throughout**. Keep new code async.
- **Adding a new tool**: create a `BaseTool` subclass with an async `_arun` in `agents/tools/`, and add an instance to `TOOLS` in `agents/tools/__init__.py`. It is then in `TOOL_MAP` automatically.
- **Adding a new Skill**: create `agents/skills/<name>/SKILL.md` with frontmatter. No registration and no restart needed.
- **No read-modify-write against Redis from Python.** Short-term memory and the candidate pool both do their read + write inside one Lua script; follow that pattern.
- **Blocking calls** go to the dedicated pool in `core/executors.py`, never `run_in_executor(None, ...)`.
- **Tests**: Redis tests use db 15, MySQL lock tests use `TEST_SQL_DATABASE_URL` — it must point at a dedicated test database, never the dev one. Locally a missing service skips; CI sets `REQUIRE_REDIS=1` / `REQUIRE_MYSQL=1` (Redis 8.0, MySQL 8.4) so a missing service fails instead.
- **Memory writes always use the original user text**, not the memory-augmented `user_input`, to prevent context pollution across sessions.
- The old linear planner architecture (`graph_build.py`, `planner_agent.py`, `analyse_agent.py`, `image_gene_agent.py`, `prompt.py`) was deleted in favour of `react_agent.py`. Do not restore it; check git history if you need to see what it did.
