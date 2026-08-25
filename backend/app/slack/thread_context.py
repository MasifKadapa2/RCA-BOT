"""Thread context retrieval service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ThreadContext:
    channel_id: str
    thread_ts: str
    root_text: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def joined_text(self) -> str:
        return "\n".join(str(m.get("text") or "") for m in self.messages)


class ThreadContextService:
    """Resolves the root of a Slack thread and retrieves all its messages."""

    def __init__(self, gateway) -> None:
        self._gateway = gateway

    def resolve_thread_ts(self, event_thread_ts: str | None, message_ts: str) -> str:
        """Mentions inside a thread use the parent ts; root mentions use own ts."""
        if event_thread_ts and event_thread_ts != message_ts:
            return event_thread_ts
        return message_ts

    def fetch(self, channel_id: str, thread_ts: str, triggering_event: dict | None = None) -> ThreadContext:
        messages = self._gateway.fetch_thread_messages(channel_id, thread_ts)
        if not messages and triggering_event:
            # conversations.replies should always return at least the root;
            # fall back to the triggering event to stay operational.
            messages = [triggering_event]
        root = next((m for m in messages if m.get("thread_ts") in (None, m.get("ts"))), None)
        root_text = str((root or (messages[0] if messages else {})).get("text") or "")
        return ThreadContext(
            channel_id=channel_id,
            thread_ts=thread_ts,
            root_text=root_text,
            messages=messages,
        )
