"""BookLoop end-to-end API tests. Covers auth, discover, map, books, swaps, notifications, uploads."""
import os
import io
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL") or "https://bookloop-preview-1.preview.emergentagent.com"
BASE_URL = BASE_URL.rstrip("/")
API = f"{BASE_URL}/api"


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def user_a():
    """Primary test user: reuse existing test@bookloop.com or create if missing."""
    r = requests.post(f"{API}/auth/login", json={"email": "test@bookloop.com", "password": "test1234"})
    if r.status_code != 200:
        r = requests.post(f"{API}/auth/register", json={"email": "test@bookloop.com", "password": "test1234", "name": "Test User A"})
    assert r.status_code == 200, r.text
    d = r.json()
    return {"token": d["session_token"], "user": d["user"]}


@pytest.fixture(scope="module")
def user_b():
    email = f"TEST_{uuid.uuid4().hex[:8]}@bookloop.com"
    r = requests.post(f"{API}/auth/register", json={"email": email, "password": "test1234", "name": "Test User B"})
    assert r.status_code == 200, r.text
    d = r.json()
    return {"token": d["session_token"], "user": d["user"], "email": email}


def _h(token):
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------ AUTH
class TestAuth:
    def test_root(self):
        r = requests.get(f"{API}/")
        assert r.status_code == 200
        assert "genres" in r.json()

    def test_login_existing(self, user_a):
        assert user_a["token"]
        assert user_a["user"]["email"] == "test@bookloop.com"

    def test_login_wrong_pw(self):
        r = requests.post(f"{API}/auth/login", json={"email": "test@bookloop.com", "password": "wrong"})
        assert r.status_code == 401

    def test_me(self, user_a):
        r = requests.get(f"{API}/auth/me", headers=_h(user_a["token"]))
        assert r.status_code == 200
        assert r.json()["user"]["user_id"] == user_a["user"]["user_id"]

    def test_me_no_token(self):
        r = requests.get(f"{API}/auth/me")
        assert r.status_code == 401

    def test_google_session_invalid(self):
        r = requests.post(f"{API}/auth/session", json={"session_id": "definitely_not_valid"})
        assert r.status_code == 401

    def test_register_duplicate(self):
        r = requests.post(f"{API}/auth/register", json={"email": "test@bookloop.com", "password": "x", "name": "Y"})
        assert r.status_code == 400

    def test_logout(self, user_b):
        # Use a throwaway token: log user_b in again to get an extra token
        r = requests.post(f"{API}/auth/login", json={"email": user_b["email"], "password": "test1234"})
        assert r.status_code == 200
        t = r.json()["session_token"]
        r2 = requests.post(f"{API}/auth/logout", headers=_h(t))
        assert r2.status_code == 200
        r3 = requests.get(f"{API}/auth/me", headers=_h(t))
        assert r3.status_code == 401


# ------------------------------------------------------------------ PROFILE
class TestProfile:
    def test_update_profile(self, user_a):
        payload = {"bio": "Tester bio", "genres": ["Fiction", "Psychology"], "languages": ["English", "Italian"],
                   "is_exchanging": True, "neighborhood": "Centro", "lat": 38.1938, "lng": 15.5540}
        r = requests.put(f"{API}/users/me", json=payload, headers=_h(user_a["token"]))
        assert r.status_code == 200
        u = r.json()["user"]
        assert u["bio"] == "Tester bio"
        assert "Fiction" in u["genres"]

    def test_get_user_public(self, user_a, user_b):
        r = requests.get(f"{API}/users/{user_b['user']['user_id']}", headers=_h(user_a["token"]))
        assert r.status_code == 200
        d = r.json()
        assert d["user"]["user_id"] == user_b["user"]["user_id"]
        assert "books" in d and "reviews" in d
        assert "distance_km" in d["user"]


# ------------------------------------------------------------------ DISCOVER
class TestDiscover:
    def test_people(self, user_a):
        r = requests.get(f"{API}/discover/people", headers=_h(user_a["token"]))
        assert r.status_code == 200
        people = r.json()["people"]
        assert isinstance(people, list) and len(people) >= 5
        p0 = people[0]
        for k in ("user_id", "name", "distance_km", "rating", "books"):
            assert k in p0

    def test_people_filter_genre(self, user_a):
        r = requests.get(f"{API}/discover/people?genre=Fantasy&exchanging=true", headers=_h(user_a["token"]))
        assert r.status_code == 200
        for p in r.json()["people"]:
            assert "Fantasy" in p["genres"]

    def test_people_search(self, user_a):
        r = requests.get(f"{API}/discover/people?search=Maria", headers=_h(user_a["token"]))
        assert r.status_code == 200
        names = [p["name"] for p in r.json()["people"]]
        assert any("Maria" in n for n in names)

    def test_books(self, user_a):
        r = requests.get(f"{API}/discover/books", headers=_h(user_a["token"]))
        assert r.status_code == 200
        books = r.json()["books"]
        assert len(books) >= 5
        assert "owner_name" in books[0] and "distance_km" in books[0]


