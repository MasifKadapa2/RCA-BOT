"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function ReindexButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function triggerIndexing() {
    setBusy(true);
    setResult(null);
    setError(null);
    try {
      const res = await fetch("/api/repositories/index", { method: "POST" });
      if (res.ok) {
        const body = await res.json();
        const stats = body.stats ?? {};
        setResult(
          `Indexed ${stats.files_indexed ?? 0} file(s), ` +
            `${stats.chunks_created ?? 0} chunk(s) in ${stats.duration_seconds ?? "?"}s.`,
        );
        router.refresh();
      } else {
        const body = await res.json().catch(() => ({}));
        setError(body.detail ?? `Indexing failed (HTTP ${res.status})`);
      }
    } catch {
      setError("Could not reach the backend.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <button
        onClick={triggerIndexing}
        disabled={busy}
        className="rounded-md bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
      >
        {busy ? "Indexing…" : "Re-index repository"}
      </button>
      {result ? <p className="text-xs text-emerald-400">{result}</p> : null}
      {error ? <p className="text-xs text-red-400">{error}</p> : null}
    </div>
  );
}
