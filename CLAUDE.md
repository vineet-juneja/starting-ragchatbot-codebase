# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Dependency management and execution use [`uv`](https://docs.astral.sh/uv/) exclusively. Never call `pip` or bare `python`, and never hand-edit `pyproject.toml` dependencies without re-locking through `uv`.

```bash
uv sync                                              # install/refresh dependencies from uv.lock
uv add <pkg>                                         # add a dependency (updates pyproject.toml + uv.lock)
uv remove <pkg>                                      # remove a dependency
uv lock                                              # re-resolve the lockfile
./run.sh                                             # start the app (bash; from repo root)
cd backend && uv run uvicorn app:app --reload --port 8000   # start the app directly (use this on Windows)
uv run python -c "..."                               # run one-off Python inside the project env
uv run pytest                                        # run the test suite (tests/, pythonpath=backend)
```

### Code quality

```bash
./scripts/format.sh    # apply isort + black to backend/, tests/, main.py (writes changes)
./scripts/lint.sh      # isort --check-only, black --check, flake8 (no writes; CI-style, non-zero on failure)
./scripts/quality.sh   # format.sh + lint.sh + pytest, in one shot
```

- **black** (formatter) and **isort** (`profile = "black"`) are configured in `pyproject.toml`; **flake8** in `.flake8` (line length 88; `E203`/`W503`/`E501` deferred to black; `backend/app.py` skips `E402` for its pre-import `warnings.filterwarnings`).
- All three are `dev` dependency-group packages — run them via `uv run` (the scripts already do).
- Scripts are bash; on Windows run them from Git Bash.

- App serves at `http://localhost:8000`, interactive API docs at `/docs`.
- Requires a `.env` in the repo root with `ANTHROPIC_API_KEY=...` (see `.env.example`). `backend/config.py` loads it via `python-dotenv`.
- **On Windows, run inside Git Bash** (per README) or use the direct `uvicorn` command above — `run.sh` is a bash script.
- `main.py` at the repo root is an unused stub.

## Runtime architecture

This is a tool-calling RAG system, not a retrieve-then-generate pipeline. The backend is FastAPI; the frontend is static vanilla JS served by the same app.

**The query flow** (`backend/rag_system.py` → `ai_generator.py` → `search_tools.py` → `vector_store.py`):

1. `POST /api/query` (`app.py`) creates/reuses a session and calls `RAGSystem.query()`.
2. `AIGenerator.generate_response()` makes a **first Claude call** with the `search_course_content` tool offered (`tool_choice: auto`). Claude decides whether a search is needed.
3. If Claude returns `stop_reason == "tool_use"`, `_handle_tool_execution()` runs the tool via `ToolManager`, appends the result, and makes a **second Claude call with the tools removed** — forcing a prose answer. Only one search round-trip is possible by design (also stated in the system prompt).
4. Sources are not returned by the tool call chain directly: `CourseSearchTool` stores them on `self.last_sources` during result formatting; `RAGSystem` reads them via `ToolManager.get_last_sources()` then calls `reset_sources()`.

**Vector store** (`backend/vector_store.py`) uses two ChromaDB collections:

- `course_catalog` — one entry per course (title as ID, lessons serialized to a `lessons_json` metadata string). Used to **resolve a fuzzy `course_name`** ("MCP", "Introduction") to an exact title via semantic search before filtering content.
- `course_content` — the chunked lesson text, filtered by resolved `course_title` and/or `lesson_number`.

**Document ingestion** (`backend/document_processor.py`, triggered on startup in `app.py`):

- Reads `docs/*.{txt,pdf,docx}` (only `.txt` is actually parsed — everything is read as plain text).
- Expects a header block: `Course Title:`, `Course Link:`, `Course Instructor:`, then `Lesson N: <title>` markers each optionally followed by `Lesson Link:`.
- Chunking is sentence-based with overlap; sizes come from `config.py` (`CHUNK_SIZE`, `CHUNK_OVERLAP`).
- **Dedup is by course title** against `course_catalog`. Startup passes `clear_existing=False`, so editing an existing doc's body does not re-index it — delete `backend/chroma_db/` or set `clear_existing=True` in `app.py` to force a rebuild.

**Sessions** (`backend/session_manager.py`) are in-memory only (lost on restart). History is truncated to the last `MAX_HISTORY * 2` messages and passed to Claude as a formatted string appended to the system prompt.

## Conventions

- **All backend imports are flat** (`from config import config`, `from rag_system import RAGSystem`) — the app must be launched with `backend/` as the working directory.
- **All tunables live in `backend/config.py`** (`Config` dataclass): model name, embedding model, chunk sizes, `MAX_RESULTS`, `MAX_HISTORY`, `CHROMA_PATH`.
- **Adding a tool:** implement the `Tool` ABC in `search_tools.py` (`get_tool_definition` + `execute`), then instantiate and `tool_manager.register_tool(...)` it in `RAGSystem.__init__`. Anything exposing a `last_sources` list is automatically picked up by the sources plumbing.
- Data models (`backend/models.py`) are Pydantic: `Course` → `Lesson[]`, and `CourseChunk` for vector rows. A course's `title` is its unique identifier throughout.
- Static frontend is mounted at `/` with no-cache headers (`DevStaticFiles` in `app.py`); CORS and trusted hosts are wide open for dev.
