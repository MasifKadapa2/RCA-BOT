# RCA Bot — Architecture (V1)

## Overview

RCA Bot investigates Sentry production issues from Slack using only local
resources: a configured repository checkout, Chroma/SQLite for the code index,
and Ollama for inference. Slack is the primary interface; the Next.js
dashboard is secondary (history + debugging).

## ASCII architecture diagram

```
                          ┌────────────────────────────────────────────────┐
                          │                 DEVELOPER'S LAPTOP             │
                          │                                                │
 ┌─────────┐   alerts    │  ┌──────────────────────────┐                  │
 │ Sentry  ├────────────►│  │ #sentry-alerts (Slack)   │                  │
 │ (cloud) │             │  └────────────┬─────────────┘                  │
 │         │             │               │ @rca-bot investigate           │
 │         │             │  ┌────────────▼─────────────┐                  │
 │         │◄────────────┼──┤ Slack Socket Mode        │                  │
 │         │  REST/API   │  │ listener (Bolt)          │                  │
 │         │  issues &   │  └────────────┬─────────────┘                  │
 │         │  events     │               │ app_mention                    │
 │         │             │  ┌────────────▼──────────────────────────┐    │
 │         │             │  │ IncidentInvestigator (orchestrator)   │    │
 │         │             │  └──┬───────────┬──────────────┬─────────┘    │
 └─────────┘             │     │           │              │              │
                          │     ▼           ▼              ▼              │
                          │  SentryClient  ThreadContext  CodeRetriever   │
                          │  (httpx)       Service        ├ LexicalRetriever│
                          │                              │ VectorRetriever│
                          │                              │  (Chroma+SQLite)│
                          │     ┌────────────────────────┴──────┐        │
                          │     ▼                               ▼        │
                          │  OllamaProvider ──► RCAValidator ──► SQLite   │
                          │  (local LLM)        (strict, no clamp) persist│
                          │                               ├ Investigation │
                          │                               ├ Evidence      │
                          │                               ├ Retrieval Trace│
                          │                               └ Performance   │
                          │                                     │        │
                          │  RCA posted in same thread ◄────────┘        │
                          │  (CONFIDENT/LOW_CONFIDENCE/INSUFFICIENT)     │
                          │                                             │
                          │  ┌────────────────────────────┐             │
                          │  │ FastAPI (port 8000)        │             │
                          │  │ /health /api/incidents …   │             │
                          │  │ /api/incidents/{id}/retrieval            │
                          │  └────────────▲───────────────┘             │
                          │               │ fetch /api/*                │
                          │  ┌────────────┴───────────────┐             │
                          │  │ Next.js dashboard (:3000)  │             │
                          │  │ retrieval trace + perf     │             │
                          │  └────────────────────────────┘             │
                          └────────────────────────────────────────────────┘
```

## Module map

| Module | Responsibility |
| --- | --- |
| `app/slack/gateway.py` | `SlackGateway` protocol + `WebClientGateway` / `InMemoryGateway` (InMemory test-only) |
| `app/slack/thread_context.py` | Root-thread resolution, full thread retrieval |
| `app/slack/handlers.py` | Channel validation, idempotency, progress message |
| `app/slack/handler.py` | Bolt wiring (`create_bolt_app`/`start_socket_mode`) |
| `app/slack/__main__.py` | Socket Mode entrypoint (`python -m app.slack`) |
| `app/slack/formatter.py` | Block Kit rendering of progress/RCA/failures + `INSUFFICIENT_EVIDENCE` |
| `app/slack/responder.py` | Posts into the originating thread |
| `app/sentry/client.py` | The **only** module calling Sentry's Web API |
| `app/sentry/resolver.py` | Sentry issue URL extraction from thread text |
| `app/sentry/normalization.py` | Raw payloads → typed models, frame normalization |
| `app/investigation/context_builder.py` | Issue/event → normalized incident context |
| `app/retrieval/query_builder.py` | Exception/stack → lexical terms + semantic query |
| `app/retrieval/exact.py` | `LexicalRetriever` (formerly `ExactRetriever`, alias kept) over SQLite (`ILIKE` substring, weighted) |
| `app/retrieval/vector.py` | `VectorRetriever` over `ChromaVectorStore` (cosine) |
| `app/retrieval/retriever.py` | `CodeRetriever` hybrid merge, dedup, rank (lexical > vector), `retrieve_with_trace` |
| `app/ingestion/*` | File filtering (ignores `data/`, `chroma`, secrets), tree-sitter + fallback, hashing, incremental indexing |
| `app/ingestion/vector_store.py` | `ChromaVectorStore` (default, persistent) + `InMemoryVectorStore` (tests) |
| `app/llm/provider.py` | `LLMProvider` abstraction, structured-output parsing with `analysis_status` |
| `app/llm/ollama_provider.py` | Ollama HTTP implementation |
| `app/llm/prompts.py` | Grounding prompt with `CONFIDENT/LOW_CONFIDENCE/INSUFFICIENT_EVIDENCE` |
| `app/investigation/investigator.py` | Orchestrates pipeline, records latencies, persists `analysis_status` + `retrieval trace` |
| `app/investigation/validator.py` | Strict validation (never clamps lines, rejects traversal/symlink/absolute/out-of-range) |
| `app/evaluation/*` | 20-case RAG dataset + `Recall@k`/`MRR` metrics, no Ollama required |

