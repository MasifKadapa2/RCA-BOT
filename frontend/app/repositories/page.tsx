"use client";

import { useState } from "react";
import { api, formatDateTime } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";

export default function RepositoriesPage() {
  const [repos, setRepos] = useState<Awaited<ReturnType<typeof api.repositories>>>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  async function refresh() {
    const data = await api.repositories();
    setRepos(data);
    setLoaded(true);
  }

  if (!loaded) {
    void refresh();
  }

  async function triggerIndex() {
    setBusy(true);
    setMessage(null);
    const result = await api.triggerIndex();
    setMessage(result.ok ? "Indexing completed." : `Indexing failed: ${result.detail}`);
    setBusy(false);
    await refresh();
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Repositories</h1>
        <Button onClick={triggerIndex} disabled={busy}>
          {busy ? "Indexing…" : "Run indexing"}
        </Button>
      </div>

      {message ? (
        <p className="rounded-md border border-slate-200 bg-slate-50 px-4 py-2 text-sm text-slate-600">
          {message}
        </p>
      ) : null}

      <Card>
        <CardHeader
          title="Configured repositories"
          subtitle="V1 supports a single repository, configured via REPOSITORY_PATH on the server."
        />
        <CardBody className="px-0 py-0">
          {!repos ? (
            <p className="px-6 py-4 text-sm text-slate-500">Backend unreachable.</p>
          ) : repos.length === 0 ? (
            <p className="px-6 py-4 text-sm text-slate-500">
              No repositories indexed yet. Configure REPOSITORY_PATH and run indexing.
            </p>
          ) : (
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="px-6 py-3">Name</th>
                  <th className="px-6 py-3">Path</th>
                  <th className="px-6 py-3">Indexed files</th>
                  <th className="px-6 py-3">Last update</th>
                </tr>
              </thead>
              <tbody>
                {repos.map((repo) => (
                  <tr key={repo.id} className="border-t border-slate-100">
                    <td className="px-6 py-3 font-medium">{repo.name}</td>
                    <td className="px-6 py-3 font-mono text-xs">{repo.path}</td>
                    <td className="px-6 py-3">{repo.indexed_files}</td>
                    <td className="px-6 py-3 text-slate-500">{formatDateTime(repo.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardBody>
      </Card>

      <p className="text-xs text-slate-400">
        Indexing walks the configured repository, skips ignored/binary/secret files, chunks sources
        semantically (tree-sitter) and stores embeddings locally in ChromaDB. Re-run after code
        changes — only changed files are re-embedded.
      </p>
    </div>
  );
}
