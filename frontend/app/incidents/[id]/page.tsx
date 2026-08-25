import Link from "next/link";
import { api, formatDateTime } from "@/lib/api";
import { StatusBadge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { CodeSnippet } from "@/components/code-viewer";

export const dynamic = "force-dynamic";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader title={title} />
      <CardBody>{children}</CardBody>
    </Card>
  );
}

export default async function IncidentDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const [incident, retrieval] = await Promise.all([api.incident(id), api.retrieval(id)]);

  if (!incident) {
    return (
      <Card>
        <CardBody>
          <p className="text-sm text-slate-500">Incident #{id} not found (or backend unreachable).</p>
          <Link href="/incidents" className="mt-2 inline-block text-sm text-blue-600 hover:underline">
            ← Back to incidents
          </Link>
        </CardBody>
      </Card>
    );
  }

  const investigation = incident.investigation;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">
            Incident #{incident.id} — {incident.sentry_title ?? "Unknown issue"}
          </h1>
          <p className="mt-1 text-sm text-slate-500">{formatDateTime(incident.created_at)}</p>
        </div>
        <StatusBadge status={incident.status} />
      </div>

      {incident.error_message ? (
        <Section title="Error">
          <p className="text-sm text-red-600">{incident.error_message}</p>
        </Section>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="Overview">
          <dl className="grid grid-cols-[10rem_1fr] gap-y-2 text-sm">
            <dt className="text-slate-500">Sentry issue</dt>
            <dd className="font-mono text-xs">{incident.sentry_issue_id ?? "—"}</dd>
            <dt className="text-slate-500">Environment</dt>
            <dd>{incident.environment ?? incident.sentry_environment ?? "—"}</dd>
            <dt className="text-slate-500">Release</dt>
            <dd className="font-mono text-xs">{incident.release ?? incident.sentry_release ?? "—"}</dd>
            <dt className="text-slate-500">Platform</dt>
            <dd>{incident.platform ?? "—"}</dd>
            <dt className="text-slate-500">Transaction</dt>
            <dd className="font-mono text-xs">{incident.transaction ?? "—"}</dd>
            <dt className="text-slate-500">Slack thread</dt>
            <dd className="font-mono text-xs">
              {incident.slack_channel_id ? `#${incident.slack_channel_id}` : "—"} ·{" "}
              {incident.slack_thread_ts ?? "—"}
            </dd>
          </dl>
        </Section>

        <Section title="Exception">
          {incident.exception?.type ? (
            <div>
              <p className="font-mono text-sm font-semibold text-red-600">
                {incident.exception.type}
              </p>
              <p className="mt-1 text-sm text-slate-700">{incident.exception.value}</p>
            </div>
          ) : (
            <p className="text-sm text-slate-500">No exception data.</p>
          )}
        </Section>
      </div>

      <Section title="Stack trace (in-app frames first)">
        {!incident.stack_trace.length ? (
          <p className="text-sm text-slate-500">No frames recorded.</p>
        ) : (
          <ol className="space-y-2">
            {incident.stack_trace.map((frame, index) => (
              <li key={index} className="font-mono text-xs">
                <span className={frame.in_app ? "text-slate-900" : "text-slate-400"}>
                  {frame.filename ?? frame.module ?? "(unknown)"}
                  {frame.line_number ? `:${frame.line_number}` : ""}
                  {frame.function ? ` in \`${frame.function}\`` : ""}
                </span>
                {frame.context_line ? (
                  <span className="ml-3 rounded bg-slate-100 px-2 py-0.5 text-slate-700">
                    {frame.context_line.trim()}
                  </span>
                ) : null}
              </li>
            ))}
          </ol>
        )}
      </Section>

      {investigation ? (
        <>
          <Section title="Summary">{investigation.summary ?? "—"}</Section>
          <Section title="Root Cause">{investigation.root_cause ?? "—"}</Section>

          <Section title="Evidence">
            <ul className="space-y-2 text-sm">
              {(incident.evidence ?? []).map((ev, index) => (
                <li key={index}>
                  <span className="mr-2 rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 font-mono text-[11px] uppercase">
                    {ev.source_type}
                  </span>
                  <code className="font-mono text-xs text-slate-700">{ev.source_reference}</code>
                  <p className="mt-0.5 text-slate-600">{ev.explanation}</p>
                </li>
              ))}
            </ul>
          </Section>

          <Section title="Suggested Fix">{investigation.suggested_fix ?? "—"}</Section>

          <Section title="Relevant Code">
            {!incident.code_snippets.length ? (
              <p className="text-sm text-slate-500">No code snippets stored.</p>
            ) : (
              <ul className="space-y-3">
                {incident.code_snippets.map((snippet, index) => (
                  <li key={index}>
                    <CodeSnippet
                      path={snippet.path}
                      startLine={snippet.start_line ?? 1}
                      endLine={snippet.end_line ?? undefined}
                      content={snippet.content}
                    />
                    {snippet.symbol_name ? (
                      <p className="mt-1 text-xs text-slate-400">symbol: {snippet.symbol_name}</p>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </Section>

          {incident.relevant_files.length ? (
            <Section title="Referenced Files">
              <ul className="space-y-1 text-sm">
                {incident.relevant_files.map((rf, index) => (
                  <li key={index}>
                    <code className="font-mono text-xs text-slate-700">
                      {rf.path}:{rf.start_line}-{rf.end_line}
                    </code>
                    {rf.reason ? <span className="ml-2 text-slate-500">{rf.reason}</span> : null}
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}

          <Section title="Analysis">
            <div className="flex items-center gap-3">
              <p className="text-2xl font-semibold">{investigation.confidence ?? 0}%</p>
              {investigation.analysis_status ? (
                <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium border ${investigation.analysis_status === "CONFIDENT" ? "bg-emerald-50 text-emerald-700 border-emerald-200" : investigation.analysis_status === "LOW_CONFIDENCE" ? "bg-amber-50 text-amber-700 border-amber-200" : "bg-red-50 text-red-700 border-red-200"}`}>
                  {investigation.analysis_status}
                </span>
              ) : null}
            </div>
            {investigation.analysis_status === "INSUFFICIENT_EVIDENCE" ? (
              <p className="mt-2 text-sm font-medium text-amber-700">⚠️ Insufficient evidence — unable to establish a root cause from the available evidence.</p>
            ) : null}
            {investigation.unknowns.length ? (
              <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-slate-600">
                {investigation.unknowns.map((unknown, index) => (
                  <li key={index}>{unknown}</li>
                ))}
              </ul>
            ) : null}
            <p className="mt-4 text-xs text-slate-400">
              ⚠️ AI-generated analysis. Verify before applying changes.
            </p>
          </Section>

          <Section title="Retrieval Trace">
            {!retrieval || (!retrieval.query && !retrieval.lexical_results && !retrieval.vector_results) ? (
              <p className="text-sm text-slate-500">No retrieval trace stored for this investigation.</p>
            ) : (
              <div className="space-y-4 text-sm">
                {retrieval.query ? (
                  <div>
                    <h4 className="font-medium text-slate-900">Query</h4>
                    <p className="mt-1 font-mono text-xs text-slate-600">
                      {retrieval.query.semantic_query || "—"}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      Filenames: {retrieval.query.exact_filenames?.join(", ") || "—"} <br />
                      Symbols: {retrieval.query.exact_symbols?.join(", ") || "—"}
                    </p>
                  </div>
                ) : null}
                {retrieval.lexical_results?.length ? (
                  <div>
                    <h4 className="font-medium text-slate-900">Lexical Results</h4>
                    <ol className="mt-1 space-y-1">
                      {retrieval.lexical_results.slice(0, 10).map((r) => (
                        <li key={`${r.path}-${r.start_line}`} className="font-mono text-xs">
                          <span className="font-semibold">{r.rank}.</span> {r.path}:{r.start_line}-{r.end_line} {r.symbol ? `(${r.symbol})` : ""} — {r.match_reason} {r.score ? `score:${r.score}` : ""}
                        </li>
                      ))}
                    </ol>
                  </div>
                ) : null}
                {retrieval.vector_results?.length ? (
                  <div>
                    <h4 className="font-medium text-slate-900">Vector Results</h4>
                    <ol className="mt-1 space-y-1">
                      {retrieval.vector_results.slice(0, 10).map((r) => (
                        <li key={`${r.path}-${r.start_line}`} className="font-mono text-xs">
                          <span className="font-semibold">{r.rank}.</span> {r.path}:{r.start_line}-{r.end_line} {r.symbol ? `(${r.symbol})` : ""} — similarity:{r.similarity ?? r.score}
                        </li>
                      ))}
                    </ol>
                  </div>
                ) : null}
                {retrieval.final_context?.length ? (
                  <div>
                    <h4 className="font-medium text-slate-900">Final Context (passed to LLM)</h4>
                    <ol className="mt-1 space-y-1">
                      {retrieval.final_context.map((c) => (
                        <li key={`${c.path}-${c.start_line}`} className="font-mono text-xs">
                          [C{c.rank}] {c.path}:{c.start_line}-{c.end_line} {c.symbol ? `(${c.symbol})` : ""}
                        </li>
                      ))}
                    </ol>
                  </div>
                ) : null}
              </div>
            )}
          </Section>

          <Section title="Performance">
            <div className="space-y-2 text-sm">
              <div className="grid grid-cols-[10rem_1fr] gap-y-1">
                <span className="text-slate-500">Slack</span><span>{investigation.slack_latency_ms ?? "—"} ms</span>
                <span className="text-slate-500">Sentry</span><span>{investigation.sentry_latency_ms ?? "—"} ms</span>
                <span className="text-slate-500">Retrieval</span><span>{investigation.retrieval_latency_ms ?? "—"} ms</span>
                <span className="text-slate-500">LLM</span><span>{investigation.llm_latency_ms ? `${(investigation.llm_latency_ms/1000).toFixed(1)} s` : "—"}</span>
                <span className="text-slate-500">Validation</span><span>{investigation.validation_latency_ms ?? "—"} ms</span>
                <span className="text-slate-500 font-medium">Total</span><span className="font-medium">{investigation.total_latency_ms ? `${(investigation.total_latency_ms/1000).toFixed(1)} s` : "—"}</span>
              </div>
              <div className="pt-2 text-xs text-slate-500">
                LLM Model: <code className="font-mono">{investigation.llm_model ?? "—"}</code> <br />
                Embedding Model: <code className="font-mono">{investigation.embedding_model ?? "—"}</code>
              </div>
              <details className="pt-2">
                <summary className="cursor-pointer text-xs text-slate-500">Technical Details</summary>
                <pre className="mt-2 overflow-x-auto rounded bg-slate-50 p-2 font-mono text-xs text-slate-600">
                  {JSON.stringify({ llm_model: investigation.llm_model, embedding_model: investigation.embedding_model, latencies: { slack: investigation.slack_latency_ms, sentry: investigation.sentry_latency_ms, retrieval: investigation.retrieval_latency_ms, llm: investigation.llm_latency_ms, validation: investigation.validation_latency_ms, total: investigation.total_latency_ms } }, null, 2)}
                </pre>
              </details>
            </div>
          </Section>
        </>
      ) : (
        <Section title="Investigation">
          <p className="text-sm text-slate-500">No completed investigation for this incident.</p>
        </Section>
      )}

      <Link href="/incidents" className="inline-block text-sm text-blue-600 hover:underline">
        ← Back to incidents
      </Link>
    </div>
  );
}
