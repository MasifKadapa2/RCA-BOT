# V2 — Change Intelligence

V2 answers: **What changed that most likely caused this incident?**

V1: *“What is failing and where?”* — V2: *“What recent commit most likely caused it, and why?”*

## Purpose

Given a Sentry issue + stack trace + local code, determine the recent change most likely responsible, with explainable evidence and diffs. This is **evidence-backed regression analysis**, not a generic Git chatbot or auto-fixer.

**Core flow:**

```
Slack → Sentry (issue/event + normalized stack) → Hybrid Code Retrieval (Lexical + Vector/Chroma, with trace)
→ Git Candidate Generator (deterministic signals) → Deterministic Ranker (Top N)
→ Bounded Diff Extraction → Causal LLM (V2 prompt, FACT/INFERENCE/HYPOTHESIS)
→ Strict RegressionValidator → RegressionAnalysis → Slack + Dashboard
```

Important: `RETRIEVAL + RANKING + VALIDATION` are deterministic application code; `CAUSAL INTERPRETATION` is the LLM. The LLM never invents SHAs/paths/lines.

## Architecture extension

V1 pipeline is preserved. V2 adds `backend/app/git/` (safe `git` via fixed binary, list args, `shell=False`, validated `REPOSITORY_PATH`, timeouts, bounded output) and `backend/app/change_intelligence/` (candidate generation, diff analysis, causal reasoning, validation).

```
Repositories (existing)
git_commits | git_file_changes | git_change_hunks | git_index_state  (SQLite, incremental)
```

## Git indexing

```bash
python -m app.ingestion.index_git              # uses REPOSITORY_PATH
python -m app.ingestion.index_git --path /repo
```

- Window: `GIT_HISTORY_DAYS=90` (default) and `GIT_MAX_COMMITS=500`
- Bounded diffs: `MAX_COMMIT_DIFF_CHARS=20000` per commit, `MAX_PATCH_CHARS=2000` per hunk
- Incremental: shas already in `git_commits` are skipped; second run → `skipped_commits`
- Security: `.env`, `*.pem`, `*.key`, `*credential*`, `*secret*` patches are never stored
- Non-Git repo → `NotAGitRepositoryError` with clear message, Slack shows *“change intelligence unavailable”* but V1 RCA still posts

## Candidate generation & ranking

Window: `CHANGE_WINDOW_HOURS=24` before incident `firstSeen`.

**Signals (explainable, weighted):**
- **A Stack file match** `+3.0` — commit touched a stack-trace file
- **B Retrieved file match** `+2.5` — touched a V1-retrieved file
- **C Symbol match** `+2.0` — diff mentions failing/retrieved symbols
- **D Line proximity** `+1.5` — hunk near stack frame line (±15)
- **E Temporal proximity** `≤1.0` — closer to incident (never sufficient alone)
- **F Diff relevance** `≤1.5` — exception tokens in patch
- **G Modified-file** `≤1.0` — prefers `M` over `A`/`R`/`D`

Commits **after** the incident are `context_only` (never causal).

`MAX_CHANGE_CANDIDATES=10`; top 5 diffs (bounded by `MAX_DIFF_CHARS=12000`) go to the LLM.

## Diff analysis

For the top candidates, hunks are read from `git_change_hunks` (already bounded) and shown as `@@ -old,start +new,start` patches. Only relevant hunks are sent.

## Causal reasoning

Separate V2 prompt `backend/app/llm/prompts_v2.py` (`SYSTEM_PROMPT_V2`) requires `FACT`/`INFERENCE`/`HYPOTHESIS` labeling and allows the LLM to conclude `NO_CONFIDENT_REGRESSION` or `INSUFFICIENT_EVIDENCE`. It never claims `“this commit caused the incident”` without strong support; prefers `“is the strongest candidate because …”`.

**Schema:**

```python
RegressionStatus = LIKELY_REGRESSION | POSSIBLE_REGRESSION | NO_CONFIDENT_REGRESSION | INSUFFICIENT_EVIDENCE
ChangeCandidate = {commit_sha, short_sha, message, author, committed_at, changed_files, score, signals, hunks, relevance_reason}
RegressionAnalysis = {status, likely_commit, candidate_commits, causal_explanation,
                      evidence_for: [GitEvidenceRef], evidence_against, confidence, unknowns, recommended_action}
GitEvidenceRef = {commit_sha, file_path, start_line?, end_line?, description}
```

