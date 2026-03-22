"""
Unit tests for the generic webhook receiver.

The receiver is an abstraction layer between external systems (GitHub,
JIRA, etc.) and the PA. It normalizes webhooks into WebhookEvents and
dispatches to registered handlers. Currently a stub — full source-specific
parsing comes in m4 (PR agent) and m5 (integrations).

Traceability:
  Journey: specs/journeys/feature-request-lifecycle.md
  Story: specs/stories/orchestration/pa-evaluates-and-routes.md
  Feature: specs/features/orchestration/pa-evaluates-and-routes.feature
  Domain Model: specs/models/task/task.lifecycle.yaml
"""

import hashlib
import hmac
import pytest
from unittest.mock import AsyncMock

from src.orchestrator.webhook_receiver import (
    GitHubWebhookAdapter,
    WebhookAdapter,
    WebhookEvent,
    WebhookReceiver,
    WebhookSource,
    add_webhook_routes,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def receiver():
    return WebhookReceiver()


@pytest.fixture
def github_pr_payload():
    """Minimal GitHub pull_request webhook payload."""
    return {
        "action": "opened",
        "pull_request": {
            "number": 42,
            "html_url": "https://github.com/org/repo/pull/42",
            "title": "Add auth flow",
        },
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "coding-agent"},
    }


@pytest.fixture
def github_pr_headers():
    return {
        "x-github-event": "pull_request",
        "x-github-delivery": "delivery-abc-123",
        "content-type": "application/json",
    }


# ---------------------------------------------------------------------------
# WebhookEvent model
# ---------------------------------------------------------------------------


class TestWebhookEvent:

    def test_minimal_event(self):
        event = WebhookEvent(
            source=WebhookSource.GITHUB,
            event_type="pull_request.opened",
        )
        assert event.source == WebhookSource.GITHUB
        assert event.event_type == "pull_request.opened"
        assert event.received_at is not None

    def test_webhook_sources(self):
        expected = {"github", "jira", "generic"}
        actual = {s.value for s in WebhookSource}
        assert actual == expected


# ---------------------------------------------------------------------------
# Generic adapter
# ---------------------------------------------------------------------------


class TestGenericAdapter:

    def test_passthrough_parsing(self):
        adapter = WebhookAdapter()
        event = adapter.parse(
            headers={},
            body={"event_type": "custom.event", "action": "triggered"},
        )
        assert event.source == WebhookSource.GENERIC
        assert event.event_type == "custom.event"
        assert event.action == "triggered"

    def test_unknown_fields_preserved_in_raw(self):
        adapter = WebhookAdapter()
        body = {"event_type": "test", "custom_field": "value"}
        event = adapter.parse({}, body)
        assert event.raw_payload["custom_field"] == "value"

    def test_signature_always_valid(self):
        adapter = WebhookAdapter()
        assert adapter.verify_signature({}, b"", None) is True


# ---------------------------------------------------------------------------
# GitHub adapter
# ---------------------------------------------------------------------------


