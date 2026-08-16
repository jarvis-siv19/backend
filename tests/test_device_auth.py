import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings


def test_device_request_requires_its_private_secret(client):
    started = client.post("/db/device-auth/start")
    assert started.status_code == 200
    body = started.json()
    assert len(body["deviceCode"]) == 8
    assert body["deviceSecret"] not in body["verificationUrl"]

    wrong = client.post(
        "/db/device-auth/poll",
        json={"deviceCode": body["deviceCode"], "deviceSecret": "wrong"},
    )
    assert wrong.json()["status"] == "expired"

    pending = client.post(
        "/db/device-auth/poll",
        json={
            "deviceCode": body["deviceCode"],
            "deviceSecret": body["deviceSecret"],
        },
    )
    assert pending.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_approved_device_token_is_consumed_once(client):
    body = client.post("/db/device-auth/start").json()
    # TestClient owns the app's event loop. Use a client owned by this async
    # test's loop for direct fixture setup, otherwise Motor raises a cross-loop
    # Future error before the endpoint can be tested.
    mongo = AsyncIOMotorClient(settings.MONGODB_URI)
    try:
        await mongo[settings.DB_NAME].device_auth_requests.update_one(
            {"deviceCode": body["deviceCode"]},
            {"$set": {"status": "approved", "email": "test@example.com", "tokenHash": "once"}},
        )
    finally:
        mongo.close()

    payload = {
        "deviceCode": body["deviceCode"],
        "deviceSecret": body["deviceSecret"],
    }
    first = client.post("/db/device-auth/poll", json=payload).json()
    assert first == {"status": "approved", "email": "test@example.com", "tokenHash": "once"}
    assert client.post("/db/device-auth/poll", json=payload).json()["status"] == "expired"