## Evidence & strict validation

`EvidenceType` extended with `GIT`. `RegressionValidator` checks:
- SHA exists in the indexed repo (full/short/prefix)
- commit was part of the candidate analysis
- file was changed in that commit
- hunk/line range exists
- no `..` traversal
Fabricated references are rejected, never repaired; a `LIKELY` claim whose `likely_commit` fails validation is downgraded to `POSSIBLE`.

## LLM context (bounded)

```
INCIDENT (Sentry + stack + first occurrence)
V1 CODE EVIDENCE ([C1] blocks, ≤3)
CANDIDATE [G1] sha, message, author, time, files, signals, bounded diff
CANDIDATE [G2] …
```
Never the whole repo or unbounded history.

## Timeline

`app/change_intelligence/change_analyzer.py:build_timeline` merges candidate `committed_at` (labeled *“Commit created …”*), `first_seen`/`last_seen`, and investigation times. It never claims deployment. API: `GET /api/incidents/{id}/timeline`.

## Slack & dashboard

- Slack remains `@rca-bot investigate` in `SLACK_ALERT_CHANNEL_ID` (triggered by any `app_mention` in V1; now also works with a bare tag). After the V1 RCA, V2 posts a follow-up thread message `🔍 Change Intelligence` with status, likely commit, why-checklist, causal explanation, Git evidence, unknowns, `⚠️ Deployment timing is unknown…`, and `Suggested action`. Git failures post `ℹ️ RCA completed, but change intelligence is unavailable…` without breaking V1.
- Dashboard incident detail adds **Change Intelligence** (status badge, likely commit, why-checklist, causal explanation, Git evidence, candidate list, unknowns, recommended action), **Timeline**, and **Performance** already shows V1 latencies plus `causal_llm`, `git_candidate`, `diff_analysis`, `regression_validation`, `regression_model`.

## Configuration

All in `.env.example`:

```
GIT_HISTORY_DAYS=90
GIT_MAX_COMMITS=500
CHANGE_WINDOW_HOURS=24
MAX_CHANGE_CANDIDATES=10
MAX_DIFF_CHARS=12000
MAX_COMMIT_DIFF_CHARS=20000
MAX_PATCH_CHARS=2000
GIT_TIMEOUT_SECONDS=30
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
LLM_API_KEY=
LLM_MODEL=gemini-2.0-flash
```

`GROQ_*` remains as an alternative free cloud path.

## APIs

- `POST /api/repositories/index_git` — incremental index, returns `{commits_indexed, new_commits, skipped_commits, files_changed, hunks_indexed}`
- `GET /api/incidents/{id}/regression` — `RegressionOut`
- `GET /api/incidents/{id}/changes` — alias focused on candidates
- `GET /api/incidents/{id}/timeline` — chronological entries
- `GET /api/incidents/{id}/retrieval` — already in V1, now includes `query/lexical/vector/final` for observability

## Evaluation

```bash
# V1 retrieval (hash embeddings, synthetic repo)
python -m app.evaluation.run

# V2 candidate retrieval + deterministic classification (no LLM)
python -m app.evaluation.run_regression
pytest tests/evaluation -q
```

Dataset: 20 V2 cases (direct/indirect/unrelated/multiple/older/newest-unrelated/same-file/symbol/nearby/rename/delete/refactor/config/async/validation/api/dependency/merge/none/insufficient). Metrics: `Candidate Recall@1/3/5`, `MRR`, `classification_accuracy` — all deterministic, no leakage (expected SHAs never fed to ranking).

## Limitations (intentional V1+V2)

- One local Git repo (`REPOSITORY_PATH` must be a Git working tree)
- No deployment data — commit time ≠ deploy time
- Bounded diff window; huge diffs truncated and flagged
- LLM is a reasoning step only; the system never modifies code, creates PRs/branches, merges, deploys, or executes repo code

## Security

Fixed binary, structured args, `shell=False`, validated `REPOSITORY_PATH` and `repository-relative` paths, timeouts, bounded output, and reuse of V1 `FileFilter` patterns for secret files in diffs.
