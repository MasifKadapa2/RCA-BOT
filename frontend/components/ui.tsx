import type { ReactNode } from "react";

export function Card({
  title,
  children,
  className = "",
}: {
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-lg border border-zinc-800 bg-zinc-950/60 p-5 ${className}`}
    >
      {title ? (
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-400">
          {title}
        </h2>
      ) : null}
      {children}
    </section>
  );
}

const statusStyles: Record<string, string> = {
  COMPLETED: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  FAILED: "bg-red-500/15 text-red-400 border-red-500/30",
  INVESTIGATING: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  PENDING: "bg-zinc-500/15 text-zinc-400 border-zinc-500/30",
};

export function StatusBadge({ status }: { status: string }) {
  const style = statusStyles[status] ?? statusStyles.PENDING;
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${style}`}
    >
      {status}
    </span>
  );
}

export function ConfidenceBar({ confidence }: { confidence: number | null }) {
  if (confidence === null || confidence === undefined) {
    return <span className="text-sm text-zinc-500">—</span>;
  }
  const color =
    confidence >= 70
      ? "bg-emerald-500"
      : confidence >= 40
        ? "bg-amber-500"
        : "bg-red-500";
  return (
    <div className="flex min-w-28 items-center gap-2">
      <div className="h-1.5 w-full overflow-hidden rounded bg-zinc-800">
        <div className={`h-full ${color}`} style={{ width: `${confidence}%` }} />
      </div>
      <span className="text-xs tabular-nums text-zinc-400">{confidence}%</span>
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-dashed border-zinc-800 p-8 text-center text-sm text-zinc-500">
      {message}
    </div>
  );
}

export function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  const diff = Date.now() - then;
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}
