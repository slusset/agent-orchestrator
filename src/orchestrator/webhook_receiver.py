"""
Webhook Receiver: Generic inbound event handler for external systems.

Abstraction layer that receives webhooks from GitHub, JIRA, etc.
and normalizes them into internal WebhookEvent objects the PA can
act on. Source-specific parsing is handled by pluggable adapters.

Current state: stub. Accepts and logs events, dispatches to registered
handlers. GitHub-specific parsing comes in m4 (PR agent) and m5
(integrations).

Architecture:
    External System → POST /webhooks/{source} → WebhookReceiver
        → adapter.parse(headers, body) → WebhookEvent
        → handler(event)  (registered per event_type)
"""

from __future__ import annotations

import hashlib
import json as json_lib
import hmac
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------


class WebhookSource(str, Enum):
    """Known webhook sources."""

    GITHUB = "github"
    JIRA = "jira"
    GENERIC = "generic"


class WebhookEvent(BaseModel):
    """
    Normalized webhook event — source-agnostic.

    Adapters parse raw payloads into this format so the PA
    doesn't need to know about GitHub vs JIRA payload shapes.
    """

    source: WebhookSource
    event_type: str  # e.g., "pull_request.opened", "issue.updated"
    action: str = ""  # e.g., "opened", "closed", "merged"
    resource_id: str = ""  # e.g., PR number, issue key
    resource_url: str = ""  # Link to the resource
    sender: str = ""  # Who triggered the event
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    received_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Adapter protocol
# ---------------------------------------------------------------------------


class WebhookAdapter:
    """
    Base adapter for parsing source-specific webhook payloads.

    Subclass for each source (GitHub, JIRA, etc.). The stub
    GenericAdapter handles unknown sources by passing through.
    """

    source: WebhookSource = WebhookSource.GENERIC

    def parse(
        self, headers: dict[str, str], body: dict[str, Any]
    ) -> WebhookEvent:
        """Parse a raw webhook payload into a WebhookEvent."""
        return WebhookEvent(
            source=self.source,
            event_type=body.get("event_type", "unknown"),
            action=body.get("action", ""),
            resource_id=str(body.get("resource_id", "")),
            resource_url=body.get("resource_url", ""),
            sender=body.get("sender", ""),
            raw_payload=body,
        )

    def verify_signature(
        self, headers: dict[str, str], body: bytes, secret: str | None
    ) -> bool:
        """Verify webhook signature. Override per source."""
        return True  # No verification by default


class GitHubWebhookAdapter(WebhookAdapter):
    """
    GitHub webhook adapter — stub.

    Parses enough of the GitHub payload to produce a useful
    WebhookEvent. Full parsing (PR details, review state, etc.)
    comes in m4/m5.
    """

    source = WebhookSource.GITHUB

    def parse(
        self, headers: dict[str, str], body: dict[str, Any]
    ) -> WebhookEvent:
        event_header = headers.get("x-github-event", "unknown")
        action = body.get("action", "")
        event_type = f"{event_header}.{action}" if action else event_header

        # Extract resource info based on event type
        resource_id = ""
        resource_url = ""
        sender = ""

        if "pull_request" in body:
            pr = body["pull_request"]
            resource_id = str(pr.get("number", ""))
            resource_url = pr.get("html_url", "")
        elif "issue" in body:
            issue = body["issue"]
            resource_id = str(issue.get("number", ""))
            resource_url = issue.get("html_url", "")

        if "sender" in body:
            sender = body["sender"].get("login", "")

        return WebhookEvent(
            source=WebhookSource.GITHUB,
            event_type=event_type,
            action=action,
            resource_id=resource_id,
            resource_url=resource_url,
            sender=sender,
            raw_payload=body,
            metadata={
                "delivery_id": headers.get("x-github-delivery", ""),
                "repository": body.get("repository", {}).get("full_name", ""),
            },
        )

    def verify_signature(
        self, headers: dict[str, str], body: bytes, secret: str | None
    ) -> bool:
        """Verify GitHub HMAC-SHA256 signature."""
        if not secret:
            return True  # No secret configured, skip verification

        signature = headers.get("x-hub-signature-256", "")
        if not signature:
            return False

        expected = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected)


# ---------------------------------------------------------------------------
# Webhook receiver
# ---------------------------------------------------------------------------

# Type alias for event handlers
EventHandler = Callable[[WebhookEvent], Awaitable[None]]