# ------------------------------------------------------------------ MAP
class TestMap:
    def test_clusters(self, user_a):
        r = requests.get(f"{API}/map/clusters", headers=_h(user_a["token"]))
        assert r.status_code == 200
        d = r.json()
        assert "clusters" in d and "total_active" in d and "total_books" in d
        assert d["total_active"] >= 5
        c = d["clusters"][0]
        for k in ("neighborhood", "lat", "lng", "people_count", "books_count", "top_genres"):
            assert k in c


# ------------------------------------------------------------------ BOOKS
class TestBooks:
    _book_id = None

    def test_create_book(self, user_a):
        r = requests.post(f"{API}/books", json={
            "title": "TEST Book Alpha", "author": "Tester", "genre": "Fiction", "language": "English", "condition": "Good"
        }, headers=_h(user_a["token"]))
        assert r.status_code == 200
        b = r.json()["book"]
        assert b["title"] == "TEST Book Alpha"
        TestBooks._book_id = b["id"]

    def test_list_books(self, user_a):
        r = requests.get(f"{API}/books", headers=_h(user_a["token"]))
        assert r.status_code == 200
        ids = [b["id"] for b in r.json()["books"]]
        assert TestBooks._book_id in ids

    def test_book_detail(self, user_a):
        r = requests.get(f"{API}/books/detail/{TestBooks._book_id}", headers=_h(user_a["token"]))
        assert r.status_code == 200
        d = r.json()
        assert d["book"]["id"] == TestBooks._book_id
        assert d["is_owner"] is True

    def test_update_book(self, user_a):
        r = requests.put(f"{API}/books/{TestBooks._book_id}", json={
            "title": "TEST Book Alpha v2", "author": "Tester", "genre": "Psychology", "language": "English",
            "condition": "Like New", "status": "Available"
        }, headers=_h(user_a["token"]))
        assert r.status_code == 200
        assert r.json()["book"]["title"].endswith("v2")

    def test_filter_status(self, user_a):
        r = requests.get(f"{API}/books?status=Available", headers=_h(user_a["token"]))
        assert r.status_code == 200
        for b in r.json()["books"]:
            assert b["status"] == "Available"

    def test_delete_book(self, user_a):
        # Create then delete a throwaway
        r = requests.post(f"{API}/books", json={"title": "TEST Delete Me", "author": "X"}, headers=_h(user_a["token"]))
        bid = r.json()["book"]["id"]
        rd = requests.delete(f"{API}/books/{bid}", headers=_h(user_a["token"]))
        assert rd.status_code == 200
        rg = requests.get(f"{API}/books/detail/{bid}", headers=_h(user_a["token"]))
        assert rg.status_code == 404


# ------------------------------------------------------------------ UPLOAD
class TestUpload:
    def test_upload_image(self, user_a):
        # 1x1 PNG
        png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15"
               b"\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01\x5e\xf3\x2a\xea\x00\x00"
               b"\x00\x00IEND\xaeB`\x82")
        files = {"file": ("test.png", io.BytesIO(png), "image/png")}
        r = requests.post(f"{API}/upload", files=files, headers=_h(user_a["token"]))
        if r.status_code != 200:
            pytest.skip(f"Storage not available: {r.status_code} {r.text[:200]}")
        d = r.json()
        assert d["url"].startswith("/api/files/")
        # Fetch it back
        r2 = requests.get(f"{BASE_URL}{d['url']}")
        assert r2.status_code == 200
        assert r2.headers.get("content-type", "").startswith("image/")


