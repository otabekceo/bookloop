"""BookLoop end-to-end API tests. Covers auth, discover, map, books, swaps, notifications, uploads."""
import os
import io
import time
import uuid
import pytest
import requests

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
        # An id that was never issued by a completed Google sign-in is simply unauthorized.
        assert r.status_code == 401, r.text

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

    def test_empty_message_rejected(self, user_a):
        sid = TestChatImage.state["sid"]
        r = requests.post(f"{API}/swaps/{sid}/messages", json={"text": ""}, headers=_h(user_a["token"]))
        assert r.status_code == 400

    def test_image_message_created(self, user_a):
        sid = TestChatImage.state["sid"]
        img = "/api/files/dummy/path.png"
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
        rc = requests.post(f"{API}/auth/register", json={"email": email, "password": "test1234", "name": "TEST Reviewee"})
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
    r = requests.post(f"{API}/auth/register", json={"email": email, "password": "test1234", "name": f"Guard {label}"})
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
    def test_messages_allowed_pending_and_active(self, world):
        w = world
        assert _swap(w["a"], "/messages", w["sid"], {"text": "pending hi"}).status_code == 200
        _to_active(w)
        assert _swap(w["b"], "/messages", w["sid"], {"text": "active hi"}).status_code == 200

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
        assert _swap(w["a"], "/messages", w["sid"], {"text": "hello"}).status_code == 200
        row = _swap_row(w["a"], w["sid"])
        assert row["last_message"] == "hello" and row["last_message_key"] is None  # user text is never a key
        assert _swap(w["a"], "/messages", w["sid"], {"image_url": "/api/files/x/y.png"}).status_code == 200
        row = _swap_row(w["a"], w["sid"])
        assert row["last_message_key"] == "swapChat.sys.photo"

    def test_user_text_message_has_no_key(self, world):
        w = world
        r = _swap(w["a"], "/messages", w["sid"], {"text": "ciao"}).json()["message"]
        assert r["key"] is None and r["text"] == "ciao"

    def test_locales_define_every_backend_key(self):
        import json
        from pathlib import Path
        d = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n" / "locales"
        if not d.exists():
            pytest.skip("frontend locales not found")
        sys_keys = ["interested", "proposed", "confirmed", "declined", "cancelled", "markedComplete",
                    "completed", "badgeUnlocked", "photo"]
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
            sets[lang] = set(leaves(loc))
        assert all(s == sets["en"] for s in sets.values()), {
            l: sorted(sets["en"] ^ s)[:5] for l, s in sets.items() if s != sets["en"]}


class TestPasswordLimits:
    def _register(self, password):
        return requests.post(f"{API}/auth/register",
                             json={"email": f"TEST_{uuid.uuid4().hex[:10]}@bookloop.com", "password": password, "name": "Pw"})

    def test_72_byte_password_works_and_logs_in(self):
        pw = "p" * 72
        email = f"TEST_{uuid.uuid4().hex[:10]}@bookloop.com"
        assert requests.post(f"{API}/auth/register", json={"email": email, "password": pw, "name": "Pw"}).status_code == 200
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
        r = requests.post(f"{API}/auth/register",
                          json={"email": email, "password": "test1234", "name": "Lang", "preferred_language": "it"})
        assert r.status_code == 200 and r.json()["user"]["preferred_language"] == "it"
        login = requests.post(f"{API}/auth/login", json={"email": email, "password": "test1234"})
        assert login.json()["user"]["preferred_language"] == "it"

    def test_register_ignores_unknown_language(self):
        email = f"TEST_{uuid.uuid4().hex[:10]}@bookloop.com"
        r = requests.post(f"{API}/auth/register",
                          json={"email": email, "password": "test1234", "name": "Lang", "preferred_language": "xx"})
        assert r.status_code == 200 and r.json()["user"]["preferred_language"] is None

    def test_register_without_language_still_works(self):
        email = f"TEST_{uuid.uuid4().hex[:10]}@bookloop.com"
        r = requests.post(f"{API}/auth/register", json={"email": email, "password": "test1234", "name": "Lang"})
        assert r.status_code == 200 and r.json()["user"]["preferred_language"] is None


# ------------------------------------------------------------------ GOOGLE SIGN-IN (against a fake Google)
# These run only when the backend under test was started with a fake Google, e.g.:
#   GOOGLE_CLIENT_ID=test-client-id GOOGLE_CLIENT_SECRET=test-secret
#   GOOGLE_AUTH_URL=http://127.0.0.1:8099/auth GOOGLE_TOKEN_URL=http://127.0.0.1:8099/token
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
                claims = {"iss": "https://accounts.google.com", "aud": TEST_CLIENT_ID, "email": email,
                          "email_verified": True, "name": name or "Google User",
                          "picture": "https://example.com/p.png", "exp": int(time.time()) + 3600}
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


