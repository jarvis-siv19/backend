import requests
import os
import json

SUPABASE_URL = "https://jolggtuiatbxrjomfbxl.supabase.co"
SUPABASE_KEY = "sb_publishable_Y-dpGB63ipwupP3Y3-6HAw_6d6w3wpO"
BACKEND_URL = "http://localhost:8081"

email = "sivaganesh1903@gmail.com"
password = "test123#"

print("1. Authenticating with Supabase...")
res = requests.post(
    f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
    headers={"apikey": SUPABASE_KEY},
    json={"email": email, "password": password}
)

if res.status_code != 200:
    print("Supabase login failed!", res.text)
    exit(1)

data = res.json()
token = data["access_token"]
uid = data["user"]["id"]
print("✅ Got JWT token")

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

print("\n2. Testing Backend Endpoints")
endpoints = [
    ("POST", "/db/login", {"email": email, "password": password}),
    ("GET", "/db/user/account", None),
    ("POST", "/db/dashboard/summary", {"startDate": "2026-06-01T00:00:00.000", "endDate": "2026-06-01T23:59:59.000"}),
    ("GET", "/db/user/events", None),
]

for method, path, payload in endpoints:
    url = f"{BACKEND_URL}{path}"
    print(f"\nTesting {method} {path}...")
    try:
        if method == "GET":
            r = requests.get(url, headers=headers)
        elif method == "POST":
            r = requests.post(url, headers=headers, json=payload)
            
        print(f"Status Code: {r.status_code}")
        if r.status_code != 200:
            print(f"Response: {r.text}")
    except Exception as e:
        print(f"Request Exception: {e}")