class TestGitHubAdapter:
    """
    Test GitHub-specific webhook parsing.

    Currently a stub — extracts PR/issue basics. Full parsing
    (review state, merge status, check runs) comes in m4/m5.
    """

    def test_parse_pr_opened(self, github_pr_headers, github_pr_payload):
        adapter = GitHubWebhookAdapter()
        event = adapter.parse(github_pr_headers, github_pr_payload)

        assert event.source == WebhookSource.GITHUB
        assert event.event_type == "pull_request.opened"
        assert event.action == "opened"
        assert event.resource_id == "42"
        assert event.resource_url == "https://github.com/org/repo/pull/42"
        assert event.sender == "coding-agent"
        assert event.metadata["repository"] == "org/repo"
        assert event.metadata["delivery_id"] == "delivery-abc-123"

    def test_parse_pr_merged(self, github_pr_headers, github_pr_payload):
        github_pr_payload["action"] = "closed"
        github_pr_payload["pull_request"]["merged"] = True
        event = GitHubWebhookAdapter().parse(github_pr_headers, github_pr_payload)

        assert event.event_type == "pull_request.closed"
        assert event.action == "closed"

    def test_parse_issue_event(self):
        adapter = GitHubWebhookAdapter()
        event = adapter.parse(
            {"x-github-event": "issues"},
            {
                "action": "labeled",
                "issue": {
                    "number": 7,
                    "html_url": "https://github.com/org/repo/issues/7",
                },
                "sender": {"login": "user"},
            },
        )
        assert event.event_type == "issues.labeled"
        assert event.resource_id == "7"

    def test_parse_event_without_action(self):
        adapter = GitHubWebhookAdapter()
        event = adapter.parse(
            {"x-github-event": "ping"},
            {"zen": "Keep it simple"},
        )
        assert event.event_type == "ping"
        assert event.action == ""

    def test_verify_signature_valid(self):
        adapter = GitHubWebhookAdapter()
        secret = "my-secret"
        body = b'{"test": true}'
        sig = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()

        assert adapter.verify_signature(
            {"x-hub-signature-256": sig}, body, secret
        ) is True

    def test_verify_signature_invalid(self):
        adapter = GitHubWebhookAdapter()
        assert adapter.verify_signature(
            {"x-hub-signature-256": "sha256=bad"},
            b'{"test": true}',
            "my-secret",
        ) is False

    def test_verify_signature_missing_header(self):
        adapter = GitHubWebhookAdapter()
        assert adapter.verify_signature({}, b"body", "secret") is False

    def test_verify_no_secret_skips(self):
        adapter = GitHubWebhookAdapter()
        assert adapter.verify_signature({}, b"body", None) is True


# ---------------------------------------------------------------------------
# WebhookReceiver
# ---------------------------------------------------------------------------


class TestWebhookReceiver:

    @pytest.mark.asyncio
    async def test_receive_github_event(
        self, receiver, github_pr_headers, github_pr_payload
    ):
        event = await receiver.receive(
            "github", github_pr_headers, github_pr_payload
        )
        assert event.source == WebhookSource.GITHUB
        assert event.event_type == "pull_request.opened"
        assert len(receiver.received_events) == 1

    @pytest.mark.asyncio
    async def test_receive_unknown_source_uses_generic(self, receiver):
        event = await receiver.receive(
            "slack",
            {},
            {"event_type": "message.posted", "action": "new"},
        )
        assert event.source == WebhookSource.GENERIC

    @pytest.mark.asyncio
    async def test_handler_dispatched_on_exact_match(self, receiver):
        handler = AsyncMock()
        receiver.register_handler("pull_request.opened", handler)

        await receiver.receive(
            "github",
            {"x-github-event": "pull_request"},
            {"action": "opened", "pull_request": {"number": 1, "html_url": ""}, "sender": {"login": "x"}},
        )
        handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handler_dispatched_on_prefix_match(self, receiver):
        handler = AsyncMock()
        receiver.register_handler("pull_request", handler)

        await receiver.receive(
            "github",
            {"x-github-event": "pull_request"},
            {"action": "opened", "pull_request": {"number": 1, "html_url": ""}, "sender": {"login": "x"}},
        )
        handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_wildcard_handler_matches_all(self, receiver):
        handler = AsyncMock()
        receiver.register_handler("*", handler)

        await receiver.receive("generic", {}, {"event_type": "anything"})
        handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handler_not_called_on_mismatch(self, receiver):
        handler = AsyncMock()
        receiver.register_handler("issue", handler)

        await receiver.receive(
            "github",
            {"x-github-event": "pull_request"},
            {"action": "opened", "pull_request": {"number": 1, "html_url": ""}, "sender": {"login": "x"}},
        )
        handler.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_multiple_handlers(self, receiver):
        pr_handler = AsyncMock()
        all_handler = AsyncMock()
        receiver.register_handler("pull_request", pr_handler)
        receiver.register_handler("*", all_handler)

        await receiver.receive(
            "github",
            {"x-github-event": "pull_request"},
            {"action": "opened", "pull_request": {"number": 1, "html_url": ""}, "sender": {"login": "x"}},
        )
        pr_handler.assert_awaited_once()
        all_handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handler_failure_doesnt_break_receiver(self, receiver):
        bad_handler = AsyncMock(side_effect=RuntimeError("boom"))
        good_handler = AsyncMock()
        receiver.register_handler("*", bad_handler)
        receiver.register_handler("*", good_handler)

        await receiver.receive("generic", {}, {"event_type": "test"})
        good_handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_signature_verification_failure(self, receiver):
        receiver.set_secret("github", "my-secret")

        with pytest.raises(ValueError, match="Invalid webhook signature"):
            await receiver.receive(
                "github",
                {"x-hub-signature-256": "sha256=invalid"},
                {"action": "opened"},
                raw_body=b'{"action": "opened"}',
            )

    @pytest.mark.asyncio
    async def test_signature_not_checked_without_raw_body(self, receiver):
        """If no raw_body, signature check is skipped."""
        receiver.set_secret("github", "my-secret")

        # Should not raise — raw_body is None
        event = await receiver.receive(
            "github",
            {"x-github-event": "ping"},
            {"zen": "test"},
        )
        assert event.event_type == "ping"

    @pytest.mark.asyncio
    async def test_clear_received(self, receiver):
        await receiver.receive("generic", {}, {"event_type": "a"})
        await receiver.receive("generic", {}, {"event_type": "b"})
        assert len(receiver.received_events) == 2

        receiver.clear_received()
        assert len(receiver.received_events) == 0

    @pytest.mark.asyncio
    async def test_register_custom_adapter(self, receiver):
        class JiraAdapter(WebhookAdapter):
            source = WebhookSource.JIRA

            def parse(self, headers, body):
                return WebhookEvent(
                    source=WebhookSource.JIRA,
                    event_type=body.get("webhookEvent", "unknown"),
                    resource_id=body.get("issue", {}).get("key", ""),
                )

        receiver.register_adapter("jira", JiraAdapter())
        event = await receiver.receive(
            "jira",
            {},
            {"webhookEvent": "jira:issue_updated", "issue": {"key": "PROJ-42"}},
        )
        assert event.source == WebhookSource.JIRA
        assert event.resource_id == "PROJ-42"


