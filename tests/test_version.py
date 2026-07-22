"""Tests for the /version endpoint and its admin override."""

import pytest
from fastapi.testclient import TestClient

from app.auth import get_admin_user, get_current_user
from app.main import app


async def _mock_admin():
    return {"uid": "admin_ver", "email": "a@example.com", "name": "A", "role": "admin"}


def test_version_returns_defaults(client):
    resp = client.get("/version")
    assert resp.status_code == 200
    data = resp.json()
    assert "latest" in data
    assert "minSupported" in data
    assert "android" in data and "ios" in data


def test_admin_can_override_version():
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[get_admin_user] = _mock_admin
    app.dependency_overrides[get_current_user] = _mock_admin
    try:
        with TestClient(app) as c:
            put = c.put(
                "/db/admin/app-version",
                json={
                    "latest": "1.4.0",
                    "latestBuild": 9,
                    "minSupported": "1.2.0",
                    "android": {"url": "https://example.com/app.apk"},
                    "notes": "Bug fixes",
                },
            )
            assert put.status_code == 200, put.text
            assert put.json()["latest"] == "1.4.0"

            got = c.get("/version").json()
            assert got["latest"] == "1.4.0"
            assert got["latestBuild"] == 9
            assert got["minSupported"] == "1.2.0"
            assert got["android"]["url"] == "https://example.com/app.apk"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)
