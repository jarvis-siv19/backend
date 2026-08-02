"""/db/login must work with no request body.

Clients that register after an OAuth or email-confirmation redirect hold a
verified session but have no form fields to send. When LoginRequest.email was
required, those calls 422'd, the registration was silently skipped, and every
later request failed with "Invite code is required for registration".
"""

import pytest
from fastapi.testclient import TestClient

from app.auth import get_verified_identity
from app.main import app
from conftest import MOCK_USER_UID, MOCK_USER_EMAIL


@pytest.fixture
def identity_client():
    """Client whose token resolves to the pre-seeded first user."""

    async def mock_identity():
        return {"uid": MOCK_USER_UID, "email": MOCK_USER_EMAIL, "name": "Test User"}

    app.dependency_overrides[get_verified_identity] = mock_identity
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_verified_identity, None)


def test_login_without_body_uses_jwt_identity(identity_client):
    response = identity_client.post("/db/login")
    assert response.status_code == 200
    assert response.json()["firebaseUid"] == MOCK_USER_UID


def test_login_with_invite_code_only(identity_client):
    """The registration bootstrap sends just the cached invite code."""
    response = identity_client.post("/db/login", json={"inviteCode": ""})
    assert response.status_code == 200
    assert response.json()["email"] == MOCK_USER_EMAIL
