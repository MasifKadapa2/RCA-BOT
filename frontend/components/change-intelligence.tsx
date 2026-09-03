import { Card, CardBody, CardHeader } from "@/components/ui/card";
import type { RegressionTrace } from "@/lib/api";

const STATUS_STYLES: Record<string, string> = {
  LIKELY_REGRESSION: "bg-emerald-50 text-emerald-700 border-emerald-200",
  POSSIBLE_REGRESSION: "bg-amber-50 text-amber-700 border-amber-200",
  NO_CONFIDENT_REGRESSION: "bg-slate-100 text-slate-600 border-slate-200",
  INSUFFICIENT_EVIDENCE: "bg-red-50 text-red-700 border-red-200",
};

const SIGNAL_LABELS: Record<string, string> = {
  stack_file_match: "Stack trace file match",
  retrieved_file_match: "Modified retrieved code",
  symbol_match: "Changed the affected symbol",
  line_proximity: "Changed lines near failing location",
  temporal_proximity: "Commit before first observed error",
  diff_relevance: "Diff mentions exception terms",
  modified_files: "Modified existing files",
};

export function ChangeIntelligenceCard({
  regression,
}: {
  regression: RegressionTrace | null;
}) {
  if (!regression || !regression.status) {
    return (
      <Card>
        <CardHeader title="Change Intelligence" />
        <CardBody>
          <p className="text-sm text-slate-500">
            Change Intelligence unavailable for this historical investigation.
          </p>
        </CardBody>
      </Card>
    );
  }

  const style = STATUS_STYLES[regression.status] ?? STATUS_STYLES.NO_CONFIDENT_REGRESSION;
  const likely = regression.candidates?.find(
    (c) => regression.likely_commit && c.short_sha.startsWith(regression.likely_commit.slice(0, 7))
  );

  return (
    <Card>
      <CardHeader title="Change Intelligence" subtitle="Why did this change cause this incident?" />
      <CardBody className="space-y-4 text-sm">
        <div className="flex items-center gap-3">
          <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium border ${style}`}>
            {regression.status}
          </span>
          {regression.confidence !== null ? (
            <span className="text-lg font-semibold">{regression.confidence}%</span>
          ) : null}
        </div>

        {likely ? (
          <div>
            <h4 className="font-medium text-slate-900">Likely Regression</h4>
            <p className="font-mono text-xs">
              <code>{likely.short_sha}</code> — “{likely.message}”
            </p>
            {likely.author ? <p className="text-xs text-slate-500">Author: {likely.author}</p> : null}
            {likely.committed_at ? (
              <p className="text-xs text-slate-500">Committed: {new Date(likely.committed_at).toLocaleString()}</p>
            ) : null}
            {likely.signals ? (
              <ul className="mt-2 space-y-0.5">
                {Object.keys(likely.signals).map((key) => (
                  <li key={key} className="text-xs text-emerald-700">✓ {SIGNAL_LABELS[key] ?? key}</li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}

        {regression.causal_explanation ? (
          <div>
            <h4 className="font-medium text-slate-900">Causal explanation</h4>
            <p className="mt-1 text-slate-700">{regression.causal_explanation}</p>
          </div>
        ) : null}

        {regression.evidence_for?.length ? (
          <div>
            <h4 className="font-medium text-slate-900">Git evidence</h4>
            <ul className="mt-1 space-y-1">
              {regression.evidence_for.map((ref, i) => (
                <li key={i} className="font-mono text-xs">
                  <code>{ref.commit_sha.slice(0, 8)}</code> {ref.file_path}
                  {ref.start_line ? `:${ref.start_line}${ref.end_line ? `-${ref.end_line}` : ""}` : ""}
                  <span className="ml-2 font-sans text-slate-500">{ref.description}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {regression.candidates?.length ? (
          <details>
            <summary className="cursor-pointer text-xs text-slate-500">
              Candidate commits ({regression.candidates.length})
            </summary>
            <ol className="mt-2 space-y-2">
              {regression.candidates.map((cand) => (
                <li key={cand.commit_sha} className="rounded border border-slate-100 p-2">
                  <div className="flex justify-between font-mono text-xs">
                    <span><code>{cand.short_sha}</code> “{cand.message}”</span>
                    <span className="font-semibold">{cand.score}</span>
                  </div>
                  {cand.relevance_reason ? (
                    <p className="mt-1 text-[11px] text-slate-500">{cand.relevance_reason}</p>
                  ) : null}
                  {cand.changed_files?.length ? (
                    <p className="mt-1 truncate font-mono text-[11px] text-slate-400">
                      {cand.changed_files.join(", ")}
                    </p>
                  ) : null}
                </li>
              ))}
            </ol>
          </details>
        ) : null}

        {regression.unknowns?.length ? (
          <div>
            <h4 className="font-medium text-slate-900">Unknowns</h4>
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-slate-600">
              {regression.unknowns.map((u, i) => (<li key={i}>{u}</li>))}
            </ul>
            <p className="mt-2 text-[11px] text-slate-400">
              ⚠️ Deployment timing is unknown from Git metadata alone.
            </p>
          </div>
        ) : null}

        {regression.recommended_action ? (
          <div>
            <h4 className="font-medium text-slate-900">Suggested action</h4>
            <p className="mt-1 whitespace-pre-wrap text-slate-700">{regression.recommended_action}</p>
          </div>
        ) : null}

        {regression.regression_model ? (
          <p className="text-[11px] text-slate-400">Regression model: <code className="font-mono">{regression.regression_model}</code></p>
        ) : null}
      </CardBody>
    </Card>
  );
}

export function TimelineCard({ entries }: { entries: Array<{ time?: string | null; label: string; detail?: string | null }> }) {
  if (!entries?.length) return null;
  return (
    <Card>
      <CardHeader title="Timeline" subtitle="Commits are shown as commits — deployment timing is not inferred." />
      <CardBody>
        <ol className="relative space-y-3 border-l border-slate-200 pl-4">
          {entries.map((entry, i) => (
            <li key={i}>
              <span className="absolute -left-1.5 mt-1 h-3 w-3 rounded-full border-2 border-white bg-slate-300" />
              <p className="text-sm font-medium text-slate-900">{entry.label}</p>
              {entry.time ? (
                <p className="text-xs text-slate-500">{new Date(entry.time).toLocaleString()}</p>
              ) : null}
              {entry.detail ? <p className="text-xs text-slate-400">{entry.detail}</p> : null}
            </li>
          ))}
        </ol>
      </CardBody>
    </Card>
  );
}
