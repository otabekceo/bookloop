"""BookLoop end-to-end API tests. Covers auth, discover, map, books, swaps, notifications, uploads.

IMPORTANT: the backend under test MUST be started with OTP_DEBUG_MODE=true. Most fixtures/tests need
a fast, no-email way to get a working account, via /api/auth/register/dev-instant — a route that only
exists (mirrors the old instant /auth/register contract) when that flag is set; without it these all
404. The dedicated TestRegistrationOtp class below exercises the REAL email/OTP/password flow and
also needs it, to read back the OTP via the `debug_otp` field instead of a real inbox. e.g.:

    OTP_DEBUG_MODE=true python -m uvicorn server:app --host 127.0.0.1 --port 8001   (bash)
    $env:OTP_DEBUG_MODE='true'; python -m uvicorn server:app --port 8001            (PowerShell)

Never run a backend with this flag reachable from the internet — see backend/.env.example.
"""
import os
import io
import re
import time
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

# Loads the same backend/.env the server itself reads, so TestRegistrationOtp's direct-to-Mongo
# helpers (below) talk to the same database as whatever backend EXPO_PUBLIC_BACKEND_URL points at.
load_dotenv(Path(__file__).parent.parent / ".env")

# The tests create users, books and swaps: run them against a local backend on a throwaway database.
# Use 127.0.0.1 rather than "localhost" (on Windows "localhost" adds ~2 s to every request).
BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL") or "http://127.0.0.1:8001"
BASE_URL = BASE_URL.rstrip("/")
API = f"{BASE_URL}/api"


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def user_a():
    """Primary test user: reuse existing test@bookloop.com or create if missing."""
    r = requests.post(f"{API}/auth/login", json={"email": "test@bookloop.com", "password": "test1234"})
    if r.status_code != 200:
        r = requests.post(f"{API}/auth/register/dev-instant", json={"email": "test@bookloop.com", "password": "test1234", "name": "Test User A"})
    assert r.status_code == 200, r.text
    d = r.json()
    return {"token": d["session_token"], "user": d["user"]}


@pytest.fixture(scope="module")
def user_b():
    email = f"TEST_{uuid.uuid4().hex[:8]}@bookloop.com"
    r = requests.post(f"{API}/auth/register/dev-instant", json={"email": email, "password": "test1234", "name": "Test User B"})
    assert r.status_code == 200, r.text
    d = r.json()
    return {"token": d["session_token"], "user": d["user"], "email": email}


def _h(token):
    return {"Authorization": f"Bearer {token}"}


ONE_PIXEL_PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15"
                 b"\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01\x5e\xf3\x2a\xea\x00\x00"
                 b"\x00\x00IEND\xaeB`\x82")


def _own_upload(token):
    """Upload a tiny image as this user; returns its (unsigned) /api/files URL. Chat photos, avatars and
    covers must be the user's own uploads, so tests that need an image URL make a real one."""
    r = requests.post(f"{API}/upload", files={"file": ("p.png", io.BytesIO(ONE_PIXEL_PNG), "image/png")}, headers=_h(token))
    assert r.status_code == 200, r.text
    return r.json()["url"].split("?", 1)[0]


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
        verifier = "v" * 43
        r = requests.post(f"{API}/auth/session", json={"session_id": "definitely_not_valid", "code_verifier": verifier})
        # An id that was never issued by a completed Google sign-in is simply unauthorized.
        assert r.status_code == 401, r.text
        # Without the app's verifier it is refused before anything is looked up (older app builds).
        r = requests.post(f"{API}/auth/session", json={"session_id": "definitely_not_valid"})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "pkce_required", r.text

    def test_register_duplicate(self):
        r = requests.post(f"{API}/auth/register/dev-instant", json={"email": "test@bookloop.com", "password": "x", "name": "Y"})
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
        assert r.status_code == 200, r.text  # storage is local disk now, so this must work
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
        assert s["note"] == "Hi!"  # an older client's first message is kept as the proposal note
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

    def test_send_message_locked_until_accepted(self, user_a, user_b):
        sid = TestSwapFlow.state["swap_id"]
        r = requests.post(f"{API}/swaps/{sid}/messages", json={"text": "Ciao!"}, headers=_h(user_a["token"]))
        if r.status_code == 403:  # a fresh pair: chat is locked until the recipient accepts
            assert r.json()["detail"]["code"] == "chat_locked"
            assert requests.post(f"{API}/swaps/{sid}/allow-chat", headers=_h(user_b["token"])).status_code == 200
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


# ------------------------------------------------------------------ BOOK SEARCH (OpenLibrary proxy)
class TestBookSearch:
    def test_search_requires_auth(self):
        r = requests.get(f"{API}/books/search", params={"q": "atomic habits"})
        assert r.status_code == 401

    def test_search_returns_results(self, user_a):
        r = requests.get(f"{API}/books/search", params={"q": "atomic habits"}, headers=_h(user_a["token"]))
        assert r.status_code == 200
        d = r.json()
        assert "results" in d
        assert isinstance(d["results"], list)
        # OpenLibrary is external; only assert shape if non-empty
        if d["results"]:
            item = d["results"][0]
            for k in ("title", "author", "cover_url", "isbn", "language"):
                assert k in item

    def test_search_too_short(self, user_a):
        r = requests.get(f"{API}/books/search", params={"q": "a"}, headers=_h(user_a["token"]))
        assert r.status_code == 200
        assert r.json()["results"] == []

    def test_isbn_lookup(self, user_a):
        r = requests.get(f"{API}/books/isbn/9780735211292", headers=_h(user_a["token"]))
        assert r.status_code == 200
        res = r.json()["result"]
        for k in ("title", "author", "isbn", "language", "cover_url"):
            assert k in res
        assert res["isbn"] == "9780735211292"

    def test_isbn_unknown_fallback(self, user_a):
        # Even unknown ISBN returns 200 with a shell result (cover_url built from ISBN)
        r = requests.get(f"{API}/books/isbn/0000000000000", headers=_h(user_a["token"]))
        assert r.status_code == 200
        res = r.json()["result"]
        assert res["isbn"] == "0000000000000"
        assert "cover_url" in res


# ------------------------------------------------------------------ CHAT IMAGE MESSAGES
class TestChatImage:
    state = {}

    def test_setup_swap(self, user_a, user_b):
        r = requests.post(f"{API}/swaps", json={"receiver_id": user_b["user"]["user_id"], "message": "Hi again!"},
                          headers=_h(user_a["token"]))
        assert r.status_code == 200
        TestChatImage.state["sid"] = r.json()["swap"]["id"]
        # The recipient opens the chat (a no-op if these two already met in TestSwapFlow).
        requests.post(f"{API}/swaps/{TestChatImage.state['sid']}/allow-chat", headers=_h(user_b["token"]))

    def test_empty_message_rejected(self, user_a):
        sid = TestChatImage.state["sid"]
        r = requests.post(f"{API}/swaps/{sid}/messages", json={"text": ""}, headers=_h(user_a["token"]))
        assert r.status_code == 400

    def test_image_message_created(self, user_a):
        sid = TestChatImage.state["sid"]
        img = _own_upload(user_a["token"])
        r = requests.post(f"{API}/swaps/{sid}/messages",
                          json={"text": "", "image_url": img},
                          headers=_h(user_a["token"]))
        assert r.status_code == 200, r.text
        m = r.json()["message"]
        # internal file URLs come back signed (?exp=&sig=); the path itself is unchanged
        assert m["image_url"].startswith(img + "?exp=")
        assert m["type"] == "image"

    def test_image_message_visible_in_detail(self, user_a):
        sid = TestChatImage.state["sid"]
        r = requests.get(f"{API}/swaps/{sid}", headers=_h(user_a["token"]))
        assert r.status_code == 200
        msgs = r.json()["messages"]
        img_msgs = [m for m in msgs if m.get("image_url")]
        assert len(img_msgs) >= 1
        assert img_msgs[-1]["type"] == "image"


# ------------------------------------------------------------------ USER REVIEWS PAYLOAD
class TestUserReviews:
    def test_reviews_shape(self, user_a):
        """Self-contained: create user_c, run a mini swap A<->C, complete + rate C, verify /users/{c} returns reviews."""
        email = f"TEST_rev_{uuid.uuid4().hex[:8]}@bookloop.com"
        rc = requests.post(f"{API}/auth/register/dev-instant", json={"email": email, "password": "test1234", "name": "TEST Reviewee"})
        assert rc.status_code == 200
        uc = rc.json()
        tc, ucid = uc["session_token"], uc["user"]["user_id"]
        # give each side a book
        ba = requests.post(f"{API}/books", json={"title": "TEST Rev Book A", "author": "x"}, headers=_h(user_a["token"])).json()["book"]["id"]
        bc = requests.post(f"{API}/books", json={"title": "TEST Rev Book C", "author": "x"}, headers=_h(tc)).json()["book"]["id"]
        # A requests swap with C
        sid = requests.post(f"{API}/swaps", json={"receiver_id": ucid, "message": "hi"}, headers=_h(user_a["token"])).json()["swap"]["id"]
        requests.post(f"{API}/swaps/{sid}/propose", json={"offered_book_id": ba, "requested_book_id": bc}, headers=_h(user_a["token"]))
        requests.post(f"{API}/swaps/{sid}/accept", headers=_h(tc))
        requests.post(f"{API}/swaps/{sid}/complete", headers=_h(user_a["token"]))
        requests.post(f"{API}/swaps/{sid}/complete", headers=_h(tc))
        rr = requests.post(f"{API}/swaps/{sid}/rate", json={"stars": 5, "review": "Amazing swap"}, headers=_h(user_a["token"]))
        assert rr.status_code == 200, rr.text
        # Now hit the user endpoint
        r = requests.get(f"{API}/users/{ucid}", headers=_h(user_a["token"]))
        assert r.status_code == 200
        u = r.json()["user"]
        assert "rating" in u and "rating_count" in u
        assert u["rating_count"] >= 1
        reviews = r.json()["reviews"]
        assert isinstance(reviews, list) and len(reviews) >= 1
        rev = reviews[0]
        for k in ("stars", "created_at", "rater_name"):
            assert k in rev
        assert rev["stars"] == 5


# ------------------------------------------------------------------ NOTIFICATIONS
class TestNotifications:
    def test_notifications(self, user_b):
        r = requests.get(f"{API}/notifications", headers=_h(user_b["token"]))
        assert r.status_code == 200
        d = r.json()
        for k in ("incoming_requests", "active_swaps", "to_rate", "total"):
            assert k in d
            assert isinstance(d[k], int)


# ------------------------------------------------------------------ SWAP GUARDS (state machine / permissions)
# Every test builds its own fresh users, books and swap, so tests are independent of each other
# and of the shared TestSwapFlow state.
def _new_user(label):
    email = f"TEST_{uuid.uuid4().hex[:10]}@bookloop.com"
    r = requests.post(f"{API}/auth/register/dev-instant", json={"email": email, "password": "test1234", "name": f"Guard {label}"})
    assert r.status_code == 200, r.text
    d = r.json()
    return {"token": d["session_token"], "id": d["user"]["user_id"]}


def _new_book(u, title, status="Available"):
    r = requests.post(f"{API}/books", json={"title": f"TEST {title}", "author": "T", "genre": "Fiction",
                                             "language": "English", "condition": "Good", "status": status},
                      headers=_h(u["token"]))
    assert r.status_code == 200, r.text
    return r.json()["book"]["id"]


def _set_book_status(u, book_id, status):
    # PUT replaces the whole book, so send the full body.
    r = requests.put(f"{API}/books/{book_id}", json={"title": "TEST changed", "author": "T", "genre": "Fiction",
                                                      "language": "English", "condition": "Good", "status": status},
                     headers=_h(u["token"]))
    assert r.status_code == 200, r.text


def _book_status(u, book_id):
    r = requests.get(f"{API}/books", headers=_h(u["token"]))
    assert r.status_code == 200, r.text
    return next(b["status"] for b in r.json()["books"] if b["id"] == book_id)


def _swap(u, path, sid, body=None, method="post"):
    return getattr(requests, method)(f"{API}/swaps/{sid}{path}", json=body, headers=_h(u["token"]))


def _swap_status(u, sid):
    r = requests.get(f"{API}/swaps/{sid}", headers=_h(u["token"]))
    assert r.status_code == 200, r.text
    return r.json()["swap"]["status"]


def _swaps_count(u):
    r = requests.get(f"{API}/auth/me", headers=_h(u["token"]))
    return r.json()["user"]["swaps_count"]


@pytest.fixture()
def world():
    """A (requester), B (receiver), C (outsider) with one book each and a fresh pending swap A->B."""
    a, b, c = _new_user("A"), _new_user("B"), _new_user("C")
    w = {"a": a, "b": b, "c": c,
         "ba": _new_book(a, "A book"), "bb": _new_book(b, "B book"), "bc": _new_book(c, "C book")}
    r = requests.post(f"{API}/swaps", json={"receiver_id": b["id"]}, headers=_h(a["token"]))
    assert r.status_code == 200, r.text
    w["sid"] = r.json()["swap"]["id"]
    return w


def _propose(w, proposer="a", offered=None, requested=None):
    p, o = (w["a"], w["b"]) if proposer == "a" else (w["b"], w["a"])
    offered = offered or w[f"b{proposer}"]
    requested = requested or (w["bb"] if proposer == "a" else w["ba"])
    return _swap(p, "/propose", w["sid"], {"offered_book_id": offered, "requested_book_id": requested})


def _to_active(w):
    assert _propose(w).status_code == 200
    assert _swap(w["b"], "/accept", w["sid"]).status_code == 200
    assert _swap_status(w["a"], w["sid"]) == "active"


def _to_completed(w):
    _to_active(w)
    assert _swap(w["a"], "/complete", w["sid"]).status_code == 200
    assert _swap(w["b"], "/complete", w["sid"]).status_code == 200
    assert _swap_status(w["a"], w["sid"]) == "completed"


