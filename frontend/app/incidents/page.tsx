import Link from "next/link";
import { api, formatDateTime } from "@/lib/api";
import { ConfidenceBadge, StatusBadge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";

export const dynamic = "force-dynamic";

export default async function IncidentsPage() {
  const incidents = await api.incidents(100);

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Incidents</h1>
      <Card>
        <CardHeader title="All investigations" subtitle="Newest first." />
        <CardBody className="px-0 py-0">
          {!incidents || incidents.length === 0 ? (
            <p className="px-6 py-4 text-sm text-slate-500">No incidents recorded.</p>
          ) : (
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="px-6 py-3">ID</th>
                  <th className="px-6 py-3">Sentry issue</th>
                  <th className="px-6 py-3">Title</th>
                  <th className="px-6 py-3">Status</th>
                  <th className="px-6 py-3">Confidence</th>
                  <th className="px-6 py-3">Created</th>
                </tr>
              </thead>
              <tbody>
                {(incidents ?? []).map((incident) => (
                  <tr key={incident.id} className="border-t border-slate-100 hover:bg-slate-50">
                    <td className="px-6 py-3">
                      <Link href={`/incidents/${incident.id}`} className="font-medium text-blue-600 hover:underline">
                        #{incident.id}
                      </Link>
                    </td>
                    <td className="px-6 py-3 font-mono text-xs">{incident.sentry_issue_id ?? "—"}</td>
                    <td className="max-w-sm truncate px-6 py-3">{incident.title ?? "—"}</td>
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
