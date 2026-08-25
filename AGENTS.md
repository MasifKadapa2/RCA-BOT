# AGENTS.md

Guidance for AI coding agents (OpenCode and friends) working in this repo.

## Project purpose

RCA Bot is a **local-first** assistant that investigates Sentry production
issues from Slack. A developer replies `@rca-bot investigate` in a Sentry
alert thread; the bot fetches authoritative issue/event data from the Sentry
Web API, retrieves relevant code from **one configured local repository**,
asks a local LLM (Ollama) for a structured RCA, validates it against real
evidence, posts it back into the same Slack thread, and persists everything
for the Next.js dashboard.

## Architecture (read ARCHITECTURE.md for details)

* `backend/app/slack/` — Bolt + Socket Mode listener, thread context,
  Block Kit formatting/responses. Entry: `python -m app.slack`.
* `backend/app/sentry/` — `SentryClient` (only place calling Sentry),
  URL resolver, payload normalization.
* `backend/app/ingestion/` — file filtering (ignores `data/`/`chroma`/secrets), tree-sitter chunking with
  line-based fallback, hashing, incremental indexer. Vector store: `ChromaVectorStore` (persistent, default) + `InMemoryVectorStore` (tests).
  CLI: `python -m app.ingestion.index_repository`.
* `backend/app/retrieval/` — query building, **Lexical** (`LexicalRetriever`, alias `ExactRetriever`) + **Vector** (`Chroma`), `CodeRetriever` hybrid merge with `retrieve_with_trace` (query/lexical/vector/final).
* `backend/app/llm/` — `LLMProvider` abstraction; Ollama; grounded prompts with `analysis_status` (`CONFIDENT`/`LOW_CONFIDENCE`/`INSUFFICIENT_EVIDENCE`).
* `backend/app/investigation/` — orchestrator (records `*_latency_ms`, `llm_model`/`embedding_model`), context builder, **strict** `RCAValidator` (never clamps lines).
* `backend/app/api/`, `app/main.py` — FastAPI (`/health`, `/api/incidents*`, `/api/incidents/{id}/retrieval`, `/api/repositories*`).
* `backend/app/evaluation/` — 20-case RAG dataset + `Recall@k`/`MRR` (`python -m app.evaluation.run`).
* `frontend/` — Next.js App Router dashboard (retrieval trace + performance + `analysis_status` badge).

## Coding conventions

* Python 3.12+, type hints everywhere, SQLAlchemy 2.0 mapped_column style,
  Pydantic v2 for schemas/settings.
* Keep external services behind protocols/adapters (`SlackGateway`,
  `LLMProvider`, `EmbeddingProvider`, `VectorStore`, `SentryClient`) so tests
  never need credentials.
* Services receive sessions/factories explicitly; no hidden globals beyond
  `get_settings()` / engine singletons.
* Errors raise subclasses of `app.core.errors.RCABotError`; user-facing text
  comes from `safe_message`/`humanize_error` — internal details stay in logs.
* No comments unless genuinely non-obvious; no print debugging; use
  `app.core.logging.get_logger`.

## Testing requirements

```bash
cd backend && python -m pytest tests -v      # must pass before any handoff
```

* Every bug fix or new behavior gets a test.
* External integrations are exercised through fakes/MockTransport only —
  real Slack/Sentry/Ollama must never be required by CI or unit tests.
* Fixture data lives in `tests/fixtures/` (sample repo + JSON payloads).

## Security rules (non-negotiable)

* Never log, display, commit, or transmit tokens/secrets.
* Never index or embed `.env*`, credential, secret or key files; `data/`/`chroma/` are never indexed.
* Filesystem access is confined to the configured `REPOSITORY_PATH`
  (reject `..` traversal, absolute paths, symlink escapes).
* Never send whole files/repos to the LLM — only retrieved chunks within
  configured character limits.
* The LLM output is untrusted: strictly validate files/lines/evidence (`RCAValidator` never clamps, marks invalid) before persisting or posting.
* The application executes nothing suggested by the model.

## Forbidden functionality (belongs to V2/V3 — do NOT implement)

* Git history, blame, commit analysis
* GitHub integration, PR/branch creation
* Automatic code modification or execution
* Test execution by the LLM
* Historical incident RAG
* Multi-repository support
* Autonomous agents, background investigation loops
* Deployment, logs/traces, additional incident sources
* Cloud LLMs, paid vector DBs, LangChain/LangGraph (unless already required)

## How to run

Backend API:
```bash
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Slack Socket Mode listener:
```bash
cd backend && python -m app.slack
```

Repository indexing (Chroma persistent at `data/chroma`):
```bash
cd backend && python -m app.ingestion.index_repository
```

Frontend:
```bash
cd frontend && npm install && npm run dev   # http://localhost:3000
```

Configuration check:
```bash
python scripts/check_setup.py
```

Evaluation (no Ollama required for retrieval):
```bash
cd backend && python -m app.evaluation.run
cd backend && python -m pytest tests/evaluation -q
```

All configuration comes from `.env` at the repo root (see `.env.example`). Data lives in `PROJECT_ROOT/data` (git-ignored), never inside `REPOSITORY_PATH`.
