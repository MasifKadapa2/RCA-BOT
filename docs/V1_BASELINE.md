# V1 Baseline (Frozen)

V1 is permanently recoverable via `git checkout v1.0.0` (tag `v1.0.0`, commit `1a735ab`).

## Verification commands used

```bash
cd backend && ./.venv/bin/python -m pytest tests -q
# → 125 passed

cd backend && ./.venv/bin/python -m app.evaluation.run
# RCA Bot Retrieval Evaluation
# Cases: 20
# Recall@5:  90.0%
# Recall@10: 95.0%
# MRR:       0.91
```

## V1 architecture summary

Slack `app_mention` (Bolt/Socket Mode, channel-restricted, idempotent)
→ ThreadContextService → SentryReferenceResolver → SentryClient (Web API)
→ IncidentContextBuilder (normalized stack frames) → Hybrid Retrieval
(LexicalRetriever over SQLite + VectorRetriever over Chroma, merged/ranked)
→ LLMProvider (Groq cloud default / Ollama local fallback / generic OpenAI-compat)
→ IncidentAnalysis JSON (analysis_status: CONFIDENT/LOW_CONFIDENCE/INSUFFICIENT_EVIDENCE)
→ RCAValidator (strict, never clamps/hides hallucinations)
→ SQLite persistence (incl. retrieval trace + latencies + model metadata)
→ Same-thread Slack Block Kit reply → Next.js dashboard.

## V1 known limitations

- One repository (`REPOSITORY_PATH`), one alert channel.
- No Git/change intelligence (added in V2).
- Groq free tier 8k tokens/min; Gemini/Ollama supported as alternatives.
- No deployment metadata; commit time ≠ deploy time.
- Single listener per token required (duplicate Socket Mode sessions race).

## Baseline metrics

| Metric | Value |
| --- | --- |
| Tests | 125 passed |
| Cases | 20 |
| Recall@5 | 0.90 |
| Recall@10 | 0.95 |
| MRR | 0.91 |
