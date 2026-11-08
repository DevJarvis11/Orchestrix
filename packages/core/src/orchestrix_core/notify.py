"""Escalation notifications (FR-46): the in-app inbox is the escalations table; Slack is an
incoming webhook with a deep link. Without a webhook URL the Slack side is a no-op."""

from typing import Any

import httpx

from orchestrix_core.logging import get_logger

log = get_logger(__name__)


class Notifier:
    def __init__(self, http: httpx.AsyncClient, webhook_url: str | None, web_url: str) -> None:
        self._http = http
        self._webhook = webhook_url
        self._web = web_url.rstrip("/")

    def link(self, escalation_id: str) -> str:
        return f"{self._web}/escalations/{escalation_id}"

    async def escalation(
        self, escalation_id: str, payload: dict[str, Any], *, reminder: bool = False
    ) -> None:
        level = str(payload.get("level", ""))
        prefix = "Reminder: " if reminder else ""
        verb = "FYI" if level == "notify" else "Approval needed"
        text = f"{prefix}{verb}: {payload.get('title', 'escalation')} <{self.link(escalation_id)}|open>"
        await self.send(text)

    async def send(self, text: str) -> None:
        if not self._webhook:
            return
        try:
            response = await self._http.post(self._webhook, json={"text": text}, timeout=5)
            response.raise_for_status()
        except httpx.HTTPError as exc:  # a notification failure never blocks a run
            log.warning("slack notification failed", error=str(exc))