## Investigation flow

1. **Trigger.** `app_mention` in the configured alert channel only. Other
   channels receive *"This channel is not configured…"*.
2. **Idempotency.** Unique constraint `(slack_channel_id,
   triggering_message_ts)` on `incidents`; duplicate triggers are answered
   once and never re-investigated.
3. **Thread context.** `conversations.replies` with cursor pagination; the
   root `thread_ts` is derived from the event (mention-in-thread vs root). Timed as `slack_latency_ms`.
4. **Sentry reference.** Regex resolution over all thread messages supporting
   `*.sentry.io`, `/organizations/<org>/issues/<id>/`, self-hosted hosts and
   legacy `<org>/<project>/issues/<id>/` layouts. No link → explicit guidance
   message; V1 never guesses by error text.
5. **Authoritative Sentry data.** `GET /issues/{id}/` +
   `GET /issues/{id}/events/latest/` (fallback: events list). Bearer auth
   server-side only. Timed as `sentry_latency_ms`.
6. **Normalization.** Exceptions and frames become typed models
   (`StackFrame`, `ExceptionInfo`, `SentryEventData`) tolerating missing
   fields across SDKs/platforms; in-app frames are prioritized over library
   frames.
7. **Hybrid Retrieval.** Queries are built from filenames, symbols, modules,
   exception type/value and issue title. **Lexical** (SQLite `ILIKE` substring, weighted: exact filename > exact symbol > stack-frame > content) + **Vector** (Chroma cosine) → merged, de-duplicated by path+line, boosted on stack-frame file, capped at `MAX_RETRIEVAL_RESULTS` and `MAX_LLM_CONTEXT_CHARS`. Full trace (query + lexical + vector + final) persisted for dashboard (`GET /api/incidents/{id}/retrieval`). Timed as `retrieval_latency_ms`.
8. **LLM.** Only the incident facts, normalized stack, thread excerpt and
   retrieved snippets are sent — never the repository, secrets or tokens.
   System prompt enforces grounding + `analysis_status` (`CONFIDENT`/`LOW_CONFIDENCE`/`INSUFFICIENT_EVIDENCE`); `INSUFFICIENT_EVIDENCE` must output `summary: Unable to establish...` and not fabricate a cause. Output must be JSON matching `IncidentAnalysis`; malformed triggers one retry. Timed as `llm_latency_ms`; `llm_model`/`embedding_model` persisted.
9. **Validation (strict).** File must exist, be inside `REPOSITORY_PATH`, not escape via `../`/symlink/absolute, and be in retrieved context; `1 <= start_line <= end_line <= file length` (otherwise **rejected, never clamped**); `CODE` evidence must be retrieved, `SENTRY` must match investigated issue, `SLACK` must be from thread. Invalid refs are dropped and logged. Timed as `validation_latency_ms`.
10. **Persistence & response.** States `PENDING → INVESTIGATING → COMPLETED/FAILED` in SQLite; `Investigations` stores `analysis_status`, latencies, models, retrieval trace, and `code_snippets`; evidence rows stored; RCA posted in same thread with status-aware formatting (`⚠️ Insufficient evidence` for `INSUFFICIENT_EVIDENCE`) + AI disclaimer. Timed `total_latency_ms`.

