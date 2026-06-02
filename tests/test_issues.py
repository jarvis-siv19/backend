import pytest

def test_create_issue(client):
    """Test standard user can create an issue."""
    payload = {
        "type": "feature_request",
        "title": "Add dark mode",
        "description": "Please add dark mode"
    }
    response = client.post("/db/issues/", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == payload["title"]
    assert data["type"] == payload["type"]
    assert data["status"] == "open"
    assert "id" in data
    assert "createdAt" in data