class TestSwapGuards:
    # ---- accept
    def test_cannot_accept_own_proposal(self, world):
        w = world
        assert _propose(w).status_code == 200
        r = _swap(w["a"], "/accept", w["sid"])
        assert r.status_code == 403
        assert _swap_status(w["a"], w["sid"]) == "pending"
        assert _book_status(w["a"], w["ba"]) == "Available"

    def test_accept_fails_without_proposal(self, world):
        r = _swap(world["b"], "/accept", world["sid"])
        assert r.status_code == 400
        assert _swap_status(world["a"], world["sid"]) == "pending"

    def test_accept_reserves_both_books(self, world):
        w = world
        _to_active(w)
        assert _book_status(w["a"], w["ba"]) == "Reserved"
        assert _book_status(w["b"], w["bb"]) == "Reserved"

    def test_accept_requires_books_still_available(self, world):
        w = world
        assert _propose(w).status_code == 200
        _set_book_status(w["a"], w["ba"], "Swapped")  # book leaves the pool after the proposal
        r = _swap(w["b"], "/accept", w["sid"])
        assert r.status_code == 409
        assert _swap_status(w["a"], w["sid"]) == "pending"

    def test_accept_only_in_pending(self, world):
        w = world
        _to_active(w)
        assert _swap(w["b"], "/accept", w["sid"]).status_code == 409

    def test_two_swaps_cannot_both_claim_the_same_book(self, world):
        from concurrent.futures import ThreadPoolExecutor
        w = world
        d = _new_user("D")
        bd = _new_book(d, "D book")
        sid2 = requests.post(f"{API}/swaps", json={"receiver_id": w["a"]["id"]}, headers=_h(d["token"])).json()["swap"]["id"]
        # Swap 1: A offers ba for bb.  Swap 2: D offers bd for ba.  Both proposals are valid while pending.
        assert _propose(w).status_code == 200
        assert _swap(d, "/propose", sid2, {"offered_book_id": bd, "requested_book_id": w["ba"]}).status_code == 200
        jobs = [(w["b"], w["sid"]), (w["a"], sid2)]  # B accepts swap 1, A accepts swap 2 - both want ba
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(lambda j: _swap(j[0], "/accept", j[1]).status_code, jobs))
        assert sorted(codes) == [200, 409], codes
        loser_sid, loser_books = (sid2, [bd]) if codes[0] == 200 else (w["sid"], [w["bb"]])
        loser_user = d if loser_sid == sid2 else w["b"]
        assert _swap_status(w["a"] if loser_sid == w["sid"] else d, loser_sid) == "pending"
        assert _book_status(w["a"], w["ba"]) == "Reserved"
        # the loser's other book was rolled back, not left reserved
        assert _book_status(loser_user, loser_books[0]) == "Available"

    # ---- propose
    def test_cannot_propose_book_you_do_not_own(self, world):
        w = world
        # B's book offered by A, and C's book offered by A
        assert _propose(w, offered=w["bb"]).status_code == 400
        assert _propose(w, offered=w["bc"]).status_code == 400
        assert _propose(w, offered="book_doesnotexist").status_code == 400

    def test_requested_book_must_belong_to_other_participant(self, world):
        w = world
        assert _propose(w, requested=w["ba"]).status_code == 400  # my own book
        assert _propose(w, requested=w["bc"]).status_code == 400  # outsider's book
        assert _propose(w, requested="book_doesnotexist").status_code == 400

    def test_propose_requires_both_books_available(self, world):
        w = world
        _set_book_status(w["a"], w["ba"], "Reserved")
        assert _propose(w).status_code == 400
        _set_book_status(w["a"], w["ba"], "Available")
        _set_book_status(w["b"], w["bb"], "Reserved")
        assert _propose(w).status_code == 400
        _set_book_status(w["b"], w["bb"], "Available")
        assert _propose(w).status_code == 200

    def test_propose_rejected_leaves_no_proposal(self, world):
        w = world
        assert _propose(w, offered=w["bb"]).status_code == 400
        d = requests.get(f"{API}/swaps/{w['sid']}", headers=_h(w["a"]["token"])).json()
        assert d["swap"]["active_proposal"] is None

    def test_propose_only_while_pending(self, world):
        w = world
        _to_active(w)
        assert _propose(w, "b").status_code == 409
        assert _swap_status(w["a"], w["sid"]) == "active"  # not demoted back to pending

    def test_propose_cannot_revive_declined_swap(self, world):
        w = world
        assert _swap(w["b"], "/decline", w["sid"]).status_code == 200
        assert _propose(w).status_code == 409
        assert _swap_status(w["a"], w["sid"]) == "declined"

    # ---- decline
    def test_decline_own_proposal_forbidden(self, world):
        w = world
        assert _propose(w).status_code == 200
        assert _swap(w["a"], "/decline", w["sid"]).status_code == 403
        assert _swap_status(w["a"], w["sid"]) == "pending"

    def test_decline_pending_by_receiver_ok(self, world):
        w = world
        assert _propose(w).status_code == 200
        assert _swap(w["b"], "/decline", w["sid"]).status_code == 200
        assert _swap_status(w["a"], w["sid"]) == "declined"

    def test_decline_fails_in_wrong_state(self, world):
        w = world
        _to_active(w)
        assert _swap(w["b"], "/decline", w["sid"]).status_code == 409
        assert _swap_status(w["a"], w["sid"]) == "active"
        # books must stay reserved after the rejected decline
        assert _book_status(w["a"], w["ba"]) == "Reserved"

    def test_decline_fails_when_already_declined(self, world):
        w = world
        assert _swap(w["b"], "/decline", w["sid"]).status_code == 200
        assert _swap(w["b"], "/decline", w["sid"]).status_code == 409

    def test_decline_fails_on_completed(self, world):
        w = world
        _to_completed(w)
        assert _swap(w["b"], "/decline", w["sid"]).status_code == 409
        assert _swap_status(w["a"], w["sid"]) == "completed"

    # ---- cancel
    def test_cancel_pending_and_active_ok(self, world):
        w = world
        assert _swap(w["a"], "/cancel", w["sid"]).status_code == 200
        assert _swap_status(w["a"], w["sid"]) == "cancelled"

    def test_cancel_active_releases_books(self, world):
        w = world
        _to_active(w)
        assert _swap(w["a"], "/cancel", w["sid"]).status_code == 200
        assert _book_status(w["a"], w["ba"]) == "Available"
        assert _book_status(w["b"], w["bb"]) == "Available"

    def test_cancel_pending_does_not_release_books_reserved_elsewhere(self, world):
        w = world
        assert _propose(w).status_code == 200  # books still Available, not reserved
        _set_book_status(w["a"], w["ba"], "Reserved")  # simulate another swap reserving it
        assert _swap(w["a"], "/cancel", w["sid"]).status_code == 200
        assert _book_status(w["a"], w["ba"]) == "Reserved"

    def test_cancel_fails_in_wrong_state(self, world):
        w = world
        assert _swap(w["a"], "/cancel", w["sid"]).status_code == 200
        assert _swap(w["a"], "/cancel", w["sid"]).status_code == 409  # already cancelled
        assert _swap(w["b"], "/decline", w["sid"]).status_code == 409  # not pending anymore

    def test_cancel_fails_on_declined(self, world):
        w = world
        assert _swap(w["b"], "/decline", w["sid"]).status_code == 200
        assert _swap(w["a"], "/cancel", w["sid"]).status_code == 409

    def test_cancel_fails_on_completed(self, world):
        w = world
        _to_completed(w)
        assert _swap(w["a"], "/cancel", w["sid"]).status_code == 409
        assert _swap_status(w["a"], w["sid"]) == "completed"
        assert _book_status(w["a"], w["ba"]) == "Swapped"  # not released back to Available

    # ---- complete
    def test_complete_fails_when_pending(self, world):
        w = world
        assert _swap(w["a"], "/complete", w["sid"]).status_code == 409
        assert _swap_status(w["a"], w["sid"]) == "pending"

    def test_complete_fails_when_cancelled(self, world):
        w = world
        assert _swap(w["a"], "/cancel", w["sid"]).status_code == 200
        assert _swap(w["a"], "/complete", w["sid"]).status_code == 409

    def test_complete_needs_both_then_marks_books_swapped(self, world):
        w = world
        _to_active(w)
        assert _swap(w["a"], "/complete", w["sid"]).status_code == 200
        assert _swap_status(w["a"], w["sid"]) == "active"
        assert _swap(w["b"], "/complete", w["sid"]).status_code == 200
        assert _swap_status(w["a"], w["sid"]) == "completed"
        assert _book_status(w["a"], w["ba"]) == "Swapped"
        assert _book_status(w["b"], w["bb"]) == "Swapped"

    def test_duplicate_complete_does_not_double_increment(self, world):
        w = world
        _to_active(w)
        assert _swap(w["a"], "/complete", w["sid"]).status_code == 200
        assert _swap(w["a"], "/complete", w["sid"]).status_code == 200  # repeat while still active: harmless
        assert _swap_status(w["a"], w["sid"]) == "active"
        assert _swaps_count(w["a"]) == 0 and _swaps_count(w["b"]) == 0
        assert _swap(w["b"], "/complete", w["sid"]).status_code == 200
        assert _swap_status(w["a"], w["sid"]) == "completed"
        assert _swaps_count(w["a"]) == 1 and _swaps_count(w["b"]) == 1
        # any further complete is rejected and changes nothing
        assert _swap(w["a"], "/complete", w["sid"]).status_code == 409
        assert _swap(w["b"], "/complete", w["sid"]).status_code == 409
        assert _swaps_count(w["a"]) == 1 and _swaps_count(w["b"]) == 1

    def test_concurrent_complete_increments_once(self, world):
        from concurrent.futures import ThreadPoolExecutor
        w = world
        _to_active(w)
        callers = [w["a"], w["b"], w["a"], w["b"]]
        with ThreadPoolExecutor(max_workers=4) as pool:
            codes = list(pool.map(lambda u: _swap(u, "/complete", w["sid"]).status_code, callers))
        assert all(c in (200, 409) for c in codes), codes
        assert _swap_status(w["a"], w["sid"]) == "completed"
        assert _swaps_count(w["a"]) == 1 and _swaps_count(w["b"]) == 1

    # ---- rating
    def test_rate_requires_completed(self, world):
        w = world
        _to_active(w)
        assert _swap(w["a"], "/rate", w["sid"], {"stars": 5}).status_code == 400

    def test_duplicate_rating_rejected(self, world):
        w = world
        _to_completed(w)
        assert _swap(w["a"], "/rate", w["sid"], {"stars": 5, "review": "ok"}).status_code == 200
        assert _swap(w["a"], "/rate", w["sid"], {"stars": 1}).status_code == 400
        u = requests.get(f"{API}/users/{w['b']['id']}", headers=_h(w["a"]["token"])).json()
        assert u["user"]["rating_count"] == 1
        assert len(u["reviews"]) == 1 and u["reviews"][0]["stars"] == 5

    def test_concurrent_duplicate_rating_creates_one(self, world):
        from concurrent.futures import ThreadPoolExecutor
        w = world
        _to_completed(w)
        with ThreadPoolExecutor(max_workers=5) as pool:
            codes = list(pool.map(lambda _: _swap(w["a"], "/rate", w["sid"], {"stars": 4}).status_code, range(5)))
        assert codes.count(200) == 1, codes
        assert all(c in (200, 400) for c in codes), codes
        u = requests.get(f"{API}/users/{w['b']['id']}", headers=_h(w["a"]["token"])).json()
        assert len(u["reviews"]) == 1 and u["user"]["rating_count"] == 1

    # ---- non-participant
    def test_non_participant_cannot_access_or_modify(self, world):
        w = world
        c, sid = w["c"], w["sid"]
        assert _propose(w).status_code == 200
        assert _swap(c, "", sid, method="get").status_code == 404
        assert _swap(c, "/messages", sid, {"text": "hi"}).status_code == 404
        assert _swap(c, "/propose", sid, {"offered_book_id": w["bc"], "requested_book_id": w["bb"]}).status_code == 404
        for action in ("accept", "decline", "cancel", "complete"):
            assert _swap(c, f"/{action}", sid).status_code == 404, action
        assert _swap(c, "/rate", sid, {"stars": 1}).status_code == 404
        # nothing changed
        assert _swap_status(w["a"], sid) == "pending"
        assert _book_status(w["c"], w["bc"]) == "Available"

    def test_non_participant_cannot_touch_active_or_completed_swap(self, world):
        w = world
        _to_completed(w)
        c, sid = w["c"], w["sid"]
        assert _swap(c, "/rate", sid, {"stars": 1}).status_code == 404
        assert _swap(c, "/cancel", sid).status_code == 404
        assert _swap_status(w["a"], sid) == "completed"
        u = requests.get(f"{API}/users/{w['a']['id']}", headers=_h(w["a"]["token"])).json()
        assert len(u["reviews"]) == 0

    def test_unauthenticated_is_rejected(self, world):
        r = requests.post(f"{API}/swaps/{world['sid']}/accept")
        assert r.status_code == 401

    # ---- messages
    def test_messages_locked_while_pending_then_allowed_when_active(self, world):
        w = world
        for u in (w["a"], w["b"]):
            r = _swap(u, "/messages", w["sid"], {"text": "pending hi"})
            assert r.status_code == 403 and r.json()["detail"]["code"] == "chat_locked"
        _to_active(w)
        assert _swap(w["b"], "/messages", w["sid"], {"text": "active hi"}).status_code == 200
        assert _swap(w["a"], "/messages", w["sid"], {"text": "active hi back"}).status_code == 200

    def test_messages_blocked_when_declined(self, world):
        w = world
        assert _swap(w["b"], "/decline", w["sid"]).status_code == 200
        assert _swap(w["a"], "/messages", w["sid"], {"text": "hello?"}).status_code == 409
        assert _swap(w["b"], "/messages", w["sid"], {"image_url": "/api/files/x.jpg"}).status_code == 409
        msgs = requests.get(f"{API}/swaps/{w['sid']}", headers=_h(w["a"]["token"])).json()["messages"]
        assert not any(m.get("text") == "hello?" for m in msgs)

    def test_messages_blocked_when_cancelled(self, world):
        w = world
        assert _swap(w["a"], "/cancel", w["sid"]).status_code == 200
        assert _swap(w["b"], "/messages", w["sid"], {"text": "hello?"}).status_code == 409

    def test_empty_message_still_400_when_allowed(self, world):
        _to_active(world)
        assert _swap(world["a"], "/messages", world["sid"], {"text": "   "}).status_code == 400