## Repository indexing

* Walk respects configurable ignore dirs/patterns; `.env*`, credential,
  secret and key files are never read; binaries are sniffed (null byte /
  entropy heuristic); oversized files skipped. `data/` and `chroma/` are always ignored so SQLite/Chroma never become part of the indexed repo.
* Tree-sitter grammars produce semantic chunks per language (Ruby, Python,
  JS/TS, Go, Java) preserving `relative_path`, `symbol_name`, `symbol_type`,
  `start_line`, `end_line`, `content`, `content_hash`. A deterministic
  line-based fallback chunker guarantees indexing never fails on one file.
* Incremental: unchanged content hash ⇒ skip embedding entirely; changed
  files have their vectors deleted and re-upserted; removed files purged from
  both stores. `ChromaVectorStore` (`data/chroma`) is the default persistent store; `InMemoryVectorStore` is test-only.

## Database

SQLite via SQLAlchemy (`data/rca_bot.db` anchored to `PROJECT_ROOT/data`, never inside `REPOSITORY_PATH`): `repositories`, `indexed_files`, `code_chunks`, `sentry_issues`, `incidents`, `investigations` (now with `analysis_status`, `llm_model`/`embedding_model`, `*_latency_ms`, `retrieval_query`/`lexical_results`/`vector_results`/`final_context`), `evidence`, `slack_messages`. Vectors live in Chroma at `CHROMA_PATH` (`PROJECT_ROOT/data/chroma`); `vector_id` columns link rows to vector entries. `init_db()` creates tables and migrates new columns idempotently.

## Frontend

Next.js App Router + Tailwind. Pages: overview stats, incident list, incident
detail (overview, Sentry info, stack trace, RCA sections with `analysis_status` badge, evidence, unknowns, relevant files, code snippets), **retrieval trace** (query + lexical/vector/final), **performance** (latency breakdown + `llm_model`/`embedding_model` in collapsible Technical Details), repositories (with re-index trigger), settings (configuration booleans only). `GET /api/incidents/{id}/retrieval` powers the trace. The API proxy keeps CORS out of the picture locally.

## Evaluation (V1 quality, no Ollama required for retrieval)

* Dataset `backend/app/evaluation/dataset.py` — 20 synthetic cases covering nil deref, missing key, wrong method, conditional, arg, serialization, DB lookup, auth, type, boundary, association, config, parsing, validation, return type, async race, propagation, API response, file/path, and one `INSUFFICIENT_EVIDENCE` case.
* Metrics `backend/app/evaluation/metrics.py` — `Recall@5`, `Recall@10`, `MRR` computed deterministically over the synthetic repo (indexed with `HashEmbeddingProvider` + `InMemoryVectorStore` or `Chroma`).
* Runner `python -m app.evaluation.run` (or `pytest tests/evaluation -q`) prints `Cases: 20 / Recall@5: 90% / Recall@10: 95% / MRR: 0.91` — no cloud LLM, optional deterministic RCA checks (file exists, line valid, evidence grounded).

## Security boundaries

* Slack token: listener process only. Sentry token: `SentryClient` only.
* Repository filesystem access confined to `REPOSITORY_PATH` with traversal
  + symlink + absolute-path rejection; secrets never indexed or embedded; `data/` never indexed.
* The LLM receives curated context and returns analysis text only; it cannot
  invoke tools, execute commands or modify code. No credentials are persisted as latency/model metadata.
* Logs carry correlation ids (`inv_<incident_id>`) but never tokens or
  full source contents.

## Deliberately out of scope (V1 — intentionally NOT implemented)

Git history, blame, commit analysis, GitHub integration, PR/branch creation, automatic code modification/execution, test execution by LLM, historical incident RAG, multi-repository, autonomous agents, deployment/log/trace integrations, additional incident sources, cloud LLMs, paid vector DBs, LangChain/LangGraph (unless already required). These belong to later versions.
