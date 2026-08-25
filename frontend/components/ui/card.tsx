import type { ReactNode } from "react";

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-slate-200 bg-white shadow-sm ${className ?? ""}`}>
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  subtitle,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
}) {
  return (
    <div className="border-b border-slate-100 px-6 py-4">
      <h2 className="text-base font-semibold text-slate-900">{title}</h2>
      {subtitle ? <p className="mt-1 text-sm text-slate-500">{subtitle}</p> : null}
    </div>
  );
}

export function CardBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={`px-6 py-4 ${className ?? ""}`}>{children}</div>;
}