# ------------------------------------------------------------------ BOOK UPDATE / PRIVACY / UPLOAD / SEARCH HARDENING
class TestBookUpdateGuards:
    def test_partial_update_keeps_other_fields(self, world):
        w = world
        r = requests.put(f"{API}/books/{w['ba']}", json={"title": "Renamed"}, headers=_h(w["a"]["token"]))
        assert r.status_code == 200
        b = r.json()["book"]
        assert b["title"] == "Renamed"
        assert b["genre"] == "Fiction" and b["condition"] == "Good" and b["status"] == "Available"

    def test_partial_update_does_not_reset_status(self, world):
        w = world
        _set_book_status(w["a"], w["ba"], "Swapped")
        r = requests.put(f"{API}/books/{w['ba']}", json={"title": "Still swapped"}, headers=_h(w["a"]["token"]))
        assert r.status_code == 200 and r.json()["book"]["status"] == "Swapped"

    def test_invalid_status_and_empty_title_rejected(self, world):
        w = world
        h = _h(w["a"]["token"])
        assert requests.put(f"{API}/books/{w['ba']}", json={"status": "Gone"}, headers=h).status_code == 400
        assert requests.put(f"{API}/books/{w['ba']}", json={"title": "  "}, headers=h).status_code == 400

    def test_cannot_change_status_of_book_in_active_swap(self, world):
        w = world
        _to_active(w)
        h = _h(w["a"]["token"])
        r = requests.put(f"{API}/books/{w['ba']}", json={"status": "Available"}, headers=h)
        assert r.status_code == 409
        assert _book_status(w["a"], w["ba"]) == "Reserved"
        # a stale full-form save that echoes the current status, or edits other fields, still works
        assert requests.put(f"{API}/books/{w['ba']}", json={"title": "x", "status": "Reserved"}, headers=h).status_code == 200

    def test_cannot_delete_book_in_active_swap(self, world):
        w = world
        _to_active(w)
        r = requests.delete(f"{API}/books/{w['ba']}", headers=_h(w["a"]["token"]))
        assert r.status_code == 409
        assert _book_status(w["a"], w["ba"]) == "Reserved"

    def test_status_editable_again_after_cancel(self, world):
        w = world
        _to_active(w)
        assert _swap(w["a"], "/cancel", w["sid"]).status_code == 200
        _set_book_status(w["a"], w["ba"], "Swapped")
        assert _book_status(w["a"], w["ba"]) == "Swapped"

    def test_other_users_cannot_edit_book(self, world):
        w = world
        r = requests.put(f"{API}/books/{w['ba']}", json={"title": "hacked"}, headers=_h(w["c"]["token"]))
        assert r.status_code == 404


class TestPrivacy:
    PRIVATE = ("email", "lat", "lng")

    def _no_private(self, obj):
        for k in self.PRIVATE:
            assert k not in obj, f"{k} leaked in {list(obj)[:6]}"

    def test_self_endpoints_keep_private_fields(self, world):
        w = world
        me = requests.get(f"{API}/auth/me", headers=_h(w["a"]["token"])).json()["user"]
        assert "@" in me["email"]
        assert "lat" in me and "lng" in me
        own = requests.get(f"{API}/users/{w['a']['id']}", headers=_h(w["a"]["token"])).json()["user"]
        assert "email" in own

    def test_other_user_profile_hides_email_and_coordinates(self, world):
        w = world
        u = requests.get(f"{API}/users/{w['b']['id']}", headers=_h(w["a"]["token"])).json()["user"]
        self._no_private(u)
        assert "distance_km" in u and "neighborhood" in u

    def test_discover_people_hides_private_fields(self, world):
        w = world
        people = requests.get(f"{API}/discover/people", headers=_h(w["a"]["token"])).json()["people"]
        assert people
        for p in people:
            self._no_private(p)

    def test_swap_and_book_owner_hide_private_fields(self, world):
        w = world
        d = requests.get(f"{API}/swaps/{w['sid']}", headers=_h(w["a"]["token"])).json()
        self._no_private(d["swap"]["other_user"])
        lst = requests.get(f"{API}/swaps", headers=_h(w["a"]["token"])).json()
        for bucket in lst.values():
            for s in bucket:
                self._no_private(s["other_user"])
        bd = requests.get(f"{API}/books/detail/{w['bb']}", headers=_h(w["a"]["token"])).json()
        self._no_private(bd["owner"])


def _messages(u, sid):
    return requests.get(f"{API}/swaps/{sid}", headers=_h(u["token"])).json()["messages"]


def _swap_row(u, sid):
    d = requests.get(f"{API}/swaps", headers=_h(u["token"])).json()
    return next(s for bucket in d.values() for s in bucket if s["id"] == sid)


class TestMessageKeys:
    """System messages are stored as i18n key + params (with English text as fallback)."""

    def test_swap_creation_message_has_key(self, world):
        w = world
        sysmsgs = [m for m in _messages(w["a"], w["sid"]) if m["type"] == "system"]
        m = sysmsgs[0]
        assert m["key"] == "swapChat.sys.interested"
        assert m["params"] == {"name": "Guard A"}
        assert "interested" in m["text"]  # English fallback kept

    def test_lifecycle_keys(self, world):
        w = world
        assert _propose(w).status_code == 200
        assert _swap(w["b"], "/accept", w["sid"]).status_code == 200
        assert _swap(w["a"], "/complete", w["sid"]).status_code == 200
        assert _swap(w["b"], "/complete", w["sid"]).status_code == 200
        keys = [m["key"] for m in _messages(w["a"], w["sid"])]
        for k in ("swapChat.sys.proposed", "swapChat.sys.confirmed", "swapChat.sys.markedComplete",
                  "swapChat.sys.completed", "swapChat.sys.badgeUnlocked"):
            assert k in keys, (k, keys)
        badge = next(m for m in _messages(w["a"], w["sid"]) if m["key"] == "swapChat.sys.badgeUnlocked")
        assert badge["params"]["badge"] == "first_loop"  # an id, translated on the client
        prop = next(m for m in _messages(w["a"], w["sid"]) if m["key"] == "swapChat.sys.proposed")
        assert prop["params"] == {"offered": "TEST A book", "requested": "TEST B book"}

    def test_decline_and_cancel_keys(self, world):
        w = world
        assert _swap(w["b"], "/decline", w["sid"]).status_code == 200
        m = _messages(w["a"], w["sid"])[-1]
        assert m["key"] == "swapChat.sys.declined" and m["params"] == {"name": "Guard B"}

    def test_last_message_key_in_swap_list(self, world):
        w = world
        assert _swap_row(w["a"], w["sid"])["last_message_key"] == "swapChat.sys.interested"
        assert _swap(w["b"], "/allow-chat", w["sid"]).status_code == 200
        assert _swap_row(w["a"], w["sid"])["last_message_key"] == "swapChat.sys.chatAccepted"
        assert _swap(w["a"], "/messages", w["sid"], {"text": "hello"}).status_code == 200
        row = _swap_row(w["a"], w["sid"])
        assert row["last_message"] == "hello" and row["last_message_key"] is None  # user text is never a key
        assert _swap(w["a"], "/messages", w["sid"], {"image_url": _own_upload(w["a"]["token"])}).status_code == 200
        row = _swap_row(w["a"], w["sid"])
        assert row["last_message_key"] == "swapChat.sys.photo"

    def test_user_text_message_has_no_key(self, world):
        w = world
        assert _swap(w["b"], "/allow-chat", w["sid"]).status_code == 200
        r = _swap(w["a"], "/messages", w["sid"], {"text": "ciao"}).json()["message"]
        assert r["key"] is None and r["text"] == "ciao"

    def test_locales_define_every_backend_key(self):
        import json
        from pathlib import Path
        d = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n" / "locales"
        if not d.exists():
            pytest.skip("frontend locales not found")
        sys_keys = ["interested", "proposed", "confirmed", "declined", "cancelled", "markedComplete",
                    "completed", "badgeUnlocked", "photo", "chatAccepted"]
        badge_ids = ["first_loop", "regular", "bookworm", "legend"]

        def leaves(o, p=""):
            if isinstance(o, dict):
                for k, v in o.items():
                    yield from leaves(v, f"{p}{k}.")
            else:
                yield p[:-1]

        sets = {}
        for lang in ("en", "uz", "ru", "it", "ar"):
            loc = json.loads((d / f"{lang}.json").read_text(encoding="utf-8"))
            for k in sys_keys:
                assert loc["swapChat"]["sys"][k].strip(), (lang, k)
            for b in badge_ids:
                assert loc["badges"]["names"][b].strip(), (lang, b)
            # shown in the NEW language while switching between LTR and RTL
            assert loc["languageSelect"]["switching"].strip(), (lang, "languageSelect.switching")
            # Plural keys carry each language's own CLDR categories (ru adds _few/_many, ar _zero/_two...),
            # so they are compared by base key (frontend/scripts/i18n-audit.js checks the categories).
            sets[lang] = {re.sub(r"_(zero|one|two|few|many|other)$", "", k) for k in leaves(loc)}
        assert all(s == sets["en"] for s in sets.values()), {
            l: sorted(sets["en"] ^ s)[:5] for l, s in sets.items() if s != sets["en"]}


class TestPasswordLimits:
    def _register(self, password):
        return requests.post(f"{API}/auth/register/dev-instant",
                             json={"email": f"TEST_{uuid.uuid4().hex[:10]}@bookloop.com", "password": password, "name": "Pw"})

    def test_72_byte_password_works_and_logs_in(self):
        pw = "p" * 72
        email = f"TEST_{uuid.uuid4().hex[:10]}@bookloop.com"
        assert requests.post(f"{API}/auth/register/dev-instant", json={"email": email, "password": pw, "name": "Pw"}).status_code == 200
        assert requests.post(f"{API}/auth/login", json={"email": email, "password": pw}).status_code == 200

    def test_over_72_bytes_is_a_clean_400_not_a_500(self):
        assert self._register("p" * 73).status_code == 400
        assert self._register("p" * 500).status_code == 400

    def test_limit_is_in_bytes_not_characters(self):
        assert self._register("é" * 37).status_code == 400  # 37 chars = 74 bytes
        assert self._register("é" * 36).status_code == 200  # 72 bytes

    def test_overlong_password_login_is_401_not_500(self):
        r = requests.post(f"{API}/auth/login", json={"email": "test@bookloop.com", "password": "p" * 200})
        assert r.status_code == 401


class TestPreferredLanguage:
    def test_update_valid_and_invalid(self, world):
        h = _h(world["a"]["token"])
        assert requests.put(f"{API}/users/me", json={"preferred_language": "xx"}, headers=h).status_code == 400
        r = requests.put(f"{API}/users/me", json={"preferred_language": "ar"}, headers=h)
        assert r.status_code == 200 and r.json()["user"]["preferred_language"] == "ar"
        assert requests.get(f"{API}/auth/me", headers=h).json()["user"]["preferred_language"] == "ar"

    def test_register_persists_chosen_language(self):
        email = f"TEST_{uuid.uuid4().hex[:10]}@bookloop.com"
        r = requests.post(f"{API}/auth/register/dev-instant",
                          json={"email": email, "password": "test1234", "name": "Lang", "preferred_language": "it"})
        assert r.status_code == 200 and r.json()["user"]["preferred_language"] == "it"
        login = requests.post(f"{API}/auth/login", json={"email": email, "password": "test1234"})
        assert login.json()["user"]["preferred_language"] == "it"

    def test_register_ignores_unknown_language(self):
        email = f"TEST_{uuid.uuid4().hex[:10]}@bookloop.com"
        r = requests.post(f"{API}/auth/register/dev-instant",
                          json={"email": email, "password": "test1234", "name": "Lang", "preferred_language": "xx"})
        assert r.status_code == 200 and r.json()["user"]["preferred_language"] is None

    def test_register_without_language_still_works(self):
        email = f"TEST_{uuid.uuid4().hex[:10]}@bookloop.com"
        r = requests.post(f"{API}/auth/register/dev-instant", json={"email": email, "password": "test1234", "name": "Lang"})
        assert r.status_code == 200 and r.json()["user"]["preferred_language"] is None


# ------------------------------------------------------------------ REGISTRATION OTP
def _otp_db():
    """The one place in this file that talks to Mongo directly rather than only through the HTTP
    API — used to time-travel OTP/cooldown timestamps instead of sleeping for real minutes."""
    return MongoClient(os.environ.get("MONGO_URL") or "mongodb://localhost:27017")[os.environ.get("DB_NAME") or "bookloop_test"]


