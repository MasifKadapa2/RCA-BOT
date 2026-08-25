import { cn } from "@/lib/utils";

const VARIANTS: Record<string, string> = {
  default: "bg-slate-100 text-slate-700",
  secondary: "bg-slate-50 text-slate-500 border border-slate-200",
  success: "bg-emerald-50 text-emerald-700 border border-emerald-200",
  warning: "bg-amber-50 text-amber-700 border border-amber-200",
  destructive: "bg-red-50 text-red-700 border border-red-200",
  info: "bg-blue-50 text-blue-700 border border-blue-200",
};

export function Badge({
  variant = "default",
  children,
  className,
}: {
  variant?: keyof typeof VARIANTS;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
        VARIANTS[variant] ?? VARIANTS.default,
        className
      )}
    >
      {children}
    </span>
  );
}

const STATUS_VARIANT: Record<string, keyof typeof VARIANTS> = {
  COMPLETED: "success",
  FAILED: "destructive",
  INVESTIGATING: "info",
  PENDING: "warning",
};

export function StatusBadge({ status }: { status: string }) {
  return <Badge variant={STATUS_VARIANT[status] ?? "default"}>{status}</Badge>;
}

export function ConfidenceBadge({ confidence }: { confidence: number | null }) {
  if (confidence === null || confidence === undefined) return <span>—</span>;
  const variant: keyof typeof VARIANTS =
    confidence >= 75 ? "success" : confidence >= 50 ? "warning" : "destructive";
  return <Badge variant={variant}>{confidence}%</Badge>;
}
