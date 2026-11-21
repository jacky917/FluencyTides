"""Tests for the Telegram webhook secret check.

Telegram Webhook 密鑰驗證的測試。

The bot trusts the user id inside an update, so the webhook must reject
any request that does not carry the secret it was bound with, including
when no secret is known at all.

Bot 信任 Update 裡的使用者 ID，所以 Webhook 必須拒絕沒帶綁定密鑰的請求，包括完全不知道密鑰的情況。
"""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import asyncio

from app.api.webhook import bind_webhook, router
from app.core.config import settings

HEADER = "X-Telegram-Bot-Api-Secret-Token"
UPDATE = {"update_id": 1}


def _client(secret: str | None) -> tuple[TestClient, list]:
    """Build a test client whose app has a fake bot and dispatcher.

    建立測試用 client，其 app 帶有假的 bot 與 dispatcher。
    """
    fed: list = []

    async def feed_update(bot, update):
        fed.append(update)

    app = FastAPI()
    app.include_router(router)
    app.state.bot = SimpleNamespace()
    app.state.dp = SimpleNamespace(feed_update=feed_update)
    if secret is not None:
        app.state.tg_webhook_secret = secret
    return TestClient(app), fed


def test_matching_secret_is_accepted():
    client, fed = _client("s3cret-token")
    response = client.post(settings.TG_WEBHOOK_PATH, json=UPDATE, headers={HEADER: "s3cret-token"})
    assert response.json() == {"ok": True}
    assert len(fed) == 1


def test_wrong_or_missing_secret_is_rejected():
    client, fed = _client("s3cret-token")
    assert client.post(settings.TG_WEBHOOK_PATH, json=UPDATE, headers={HEADER: "nope"}).json() == {
        "status": "unauthorized"
    }
    assert client.post(settings.TG_WEBHOOK_PATH, json=UPDATE).json() == {"status": "unauthorized"}
    assert fed == []


def test_no_known_secret_rejects_everything():
    """Without a bound secret even a request with any header is rejected.

    沒有綁定的密鑰時，帶任何標頭的請求都要拒絕（以前是「沒設就不驗」）。
    """
    client, fed = _client(None)
    assert client.post(settings.TG_WEBHOOK_PATH, json=UPDATE, headers={HEADER: "x"}).json() == {
        "status": "unauthorized"
    }
    assert fed == []


def test_bad_update_does_not_leak_exception_text():
    client, fed = _client("s3cret-token")
    response = client.post(
        settings.TG_WEBHOOK_PATH, json={"not": "an update"}, headers={HEADER: "s3cret-token"}
    )
    assert response.json() == {"ok": False, "error": "invalid update"}
    assert fed == []


class _FakeBot:
    """Record ``set_webhook`` calls; ``get_webhook_info`` returns ``bound_url``.

    記錄 ``set_webhook`` 的呼叫；``get_webhook_info`` 回傳 ``bound_url``。
    """

    def __init__(self, bound_url: str) -> None:
        self.bound_url = bound_url
        self.calls: list[dict] = []

    async def get_webhook_info(self):
        return SimpleNamespace(url=self.bound_url)

    async def set_webhook(self, **kwargs):
        self.calls.append(kwargs)


_DP = SimpleNamespace(resolve_used_update_types=lambda: ["message"])


def test_bind_generates_secret_when_unset(monkeypatch):
    """Without TG_WEBHOOK_SECRET a random secret is bound and stored.

    沒設 TG_WEBHOOK_SECRET 時，綁定並存下一把隨機密鑰。
    """
    monkeypatch.setattr(settings, "TG_WEBHOOK_SECRET", None)
    bot, state = _FakeBot(""), SimpleNamespace()
    secret = asyncio.run(bind_webhook(bot, _DP, state))
    assert secret and len(secret) >= 32
    assert state.tg_webhook_secret == secret
    assert bot.calls[0]["secret_token"] == secret
    assert bot.calls[0]["drop_pending_updates"] is True  # 網址不同（剛被刪除）


def test_bind_uses_configured_secret_and_keeps_pending(monkeypatch):
    monkeypatch.setattr(settings, "TG_WEBHOOK_SECRET", "configured-secret")
    bot, state = _FakeBot(settings.tg_webhook_url), SimpleNamespace()
    asyncio.run(bind_webhook(bot, _DP, state))
    assert state.tg_webhook_secret == "configured-secret"
    assert bot.calls[0]["secret_token"] == "configured-secret"
    assert bot.calls[0]["drop_pending_updates"] is False  # 網址相同：保留排隊中的 Update
