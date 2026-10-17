# RCA Bot — V1 + V2 (Change Intelligence)

Local-first AI engineering assistant that investigates Sentry production
issues directly from Slack. A developer replies to a Sentry alert thread with
`@rca-bot investigate`; the bot reads the thread, pulls authoritative data
from the Sentry Web API, searches the configured local repository, asks an
LLM (local Ollama by default; Gemini/Groq optional) for a structured
root-cause analysis, validates the
result against real evidence, and posts the RCA back into the **same Slack
thread**. Every investigation is persisted and visible in the web dashboard.

**V2 — Change Intelligence** extends the same thread with evidence-backed
regression analysis: *what recent commit most likely caused this incident?*
Deterministic git indexing + candidate ranking + strict validation answer
*which change*, and a causal LLM prompt only explains *why* — it never
invents SHAs, paths or lines. See [docs/V2_CHANGE_INTELLIGENCE.md](docs/V2_CHANGE_INTELLIGENCE.md).

> The existing Sentry → Slack integration is **external** to RCA Bot.
> Sentry already posts alerts into your alert channel; RCA Bot starts from
> the Slack side.

---

## Architecture at a glance

```
Sentry ──(existing integration)──▶ #sentry-alerts
                                      │  @rca-bot investigate
                                      ▼
                     Slack Socket Mode listener (backend)
                                      │
        thread context → Sentry Web API → stack trace normalization
                                      │
                    local repo index (Chroma + SQLite) retrieval
                                      │
                           LLM → structured RCA (JSON)
                                      │
                        evidence validation (anti-hallucination)
                                      │
                   RCA posted in same thread + persisted for dashboard
                                      │
        V2 follow-up: git candidates → rank → bounded diffs → causal LLM
                                      │
                      RegressionValidator → 🔍 Change Intelligence reply
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for details.

---

## Repository layout

| Path | Purpose |
| --- | --- |
| `backend/` | FastAPI app, Slack listener, Sentry client, indexer, retriever, LLM, git layer, change intelligence |
| `frontend/` | Next.js dashboard |
| `data/` | SQLite database + Chroma vectors (created locally, git-ignored) |
| `docs/` | V1 baseline notes + V2 Change Intelligence spec |
| `scripts/check_setup.py` | Verifies Slack/Sentry/Ollama/repo configuration |
| `.env.example` | Template for required environment variables |

---

## 1. Prerequisites

* Python 3.12+
* Node.js 20+ (for the dashboard)
* An LLM: [Ollama](https://ollama.com/download) (fully local) or a free
  Gemini/Groq API key — see section 4
* A Sentry account with an issue you can test against
* A Slack workspace where you can install an app

---

## 2. Slack app setup

RCA Bot uses **Socket Mode**, so no public URL is needed — perfect for laptops.

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → *From scratch*.
2. **Socket Mode** (left sidebar): enable it. Slack asks you to create an
   **app-level token** with scope `connections:write`. Copy it
   (`xapp-…`) → this is `SLACK_APP_TOKEN`.
3. **OAuth & Permissions → Bot Token Scopes**, add:
   * `app_mentions:read` — receive `@rca-bot` mentions
   * `chat:write` — post the RCA replies
   * `channels:history` / `groups:history` — read thread messages
4. **Event Subscriptions**: enable, subscribe to **bot events**:
   * `app_mention`
5. **Install App to Workspace** and copy the **Bot User OAuth Token**
   (`xoxb-…`) → this is `SLACK_BOT_TOKEN`.
6. Invite the bot to your alert channel: `/invite @rca-bot` in `#sentry-alerts`.
7. Get the channel ID (right-click channel → *View channel details*) →
   this is `SLACK_ALERT_CHANNEL_ID`. Only mentions in this channel trigger
   investigations.

> **Note:** the existing Sentry → Slack integration keeps posting alerts into
> that channel exactly as before. RCA Bot never replaces it.

---

## 3. Sentry setup

1. Create an auth token: Sentry → **Settings → Auth Tokens → Create New Token**
   (for sentry.io; self-hosted has the same under its admin). Give it the
   **read** scopes for events/issues (`event:read`, `issue:read`, `project:read`
   or the equivalent "Member" token template).
2. Put the token in `SENTRY_AUTH_TOKEN`, your organization slug in `SENTRY_ORG`.
3. For **self-hosted Sentry**, set `SENTRY_BASE_URL` (e.g.
   `https://sentry.mycompany.com/api/0`). SaaS users leave the default.
4. Test connectivity:

```bash
python scripts/check_setup.py
```

The bot resolves the Sentry issue ID from the URL that the Sentry→Slack
integration posts into the thread — it works with `*.sentry.io`, SaaS
`/organizations/<org>/issues/<id>/` links and most self-hosted layouts.

---

## 4. LLM setup

RCA Bot picks the first configured provider in this order:

