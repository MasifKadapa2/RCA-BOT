import type { Metadata } from "next";
import "./globals.css";
import { Nav } from "@/components/nav";

export const metadata: Metadata = {
  title: "RCA Bot Dashboard",
  description: "Investigation history and debugging for RCA Bot",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Nav />
        <main className="mx-auto max-w-6xl px-6 py-8">{children}</main>
        <footer className="mx-auto max-w-6xl px-6 pb-8 text-xs text-slate-400">
          RCA Bot v1 — local-first Sentry investigations from Slack.
        </footer>
      </body>
    </html>
  );
}
