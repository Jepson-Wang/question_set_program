# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**学生学情分析系统** — An AI-powered tutoring backend. Users submit questions or requests; a ReAct agent routes to the appropriate skill (knowledge extraction, variant question generation, general Q&A, or memory retrieval), while a three-tier memory system maintains conversational continuity.

All code lives under `backend/`. There is no frontend in this repository.

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

### Request Flow

```
POST /agent/analyse
  → JWT auth (api/dependencies.py)
  → Fetch last 3 short-term memories from Redis → format as context string
  → Build GraphState, invoke ReAct graph (agents/agent/react_agent.py)
      react_think_node  ←──────────────────────┐
           ↓ (LLM picks a skill or outputs final_result)
      should_continue                           │
           ↓ action != null && round <= 5       │
      skill_exec_node  ──────────────────────────┘
           ↓ action == null
          END
  → Write MemoryUnit(original_text, final_result) to Redis
  → Return result['final_result']
```

### Agent / Skill Layer (`backend/agents/`)

**`agents/agent/react_agent.py`** — The active graph. `react_think_node` calls the LLM with all 4 bound skills and receives JSON `{thought, action, action_args, final_result}`. `skill_exec_node` dispatches via `SKILL_MAP[action]._run(**args)`. Max 5 ReAct rounds.

**`agents/skills/`** — All skills extend LangChain `BaseTool`. The registry is in `__init__.py`:
- `common_skill` — General Q&A, explanation, problem solving
- `extract_skill` — Extracts difficulty + knowledge points from a problem
- `question_set_skill` — Generates variant problems (internally calls extract first)
- `query_memory_skill` — Fetches short-term memory; `user_id`/`session_id` are injected from GraphState by `skill_exec_node`, not passed by the LLM

`SKILL_MAP` (keyed by `skill.name`) is the single dispatch table; add new skills here.

**`agents/agent/graph_build.py`** — Old linear planner architecture, fully commented out. Superseded by `react_agent.py`.

### Memory System (`backend/agents/memory/`)

Three-tier architecture:

| Tier | Storage | Contents | Lifecycle |
|---|---|---|---|
| Short-term | Redis hash `user:{id}:session:{id}` | Raw `{user_memory, model_memory}` pairs | 24h TTL, max 10 entries |
| Long-term | MySQL `user_profile` | Grade, subject, weak_points, preferences | Persistent per user |
| Vector | Chroma (local `chroma_db/`) | LLM-refined memory summaries with tags | Written when short-term overflows |

**Overflow flow**: when short-term hits `max_memory_size`, the oldest N records are passed through `extract_memory_agent` (converts to 3rd-person summaries with tags), stored in Chroma, then deleted from Redis.

`MemoryManager.get_memory_for_planner()` returns both short + long-term memory. The API uses only `short_memory` (last 3 entries) for prompt injection; `query_memory_skill` uses `get_latest_memories()` directly.

### LLM Configuration (`agents/agent/get_llm.py`)

`get_llm(model, streaming)` returns a `ChatOpenAI` instance pointed at the DashScope OpenAI-compatible endpoint. Results are cached by argument via `@singleton_method`. Per-agent model overrides: `PLANNER_MODEL`, `EXTRACT_MODEL` env vars.

### Singleton Patterns (`core/single_tool.py`)

- `singleMeta` — metaclass for class-level singletons (used by `MemoryManager`, Redis client)
- `@singleton_method` — function-level cache keyed by arguments (used by `get_llm`, `build_extract_memory_agent`)

### API Endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/agent/analyse` | Invoke ReAct agent, returns full state |
| POST | `/agent/analyse/stream` | SSE stream: emits `thinking`/`observation`/`result` events |
| POST | `/login/login` | Returns JWT token |
| POST | `/login/register` | Creates user |

JWT is validated via `api/dependencies.py`; all agent endpoints require a valid token.

### GraphState Fields

```python
{
    'user_input': str,      # user text + prepended memory context
    'user_id': int,
    'session_id': int,
    'thought': str,         # LLM's latest reasoning
    'action': str,          # skill name or null
    'action_args': dict,    # args for chosen skill
    'messages': list[ToolMessage],  # accumulated observations
    'round': int,           # current iteration (max 5)
    'final_result': str     # populated when action is null
}
```

## Key Conventions

- All I/O (DB, Redis, LLM calls) is **async throughout**. Keep new code async.
- **Adding a new Skill**: create a `BaseTool` subclass in `agents/skills/`, add an instance to `SKILLS` list in `agents/skills/__init__.py`. It will be auto-registered in `SKILL_MAP` and bound to the LLM.
- **Memory writes always use the original user text**, not the memory-augmented `user_input`, to prevent context pollution across sessions.
- `graph_build.py` is dead code — do not reference or restore it.