1. **Generic OpenAI-compatible cloud** — `LLM_BASE_URL` + `LLM_API_KEY` +
   `LLM_MODEL` (e.g. Google Gemini free tier,
   `https://generativelanguage.googleapis.com/v1beta/openai` / `gemini-2.0-flash`).
2. **Groq free tier** — `GROQ_API_KEY` (+ optional `GROQ_MODEL`).
3. **Ollama, fully local** — `OLLAMA_BASE_URL` + `OLLAMA_MODEL`.

For local-only operation (nothing leaves your machine):

```bash
ollama pull qwen2.5-coder:3b   # recommended lightweight coding model (~2 GB)
ollama serve                   # usually already running after install
curl http://localhost:11434/api/tags   # verify
```

Any coding-capable model works — configure via `OLLAMA_MODEL`
(e.g. `qwen2.5-coder:7b`, `llama3.1:8b`, `deepseek-coder:6.7b`). Pick based on
your RAM/GPU; only retrieved snippets are sent, so ~8k context suffices.

---

## 5. Configure & install

```bash
cp .env.example .env          # then fill in the values from steps 2–4
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Full variable reference lives in [.env.example](.env.example).

---

## 6. Index the repository

Point `REPOSITORY_PATH` at the project you want investigated, then:

```bash
python -m app.ingestion.index_repository
```

* Walks the repo (ignoring `node_modules`, `.git`, secrets, binaries,
  large files), parses Ruby / Python / JS / TS / Go / Java with tree-sitter
  (line-based fallback included), embeds locally with sentence-transformers,
  and stores vectors in Chroma + metadata in SQLite.
* **Incremental:** re-run any time; unchanged files are skipped, changed files
  re-indexed, deleted files removed.
* Or trigger from the dashboard: *Repositories → Run indexing*.

---

## 7. Run everything

Terminal 1 — API server (port 8000):
```bash
cd backend && uvicorn app.main:app --reload --port 8000
```

Terminal 2 — Slack Socket Mode listener:
```bash
cd backend && python -m app.slack
```

Terminal 3 — Dashboard (port 3000):
```bash
cd frontend && npm install && npm run dev
```

An LLM must be configured (section 4) before investigations start — with
Ollama that means `ollama serve` is running.

---

## 8. End-to-end demo

1. Start your LLM (Ollama or cloud), backend, listener and dashboard (section 7).
2. Ensure the repository is indexed (section 6).
3. Wait for a Sentry alert in `#sentry-alerts` (existing integration).
4. Open the alert's thread and reply:
   ```
   @rca-bot investigate
   ```
5. The bot replies in-thread:
   🔎 **RCA Bot is investigating this incident...**
6. It then extracts the Sentry link, fetches issue + latest event via the
   Sentry Web API, normalizes the stack trace, retrieves matching code from
   your local index, calls the LLM, and validates the returned RCA.
7. Finally it posts the structured RCA in the same thread — root cause,
   evidence, suggested fix, relevant files, confidence, unknowns and the
   disclaimer ⚠️ *AI-generated analysis. Verify before applying changes.*
8. Open <http://localhost:3000> to browse investigation history, evidence,
   stack traces and retrieved code snippets.

If no Sentry link exists in the thread the bot answers with guidance instead
of guessing. Duplicate `investigate` requests on the same message are ignored.

---

## 9. V2 — Change Intelligence

V1 answers *what is failing and where*; V2 answers *what recent change most
likely caused it*. Full spec: [docs/V2_CHANGE_INTELLIGENCE.md](docs/V2_CHANGE_INTELLIGENCE.md).

Index the git history of `REPOSITORY_PATH` (incremental; secret-file patches
are never stored):

```bash
python -m app.ingestion.index_git
```

* **Deterministic ranking:** explainable weighted signals (stack-file match,
  retrieved-file match, symbol match, line proximity, temporal proximity,
  diff relevance) select `MAX_CHANGE_CANDIDATES` commits — the LLM never
  picks the commit.
* **Causal reasoning:** only bounded diffs of the top candidates reach a
  separate V2 prompt that must label every claim `FACT` / `INFERENCE` /
  `HYPOTHESIS` and may answer `NO_CONFIDENT_REGRESSION` or
  `INSUFFICIENT_EVIDENCE`.
* **Strict validation:** `RegressionValidator` rejects fabricated SHAs,
  files and line ranges, and downgrades `LIKELY_REGRESSION` →
  `POSSIBLE_REGRESSION` when the evidence does not hold.
* **Slack & dashboard:** the V1 RCA is followed by a `🔍 Change Intelligence`
  thread message; the dashboard adds **Change Intelligence**, **Timeline**
  and V2 latencies under **Performance**.
* **APIs:** `POST /api/repositories/index_git`,
  `GET /api/incidents/{id}/regression`, `/changes`, `/timeline`.
* A non-git `REPOSITORY_PATH` (or any git failure) only skips the follow-up —
  the V1 RCA still posts.