APP_REDIRECT = "http://localhost:8081/"


def _google_start(redirect=APP_REDIRECT):
    from urllib.parse import urlparse, parse_qs
    r = requests.get(f"{API}/auth/google/start", params={"redirect": redirect}, allow_redirects=False)
    assert r.status_code == 302, r.text
    q = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}
    return r.headers["location"], q


def _google_callback(code, state, **extra):
    return requests.get(f"{API}/auth/google/callback", params={"code": code, "state": state, **extra},
                        allow_redirects=False)


def _google_login(email, prefix="ok", name="Gina Google"):
    """Full happy path; returns (session response, redirect Location)."""
    from urllib.parse import urlparse, parse_qs
    _, q = _google_start()
    cb = _google_callback(f"{prefix}:{email}:{name}", q["state"])
    assert cb.status_code == 302
    loc = cb.headers["location"]
    sid = parse_qs(urlparse(loc).query).get("session_id", [None])[0]
    return sid, loc


class TestGoogleLogin:
    def test_providers_reports_google_enabled(self, fake_google):
        assert requests.get(f"{API}/auth/providers").json() == {"google": True}

    def test_start_redirects_to_google_with_our_client(self, fake_google):
        loc, q = _google_start()
        assert loc.startswith(f"http://127.0.0.1:{FAKE_GOOGLE_PORT}/auth?")
        assert q["client_id"] == TEST_CLIENT_ID and q["response_type"] == "code"
        assert "email" in q["scope"] and "openid" in q["scope"]
        assert q["redirect_uri"].endswith("/api/auth/google/callback") and len(q["state"]) >= 32
        assert "emergent" not in loc.lower()

    def test_full_login_creates_user_and_session(self, fake_google):
        email = f"g_{uuid.uuid4().hex[:8]}@example.com"
        sid, loc = _google_login(email)
        assert loc.startswith(APP_REDIRECT + "?session_id=") and sid
        r = requests.post(f"{API}/auth/session", json={"session_id": sid})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["user"]["email"] == email and d["user"]["name"] == "Gina Google"
        assert d["user"]["avatar_url"] == "https://example.com/p.png"
        me = requests.get(f"{API}/auth/me", headers=_h(d["session_token"]))
        assert me.status_code == 200 and me.json()["user"]["email"] == email

    def test_session_id_is_single_use(self, fake_google):
        sid, _ = _google_login(f"g_{uuid.uuid4().hex[:8]}@example.com")
        assert requests.post(f"{API}/auth/session", json={"session_id": sid}).status_code == 200
        assert requests.post(f"{API}/auth/session", json={"session_id": sid}).status_code == 401

    def test_unknown_session_id_rejected(self, fake_google):
        assert requests.post(f"{API}/auth/session", json={"session_id": "nope"}).status_code == 401

    def test_second_google_login_reuses_account(self, fake_google):
        email = f"g_{uuid.uuid4().hex[:8]}@example.com"
        ids = []
        for _ in range(2):
            sid, _l = _google_login(email)
            ids.append(requests.post(f"{API}/auth/session", json={"session_id": sid}).json()["user"]["user_id"])
        assert ids[0] == ids[1]

    def test_links_to_existing_password_account(self, fake_google):
        email = f"g_{uuid.uuid4().hex[:8]}@example.com"
        reg = requests.post(f"{API}/auth/register", json={"email": email, "password": "test1234", "name": "Pat"}).json()
        sid, _l = _google_login(email)
        g = requests.post(f"{API}/auth/session", json={"session_id": sid}).json()
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
        assert r.status_code == 302 and "google_error=access_denied" in r.headers["location"]

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
        r = requests.get(f"{API}/auth/google/start", params={"redirect": redirect}, allow_redirects=False)
        assert r.status_code in (400, 422), (redirect, r.status_code)

    @pytest.mark.parametrize("redirect", ["http://localhost:8081/", "http://127.0.0.1:19006/x", "exp://192.168.1.5:8081/--/",
                                          "bookloop://", "bookloop://auth"])
    def test_redirect_allowlist_accepts_app_targets(self, fake_google, redirect):
        assert requests.get(f"{API}/auth/google/start", params={"redirect": redirect},
                            allow_redirects=False).status_code == 302

    def test_redirect_with_existing_query_appends_correctly(self, fake_google):
        from urllib.parse import urlparse, parse_qs
        _, q = _google_start("exp://192.168.1.5:8081/--/?a=1")
        loc = _google_callback("ok:qq@example.com:Q", q["state"]).headers["location"]
        assert loc.startswith("exp://192.168.1.5:8081/--/?a=1&session_id=")
        assert parse_qs(urlparse(loc).query)["session_id"]


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
        raw = "/api/files/bookloop/uploads/nobody/does-not-exist.png"
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
        other = "/api/files/bookloop/uploads/nobody/other.png" + signed[len(raw):]
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
