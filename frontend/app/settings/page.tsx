import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";

export const dynamic = "force-dynamic";

const COMPONENT_LABELS: Record<string, { label: string; hint: string }> = {
  slack_configured: {
    label: "Slack",
    hint: "SLACK_BOT_TOKEN + SLACK_APP_TOKEN set (Socket Mode listener uses them)",
  },
  sentry_configured: {
    label: "Sentry",
    hint: "SENTRY_AUTH_TOKEN + SENTRY_ORG set (read access required)",
  },
  ollama_configured: {
    label: "Ollama model",
    hint: "OLLAMA_MODEL set; the server must be reachable at OLLAMA_BASE_URL",
  },
  repository_configured: {
    label: "Repository",
    hint: "REPOSITORY_PATH points at the local repository to index and search",
  },
};

export default async function SettingsPage() {
  const health = await api.health();

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Settings</h1>

      <Card>
        <CardHeader
          title="Server configuration status"
          subtitle="Values come from the backend .env — this page never displays secrets, only whether components are configured."
        />
        <CardBody>
          {!health ? (
            <p className="text-sm text-slate-500">Backend unreachable.</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {Object.entries(health.components).map(([key, configured]) => {
                const meta = COMPONENT_LABELS[key] ?? { label: key, hint: "" };
                return (
                  <li key={key} className="flex items-center justify-between py-3">
                    <div>
                      <p className="text-sm font-medium text-slate-900">{meta.label}</p>
                      <p className="text-xs text-slate-500">{meta.hint}</p>
                    </div>
                    <Badge variant={configured ? "success" : "destructive"}>
                      {configured ? "Configured" : "Not configured"}
                    </Badge>
                  </li>
                );
              })}
            </ul>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="How RCA Bot works" />
        <CardBody className="text-sm leading-6 text-slate-600">
          <ol className="list-decimal space-y-1 pl-5">
            <li>Sentry posts alerts into your configured Slack alerts channel.</li>
            <li>Reply in that thread with <code className="font-mono">@rca-bot investigate</code>.</li>
            <li>The bot fetches the Sentry issue/event via the Sentry Web API.</li>
            <li>It searches your local repository index for relevant code.</li>
            <li>A local Ollama model drafts a grounded root cause analysis.</li>
            <li>The analysis is validated against real files/lines before posting.</li>
          </ol>
          <p className="mt-3 text-xs text-slate-400">
            V1 intentionally does not create PRs, modify code, run tests or mine git history.
          </p>
        </CardBody>
      </Card>
    </div>
  );
}