class WebhookReceiver:
    """
    Receives webhooks from external systems and dispatches to handlers.

    The receiver:
    1. Looks up the adapter for the source
    2. Verifies the signature (if configured)
    3. Parses the payload into a WebhookEvent
    4. Dispatches to registered handlers

    Handlers are registered per event_type pattern. A handler for
    "pull_request" matches "pull_request.opened", "pull_request.closed", etc.
    A handler for "*" matches everything.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, WebhookAdapter] = {
            "github": GitHubWebhookAdapter(),
            "generic": WebhookAdapter(),
        }
        self._handlers: list[tuple[str, EventHandler]] = []
        self._secrets: dict[str, str] = {}
        self._received: list[WebhookEvent] = []

    def register_adapter(self, source: str, adapter: WebhookAdapter) -> None:
        """Register a webhook adapter for a source."""
        self._adapters[source] = adapter

    def register_handler(
        self, event_pattern: str, handler: EventHandler
    ) -> None:
        """
        Register a handler for events matching a pattern.

        Patterns:
            "*"                     — all events
            "pull_request"          — all pull_request events
            "pull_request.opened"   — exact match
        """
        self._handlers.append((event_pattern, handler))

    def set_secret(self, source: str, secret: str) -> None:
        """Set the webhook secret for signature verification."""
        self._secrets[source] = secret

    async def receive(
        self,
        source: str,
        headers: dict[str, str],
        body: dict[str, Any],
        raw_body: bytes | None = None,
    ) -> WebhookEvent:
        """
        Process an incoming webhook.

        Returns the parsed WebhookEvent (also dispatched to handlers).
        """
        adapter = self._adapters.get(source, self._adapters["generic"])

        # Verify signature if secret is configured
        if raw_body and source in self._secrets:
            if not adapter.verify_signature(
                headers, raw_body, self._secrets[source]
            ):
                logger.warning(
                    "[webhook] Signature verification failed for %s", source
                )
                raise ValueError(f"Invalid webhook signature for source: {source}")

        # Parse
        event = adapter.parse(headers, body)
        self._received.append(event)

        logger.info(
            "[webhook] %s event: %s (resource: %s, sender: %s)",
            event.source.value,
            event.event_type,
            event.resource_id or "—",
            event.sender or "—",
        )

        # Dispatch to matching handlers
        for pattern, handler in self._handlers:
            if self._matches(event.event_type, pattern):
                try:
                    await handler(event)
                except Exception:
                    logger.error(
                        "[webhook] Handler failed for %s event %s",
                        pattern,
                        event.event_type,
                        exc_info=True,
                    )

        return event

    @staticmethod
    def _matches(event_type: str, pattern: str) -> bool:
        """Check if an event_type matches a handler pattern."""
        if pattern == "*":
            return True
        return event_type == pattern or event_type.startswith(f"{pattern}.")

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def received_events(self) -> list[WebhookEvent]:
        return self._received

    def clear_received(self) -> None:
        self._received.clear()


# ---------------------------------------------------------------------------
# FastAPI route factory
# ---------------------------------------------------------------------------


def add_webhook_routes(
    app,
    receiver: WebhookReceiver,
    path_prefix: str = "/webhooks",
) -> None:
    """
    Add webhook routes to an existing FastAPI app.

    Creates:
        POST {path_prefix}/{source}  — receive webhook from {source}
        GET  {path_prefix}/events     — list recent events (debug)

    Note: route functions use explicit typing.get_type_hints bypass
    because `from __future__ import annotations` (PEP 563) makes all
    annotations lazy strings, which prevents FastAPI from recognizing
    `Request` as a special injection parameter.
    """
    # Import here to avoid hard FastAPI dependency for non-HTTP usage
    from fastapi import HTTPException, Request, Response

    # We must define routes with non-stringified annotations.
    # Use exec to create functions outside PEP 563 scope.
    async def _receive_webhook(source: str, request):
        """Receive a webhook from an external source."""
        raw_body = await request.body()
        try:
            body = json_lib.loads(raw_body)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        headers = dict(request.headers)

        try:
            event = await receiver.receive(source, headers, body, raw_body)
        except ValueError as e:
            raise HTTPException(status_code=401, detail=str(e))

        return Response(
            content=json_lib.dumps({
                "accepted": True,
                "source": event.source.value,
                "event_type": event.event_type,
                "resource_id": event.resource_id,
            }),
            media_type="application/json",
        )

    # Manually set annotation so FastAPI sees the real Request type
    _receive_webhook.__annotations__ = {"source": str, "request": Request, "return": Response}

    app.post(f"{path_prefix}/{{source}}")(_receive_webhook)

    @app.get(f"{path_prefix}/events")
    async def list_events(limit: int = 20):
        """List recently received webhook events (for debugging)."""
        events = receiver.received_events[-limit:]
        return [
            {
                "source": e.source.value,
                "event_type": e.event_type,
                "action": e.action,
                "resource_id": e.resource_id,
                "sender": e.sender,
                "received_at": e.received_at.isoformat(),
            }
            for e in reversed(events)
        ]
