import Link from "next/link";
import { api, formatDateTime } from "@/lib/api";
import { Badge, ConfidenceBadge, StatusBadge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";

export const dynamic = "force-dynamic";

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <Card>
      <CardBody>
        <div className="text-2xl font-semibold text-slate-900">{value}</div>
        <div className="mt-1 text-sm text-slate-500">{label}</div>
      </CardBody>
    </Card>
  );
}

export default async function DashboardHome() {
  const [stats, incidents] = await Promise.all([api.stats(), api.incidents(5)]);

  if (!stats) {
    return (
      <Card>
        <CardBody>
          <p className="text-sm text-slate-500">
            Backend unreachable at <code className="font-mono">http://localhost:8000</code>. Start
            it with <code className="font-mono">uvicorn app.main:app</code> (see README).
          </p>
        </CardBody>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Dashboard</h1>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Total investigations" value={stats.total_investigations} />
        <Stat label="Completed" value={stats.completed_investigations} />
        <Stat label="Failed" value={stats.failed_investigations} />
        <Stat label="Average confidence" value={`${stats.average_confidence}%`} />
      </div>

      <Card>
        <CardHeader
          title="Recent incidents"
          subtitle="Triggered by @rca-bot investigate in the Sentry alerts channel."
        />
        <CardBody className="px-0 py-0">
          {!incidents || incidents.length === 0 ? (
            <p className="px-6 py-4 text-sm text-slate-500">
              No incidents yet. Mention <code className="font-mono">@rca-bot investigate</code> in a
              Sentry alert thread.
            </p>
          ) : (
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="px-6 py-3">Incident</th>
                  <th className="px-6 py-3">Sentry issue</th>
                  <th className="px-6 py-3">Status</th>
                  <th className="px-6 py-3">Confidence</th>
                  <th className="px-6 py-3">Created</th>
                </tr>
              </thead>
              <tbody>
                {incidents.map((incident) => (
                  <tr key={incident.id} className="border-t border-slate-100 hover:bg-slate-50">
                    <td className="px-6 py-3">
                      <Link href={`/incidents/${incident.id}`} className="font-medium text-blue-600 hover:underline">
                        #{incident.id}
                      </Link>
                    </td>
                    <td className="max-w-xs truncate px-6 py-3">{incident.title ?? incident.sentry_issue_id ?? "—"}</td>
                    <td className="px-6 py-3"><StatusBadge status={incident.status} /></td>
                    <td className="px-6 py-3"><ConfidenceBadge confidence={incident.confidence} /></td>
                    <td className="px-6 py-3 text-slate-500">{formatDateTime(incident.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