class TestRegistrationOtp:
    """The real email/OTP/password registration flow, end to end against a live backend, including a
    REAL send through Resend on every request-otp/resend-otp call (this class reads the code back via
    `debug_otp`, only present when OTP_DEBUG_MODE=true — see module docstring — but that's purely a
    read-back shortcut, not a bypass of sending). Addresses use Resend's `delivered@resend.dev` test
    domain with unique "+" sub-addresses: without a verified sending domain, Resend's API rejects
    arbitrary "to" addresses (e.g. plain @bookloop.com ones), but always simulates a successful
    delivery to any address under resend.dev — see backend/.env.example."""

    def _email(self):
        return f"delivered+otp_{uuid.uuid4().hex[:10]}@resend.dev"

    def _request_otp(self, email, name="Otp Test"):
        # This class sends a REAL email through Resend on every call. A 502 here means the backend's
        # own EmailSendError path (Resend rejected/errored the send) — never a backend logic bug, so
        # one retry absorbs an occasional transient rate-limit from firing ~20 real sends in a row
        # without masking a real failure (a second consecutive 502 still fails the test loudly).
        for attempt in range(2):
            r = requests.post(f"{API}/auth/register/request-otp", json={"name": name, "email": email, "preferred_language": "en"})
            if r.status_code == 200 or attempt == 1:
                break
            time.sleep(2)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "debug_otp" in d, "backend must be started with OTP_DEBUG_MODE=true to run these tests"
        return d["debug_otp"]

    def _verified_token(self, email):
        code = self._request_otp(email)
        r = requests.post(f"{API}/auth/register/verify-otp", json={"email": email, "code": code})
        assert r.status_code == 200, r.text
        return r.json()["verification_token"]

    # --- OTP correctness -----------------------------------------------------------------------
    def test_valid_otp_verifies(self):
        email = self._email()
        code = self._request_otp(email)
        r = requests.post(f"{API}/auth/register/verify-otp", json={"email": email, "code": code})
        assert r.status_code == 200, r.text
        assert r.json()["verification_token"]

    def test_incorrect_otp_rejected(self):
        email = self._email()
        self._request_otp(email)
        r = requests.post(f"{API}/auth/register/verify-otp", json={"email": email, "code": "000000"})
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "invalid_otp"

    def test_expired_otp_rejected(self):
        email = self._email()
        code = self._request_otp(email)
        _otp_db().pending_registrations.update_one(
            {"email": email.lower()}, {"$set": {"otp_expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}}
        )
        r = requests.post(f"{API}/auth/register/verify-otp", json={"email": email, "code": code})
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "otp_expired"

    def test_otp_cannot_be_reused(self):
        email = self._email()
        code = self._request_otp(email)
        first = requests.post(f"{API}/auth/register/verify-otp", json={"email": email, "code": code})
        assert first.status_code == 200
        second = requests.post(f"{API}/auth/register/verify-otp", json={"email": email, "code": code})
        assert second.status_code == 400
        assert second.json()["detail"]["code"] == "otp_expired"  # the OTP hash was cleared on first use

    # --- resend + rate limiting -----------------------------------------------------------------
    def test_resend_invalidates_previous_otp(self):
        email = self._email()
        old_code = self._request_otp(email)
        # Bypass the resend cooldown by time-travelling last_otp_sent_at — the cooldown itself is
        # covered separately by test_resend_rate_limited below.
        _otp_db().pending_registrations.update_one(
            {"email": email.lower()}, {"$set": {"last_otp_sent_at": datetime.now(timezone.utc) - timedelta(seconds=61)}}
        )
        r = requests.post(f"{API}/auth/register/resend-otp", json={"email": email})
        assert r.status_code == 200, r.text
        new_code = r.json()["debug_otp"]
        assert new_code != old_code
        assert requests.post(f"{API}/auth/register/verify-otp", json={"email": email, "code": old_code}).status_code == 400
        assert requests.post(f"{API}/auth/register/verify-otp", json={"email": email, "code": new_code}).status_code == 200

    def test_resend_rate_limited(self):
        email = self._email()
        self._request_otp(email)
        r = requests.post(f"{API}/auth/register/resend-otp", json={"email": email})
        assert r.status_code == 429
        assert r.json()["detail"]["code"] == "rate_limited"

    def test_too_many_incorrect_attempts_locks_out(self):
        email = self._email()
        self._request_otp(email)
        for _ in range(5):  # OTP_MAX_ATTEMPTS in server.py
            r = requests.post(f"{API}/auth/register/verify-otp", json={"email": email, "code": "000000"})
            assert r.status_code == 400
            assert r.json()["detail"]["code"] == "invalid_otp"
        r = requests.post(f"{API}/auth/register/verify-otp", json={"email": email, "code": "000000"})
        assert r.status_code == 429
        assert r.json()["detail"]["code"] == "too_many_attempts"

    # --- can't skip verification ----------------------------------------------------------------
    def test_complete_without_verification_rejected(self):
        email = self._email()
        r = requests.post(
            f"{API}/auth/register/complete",
            json={"email": email, "password": "Str0ng!Pass", "verification_token": "not-a-real-token"},
        )
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "verification_expired"

    # --- password policy (server-side; the frontend's checklist is UX only) --------------------
    def test_weak_password_rejected(self):
        email = self._email()
        token = self._verified_token(email)
        r = requests.post(f"{API}/auth/register/complete", json={"email": email, "password": "weak", "verification_token": token})
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "weak_password"

    def test_password_without_uppercase_rejected(self):
        email = self._email()
        token = self._verified_token(email)
        r = requests.post(f"{API}/auth/register/complete", json={"email": email, "password": "lowercase1!", "verification_token": token})
        assert r.status_code == 400
        assert "no_upper" in r.json()["detail"]["violations"]

    def test_password_without_lowercase_rejected(self):
        email = self._email()
        token = self._verified_token(email)
        r = requests.post(f"{API}/auth/register/complete", json={"email": email, "password": "UPPERCASE1!", "verification_token": token})
        assert r.status_code == 400
        assert "no_lower" in r.json()["detail"]["violations"]

    def test_password_without_number_rejected(self):
        email = self._email()
        token = self._verified_token(email)
        r = requests.post(f"{API}/auth/register/complete", json={"email": email, "password": "NoNumber!!", "verification_token": token})
        assert r.status_code == 400
        assert "no_number" in r.json()["detail"]["violations"]

    def test_password_without_special_char_rejected(self):
        email = self._email()
        token = self._verified_token(email)
        r = requests.post(f"{API}/auth/register/complete", json={"email": email, "password": "NoSpecial123", "verification_token": token})
        assert r.status_code == 400
        assert "no_special" in r.json()["detail"]["violations"]

    def test_password_under_8_chars_rejected(self):
        email = self._email()
        token = self._verified_token(email)
        r = requests.post(f"{API}/auth/register/complete", json={"email": email, "password": "Sh0rt!", "verification_token": token})
        assert r.status_code == 400
        assert "too_short" in r.json()["detail"]["violations"]

    def test_valid_password_accepted_and_completes_registration(self):
        email = self._email()
        token = self._verified_token(email)
        r = requests.post(f"{API}/auth/register/complete", json={"email": email, "password": "Str0ng!Pass", "verification_token": token})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["session_token"]
        assert d["user"]["email"] == email.lower()

    # --- duplicates + onboarding state ----------------------------------------------------------
    def test_duplicate_account_prevented(self):
        email = self._email()
        token = self._verified_token(email)
        r1 = requests.post(f"{API}/auth/register/complete", json={"email": email, "password": "Str0ng!Pass", "verification_token": token})
        assert r1.status_code == 200, r1.text
        r2 = requests.post(f"{API}/auth/register/request-otp", json={"name": "Dup", "email": email})
        assert r2.status_code == 409
        assert r2.json()["detail"]["code"] == "email_registered"

    def test_first_time_account_has_onboarding_incomplete(self):
        email = self._email()
        token = self._verified_token(email)
        r = requests.post(f"{API}/auth/register/complete", json={"email": email, "password": "Str0ng!Pass", "verification_token": token})
        assert r.status_code == 200, r.text
        assert r.json()["user"]["onboarding_completed"] is False

    def test_completing_onboarding_persists(self):
        email = self._email()
        token = self._verified_token(email)
        reg = requests.post(
            f"{API}/auth/register/complete", json={"email": email, "password": "Str0ng!Pass", "verification_token": token}
        ).json()
        r = requests.put(f"{API}/users/me", json={"onboarding_completed": True}, headers=_h(reg["session_token"]))
        assert r.status_code == 200 and r.json()["user"]["onboarding_completed"] is True
        # Persists server-side, not just in that response: a fresh session sees it too.
        login = requests.post(f"{API}/auth/login", json={"email": email, "password": "Str0ng!Pass"})
        assert login.status_code == 200
        assert login.json()["user"]["onboarding_completed"] is True

    def test_existing_users_not_forced_through_onboarding(self, user_a):
        # user_a comes from /auth/register/dev-instant, which sets onboarding_completed=True (an
        # "already onboarded" account) — modeling a pre-existing user after backfill_onboarding_state.
        assert user_a["user"]["onboarding_completed"] is True


# ------------------------------------------------------------------ GOOGLE SIGN-IN (against a fake Google)
# These run only when the backend under test was started with a fake Google, e.g.:
#   GOOGLE_CLIENT_ID=test-client-id GOOGLE_CLIENT_SECRET=test-secret
#   GOOGLE_AUTH_URL=http://127.0.0.1:8099/auth GOOGLE_TOKEN_URL=http://127.0.0.1:8099/token
#   ALLOW_DEV_REDIRECTS=1   (the tests return to http://localhost / exp://, like local development)
#   uvicorn server:app --port 8002
# and the tests were started with FAKE_GOOGLE_PORT=8099 (the tests host the fake Google there).
FAKE_GOOGLE_PORT = os.environ.get("FAKE_GOOGLE_PORT")
TEST_CLIENT_ID, TEST_CLIENT_SECRET = "test-client-id", "test-secret"


def _b64(o):
    import base64, json
    return base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()


@pytest.fixture(scope="module")
def fake_google():
    if not FAKE_GOOGLE_PORT:
        pytest.skip("FAKE_GOOGLE_PORT not set (backend not pointed at a fake Google)")
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()).items()}
            code = form.get("code", "")
            ok = (form.get("client_secret") == TEST_CLIENT_SECRET and form.get("grant_type") == "authorization_code"
                  and form.get("client_id") == TEST_CLIENT_ID
                  and form.get("redirect_uri", "").endswith("/api/auth/google/callback"))
            if not ok or code == "denied":
                body, status = {"error": "invalid_grant"}, 400
            else:
                kind, _, rest = code.partition(":")
                email, _, name = rest.partition(":")
                # `sub` is Google's stable account id; "nosub" imitates a token without one and "alt" a
                # different Google account with the same address. example.com plays a Google Workspace
                # domain (`hd`); other non-Gmail addresses are consumer Google accounts without one.
                claims = {"iss": "https://accounts.google.com", "aud": TEST_CLIENT_ID, "email": email,
                          "sub": None if kind == "nosub" else (("gsub2-" if kind == "alt" else "gsub-") + email.lower()),
                          "email_verified": True, "name": name or "Google User",
                          "picture": "https://example.com/p.png", "exp": int(time.time()) + 3600}
                if email.lower().endswith("@example.com"):
                    claims["hd"] = "example.com"
                if kind == "badaud":
                    claims["aud"] = "someone-else"
                elif kind == "badiss":
                    claims["iss"] = "https://evil.example"
                elif kind == "unverified":
                    claims["email_verified"] = False
                elif kind == "expired":
                    claims["exp"] = int(time.time()) - 10
                body, status = {"access_token": "x", "id_token": f"{_b64({'alg': 'RS256'})}.{_b64(claims)}.sig"}, 200
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = HTTPServer(("127.0.0.1", int(FAKE_GOOGLE_PORT)), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


APP_REDIRECT = "http://localhost:8081/"  # accepted because the test backend runs with ALLOW_DEV_REDIRECTS=1


def _pkce():
    """(verifier, challenge) the way the app makes them (RFC 7636, S256)."""
    import base64, hashlib, secrets
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


# session_id / link_id -> the verifier of the attempt that produced it (what the real app keeps to itself).
_VERIFIERS: dict = {}


def _qs(loc):
    from urllib.parse import urlparse, parse_qs
    return {k: v[0] for k, v in parse_qs(urlparse(loc).query).items()}


def _google_start(redirect=APP_REDIRECT, pkce=None):
    """Returns (Location, query of the Google URL + `_verifier` / `_challenge` of this attempt)."""
    verifier, challenge = pkce or _pkce()
    r = requests.get(f"{API}/auth/google/start", params={"redirect": redirect, "code_challenge": challenge,
                                                         "code_challenge_method": "S256"}, allow_redirects=False)
    assert r.status_code == 302, r.text
    q = _qs(r.headers["location"])
    q.update(_verifier=verifier, _challenge=challenge)
    return r.headers["location"], q


def _google_callback(code, state, **extra):
    return requests.get(f"{API}/auth/google/callback", params={"code": code, "state": state, **extra},
                        allow_redirects=False)


def _google_login(email, prefix="ok", name="Gina Google", pkce=None):
    """Full happy path up to the app's return; returns (session_id or None, redirect Location)."""
    _, q = _google_start(pkce=pkce)
    cb = _google_callback(f"{prefix}:{email}:{name}", q["state"])
    assert cb.status_code == 302
    loc = cb.headers["location"]
    sid = _qs(loc).get("session_id")
    if sid:
        _VERIFIERS[sid] = q["_verifier"]
    return sid, loc


def _session(sid, verifier="__attempt__"):
    """POST /auth/session as the app does: with the verifier of the attempt that got this sid."""
    body = {"session_id": sid}
    v = _VERIFIERS.get(sid) if verifier == "__attempt__" else verifier
    if v is not None:
        body["code_verifier"] = v
    return requests.post(f"{API}/auth/session", json=body)


class TestGoogleLogin:
    def test_providers_reports_google_enabled(self, fake_google):
        assert requests.get(f"{API}/auth/providers").json()["google"] is True

    def test_start_redirects_to_google_with_our_client(self, fake_google):
        loc, q = _google_start()
        assert loc.startswith(f"http://127.0.0.1:{FAKE_GOOGLE_PORT}/auth?")
        assert q["client_id"] == TEST_CLIENT_ID and q["response_type"] == "code"
        assert "email" in q["scope"] and "openid" in q["scope"]
        assert q["redirect_uri"].endswith("/api/auth/google/callback") and len(q["state"]) >= 32
        assert "emergent" not in loc.lower()
        # Our app<->backend binding is not sent to Google.
        assert q["_challenge"] not in loc and q["_verifier"] not in loc

    def test_full_login_creates_user_and_session(self, fake_google):
        email = f"g_{uuid.uuid4().hex[:8]}@example.com"
        sid, loc = _google_login(email)
        assert loc.startswith(APP_REDIRECT + "?session_id=") and sid
        r = _session(sid)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["user"]["email"] == email and d["user"]["name"] == "Gina Google"
        assert d["user"]["avatar_url"] == "https://example.com/p.png"
        me = requests.get(f"{API}/auth/me", headers=_h(d["session_token"]))
        assert me.status_code == 200 and me.json()["user"]["email"] == email

    def test_session_id_is_single_use(self, fake_google):
        sid, _ = _google_login(f"g_{uuid.uuid4().hex[:8]}@example.com")
        assert _session(sid).status_code == 200
        assert _session(sid).status_code == 401

    def test_unknown_session_id_rejected(self, fake_google):
        assert _session("nope", _pkce()[0]).status_code == 401

    def test_second_google_login_reuses_account(self, fake_google):
        email = f"g_{uuid.uuid4().hex[:8]}@example.com"
        ids = []
        for _ in range(2):
            sid, _l = _google_login(email)
            ids.append(_session(sid).json()["user"]["user_id"])
        assert ids[0] == ids[1]

    def test_links_to_existing_password_account(self, fake_google):
        email = f"g_{uuid.uuid4().hex[:8]}@example.com"
        reg = requests.post(f"{API}/auth/register/dev-instant", json={"email": email, "password": "test1234", "name": "Pat"}).json()
        sid, _l = _google_login(email)
        g = _session(sid).json()
        assert g["user"]["user_id"] == reg["user"]["user_id"]
        # the password still works
        assert requests.post(f"{API}/auth/login", json={"email": email, "password": "test1234"}).status_code == 200

    @pytest.mark.parametrize("prefix", ["badaud", "badiss", "unverified", "expired"])
    def test_bad_id_token_is_rejected(self, fake_google, prefix):
        email = f"g_{uuid.uuid4().hex[:8]}@example.com"
        sid, loc = _google_login(email, prefix=prefix)
        assert sid is None and "google_error=failed" in loc
        # and no account was created for it
        assert requests.post(f"{API}/auth/login", json={"email": email, "password": "x"}).status_code == 401

    def test_google_token_endpoint_error_is_reported(self, fake_google):
        _, q = _google_start()
        loc = _google_callback("denied", q["state"]).headers["location"]
        assert "google_error=failed" in loc and "session_id" not in loc

    def test_user_cancelled_at_google(self, fake_google):
        _, q = _google_start()
        r = requests.get(f"{API}/auth/google/callback", params={"error": "access_denied", "state": q["state"]},
                         allow_redirects=False)
        assert r.status_code == 302 and _qs(r.headers["location"])["google_error"] == "cancelled"

    def test_state_is_single_use_and_required(self, fake_google):
        _, q = _google_start()
        assert _google_callback("ok:a@example.com:A", q["state"]).status_code == 302
        assert _google_callback("ok:a@example.com:A", q["state"]).status_code == 400  # replay
        assert _google_callback("ok:a@example.com:A", "forged-state").status_code == 400
        assert requests.get(f"{API}/auth/google/callback", params={"code": "x"}, allow_redirects=False).status_code == 400

    @pytest.mark.parametrize("redirect", ["https://evil.example/steal", "http://evil.example/", "javascript:alert(1)",
                                          "frontend://",  # the app's old, pre-rename scheme
                                          "//evil.example", "http://user@evil.example/", "file:///etc/passwd", ""])
    def test_redirect_allowlist_blocks_foreign_targets(self, fake_google, redirect):
        _v, challenge = _pkce()
        r = requests.get(f"{API}/auth/google/start", params={"redirect": redirect, "code_challenge": challenge,
                                                             "code_challenge_method": "S256"}, allow_redirects=False)
        assert r.status_code in (400, 422), (redirect, r.status_code)

    # Development targets (the test backend runs with ALLOW_DEV_REDIRECTS=1). What production accepts is
    # covered exactly in tests/test_oauth_redirects.py.
    @pytest.mark.parametrize("redirect", ["http://localhost:8081/", "http://127.0.0.1:19006/x", "exp://192.168.1.5:8081/--/",
                                          "bookloop://", "bookloop://settings/account"])
    def test_redirect_allowlist_accepts_app_targets(self, fake_google, redirect):
        _google_start(redirect)

    def test_redirect_with_existing_query_appends_correctly(self, fake_google):
        _, q = _google_start("exp://192.168.1.5:8081/--/?a=1")
        loc = _google_callback("ok:qq@example.com:Q", q["state"]).headers["location"]
        assert loc.startswith("exp://192.168.1.5:8081/--/?a=1&session_id=")
        assert _qs(loc)["session_id"]


def _google_link_start(user_token, pkce=None, redirect=APP_REDIRECT):
    """Settings -> Link Google: returns (state, verifier) of the attempt."""
    verifier, challenge = pkce or _pkce()
    r = requests.post(f"{API}/auth/google/link", headers=_h(user_token),
                      json={"redirect": redirect, "code_challenge": challenge, "code_challenge_method": "S256"})
    assert r.status_code == 200, r.text
    return _qs(r.json()["url"])["state"], verifier


def _google_link(user_token, code, pkce=None):
    """Link Google up to the app's return; returns (redirect Location, verifier)."""
    state, verifier = _google_link_start(user_token, pkce)
    cb = _google_callback(code, state)
    assert cb.status_code == 302
    return cb.headers["location"], verifier


def _link_complete(user_token, link_id, verifier):
    body = {"link_id": link_id}
    if verifier is not None:
        body["code_verifier"] = verifier
    return requests.post(f"{API}/auth/google/link/complete", json=body, headers=_h(user_token) if user_token else {})


def _google_link_confirmed(user_token, code):
    """The whole Link Google flow as the app runs it; returns the /link/complete response."""
    loc, verifier = _google_link(user_token, code)
    assert "link_id=" in loc and "session_id" not in loc and "linked=" not in loc, loc
    return _link_complete(user_token, _qs(loc)["link_id"], verifier)


def _g_user(prefix="u"):
    r = requests.post(f"{API}/auth/register/dev-instant", json={
        "email": f"{prefix}_{uuid.uuid4().hex[:8]}@example.com", "password": "test1234", "name": prefix.upper()})
    assert r.status_code == 200, r.text
    return r.json()


def _google_identity(sub):
    return _otp_db().auth_identities.find_one({"provider": "google", "subject": sub})


class TestGoogleIdentities:
    def test_login_records_identity(self, fake_google):
        email = f"gi_{uuid.uuid4().hex[:8]}@example.com"
        sid, _l = _google_login(email)
        uid = _session(sid).json()["user"]["user_id"]
        ident = _google_identity("gsub-" + email)
        assert ident and ident["user_id"] == uid

    def test_identity_wins_over_email(self, fake_google):
        # Google account X is linked to account A; A's Google login keeps opening A even though
        # another account B has the Google email.
        a = _g_user("ga")
        b_email = f"gb_{uuid.uuid4().hex[:8]}@example.com"
        b = requests.post(f"{API}/auth/register/dev-instant", json={"email": b_email, "password": "test1234", "name": "B"}).json()
        assert _google_link_confirmed(a["session_token"], f"ok:{b_email}:Gee").status_code == 200
        sid, _l = _google_login(b_email)
        got = _session(sid).json()["user"]["user_id"]
        assert got == a["user"]["user_id"] != b["user"]["user_id"]

    def test_link_google_to_signed_in_account(self, fake_google):
        a = _g_user("gl")
        g_email = f"glg_{uuid.uuid4().hex[:8]}@gmail.com"
        r = _google_link_confirmed(a["session_token"], f"ok:{g_email}:L")
        assert r.status_code == 200, r.text
        assert r.json()["providers"]["google"] == {"linked": True, "email": g_email, "private_email": False}
        d = requests.get(f"{API}/auth/identities", headers=_h(a["session_token"])).json()
        assert d["providers"]["google"] == {"linked": True, "email": g_email, "private_email": False}
        sid, _l = _google_login(g_email)
        assert _session(sid).json()["user"]["user_id"] == a["user"]["user_id"]
        # linking again with the same Google account is a no-op success
        assert _google_link_confirmed(a["session_token"], f"ok:{g_email}:L").status_code == 200

    def test_google_identity_of_another_account_cannot_be_linked(self, fake_google):
        g_email = f"gt_{uuid.uuid4().hex[:8]}@example.com"
        sid, _l = _google_login(g_email)
        owner = _session(sid).json()
        attacker = _g_user("gx")
        r = _google_link_confirmed(attacker["session_token"], f"ok:{g_email}:T")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "identity_in_use"
        sid2, _l = _google_login(g_email)
        assert _session(sid2).json()["user"]["user_id"] == owner["user"]["user_id"]

    def test_link_requires_auth_and_allowed_redirect(self, fake_google):
        _v, challenge = _pkce()
        body = {"redirect": APP_REDIRECT, "code_challenge": challenge, "code_challenge_method": "S256"}
        assert requests.post(f"{API}/auth/google/link", json=body).status_code == 401
        u = _g_user("gr")
        r = requests.post(f"{API}/auth/google/link", json={**body, "redirect": "https://evil.example/"},
                          headers=_h(u["session_token"]))
        assert r.status_code == 400

    def test_google_only_account_cannot_unlink_last_method(self, fake_google):
        sid, _l = _google_login(f"go_{uuid.uuid4().hex[:8]}@example.com")
        tok = _session(sid).json()["session_token"]
        r = requests.delete(f"{API}/auth/identities/google", headers=_h(tok))
        assert r.status_code == 409 and r.json()["detail"]["code"] == "last_login_method"

    def test_token_without_sub_still_signs_in_by_email(self, fake_google):
        email = f"gn_{uuid.uuid4().hex[:8]}@example.com"
        sid, _l = _google_login(email, prefix="nosub")
        assert sid and _session(sid).status_code == 200


# ------------------------------------------------------------------ GOOGLE: which account an email may open
# A Google identity only opens an EXISTING account by email when Google vouches for the address (Gmail or a
# Workspace `hd` account) and the account has no other Google account linked. Link Google started from the
# app's URL scheme only links a Google account with the account's own email (another app on the phone could
# have started it). Every sign-in method change is recorded in security_events.
APP_SCHEME_LINK_REDIRECT = "bookloop://settings/account"  # accepted by the test backend (ALLOW_DEV_REDIRECTS=1)


def _events(user_id):
    return [(e["event"], e["provider"]) for e in _otp_db().security_events.find({"user_id": user_id}).sort("created_at", 1)]


def _g_user_with_email(email):
    r = requests.post(f"{API}/auth/register/dev-instant", json={"email": email, "password": "test1234", "name": "Em"})
    assert r.status_code == 200, r.text
    return r.json()


class TestGoogleEmailMatching:
    def test_untrusted_address_does_not_open_an_existing_account(self, fake_google):
        email = f"cons_{uuid.uuid4().hex[:8]}@example.org"  # a consumer Google account on a foreign address
        acct = _g_user_with_email(email)
        sid, loc = _google_login(email)
        assert sid is None and _qs(loc)["google_error"] == "link_required"
        assert _google_identity("gsub-" + email) is None
        assert _events(acct["user"]["user_id"]) == []

    def test_untrusted_address_without_an_account_creates_one(self, fake_google):
        email = f"newcons_{uuid.uuid4().hex[:8]}@example.org"
        sid, _l = _google_login(email)
        r = _session(sid)
        assert r.status_code == 200 and r.json()["user"]["email"] == email
        assert _events(r.json()["user"]["user_id"]) == []  # a new account: nothing changed on an existing one

    def test_gmail_address_opens_the_existing_account_and_is_reported(self, fake_google):
        email = f"gm_{uuid.uuid4().hex[:8]}@gmail.com"
        acct = _g_user_with_email(email)
        sid, _l = _google_login(email)
        assert _session(sid).json()["user"]["user_id"] == acct["user"]["user_id"]
        assert _events(acct["user"]["user_id"]) == [("signin_linked", "google")]
        # The next sign-in goes through the identity: no new event.
        sid2, _l = _google_login(email)
        assert _session(sid2).status_code == 200
        assert _events(acct["user"]["user_id"]) == [("signin_linked", "google")]

    def test_legacy_google_sub_still_opens_its_account(self, fake_google):
        email = f"leg_{uuid.uuid4().hex[:8]}@example.org"
        acct = _g_user_with_email(email)
        _otp_db().users.update_one({"user_id": acct["user"]["user_id"]}, {"$set": {"google_sub": "gsub-" + email}})
        sid, _l = _google_login(email)
        assert _session(sid).json()["user"]["user_id"] == acct["user"]["user_id"]

    def test_another_google_account_with_the_same_email_is_refused(self, fake_google):
        email = f"rec_{uuid.uuid4().hex[:8]}@example.com"
        acct = _g_user_with_email(email)
        sid, _l = _google_login(email)  # Google account A, linked by email
        assert _session(sid).json()["user"]["user_id"] == acct["user"]["user_id"]
        for prefix in ("alt", "nosub"):  # Google account B on the same (e.g. recycled) address
            sid_b, loc = _google_login(email, prefix=prefix)
            assert sid_b is None and _qs(loc)["google_error"] == "account_mismatch", prefix
        assert _google_identity("gsub2-" + email) is None
        sid, _l = _google_login(email)  # A still works
        assert _session(sid).json()["user"]["user_id"] == acct["user"]["user_id"]


class TestGoogleLinkFromAppScheme:
    def _link(self, user, code):
        state, verifier = _google_link_start(user["session_token"], redirect=APP_SCHEME_LINK_REDIRECT)
        loc = _google_callback(code, state).headers["location"]
        assert loc.startswith(APP_SCHEME_LINK_REDIRECT + "?link_id="), loc
        return _link_complete(user["session_token"], _qs(loc)["link_id"], verifier)

    def test_other_email_is_refused(self, fake_google):
        u = _g_user("ls")
        g_email = f"lsx_{uuid.uuid4().hex[:8]}@gmail.com"
        r = self._link(u, f"ok:{g_email}:X")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "google_email_mismatch"
        assert _google_identity("gsub-" + g_email) is None
        assert _events(u["user"]["user_id"]) == []

    def test_own_email_links_and_is_reported(self, fake_google):
        u = _g_user("lo")
        r = self._link(u, f"ok:{u['user']['email']}:O")
        assert r.status_code == 200 and r.json()["providers"]["google"]["linked"] is True
        assert _events(u["user"]["user_id"]) == [("linked", "google")]

    def test_web_origin_link_may_use_another_email(self, fake_google):
        # A web origin can't be claimed by another app: the email rule doesn't apply there.
        u = _g_user("lwo")
        assert _google_link_confirmed(u["session_token"], f"ok:lwo_{uuid.uuid4().hex[:8]}@gmail.com:W").status_code == 200


class TestSecurityEvents:
    def test_link_relink_unlink_and_account_deletion(self, fake_google):
        u = _g_user("se")
        uid, g_email = u["user"]["user_id"], f"se_{uuid.uuid4().hex[:8]}@gmail.com"
        assert _google_link_confirmed(u["session_token"], f"ok:{g_email}:S").status_code == 200
        assert _google_link_confirmed(u["session_token"], f"ok:{g_email}:S").status_code == 200  # no-op
        assert requests.delete(f"{API}/auth/identities/google", headers=_h(u["session_token"])).status_code == 200
        assert _events(uid) == [("linked", "google"), ("unlinked", "google")]
        assert _otp_db().security_events.find_one({"user_id": uid, "event": "linked"})["provider_email"] == g_email
        r = requests.post(f"{API}/users/me/delete", json={"confirm": True}, headers=_h(u["session_token"]))
        assert r.status_code == 200
        assert _events(uid) == []

    def test_failed_link_records_nothing(self, fake_google):
        owner_email = f"sf_{uuid.uuid4().hex[:8]}@gmail.com"
        sid, _l = _google_login(owner_email)
        assert _session(sid).status_code == 200
        other = _g_user("sf")
        assert _google_link_confirmed(other["session_token"], f"ok:{owner_email}:F").status_code == 409
        assert _events(other["user"]["user_id"]) == []


class TestClientRecord:
    def test_app_version_headers_are_recorded(self):
        u = _g_user("cv")
        hdr = {**_h(u["session_token"]), "X-App-Version": "2.3.0", "X-App-Platform": "android"}
        assert requests.get(f"{API}/auth/me", headers=hdr).status_code == 200
        doc = _otp_db().users.find_one({"user_id": u["user"]["user_id"]})
        assert doc["client_version"] == "2.3.0" and doc["client_platform"] == "android" and doc["client_seen_at"]
        me = requests.get(f"{API}/auth/me", headers=hdr).json()["user"]
        assert "client_version" not in me and "client_seen_at" not in me

    def test_missing_or_malformed_headers_record_none(self):
        u = _g_user("cn")
        hdr = {**_h(u["session_token"]), "X-App-Version": "<script>", "X-App-Platform": "a" * 40}
        assert requests.get(f"{API}/auth/me", headers=hdr).status_code == 200
        doc = _otp_db().users.find_one({"user_id": u["user"]["user_id"]})
        assert doc["client_version"] is None and doc["client_platform"] is None

    def test_google_sign_in_records_how_it_ran(self, fake_google):
        sid, _l = _google_login(f"cg_{uuid.uuid4().hex[:8]}@gmail.com")
        r = requests.post(f"{API}/auth/session", json={"session_id": sid, "code_verifier": _VERIFIERS[sid]},
                          headers={"X-App-Version": "2.2.0", "X-App-Platform": "android"})
        assert r.status_code == 200
        doc = _otp_db().users.find_one({"user_id": r.json()["user"]["user_id"]})
        assert doc["google_signin_via"] == "google_web" and doc["client_version"] == "2.2.0"

    def test_providers_reports_native_google_flag(self):
        assert isinstance(requests.get(f"{API}/auth/providers").json()["google_native"], bool)


# ------------------------------------------------------------------ GOOGLE: binding to the app's attempt
# Regression tests for the OAuth hardening: a ticket (session_id / link_id) that reaches anyone but the
# app attempt that started the flow must be useless, every ticket is single-use and expires on its own
# (checked explicitly, not only by the TTL index), and linking needs the same signed-in account.
RFC7636_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
RFC7636_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def _parallel(fn, n=8):
    """Runs fn() n times at once; returns the status codes."""
    import threading
    barrier, codes, lock = threading.Barrier(n), [], threading.Lock()

    def run():
        barrier.wait()
        code = fn().status_code
        with lock:
            codes.append(code)

    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return sorted(codes)


def _age(collection, query, minutes):
    """Moves a record's created_at into the past; asserts it is still there (TTL cleanup hasn't run)."""
    coll = _otp_db()[collection]
    res = coll.update_one(query, {"$set": {"created_at": datetime.now(timezone.utc) - timedelta(minutes=minutes)}})
    assert res.matched_count == 1


class TestGoogleAttemptBinding:
    # ---- start
    @pytest.mark.parametrize("params", [
        {},  # an old app build: no challenge at all
        {"code_challenge_method": "S256"},
        {"code_challenge": RFC7636_CHALLENGE},  # method missing
        {"code_challenge": RFC7636_CHALLENGE, "code_challenge_method": "plain"},
        {"code_challenge": RFC7636_CHALLENGE[:-1], "code_challenge_method": "S256"},  # too short
        {"code_challenge": RFC7636_CHALLENGE[:-1] + "=", "code_challenge_method": "S256"},  # not base64url
        {"code_challenge": RFC7636_CHALLENGE + "A", "code_challenge_method": "S256"},  # too long
    ])
    def test_start_requires_an_s256_challenge(self, fake_google, params):
        r = requests.get(f"{API}/auth/google/start", params={"redirect": APP_REDIRECT, **params}, allow_redirects=False)
        assert r.status_code == 400 and r.json()["detail"]["code"] == "pkce_required", params

    def test_link_start_requires_an_s256_challenge(self, fake_google):
        u = _g_user("lp")
        for body in ({"redirect": APP_REDIRECT},
                     {"redirect": APP_REDIRECT, "code_challenge": RFC7636_CHALLENGE, "code_challenge_method": "plain"}):
            r = requests.post(f"{API}/auth/google/link", json=body, headers=_h(u["session_token"]))
            assert r.status_code == 400 and r.json()["detail"]["code"] == "pkce_required"

    def test_rfc7636_vector(self, fake_google):
        # The backend's S256 is exactly RFC 7636's (and so the app's: see frontend/scripts/logic.test.mjs).
        sid, loc = _google_login(f"rfc_{uuid.uuid4().hex[:8]}@example.com", pkce=(RFC7636_VERIFIER, RFC7636_CHALLENGE))
        assert _qs(loc)["attempt"] == RFC7636_CHALLENGE[:16]
        assert _session(sid, RFC7636_VERIFIER).status_code == 200

    def test_redirect_carries_the_attempt_but_no_secret(self, fake_google):
        _, q = _google_start()
        loc = _google_callback(f"ok:att_{uuid.uuid4().hex[:8]}@example.com:A", q["state"]).headers["location"]
        assert _qs(loc)["attempt"] == q["_challenge"][:16]
        assert q["_verifier"] not in loc and q["_challenge"] not in loc

    # ---- stolen / wrong / missing / replayed verifiers (sign-in)
    def test_stolen_session_id_is_useless_and_does_not_spoil_the_real_sign_in(self, fake_google):
        sid, _l = _google_login(f"st_{uuid.uuid4().hex[:8]}@example.com")
        # Whoever caught the redirect (another app on the scheme, a crafted exp:// target) has the id only.
        no_verifier = _session(sid, None)
        assert no_verifier.status_code == 400 and no_verifier.json()["detail"]["code"] == "pkce_required"
        for bad in ("", "short", "x" * 129, "has spaces in it" * 4):
            assert _session(sid, bad).status_code in (400, 422), bad  # 422: over the body's length limit
        for _ in range(3):
            assert _session(sid, _pkce()[0]).status_code == 401  # their own verifier
        assert _session(sid, RFC7636_VERIFIER).status_code == 401
        # None of that consumed the ticket: the app that started the sign-in still gets in.
        assert _session(sid).status_code == 200

    def test_replayed_verifier_and_session_id(self, fake_google):
        sid, _l = _google_login(f"rp_{uuid.uuid4().hex[:8]}@example.com")
        assert _session(sid).status_code == 200
        assert _session(sid).status_code == 401  # replay of the exact same request
        sid2, _l = _google_login(f"rp2_{uuid.uuid4().hex[:8]}@example.com")
        assert _session(sid2, _VERIFIERS[sid]).status_code == 401  # an old attempt's verifier
        assert _session(sid2).status_code == 200

    def test_concurrent_attempts_never_cross(self, fake_google):
        # Two sign-in attempts in flight (e.g. a stale browser tab and a new one): each ticket only
        # works with its own attempt's verifier.
        email = f"ca_{uuid.uuid4().hex[:8]}@example.com"
        sid1, loc1 = _google_login(email)
        sid2, loc2 = _google_login(email)
        assert _qs(loc1)["attempt"] != _qs(loc2)["attempt"]
        assert _session(sid1, _VERIFIERS[sid2]).status_code == 401
        assert _session(sid2, _VERIFIERS[sid1]).status_code == 401
        assert _session(sid1).status_code == 200 and _session(sid2).status_code == 200

    def test_concurrent_redemption_has_exactly_one_winner(self, fake_google):
        sid, _l = _google_login(f"cr_{uuid.uuid4().hex[:8]}@example.com")
        assert _parallel(lambda: _session(sid)) == [200] + [401] * 7

    def test_expired_session_id_rejected_even_before_ttl_cleanup(self, fake_google):
        sid, _l = _google_login(f"ex_{uuid.uuid4().hex[:8]}@example.com")
        _age("google_logins", {"sid": sid}, minutes=3)  # login tickets live 2 minutes
        assert _session(sid).status_code == 401
        assert _otp_db().google_logins.find_one({"sid": sid})  # still stored: the code check refused it

    def test_expired_sign_in_state_rejected_even_before_ttl_cleanup(self, fake_google):
        _, q = _google_start()
        _age("oauth_states", {"state": q["state"]}, minutes=11)  # states live 10 minutes
        r = _google_callback(f"ok:es_{uuid.uuid4().hex[:8]}@example.com:E", q["state"])
        assert r.status_code == 400

    def test_state_without_a_challenge_is_rejected(self, fake_google):
        # A state stored by the previous backend version (no challenge) can't produce an unbound ticket.
        state = uuid.uuid4().hex + uuid.uuid4().hex
        _otp_db().oauth_states.insert_one({"state": state, "redirect": APP_REDIRECT, "link_user_id": None,
                                           "created_at": datetime.now(timezone.utc)})
        assert _google_callback(f"ok:ns_{uuid.uuid4().hex[:8]}@example.com:N", state).status_code == 400

    def test_callback_error_is_never_reflected(self, fake_google):
        _, q = _google_start()
        r = requests.get(f"{API}/auth/google/callback", allow_redirects=False,
                         params={"state": q["state"], "error": "<script>alert(1)</script>"})
        assert _qs(r.headers["location"])["google_error"] == "cancelled" and "script" not in r.headers["location"]

    # ---- linking
    def test_link_callback_alone_links_nothing(self, fake_google):
        a = _g_user("lc")
        g_email = f"lcg_{uuid.uuid4().hex[:8]}@example.com"
        loc, _v = _google_link(a["session_token"], f"ok:{g_email}:L")
        assert "link_id=" in loc and _qs(loc)["attempt"]
        assert _google_identity("gsub-" + g_email) is None

    def test_link_hijack_victim_completing_attackers_link_links_nothing(self, fake_google):
        # T5: the attacker starts Link Google with their own session and sends the Google URL to a
        # victim, who picks their Google account. The link_id goes to the app's return screen on the
        # victim's device, not to the attacker; nothing is linked until it is confirmed.
        attacker, victim = _g_user("atk"), _g_user("vic")
        g_email = f"vg_{uuid.uuid4().hex[:8]}@example.com"
        state, attacker_verifier = _google_link_start(attacker["session_token"])
        loc = _google_callback(f"ok:{g_email}:Victim", state).headers["location"]  # the victim's browser
        lid = _qs(loc)["link_id"]
        assert _google_identity("gsub-" + g_email) is None
        # The victim's app (signed in as the victim) can't complete someone else's link, with or without
        # a verifier — and its attempts don't consume the ticket.
        assert _link_complete(victim["session_token"], lid, None).status_code == 400
        assert _link_complete(victim["session_token"], lid, _pkce()[0]).json()["detail"]["code"] == "link_invalid"
        assert _link_complete(victim["session_token"], lid, attacker_verifier).json()["detail"]["code"] == "link_invalid"
        assert _link_complete(None, lid, attacker_verifier).status_code == 401
        assert _google_identity("gsub-" + g_email) is None
        # The victim's own Google sign-in creates/opens the victim's own Google account, not the attacker's.
        sid, _l = _google_login(g_email)
        assert _session(sid).json()["user"]["user_id"] not in (attacker["user"]["user_id"],)

    def test_link_needs_the_same_account_and_its_verifier(self, fake_google):
        a, b = _g_user("la"), _g_user("lb")
        g_email = f"lsg_{uuid.uuid4().hex[:8]}@example.com"
        loc, verifier = _google_link(a["session_token"], f"ok:{g_email}:S")
        lid = _qs(loc)["link_id"]
        # B holds A's link_id AND A's verifier (worst case) and still can't attach A's Google to B.
        assert _link_complete(b["session_token"], lid, verifier).json()["detail"]["code"] == "link_invalid"
        r = _link_complete(a["session_token"], lid, None)
        assert r.status_code == 400 and r.json()["detail"]["code"] == "pkce_required"
        assert _link_complete(a["session_token"], lid, _pkce()[0]).json()["detail"]["code"] == "link_invalid"
        # A login ticket's verifier, or a link_id used as a session_id, never cross over.
        assert _session(lid, verifier).status_code == 401
        assert _link_complete(a["session_token"], lid, verifier).status_code == 200
        assert _google_identity("gsub-" + g_email)["user_id"] == a["user"]["user_id"]
        # replay
        assert _link_complete(a["session_token"], lid, verifier).json()["detail"]["code"] == "link_invalid"

    def test_login_ticket_cannot_complete_a_link(self, fake_google):
        u = _g_user("lt")
        sid, _l = _google_login(f"ltg_{uuid.uuid4().hex[:8]}@example.com")
        assert _link_complete(u["session_token"], sid, _VERIFIERS[sid]).json()["detail"]["code"] == "link_invalid"
        assert _session(sid).status_code == 200  # untouched

    def test_expired_link_ticket_rejected_even_before_ttl_cleanup(self, fake_google):
        a = _g_user("le")
        loc, verifier = _google_link(a["session_token"], f"ok:leg_{uuid.uuid4().hex[:8]}@example.com:E")
        lid = _qs(loc)["link_id"]
        _age("google_link_tickets", {"lid": lid}, minutes=6)  # link tickets live 5 minutes
        assert _link_complete(a["session_token"], lid, verifier).json()["detail"]["code"] == "link_invalid"
        assert _otp_db().google_link_tickets.find_one({"lid": lid})

    def test_concurrent_link_completion_has_exactly_one_winner(self, fake_google):
        a = _g_user("lw")
        loc, verifier = _google_link(a["session_token"], f"ok:lwg_{uuid.uuid4().hex[:8]}@example.com:W")
        lid = _qs(loc)["link_id"]
        assert _parallel(lambda: _link_complete(a["session_token"], lid, verifier)) == [200] + [400] * 7

    def test_two_accounts_racing_to_link_one_google_account(self, fake_google):
        a, b = _g_user("ra"), _g_user("rb")
        g_email = f"rg_{uuid.uuid4().hex[:8]}@example.com"
        loc_a, ver_a = _google_link(a["session_token"], f"ok:{g_email}:R")
        loc_b, ver_b = _google_link(b["session_token"], f"ok:{g_email}:R")
        import threading
        out = {}
        barrier = threading.Barrier(2)

        def go(name, tok, loc, ver):
            barrier.wait()
            out[name] = _link_complete(tok, _qs(loc)["link_id"], ver)

        ts = [threading.Thread(target=go, args=("a", a["session_token"], loc_a, ver_a)),
              threading.Thread(target=go, args=("b", b["session_token"], loc_b, ver_b))]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        codes = sorted(r.status_code for r in out.values())
        assert codes == [200, 409], codes
        loser = next(r for r in out.values() if r.status_code == 409)
        assert loser.json()["detail"]["code"] == "identity_in_use"
        winner = "a" if out["a"].status_code == 200 else "b"
        assert _google_identity("gsub-" + g_email)["user_id"] == {"a": a, "b": b}[winner]["user"]["user_id"]

    def test_conflict_is_rechecked_at_completion(self, fake_google):
        # The Google account was free when the browser came back, but someone signed in with it before
        # the app confirmed: the confirmation sees the current state and refuses.
        a = _g_user("rc")
        g_email = f"rcg_{uuid.uuid4().hex[:8]}@example.com"
        loc, verifier = _google_link(a["session_token"], f"ok:{g_email}:C")
        sid, _l = _google_login(g_email)
        other = _session(sid).json()["user"]["user_id"]
        r = _link_complete(a["session_token"], _qs(loc)["link_id"], verifier)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "identity_in_use"
        assert _google_identity("gsub-" + g_email)["user_id"] == other

    def test_second_google_account_cannot_be_linked_over_the_first(self, fake_google):
        a = _g_user("sg")
        assert _google_link_confirmed(a["session_token"], f"ok:sg1_{uuid.uuid4().hex[:8]}@example.com:1").status_code == 200
        r = _google_link_confirmed(a["session_token"], f"ok:sg2_{uuid.uuid4().hex[:8]}@example.com:2")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "provider_already_linked"

    def test_failed_link_at_google_reports_without_ticket(self, fake_google):
        a = _g_user("lf")
        state, _v = _google_link_start(a["session_token"])
        loc = _google_callback("denied", state).headers["location"]
        assert _qs(loc).get("link_error") == "failed" and "link_id" not in loc and _qs(loc)["attempt"]

    def test_account_deletion_drops_pending_google_records(self, fake_google):
        a = _g_user("ld")
        loc, _v = _google_link(a["session_token"], f"ok:ldg_{uuid.uuid4().hex[:8]}@example.com:D")
        _google_link_start(a["session_token"])  # a state still waiting for Google
        uid = a["user"]["user_id"]
        assert requests.post(f"{API}/users/me/delete", json={"confirm": True}, headers=_h(a["session_token"])).status_code == 200
        db = _otp_db()
        assert db.google_link_tickets.count_documents({"user_id": uid}) == 0
        assert db.oauth_states.count_documents({"link_user_id": uid}) == 0


class TestSearchAndUpload:
    def test_regex_metacharacters_are_literal(self, world):
        h = _h(world["a"]["token"])
        for s in ("(", "[", "*", "a{1", "\\"):
            assert requests.get(f"{API}/discover/people", params={"search": s}, headers=h).status_code == 200, s
            assert requests.get(f"{API}/discover/books", params={"search": s}, headers=h).status_code == 200, s
        # ".*" must not act as a wildcard
        r = requests.get(f"{API}/discover/people", params={"search": ".*"}, headers=h).json()
        assert r["people"] == []

    def test_search_still_matches_plain_text(self, world):
        r = requests.get(f"{API}/discover/people", params={"search": "guard"}, headers=_h(world["a"]["token"])).json()
        assert any("Guard" in p["name"] for p in r["people"])

    def test_upload_rejects_non_image(self, world):
        files = {"file": ("evil.png", io.BytesIO(b"<html>not an image</html>"), "image/png")}
        r = requests.post(f"{API}/upload", files=files, headers=_h(world["a"]["token"]))
        assert r.status_code == 400

    def test_upload_rejects_oversize(self, world):
        big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (8 * 1024 * 1024 + 10)
        files = {"file": ("big.png", io.BytesIO(big), "image/png")}
        r = requests.post(f"{API}/upload", files=files, headers=_h(world["a"]["token"]))
        assert r.status_code == 413

    def test_file_urls_are_signed_and_gated(self, world):
        w = world
        h = _h(w["a"]["token"])
        # A cover may only point into the user's own upload folder; the file itself needn't exist here.
        raw = f"/api/files/bookloop/uploads/{w['a']['id']}/does-not-exist.png"
        r = requests.post(f"{API}/books", json={"title": "TEST cover", "cover_url": raw}, headers=h)
        assert r.status_code == 200
        signed = r.json()["book"]["cover_url"]
        assert signed.startswith(raw + "?exp=") and "&sig=" in signed
        # signing is idempotent: saving a signed URL back doesn't stack query strings
        bid = r.json()["book"]["id"]
        r2 = requests.put(f"{API}/books/{bid}", json={"cover_url": signed}, headers=h)
        assert r2.json()["book"]["cover_url"].count("?") == 1
        # gate: no credentials -> 401; tampered/expired signature -> 403
        assert requests.get(f"{BASE_URL}{raw}").status_code == 401
        assert requests.get(f"{BASE_URL}{signed[:-4]}beef").status_code == 403
        assert requests.get(f"{BASE_URL}{raw}?exp=1&sig=abc").status_code == 403
        # a signature for one path can't be replayed on another
        other = f"/api/files/bookloop/uploads/{w['a']['id']}/other.png" + signed[len(raw):]
        assert requests.get(f"{BASE_URL}{other}").status_code == 403
        # valid signature, or a bearer token, passes the gate (then 404: no such upload record)
        assert requests.get(f"{BASE_URL}{signed}").status_code == 404
        assert requests.get(f"{BASE_URL}{raw}", headers=h).status_code == 404

    def test_external_urls_are_not_signed(self, world):
        h = _h(world["a"]["token"])
        ext = "https://covers.openlibrary.org/b/isbn/1-L.jpg"
        r = requests.post(f"{API}/books", json={"title": "TEST ext", "cover_url": ext}, headers=h)
        assert r.json()["book"]["cover_url"] == ext

    PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15"
           b"\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01\x5e\xf3\x2a\xea\x00\x00"
           b"\x00\x00IEND\xaeB`\x82")

    def _upload(self, u, data=None, name="p.png", ctype="image/png"):
        r = requests.post(f"{API}/upload", files={"file": (name, io.BytesIO(data or self.PNG), ctype)},
                          headers=_h(u["token"]))
        assert r.status_code == 200, r.text
        return r.json()

    def test_upload_roundtrip_bytes_and_headers(self, world):
        w = world
        d = self._upload(w["a"])
        assert d["path"].startswith(f"bookloop/uploads/{w['a']['id']}/") and d["path"].endswith(".png")
        assert "?exp=" in d["url"] and "&sig=" in d["url"]  # upload response is signed too
        r = requests.get(f"{BASE_URL}{d['url']}")
        assert r.status_code == 200 and r.content == self.PNG
        assert r.headers["content-type"] == "image/png"
        assert r.headers.get("x-content-type-options") == "nosniff"
        assert "private" in r.headers.get("cache-control", "")

    def test_upload_stores_real_type_not_client_claim(self, world):
        # a PNG uploaded as "photo.jpg" / image/jpeg is stored and served as PNG
        d = self._upload(world["a"], name="photo.jpg", ctype="image/jpeg")
        assert d["path"].endswith(".png")
        assert requests.get(f"{BASE_URL}{d['url']}").headers["content-type"] == "image/png"

    def test_uploads_get_distinct_paths(self, world):
        a, b = self._upload(world["a"]), self._upload(world["a"])
        assert a["path"] != b["path"]

    def test_uploaded_file_needs_link_or_token(self, world):
        w = world
        d = self._upload(w["a"])
        raw = d["url"].split("?")[0]
        assert requests.get(f"{BASE_URL}{raw}").status_code == 401
        assert requests.get(f"{BASE_URL}{raw}", headers=_h(w["c"]["token"])).status_code == 200  # any signed-in user
        assert requests.get(f"{BASE_URL}{d['url'][:-3]}bad").status_code == 403

    def test_files_path_traversal_is_not_served(self, world):
        h = _h(world["a"]["token"])
        for p in ("..%2f..%2fserver.py", "..%2f..%2f.env", "%2e%2e%2f%2e%2e%2fserver.py"):
            assert requests.get(f"{API}/files/{p}", headers=h).status_code == 404, p

    def test_storage_path_resolver_refuses_escape(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        try:
            import server
        except Exception as e:  # e.g. no MONGO_URL configured in this environment
            pytest.skip(f"cannot import server: {e!r}")
        for bad in ("../server.py", "bookloop/../../server.py", "/etc/passwd", "C:/Windows/win.ini"):
            with pytest.raises(FileNotFoundError):
                server._storage_file(bad)
        assert server._storage_file("bookloop/uploads/u/x.png").is_relative_to(server.STORAGE_DIR)

    def test_upload_requires_auth(self):
        files = {"file": ("x.png", io.BytesIO(b"\x89PNG\r\n\x1a\n"), "image/png")}
        assert requests.post(f"{API}/upload", files=files).status_code == 401


# ------------------------------------------------------------------ LOCATION (device-reported only)
TASHKENT = {"lat": 41.3111, "lng": 69.2797}


def _set_location(u, **body):
    return requests.put(f"{API}/users/me/location", json=body, headers=_h(u["token"]))


class TestLocation:
    def test_new_account_has_no_made_up_location(self):
        u = _new_user("Loc")
        me = requests.get(f"{API}/auth/me", headers=_h(u["token"])).json()["user"]
        assert me["lat"] is None and me["lng"] is None
        assert me["city"] is None and me["neighborhood"] is None
        assert me["location_updated_at"] is None

    def test_device_location_is_stored_with_area(self):
        u = _new_user("Loc")
        r = _set_location(u, **TASHKENT, city="Tashkent", neighborhood="Yunusobod", country="Uzbekistan")
        assert r.status_code == 200, r.text
        me = requests.get(f"{API}/auth/me", headers=_h(u["token"])).json()["user"]
        assert me["city"] == "Tashkent" and me["neighborhood"] == "Yunusobod"
        assert abs(me["lat"] - TASHKENT["lat"]) < 1e-3 and abs(me["lng"] - TASHKENT["lng"]) < 1e-3
        assert me["location_updated_at"]

    def test_city_fallback_when_no_neighborhood(self):
        u = _new_user("Loc")
        me = _set_location(u, **TASHKENT, city="Tashkent", neighborhood="Tashkent").json()["user"]
        assert me["city"] == "Tashkent" and me["neighborhood"] is None

    def test_invalid_coordinates_rejected(self):
        u = _new_user("Loc")
        assert _set_location(u, lat=95, lng=10).status_code == 422
        assert _set_location(u, lat=10, lng=200).status_code == 422

    def test_profile_update_cannot_set_location(self):
        u = _new_user("Loc")
        r = requests.put(f"{API}/users/me", json={"bio": "hi", "neighborhood": "Centro", "city": "Messina",
                                                   "lat": 38.1938, "lng": 15.554}, headers=_h(u["token"]))
        assert r.status_code == 200
        me = r.json()["user"]
        assert me["bio"] == "hi"
        assert me["city"] is None and me["neighborhood"] is None and me["lat"] is None

    def test_close_readers_are_reported_as_within_1km_only(self):
        # Pre-launch privacy: distances are coarse (whole km, 1 km floor, measured to a ~1 km cell), so
        # a reader 22 m away is only ever "within 1 km" — no finer proximity than that leaves the API.
        a, b = _new_user("Near A"), _new_user("Near B")
        _set_location(a, **TASHKENT, city="Tashkent")
        _set_location(b, lat=TASHKENT["lat"] + 0.0002, lng=TASHKENT["lng"], city="Tashkent")  # ~22 m apart
        u = requests.get(f"{API}/users/{b['id']}", headers=_h(a["token"])).json()["user"]
        assert u["distance_km"] == 1.0
        assert "lat" not in u and "lng" not in u
        people = requests.get(f"{API}/discover/people?search=Near%20B", headers=_h(a["token"])).json()["people"]
        match = [p for p in people if p["user_id"] == b["id"]]
        assert match and match[0]["distance_km"] == 1.0

    def test_unknown_location_distance_is_sentinel(self):
        a, b = _new_user("Loc A"), _new_user("Loc B")
        _set_location(a, **TASHKENT, city="Tashkent")
        u = requests.get(f"{API}/users/{b['id']}", headers=_h(a["token"])).json()["user"]
        assert u["distance_km"] == 999.0 and u["neighborhood"] is None and u["city"] is None

    def test_map_clusters_use_real_areas_without_exact_coordinates(self):
        a = _new_user("Map")
        _set_location(a, lat=41.31234, lng=69.28765, city="Tashkent", neighborhood="Chilonzor")
        d = requests.get(f"{API}/map/clusters", headers=_h(a["token"])).json()
        chil = [c for c in d["clusters"] if c["neighborhood"] == "Chilonzor" and c["city"] == "Tashkent"]
        assert chil and chil[0]["id"]
        # Centers are rounded to ~1 km, never a reader's exact stored position.
        assert round(chil[0]["lat"], 2) == chil[0]["lat"] and round(chil[0]["lng"], 2) == chil[0]["lng"]
        assert all(c["neighborhood"] for c in d["clusters"])


# ------------------------------------------------------------------ DISCOVER: DISTANCE "ALL" + MAP CLUSTERS
MESSINA = {"lat": 38.1938, "lng": 15.5540}


class TestDiscoverDistanceAndClusters:
    """A reader ~4,000 km away (Tashkent, viewer in Messina) shows on the map, so "Explore people" on
    that cluster must list them even though the viewer's normal radius is 25 km. "All" = no radius."""

    @pytest.fixture(scope="class")
    def geo(self):
        tag = uuid.uuid4().hex[:8]
        area = f"Chilonzor {tag}"  # unique area -> a cluster holding only this test's readers
        viewer, near, far, far_off = (_new_user(f"{tag} {n}") for n in ("Viewer", "Near", "Far", "FarOff"))
        _set_location(viewer, **MESSINA, city="Messina")
        _set_location(near, lat=MESSINA["lat"] + 0.045, lng=MESSINA["lng"], city="Messina")  # ~5 km
        for u in (far, far_off):
            assert _set_location(u, **TASHKENT, city="Tashkent", neighborhood=area).status_code == 200
        r = requests.put(f"{API}/users/me", json={"is_exchanging": False}, headers=_h(far_off["token"]))
        assert r.status_code == 200
        return {"tag": tag, "area": area, "viewer": viewer, "near": near, "far": far, "far_off": far_off}

    def _ids(self, geo, **params):
        params.setdefault("search", geo["tag"])
        r = requests.get(f"{API}/discover/people", params=params, headers=_h(geo["viewer"]["token"]))
        assert r.status_code == 200, r.text
        return {p["user_id"]: p for p in r.json()["people"]}

    def _cluster_id(self, geo):
        d = requests.get(f"{API}/map/clusters", headers=_h(geo["viewer"]["token"])).json()
        match = [c for c in d["clusters"] if c["neighborhood"] == geo["area"] and c["city"] == "Tashkent"]
        assert len(match) == 1, "the distant reader must be on the viewer's map"
        return match[0]["id"]

    def test_distant_cluster_lists_its_readers_beyond_the_radius(self, geo):
        cid = self._cluster_id(geo)
        # Exactly what the app sends from the map: the cluster, the default filters, no search. A stale
        # max_distance from an older client must not re-introduce the radius.
        for extra in ({}, {"max_distance": "25"}):
            people = self._ids(geo, cluster=cid, exchanging="true", genre="All", language="All", search="", **extra)
            assert geo["far"]["id"] in people
            assert people[geo["far"]["id"]]["distance_km"] > 2000
            # Only that area's readers: not the viewer's nearby reader, not the viewer.
            assert geo["near"]["id"] not in people and geo["viewer"]["id"] not in people
            assert set(people) == {geo["far"]["id"]}

    def test_cluster_keeps_eligibility_rules(self, geo):
        cid = self._cluster_id(geo)
        assert geo["far_off"]["id"] not in self._ids(geo, cluster=cid, exchanging="true")
        both = self._ids(geo, cluster=cid, exchanging="false")
        assert {geo["far"]["id"], geo["far_off"]["id"]} <= set(both)
        assert self._ids(geo, cluster="000000000000", search="") == {}

    def test_normal_discover_respects_selected_distance(self, geo):
        p25 = self._ids(geo, max_distance="25")
        assert geo["near"]["id"] in p25 and geo["far"]["id"] not in p25
        assert geo["near"]["id"] not in self._ids(geo, max_distance="1")

    def test_all_returns_readers_regardless_of_distance(self, geo):
        people = self._ids(geo, max_distance="all")
        assert {geo["near"]["id"], geo["far"]["id"]} <= set(people)
        assert self._ids(geo, max_distance="ALL").keys() == people.keys()
        # Older clients that send no max_distance keep getting no limit.
        assert self._ids(geo).keys() == people.keys()

    def test_all_back_to_a_radius_restores_filtering(self, geo):
        assert geo["far"]["id"] in self._ids(geo, max_distance="all")
        p = self._ids(geo, max_distance="25")
        assert geo["far"]["id"] not in p and geo["near"]["id"] in p
        assert geo["far"]["id"] in self._ids(geo, max_distance="all")

    def test_numeric_radii_from_existing_clients_still_work(self, geo):
        for v in ("1", "5", "10", "25", "50", "25.0"):
            assert geo["far"]["id"] not in self._ids(geo, max_distance=v), v

    def test_invalid_max_distance_rejected(self, geo):
        for v in ("abc", "-5", "nan", "inf", "9999km"):
            r = requests.get(f"{API}/discover/people", params={"max_distance": v}, headers=_h(geo["viewer"]["token"]))
            assert r.status_code == 422, v


# ------------------------------------------------------------------ DEMO COMMUNITY CONSISTENCY
DEMO_NAMES = {"Maria Rossi", "Luca Bianchi", "Laura Conti", "Ahmed Hassan", "Giulia Marino", "Marco De Luca",
              "Sofia Greco", "Antonio Ferrara", "Elena Russo", "Davide Romano"}


class TestDemoCommunity:
    @pytest.fixture(scope="class")
    def demo_people(self):
        u = _new_user("Demo")
        # Looked up by name: a throwaway test database can hold thousands of other readers.
        demo = []
        for name in DEMO_NAMES:
            people = requests.get(f"{API}/discover/people", params={"search": name}, headers=_h(u["token"])).json()["people"]
            demo += [p for p in people if p["name"] == name]
        return u, demo

    def test_exactly_ten_demo_readers(self, demo_people):
        _, demo = demo_people
        assert sorted(p["name"] for p in demo) == sorted(DEMO_NAMES)

    def test_reviews_never_exceed_completed_swaps(self, demo_people):
        _, demo = demo_people
        for p in demo:
            assert p["rating_count"] <= p["swaps_count"], p["name"]

    def test_review_counts_are_backed_by_demo_reviews(self, demo_people):
        u, demo = demo_people
        assert any(p["rating_count"] > 0 for p in demo)
        for p in demo:
            d = requests.get(f"{API}/users/{p['user_id']}", headers=_h(u["token"])).json()
            assert len(d["reviews"]) == p["rating_count"], p["name"]
            assert all(r["rater_name"] in DEMO_NAMES and r["review"] for r in d["reviews"])
            if d["reviews"]:
                avg = round(sum(r["stars"] for r in d["reviews"]) / len(d["reviews"]), 1)
                assert p["rating"] == avg


# ------------------------------------------------------------------ SWAP LISTS: ONE BUCKET PER SWAP
def _buckets(u):
    r = requests.get(f"{API}/swaps", headers=_h(u["token"]))
    assert r.status_code == 200, r.text
    return r.json()


def _where(u, sid):
    """Every bucket this swap id appears in, for this user (must always be exactly one)."""
    b = _buckets(u)
    return [name for name, rows in b.items() for row in rows if row["id"] == sid]


def _proposed_swap():
    a, b = _new_user("Life A"), _new_user("Life B")
    ba, bb = _new_book(a, "Life A book"), _new_book(b, "Life B book")
    sid = requests.post(f"{API}/swaps", json={"receiver_id": b["id"]}, headers=_h(a["token"])).json()["swap"]["id"]
    r = _swap(a, "/propose", sid, {"offered_book_id": ba, "requested_book_id": bb})
    assert r.status_code == 200, r.text
    return a, b, sid, ba, bb


def _chatting_swap():
    """A pending swap whose recipient tapped "Accept to continue chatting"."""
    a, b, sid, ba, bb = _proposed_swap()
    assert _swap(b, "/allow-chat", sid).status_code == 200
    return a, b, sid, ba, bb


class TestSwapLifecycleBuckets:
    def test_pending_active_completed_each_in_exactly_one_bucket(self):
        a, b, sid, _, _ = _proposed_swap()
        assert _where(a, sid) == ["outgoing"] and _where(b, sid) == ["incoming"]
        assert _swap(b, "/accept", sid).status_code == 200
        assert _where(a, sid) == ["active"] and _where(b, sid) == ["active"]
        assert _swap(a, "/complete", sid).status_code == 200
        assert _where(a, sid) == ["active"] and _where(b, sid) == ["active"]  # half-confirmed stays active
        assert _swap(b, "/complete", sid).status_code == 200
        assert _where(a, sid) == ["completed"] and _where(b, sid) == ["completed"]
        n = requests.get(f"{API}/notifications", headers=_h(b["token"])).json()
        assert n["active_swaps"] == 0

    def test_declined_and_cancelled_go_to_history_only(self):
        a, b, sid, _, _ = _proposed_swap()
        assert _swap(b, "/decline", sid).status_code == 200
        assert _where(a, sid) == ["completed"] and _where(b, sid) == ["completed"]
        a2, b2, sid2, _, _ = _proposed_swap()
        assert _swap(a2, "/cancel", sid2).status_code == 200
        assert _where(a2, sid2) == ["completed"] and _where(b2, sid2) == ["completed"]
        row = next(r for r in _buckets(a2)["completed"] if r["id"] == sid2)
        assert row["status"] == "cancelled" and row["other_user"]["user_id"] == b2["id"]

    def test_swaps_with_reports_open_and_latest(self):
        a, b, sid, _, _ = _proposed_swap()
        w = requests.get(f"{API}/swaps/with/{b['id']}", headers=_h(a["token"])).json()
        assert w["open"]["id"] == sid and w["latest"]["id"] == sid
        _swap(b, "/accept", sid)
        _swap(a, "/complete", sid)
        _swap(b, "/complete", sid)
        w = requests.get(f"{API}/swaps/with/{b['id']}", headers=_h(a["token"])).json()
        assert w["open"] is None and w["latest"]["id"] == sid and w["latest"]["status"] == "completed"
        # The other side sees the same pair.
        w_b = requests.get(f"{API}/swaps/with/{a['id']}", headers=_h(b["token"])).json()
        assert w_b["open"] is None and w_b["latest"]["id"] == sid


# ------------------------------------------------------------------ EXCHANGE CHAT: PROPOSAL BOOKS
class TestProposalBooksInChat:
    def test_both_sides_see_both_titles(self):
        a, b, sid, ba, bb = _proposed_swap()
        for u in (a, b):
            d = requests.get(f"{API}/swaps/{sid}", headers=_h(u["token"])).json()
            prop = next(m for m in d["messages"] if m["type"] == "proposal")["proposal"]
            assert prop["offered"]["title"] == "TEST Life A book"
            assert prop["requested"]["title"] == "TEST Life B book"
            ap = d["swap"]["active_proposal"]
            assert ap["offered"]["id"] == ba and ap["requested"]["id"] == bb

    def test_missing_book_resolves_to_null_not_error(self):
        a, b, sid, ba, _ = _proposed_swap()
        _otp_db().books.delete_one({"id": ba})  # a book gone for good (not a soft delete)
        d = requests.get(f"{API}/swaps/{sid}", headers=_h(b["token"])).json()
        prop = next(m for m in d["messages"] if m["type"] == "proposal")["proposal"]
        assert prop["offered"] is None and prop["requested"]["title"] == "TEST Life B book"


# ------------------------------------------------------------------ MESSAGING: IDEMPOTENT + INCREMENTAL
class TestMessaging:
    def test_client_id_makes_resend_idempotent(self):
        a, b, sid, _, _ = _chatting_swap()
        body = {"text": "hello once", "client_id": "c_test_" + uuid.uuid4().hex[:8]}
        first = _swap(a, "/messages", sid, body).json()["message"]
        again = _swap(a, "/messages", sid, body).json()
        assert again["duplicate"] is True and again["message"]["id"] == first["id"]
        msgs = requests.get(f"{API}/swaps/{sid}", headers=_h(b["token"])).json()["messages"]
        assert sum(1 for m in msgs if m.get("text") == "hello once") == 1

    def test_same_client_id_from_another_user_is_a_different_message(self):
        a, b, sid, _, _ = _chatting_swap()
        cid = "c_shared_" + uuid.uuid4().hex[:8]
        m1 = _swap(a, "/messages", sid, {"text": "from a", "client_id": cid}).json()["message"]
        m2 = _swap(b, "/messages", sid, {"text": "from b", "client_id": cid}).json()["message"]
        assert m1["id"] != m2["id"]

    def test_incremental_poll_returns_only_newer_messages_in_order(self):
        a, b, sid, _, _ = _chatting_swap()
        full = requests.get(f"{API}/swaps/{sid}", headers=_h(b["token"])).json()
        assert full["full"] is True and "my_books" in full
        anchor = full["messages"][-1]["id"]
        sent = [_swap(a, "/messages", sid, {"text": f"m{i}"}).json()["message"]["id"] for i in range(3)]
        inc = requests.get(f"{API}/swaps/{sid}", params={"after": anchor}, headers=_h(b["token"])).json()
        assert inc["full"] is False and "my_books" not in inc
        ids = [m["id"] for m in inc["messages"]]
        assert [i for i in ids if i != anchor] == sent  # new ones, in send order (anchor may repeat)
        # An unknown anchor falls back to the full conversation instead of losing messages.
        again = requests.get(f"{API}/swaps/{sid}", params={"after": "msg_nope"}, headers=_h(b["token"])).json()
        assert again["full"] is True and len(again["messages"]) >= len(full["messages"]) + 3

    def test_other_users_cannot_read_or_post(self):
        a, b, sid, _, _ = _proposed_swap()
        c = _new_user("Outsider")
        assert requests.get(f"{API}/swaps/{sid}", headers=_h(c["token"])).status_code == 404
        assert _swap(c, "/messages", sid, {"text": "x", "client_id": "c1"}).status_code == 404


class TestRussianPreference:
    def test_russian_is_accepted_and_persists(self):
        u = _new_user("Ru")
        r = requests.put(f"{API}/users/me", json={"preferred_language": "ru"}, headers=_h(u["token"]))
        assert r.status_code == 200 and r.json()["user"]["preferred_language"] == "ru"
        # A fresh read (what the app does on the next launch) still says Russian.
        assert requests.get(f"{API}/auth/me", headers=_h(u["token"])).json()["user"]["preferred_language"] == "ru"