---

## Tests

```bash
cd backend && python -m pytest tests -v
```

The suite covers Slack handling, Sentry parsing/normalization, repository
filtering/chunking/indexing, lexical + vector retrieval and ranking, Ollama
integration, RCA validation (including absolute/traversal/line-range checks),
retrieval trace, analysis status, and a fully mocked end-to-end pipeline —
plus V2 git client/indexing, candidate ranking, regression validation and a
mocked change-intelligence end-to-end run.
**No real Slack/Sentry/Ollama credentials are required.**

## Evaluation (V1 retrieval + V2 change intelligence)

```bash
# Deterministic retrieval evaluation (no Ollama required)
cd backend && python -m app.evaluation.run
# V2 candidate ranking + deterministic classification (no LLM)
cd backend && python -m app.evaluation.run_regression
# both
cd backend && python -m pytest tests/evaluation -q
```

- Dataset: 20 synthetic cases in `backend/app/evaluation/dataset.py` covering nil deref, missing key, wrong method, serialization, DB lookup, auth, type mismatch, boundary, missing association, etc., plus one `INSUFFICIENT_EVIDENCE` case. Each case stores expected file/symbol/lines and expected `analysis_status`.
- Metrics: `Recall@5`, `Recall@10`, `MRR` computed over the 20 cases (e.g. `Recall@5: 90%` on the synthetic repo).
- Optional LLM RCA checks are deterministic (file exists, line valid, evidence grounded, Sentry ID correct) — no cloud judge required. See `backend/app/evaluation/run.py`.
- V2 dataset: 20 regression cases (direct / indirect / unrelated / multiple / older / rename / delete / dependency / none / insufficient …) with metrics `Candidate Recall@1/3/5`, `MRR` and `classification_accuracy` — deterministic, and expected SHAs are never fed to the ranker (no leakage).

---

## Retrieval trace, analysis status, and observability

- **Vector store:** `ChromaVectorStore` (`data/chroma`) is the default for real runs; `InMemoryVectorStore` is test-only. Indexing writes to Chroma and survives restarts; retrieval after restart still works.
- **Data location:** SQLite `data/rca_bot.db` + Chroma `data/chroma` live under the **RCA Bot app directory** (`PROJECT_ROOT/data`), never inside `REPOSITORY_PATH`. Indexing ignores `data/`, `chroma/`, `.env*`, `*secret*`, binaries, and large files.
- **Retrieval trace:** For every investigation the dashboard shows *why* files were given to the LLM: `GET /api/incidents/{id}/retrieval` returns the query, lexical results (rank/path/lines/symbol/score/match reason), vector results (similarity), and final context `[C1] path:lines`.
- **Analysis status:** `CONFIDENT` / `LOW_CONFIDENCE` / `INSUFFICIENT_EVIDENCE` (`analysis_status` + `confidence`). When evidence is insufficient the bot posts `⚠️ Insufficient evidence` and `summary: Unable to establish...` instead of fabricating a cause.
- **Validation — never hide hallucinations:** File must exist, be inside `REPOSITORY_PATH`, not escape via `../`/symlink/absolute, and be in retrieved context; `1 <= start_line <= end_line <= file length` (otherwise marked invalid, never clamped); `CODE` evidence must be retrieved, `SENTRY` must match investigated issue, `SLACK` must be from thread.
- **Latency & models:** Each investigation stores `slack_latency_ms`, `sentry_latency_ms`, `retrieval_latency_ms`, `llm_latency_ms`, `validation_latency_ms`, `total_latency_ms` and `llm_model`/`embedding_model` (shown in dashboard **Technical Details** collapsible). V2 adds `git_index_latency_ms`, `git_candidate_latency_ms`,
`diff_analysis_latency_ms`, `causal_llm_latency_ms` and
`regression_validation_latency_ms`.

## Security notes

* Tokens live only in the server process; they are never logged, never sent
  to the frontend, Slack messages or the LLM prompt.
* Only the configured `REPOSITORY_PATH` is indexed/read; path traversal is
  rejected; `.env`/credential files are never indexed.
* LLM-referenced files/lines are validated against real repository content;
  hallucinated references are removed before anything is posted or stored.
* The LLM only produces analysis text — it cannot execute commands or modify
  code.

---

## Known limitations (intentional, V1 + V2)

* One repository, one Slack alert channel; `REPOSITORY_PATH` must be a git
  working tree for change intelligence (V1 RCA works without git).
* No GitHub integration, PR/branch creation
* No automatic code modification or execution
* No test execution by the LLM
* No historical incident RAG
* No deployment metadata — commit time is not deploy time
* No autonomous agents or background investigation loops
* No deployment integrations
* No logs/traces integration
* No additional incident sources
* No cloud LLMs or paid vector DBs
* Issue search by error message (without a link) is intentionally absent.
* Indexing triggered from the dashboard runs synchronously.
