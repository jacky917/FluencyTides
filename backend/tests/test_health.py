"""健康檢查端點的測試：回報映像的 git commit，供 CI 確認部署完成。

Tests for ``/api/health``: it reports the image's git commit so CI's deploy
job can confirm the new version is live.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_reports_git_sha_from_env(monkeypatch):
    monkeypatch.setenv("APP_GIT_SHA", "0123abcd")
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "git_sha": "0123abcd"}


def test_health_git_sha_unknown_when_unset_or_empty(monkeypatch):
    """未傳 build arg 時 ENV 可能不存在或為空字串，兩者都回 unknown。"""
    monkeypatch.delenv("APP_GIT_SHA", raising=False)
    unset = client.get("/api/health").json()
    monkeypatch.setenv("APP_GIT_SHA", "")
    empty = client.get("/api/health").json()

    assert unset["git_sha"] == empty["git_sha"] == "unknown"


def test_health_needs_no_api_key(monkeypatch):
    """CI 輪詢不帶 X-API-Key：設了 API_SECRET_KEY 時健康檢查仍要可讀。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "API_SECRET_KEY", "s3cret")
    assert client.get("/api/health").status_code == 200