# ---------------------------------------------------------------------------
# FastAPI route tests
# ---------------------------------------------------------------------------


class TestWebhookHTTPRoutes:
    """Test the webhook HTTP endpoints."""

    def _make_client(self, receiver=None):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        recv = receiver or WebhookReceiver()
        add_webhook_routes(app, recv)
        return TestClient(app), recv

    def test_post_github_webhook(self, github_pr_headers, github_pr_payload):
        client, recv = self._make_client()
        response = client.post(
            "/webhooks/github",
            json=github_pr_payload,
            headers=github_pr_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["accepted"] is True
        assert data["source"] == "github"
        assert data["event_type"] == "pull_request.opened"
        assert data["resource_id"] == "42"

    def test_post_generic_webhook(self):
        client, _ = self._make_client()
        response = client.post(
            "/webhooks/custom-source",
            json={"event_type": "deploy.completed"},
        )
        assert response.status_code == 200
        assert response.json()["source"] == "generic"

    def test_post_invalid_json_400(self):
        client, _ = self._make_client()
        response = client.post(
            "/webhooks/github",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400

    def test_get_events_empty(self):
        client, _ = self._make_client()
        response = client.get("/webhooks/events")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_events_after_receiving(self, github_pr_headers, github_pr_payload):
        client, _ = self._make_client()
        client.post(
            "/webhooks/github",
            json=github_pr_payload,
            headers=github_pr_headers,
        )
        response = client.get("/webhooks/events")
        assert response.status_code == 200
        events = response.json()
        assert len(events) == 1
        assert events[0]["event_type"] == "pull_request.opened"

    def test_signature_failure_401(self):
        recv = WebhookReceiver()
        recv.set_secret("github", "secret")
        client, _ = self._make_client(recv)

        response = client.post(
            "/webhooks/github",
            json={"action": "opened"},
            headers={"x-hub-signature-256": "sha256=bad"},
        )
        assert response.status_code == 401
