"""Human notification backends for the autonomous layer's escalation decisions."""

from __future__ import annotations

import asyncio
import logging
import smtplib
from abc import ABC, abstractmethod
from email.mime.text import MIMEText
from typing import Any

log = logging.getLogger(__name__)


class NotificationBackend(ABC):
    @abstractmethod
    async def send(self, subject: str, body: str, context: dict[str, Any]) -> None: ...


class EmailBackend(NotificationBackend):
    def __init__(self, smtp_host: str, smtp_port: int, sender: str,
                 recipients: list[str], username: str = "", password: str = "") -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.sender = sender
        self.recipients = recipients
        self.username = username
        self.password = password

    async def send(self, subject: str, body: str, context: dict[str, Any]) -> None:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)

        def _send() -> None:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as s:
                if self.username:
                    s.login(self.username, self.password)
                s.sendmail(self.sender, self.recipients, msg.as_string())

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _send)
            log.info("Email notification sent to %s", self.recipients)
        except Exception as e:
            log.error("Email notification failed: %s", e)


class SlackBackend(NotificationBackend):
    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    async def send(self, subject: str, body: str, context: dict[str, Any]) -> None:
        try:
            import httpx
            payload = {"text": f"*{subject}*\n{body}"}
            async with httpx.AsyncClient() as client:
                resp = await client.post(self.webhook_url, json=payload, timeout=10)
                resp.raise_for_status()
            log.info("Slack notification sent")
        except Exception as e:
            log.error("Slack notification failed: %s", e)


class WebhookBackend(NotificationBackend):
    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.headers = headers or {}

    async def send(self, subject: str, body: str, context: dict[str, Any]) -> None:
        try:
            import httpx
            payload = {"subject": subject, "body": body, "context": context}
            async with httpx.AsyncClient() as client:
                resp = await client.post(self.url, json=payload,
                                         headers=self.headers, timeout=10)
                resp.raise_for_status()
            # Log only the host: a webhook URL often embeds a secret token
            # (e.g. Slack's hooks.slack.com/services/T…/B…/xxxx).
            from urllib.parse import urlsplit
            log.info("Webhook notification sent to %s", urlsplit(self.url).netloc)
        except Exception as e:
            log.error("Webhook notification failed: %s", e)


class HumanNotifier:
    """Aggregates multiple backends and dispatches escalation notifications."""

    def __init__(self, backends: list[NotificationBackend]) -> None:
        self._backends = backends

    def add_backend(self, backend: NotificationBackend) -> None:
        self._backends.append(backend)

    async def send(self, reason: str, context: dict[str, Any] | None = None) -> None:
        context = context or {}
        subject = "[Dyon] Human intervention required"
        lines = [f"Reason: {reason}"]
        for k, v in context.items():
            lines.append(f"  {k}: {v}")
        body = "\n".join(lines)

        await asyncio.gather(
            *[b.send(subject, body, context) for b in self._backends],
            return_exceptions=True,
        )
