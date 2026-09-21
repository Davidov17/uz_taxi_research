"""Tests for the webhook-mode glue in web/bot_webhook.py — the HTTP-layer
behavior only (secret token check, update parsing, dispatch call), not the
survey flow itself (already covered by test_handlers.py etc. against the
long-polling harness). Deliberately builds its own minimal FastAPI app
rather than importing the shared `telegram_bot.web.app:app` singleton, so
this doesn't depend on (or mutate) global settings.public_base_url, and
never calls the real `_build_bot_and_dispatcher()` — that would try to
attach `survey_router` to a second Dispatcher, which aiogram refuses once
conftest.py's session-scoped `dp` fixture has already attached it once.
"""

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from telegram_bot.web import bot_webhook

pytestmark = pytest.mark.asyncio


class FakeDispatcher:
    def __init__(self):
        self.fed_updates = []

    async def feed_update(self, bot, update):
        self.fed_updates.append(update)


def _sample_update_payload(update_id: int = 1) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "date": int(datetime.now(timezone.utc).timestamp()),
            "chat": {"id": 111, "type": "private"},
            "from": {"id": 111, "is_bot": False, "first_name": "Test"},
            "text": "/start",
        },
    }


@pytest.fixture
def webhook_app(monkeypatch):
    app = FastAPI()
    app.include_router(bot_webhook.router)
    fake_dispatcher = FakeDispatcher()
    monkeypatch.setattr(bot_webhook, "_bot", object())
    monkeypatch.setattr(bot_webhook, "_dispatcher", fake_dispatcher)
    return app, fake_dispatcher


async def test_webhook_accepts_update_without_secret_configured(webhook_app, monkeypatch):
    app, fake_dispatcher = webhook_app
    monkeypatch.setattr(bot_webhook.settings, "webhook_secret", "")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(bot_webhook.WEBHOOK_PATH, json=_sample_update_payload())
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert len(fake_dispatcher.fed_updates) == 1
    assert fake_dispatcher.fed_updates[0].update_id == 1


async def test_webhook_rejects_missing_secret_token(webhook_app, monkeypatch):
    app, fake_dispatcher = webhook_app
    monkeypatch.setattr(bot_webhook.settings, "webhook_secret", "s3cr3t")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(bot_webhook.WEBHOOK_PATH, json=_sample_update_payload())
    assert resp.status_code == 401
    assert fake_dispatcher.fed_updates == []


async def test_webhook_rejects_wrong_secret_token(webhook_app, monkeypatch):
    app, fake_dispatcher = webhook_app
    monkeypatch.setattr(bot_webhook.settings, "webhook_secret", "s3cr3t")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            bot_webhook.WEBHOOK_PATH,
            json=_sample_update_payload(),
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
    assert resp.status_code == 401
    assert fake_dispatcher.fed_updates == []


async def test_webhook_accepts_correct_secret_token(webhook_app, monkeypatch):
    app, fake_dispatcher = webhook_app
    monkeypatch.setattr(bot_webhook.settings, "webhook_secret", "s3cr3t")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            bot_webhook.WEBHOOK_PATH,
            json=_sample_update_payload(),
            headers={"X-Telegram-Bot-Api-Secret-Token": "s3cr3t"},
        )
    assert resp.status_code == 200
    assert len(fake_dispatcher.fed_updates) == 1


async def test_webhook_returns_503_when_bot_not_initialized(monkeypatch):
    app = FastAPI()
    app.include_router(bot_webhook.router)
    monkeypatch.setattr(bot_webhook, "_bot", None)
    monkeypatch.setattr(bot_webhook, "_dispatcher", None)
    monkeypatch.setattr(bot_webhook.settings, "webhook_secret", "")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(bot_webhook.WEBHOOK_PATH, json=_sample_update_payload())
    assert resp.status_code == 503
