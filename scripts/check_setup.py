#!/usr/bin/env python3
"""Verify that all external components of RCA Bot are configured correctly.

Usage:
    python scripts/check_setup.py
    python scripts/check_setup.py --json

Checks (never prints secrets):
  1. .env present / required variables set
  2. Slack authentication (auth.test)
  3. Slack channel access (conversations.info + membership hint)
  4. Slack Socket Mode token format (xapp-)
  5. Sentry authentication + organization access
  6. Ollama reachable and configured model pulled
  7. Configured repository exists and is indexable
  8. Chroma / SQLite paths writable
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

OK = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m!\033[0m"


def redact(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return value[:4] + "***" + value[-4:]


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify RCA Bot configuration")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    args = parser.parse_args()

    os.chdir(ROOT)
    failures = 0
    warnings = 0
    report: dict = {"checks": []}

    def add_check(name: str, ok: bool, detail: str = "", hint: str = ""):
        nonlocal failures, warnings
        status = "ok" if ok else "fail"
        if not ok:
            # distinguish hard fail vs warn based on hint prefix
            if hint.startswith("WARN:"):
                status = "warn"
                warnings += 1
            else:
                failures += 1
        report["checks"].append({"name": name, "status": status, "detail": detail, "hint": hint})
        if not args.json:
            icon = OK if ok else (WARN if status == "warn" else FAIL)
            line = f"{icon} {name}"
            if detail:
                line += f" — {detail}"
            if hint and not ok:
                line += f"\n  → {hint}"
            print(line)
        return ok

    if not args.json:
        print("RCA Bot — Configuration Check\n" + "=" * 32 + "\n")

    # --- .env presence -------------------------------------------------------
    env_exists = (ROOT / ".env").exists()
    add_check(".env file", env_exists, "found" if env_exists else "missing", "copy .env.example to .env and fill values" if not env_exists else "")

    from app.core.config import get_settings

    # clear cache so latest .env is read (important when re-running)
    get_settings.cache_clear()
    settings = get_settings()

    if not args.json:
        print("\nConfiguration variables:")
    add_check("SLACK_BOT_TOKEN", bool(settings.slack_bot_token), "set" if settings.slack_bot_token else "empty", "xoxb-... from Slack OAuth & Permissions" if not settings.slack_bot_token else "")
    add_check("SLACK_APP_TOKEN", bool(settings.slack_app_token), "set" if settings.slack_app_token else "empty", "xapp-... with connections:write scope" if not settings.slack_app_token else "")
    # light format validation without leaking
    if settings.slack_bot_token and not settings.slack_bot_token.startswith("xoxb-"):
        add_check("SLACK_BOT_TOKEN format", False, "should start with xoxb-", "check you copied Bot User OAuth Token, not app-level token")
    if settings.slack_app_token and not settings.slack_app_token.startswith("xapp-"):
        add_check("SLACK_APP_TOKEN format", False, "should start with xapp-", "check you copied App-Level Token with connections:write")

    add_check("SLACK_ALERT_CHANNEL_ID", bool(settings.slack_alert_channel_id), settings.slack_alert_channel_id if settings.slack_alert_channel_id else "empty", "Channel ID (C...) where Sentry alerts arrive — right-click channel → View details" if not settings.slack_alert_channel_id else "")
    add_check("SENTRY_AUTH_TOKEN", bool(settings.sentry_auth_token), "set" if settings.sentry_auth_token else "empty", "Sentry → Settings → Auth Tokens → Create (event:read, project:read)" if not settings.sentry_auth_token else "")
    add_check("SENTRY_ORG", bool(settings.sentry_org), settings.sentry_org if settings.sentry_org else "empty", "Sentry organization slug (e.g. my-org)" if not settings.sentry_org else "")
    add_check("SENTRY_BASE_URL", True, settings.sentry_base_url, "")
    add_check("OLLAMA_BASE_URL", True, settings.ollama_base_url, "")
    add_check("OLLAMA_MODEL", bool(settings.ollama_model), settings.ollama_model if settings.ollama_model else "empty", "e.g. qwen2.5-coder:3b or nemotron-mini:latest — must be pulled via 'ollama pull <model>'" if not settings.ollama_model else "")
    add_check("REPOSITORY_PATH", bool(settings.repository_path), settings.repository_path if settings.repository_path else "empty", "Absolute path to the single repo V1 investigates" if not settings.repository_path else "")
    add_check("DATABASE_URL", True, settings.database_url, "")
    add_check("CHROMA_PATH", True, settings.chroma_path, "")

    # --- Slack authentication + channel access --------------------------------
    if not args.json:
        print("\nSlack:")
    if settings.slack_bot_token:
        try:
            from slack_sdk import WebClient
            from slack_sdk.errors import SlackApiError

            client = WebClient(token=settings.slack_bot_token)
            auth = client.auth_test()
            bot_user = auth.get("user") or auth.get("user_id") or "unknown"
            team = auth.get("team") or auth.get("team_id") or ""
            add_check("Slack auth.test", True, f"authenticated as {bot_user} (team {team})")
            # Required scopes hint — auth.test scopes not directly returned, but we can infer via missing scope errors later
            if settings.slack_alert_channel_id:
                try:
                    info = client.conversations_info(channel=settings.slack_alert_channel_id)
                    ch = info.get("channel", {})
                    ch_name = ch.get("name") or ch.get("id") or settings.slack_alert_channel_id
                    is_private = ch.get("is_private")
                    is_member = ch.get("is_member")
                    detail = f"#{ch_name} ({settings.slack_alert_channel_id})"
                    if is_member is False:
                        add_check("Slack channel access", False, detail, f"WARN: Bot not in channel — run /invite @rca-bot in #{ch_name} then re-check")
                    else:
                        add_check("Slack channel access", True, detail)
                    # Additional: can we read history? Try conversations.history with limit 1 (needs channels:history)
                    try:
                        client.conversations_history(channel=settings.slack_alert_channel_id, limit=1)
                        add_check("Slack channel history scope", True, "channels:history/groups:history OK")
                    except SlackApiError as e:
                        err = e.response.get("error", str(e)) if hasattr(e, "response") else str(e)
                        if err in ("missing_scope", "not_in_channel"):
                            add_check("Slack channel history scope", False, err, "Add scopes channels:history, groups:history, im:history and reinstall app")
                        else:
                            add_check("Slack channel history scope", False, err, "Check bot scopes and channel membership")
                except Exception as exc:
                    err = getattr(exc, "response", {}).get("error", type(exc).__name__) if hasattr(exc, "response") else type(exc).__name__
                    # SlackApiError response may be dict-like
                    try:
                        err = exc.response["error"]  # type: ignore
                    except Exception:
                        pass
                    add_check("Slack channel access", False, str(exc)[:120], f"Channel {settings.slack_alert_channel_id} not accessible — check ID, bot invited, and scopes")
            else:
                add_check("Slack channel access", False, "SLACK_ALERT_CHANNEL_ID not set", "Set to channel ID where Sentry→Slack alerts arrive")
        except Exception as exc:
            add_check("Slack auth.test", False, f"{type(exc).__name__}: {str(exc)[:120]}", "Check SLACK_BOT_TOKEN, reinstall app if revoked")
    else:
        add_check("Slack auth.test", False, "skipped", "WARN: SLACK_BOT_TOKEN not set")

    # Slack Socket Mode token presence check (cannot fully validate without wss)
    if settings.slack_app_token:
        is_xapp = settings.slack_app_token.startswith("xapp-")
        add_check("Slack Socket Mode token", is_xapp, "xapp-... present" if is_xapp else "invalid prefix", "App-Level Token needs connections:write scope, enable Socket Mode" if not is_xapp else "")
    else:
        add_check("Slack Socket Mode token", False, "not set", "WARN: SLACK_APP_TOKEN required for listener (python -m app.slack)")

    # --- Sentry authentication + org access ----------------------------------
    if not args.json:
        print("\nSentry:")
    if settings.sentry_auth_token and settings.sentry_org:
        try:
            import httpx

            headers = {"Authorization": f"Bearer {settings.sentry_auth_token}"}
            base = settings.sentry_base_url.rstrip("/")
            # 1) List orgs — verifies token validity
            resp = httpx.get(f"{base}/organizations/", headers=headers, timeout=10)
            if resp.status_code == 401:
                add_check("Sentry authentication", False, "HTTP 401", "Token invalid or revoked — recreate at Sentry → Settings → Auth Tokens")
            elif resp.status_code == 403:
                add_check("Sentry authentication", False, "HTTP 403", "Token missing scopes — needs project:read, event:read, org:read")
            elif resp.status_code != 200:
                add_check("Sentry authentication", False, f"HTTP {resp.status_code}", f"Check SENTRY_BASE_URL ({base}) and token")
            else:
                try:
                    orgs = resp.json()
                    slugs = [o.get("slug") for o in orgs if isinstance(o, dict)]
                    if settings.sentry_org in slugs:
                        add_check("Sentry authentication", True, f"token valid, {len(slugs)} org(s) accessible")
                    else:
                        add_check("Sentry authentication", True, f"token valid but org '{settings.sentry_org}' not in {slugs[:3]}")
                        add_check("Sentry organization access", False, f"org '{settings.sentry_org}' not found in token orgs {slugs[:5]}", "Check SENTRY_ORG slug exactly (case-sensitive)")
                        # Still try direct org fetch for better error
                        direct = httpx.get(f"{base}/organizations/{settings.sentry_org}/", headers=headers, timeout=10)
                        if direct.status_code == 404:
                            add_check("Sentry organization direct fetch", False, "404", f"Org '{settings.sentry_org}' does not exist or token lacks org:read")
                        elif direct.status_code == 200:
                            add_check("Sentry organization direct fetch", True, f"org '{settings.sentry_org}' reachable")
                        else:
                            add_check("Sentry organization direct fetch", False, f"HTTP {direct.status_code}", "")
                    # 2) Org detail — more precise org access
                    if settings.sentry_org in slugs:
                        direct = httpx.get(f"{base}/organizations/{settings.sentry_org}/", headers=headers, timeout=10)
                        if direct.status_code == 200:
                            add_check("Sentry organization access", True, f"org '{settings.sentry_org}' accessible")
                        else:
                            add_check("Sentry organization access", False, f"HTTP {direct.status_code}", "Check org slug and token scopes")
                        # 3) Try projects list to verify project:read
                        proj_resp = httpx.get(f"{base}/organizations/{settings.sentry_org}/projects/", headers=headers, timeout=10)
                        if proj_resp.status_code == 200:
                            try:
                                projs = proj_resp.json()
                                add_check("Sentry project read", True, f"{len(projs)} project(s)")
                            except Exception:
                                add_check("Sentry project read", True, "projects endpoint OK")
                        elif proj_resp.status_code in (401, 403):
                            add_check("Sentry project read", False, f"HTTP {proj_resp.status_code}", "Token needs project:read scope")
                        else:
                            add_check("Sentry project read", False, f"HTTP {proj_resp.status_code}", "")
                except Exception as e:
                    add_check("Sentry authentication", False, f"JSON parse failed: {e}", "")
        except Exception as exc:
            add_check("Sentry authentication", False, f"{type(exc).__name__}: {str(exc)[:120]}", f"Check SENTRY_BASE_URL ({settings.sentry_base_url}) reachable")
    else:
        add_check("Sentry authentication", False, "skipped", "WARN: SENTRY_AUTH_TOKEN/SENTRY_ORG not set")
        add_check("Sentry organization access", False, "skipped", "WARN: not set")

    # --- Ollama -----------------------------------------------------------------
    if not args.json:
        print("\nOllama:")
    try:
        import httpx

        base = settings.ollama_base_url.rstrip("/")
        resp = httpx.get(f"{base}/api/tags", timeout=5)
        if resp.status_code != 200:
            add_check("Ollama reachable", False, f"HTTP {resp.status_code} at {base}", "Is 'ollama serve' running? Check OLLAMA_BASE_URL")
        else:
            data = resp.json()
            models = [m.get("name") for m in data.get("models", []) if isinstance(m, dict)]
            add_check("Ollama reachable", True, f"at {base} — {len(models)} model(s) installed")
            wanted = settings.ollama_model
            if wanted:
                # allow prefix match (e.g. gemma3:1b matches gemma3)
                pulled = any(m == wanted or m.split(":")[0] == wanted.split(":")[0] for m in models)
                if pulled:
                    add_check("Ollama model", True, f"'{wanted}' available")
                    # quick smoke: can we /api/show the model?
                    try:
                        show = httpx.post(f"{base}/api/show", json={"name": wanted}, timeout=5)
                        if show.status_code == 200:
                            add_check("Ollama model show", True, f"details for '{wanted}' OK")
                    except Exception:
                        pass
                else:
                    add_check("Ollama model", False, f"'{wanted}' not pulled", f"Run: ollama pull {wanted}  — installed: {models or 'none'}")
            else:
                add_check("Ollama model", False, "OLLAMA_MODEL not set", "Set to e.g. qwen2.5-coder:3b or nemotron-mini:latest (installed: {})".format(models[:2]))
    except Exception as exc:
        add_check("Ollama reachable", False, f"{type(exc).__name__}", f"Is 'ollama serve' running at {settings.ollama_base_url}? Try: curl {settings.ollama_base_url}/api/tags")
        if settings.ollama_model:
            add_check("Ollama model", False, "skipped (ollama unreachable)", "")

    # --- Repository + storage ---------------------------------------------------
    if not args.json:
        print("\nRepository & Storage:")
    if settings.repository_path:
        path = Path(settings.repository_path).expanduser()
        # expanduser then resolve anchoring handled in config, but check original
        if path.is_dir():
            add_check("Repository path", True, str(path))
            # count indexable files quickly
            try:
                from app.ingestion.filters import FileFilter, iter_repository_files

                flt = FileFilter(max_file_size_bytes=settings.max_file_size_bytes)
                files = iter_repository_files(path.resolve(), flt)
                add_check("Repository indexable files", len(files) > 0, f"{len(files)} files" if files else "no indexable files found", "Check language support (Ruby/Python/JS/TS/Go/Java) and ignore patterns" if not files else "")
            except Exception as e:
                add_check("Repository indexable files", False, str(e)[:80], "")
            # check not indexing secrets
            env_in_repo = (path / ".env").exists()
            if env_in_repo:
                add_check("Repository secrets guard", False, ".env found inside repo", "WARN: .env will be ignored by indexer but should not be committed")
            else:
                add_check("Repository secrets guard", True, "no .env in repo root")
        else:
            add_check("Repository path", False, str(path), "Path does not exist or not a directory — set REPOSITORY_PATH to absolute path")
    else:
        add_check("Repository path", False, "REPOSITORY_PATH not set", "WARN: Set to repo to investigate, e.g. /Users/you/projects/my-app")

    # Chroma / DB writability
    for label, raw_path in [("Chroma path", settings.chroma_path), ("Database", settings.database_url)]:
        try:
            if raw_path.startswith("sqlite:///"):
                file_part = raw_path[len("sqlite:///"):]
                p = Path(file_part).expanduser()
                # anchor relative to PROJECT_ROOT like config does
                if not p.is_absolute():
                    from app.core.config import PROJECT_ROOT

                    p = (PROJECT_ROOT / p).resolve()
                parent = p.parent
                parent.mkdir(parents=True, exist_ok=True)
                # try touch
                add_check(f"{label} writable", True, str(parent))
            else:
                p = Path(raw_path).expanduser()
                if not p.is_absolute():
                    from app.core.config import PROJECT_ROOT

                    p = (PROJECT_ROOT / p).resolve()
                p.mkdir(parents=True, exist_ok=True)
                add_check(f"{label} writable", True, str(p))
        except Exception as e:
            add_check(f"{label} writable", False, str(e)[:80], "")

    # --- Summary ----------------------------------------------------------------
    if not args.json:
        print()
        total_fail = failures
        if total_fail:
            print(f"\033[91m{total_fail} check(s) failed.\033[0m")
            if warnings:
                print(f"\033[93m{warnings} warning(s).\033[0m")
            print("Fix above, then re-run: python scripts/check_setup.py")
            return 1
        if warnings:
            print(f"\033[93m{warnings} warning(s) — review above.\033[0m")
        print("\033[92mAll checks passed.\033[0m Ready for E2E: python -m app.ingestion.index_repository → uvicorn app.main:app → python -m app.slack")
        return 0
    else:
        report["summary"] = {"failures": failures, "warnings": warnings, "ok": failures == 0}
        print(json.dumps(report, indent=2))
        return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
