import pytest
from httpx import AsyncClient

# Test Account API
@pytest.mark.asyncio
async def test_get_account(client):
    response = client.get("/db/user/account")
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "test@example.com"
    assert data["name"] == "Test User"
    assert "friendCode" in data
    assert data["friendCode"] != ""

@pytest.mark.asyncio
async def test_update_account(client):
    response = client.put("/db/user/account", json={"name": "Updated Name", "upiId": "test@upi"})
    assert response.status_code == 200
    assert response.json()["message"] == "Account updated successfully"
    
    # Verify update
    response2 = client.get("/db/user/account")
    assert response2.json()["name"] == "Updated Name"
    assert response2.json()["upiId"] == "test@upi"

# Test Event API
@pytest.mark.asyncio
async def test_create_and_get_event(client):
    event_payload = {
        "eventName": "Goa Trip",
        "description": "A fun trip to Goa",
        "users": ["test@example.com"]
    }
    
    # Create Event
    response = client.post("/db/event", json=event_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["eventName"] == "Goa Trip"
    assert data["description"] == "A fun trip to Goa"
    assert len(data["users"]) == 1
    
    event_id = data["id"]
    
    # Get User Events
    response2 = client.get("/db/user/events")
    assert response2.status_code == 200
    events_data = response2.json()
    assert len(events_data["events"]) >= 1
    
    # Verify description and eventName are correctly returned
    found_event = next((e for e in events_data["events"] if e["id"] == event_id), None)
    assert found_event is not None
    assert found_event["eventName"] == "Goa Trip"
    assert found_event["description"] == "A fun trip to Goa"
    assert len(found_event["users"]) == 1