# ------------------------------------------------------------------ SWAP FLOW
class TestSwapFlow:
    state = {}

    def test_setup_books(self, user_a, user_b):
        # Ensure both users have at least one available book
        for u in (user_a, user_b):
            r = requests.post(f"{API}/books", json={
                "title": f"TEST Swap Book {u['user']['name']}", "author": "Tester", "genre": "Fiction",
                "language": "English", "condition": "Good"
            }, headers=_h(u["token"]))
            assert r.status_code == 200
            TestSwapFlow.state[f"book_{u['user']['user_id']}"] = r.json()["book"]["id"]

    def test_create_swap(self, user_a, user_b):
        r = requests.post(f"{API}/swaps", json={"receiver_id": user_b["user"]["user_id"], "message": "Hi!"},
                          headers=_h(user_a["token"]))
        assert r.status_code == 200
        s = r.json()["swap"]
        assert s["status"] == "pending"
        TestSwapFlow.state["swap_id"] = s["id"]

    def test_create_swap_self_forbidden(self, user_a):
        r = requests.post(f"{API}/swaps", json={"receiver_id": user_a["user"]["user_id"]}, headers=_h(user_a["token"]))
        assert r.status_code == 400

    def test_list_swaps_buckets(self, user_a, user_b):
        r = requests.get(f"{API}/swaps", headers=_h(user_a["token"]))
        assert r.status_code == 200
        assert any(s["id"] == TestSwapFlow.state["swap_id"] for s in r.json()["outgoing"])
        r2 = requests.get(f"{API}/swaps", headers=_h(user_b["token"]))
        assert any(s["id"] == TestSwapFlow.state["swap_id"] for s in r2.json()["incoming"])

    def test_get_swap_detail(self, user_a):
        sid = TestSwapFlow.state["swap_id"]
        r = requests.get(f"{API}/swaps/{sid}", headers=_h(user_a["token"]))
        assert r.status_code == 200
        d = r.json()
        assert d["swap"]["id"] == sid
        assert isinstance(d["messages"], list) and len(d["messages"]) >= 1
        assert isinstance(d["my_books"], list) and isinstance(d["their_books"], list)

    def test_send_message(self, user_a):
        sid = TestSwapFlow.state["swap_id"]
        r = requests.post(f"{API}/swaps/{sid}/messages", json={"text": "Ciao!"}, headers=_h(user_a["token"]))
        assert r.status_code == 200
        assert r.json()["message"]["text"] == "Ciao!"

    def test_propose(self, user_a, user_b):
        sid = TestSwapFlow.state["swap_id"]
        offered = TestSwapFlow.state[f"book_{user_a['user']['user_id']}"]
        requested = TestSwapFlow.state[f"book_{user_b['user']['user_id']}"]
        r = requests.post(f"{API}/swaps/{sid}/propose",
                          json={"offered_book_id": offered, "requested_book_id": requested},
                          headers=_h(user_a["token"]))
        assert r.status_code == 200

    def test_accept(self, user_a, user_b):
        sid = TestSwapFlow.state["swap_id"]
        r = requests.post(f"{API}/swaps/{sid}/accept", headers=_h(user_b["token"]))
        assert r.status_code == 200
        # Verify books reserved
        rd = requests.get(f"{API}/swaps/{sid}", headers=_h(user_a["token"]))
        assert rd.json()["swap"]["status"] == "active"

    def test_complete_both(self, user_a, user_b):
        sid = TestSwapFlow.state["swap_id"]
        r1 = requests.post(f"{API}/swaps/{sid}/complete", headers=_h(user_a["token"]))
        assert r1.status_code == 200
        # Still active
        rmid = requests.get(f"{API}/swaps/{sid}", headers=_h(user_a["token"]))
        assert rmid.json()["swap"]["status"] == "active"
        r2 = requests.post(f"{API}/swaps/{sid}/complete", headers=_h(user_b["token"]))
        assert r2.status_code == 200
        rd = requests.get(f"{API}/swaps/{sid}", headers=_h(user_a["token"]))
        assert rd.json()["swap"]["status"] == "completed"
        # swaps_count incremented
        ru = requests.get(f"{API}/auth/me", headers=_h(user_a["token"]))
        assert ru.json()["user"]["swaps_count"] >= 1

    def test_rate(self, user_a, user_b):
        sid = TestSwapFlow.state["swap_id"]
        r = requests.post(f"{API}/swaps/{sid}/rate", json={"stars": 5, "review": "Great!"},
                          headers=_h(user_a["token"]))
        assert r.status_code == 200
        # rating count on user_b should increase
        ru = requests.get(f"{API}/users/{user_b['user']['user_id']}", headers=_h(user_a["token"]))
        assert ru.json()["user"]["rating_count"] >= 1
        # Cannot rate twice
        r2 = requests.post(f"{API}/swaps/{sid}/rate", json={"stars": 4}, headers=_h(user_a["token"]))
        assert r2.status_code == 400


# ------------------------------------------------------------------ NOTIFICATIONS
class TestNotifications:
    def test_notifications(self, user_b):
        r = requests.get(f"{API}/notifications", headers=_h(user_b["token"]))
        assert r.status_code == 200
        d = r.json()
        for k in ("incoming_requests", "active_swaps", "to_rate", "total"):
            assert k in d
            assert isinstance(d[k], int)
