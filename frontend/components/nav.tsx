import Link from "next/link";

const LINKS = [
  { href: "/", label: "Dashboard" },
  { href: "/incidents", label: "Incidents" },
  { href: "/repositories", label: "Repositories" },
  { href: "/settings", label: "Settings" },
];

export function Nav() {
  return (
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3">
        <Link href="/" className="flex items-center gap-2 font-semibold text-slate-900">
          <span aria-hidden>🤖</span> RCA Bot
        </Link>
        <nav className="flex gap-1">
          {LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="rounded-md px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100 hover:text-slate-900"
            >
              {link.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
