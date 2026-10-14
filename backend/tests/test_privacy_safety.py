"""Pre-launch privacy & safety guarantees, end to end through the API: location privacy, photo
metadata, image URLs, input limits, abuse limits, blocking, reports, account deletion, and that no
reader can reach another reader's private resources by changing an id.

Same requirements as test_bookloop_api.py: a backend started with OTP_DEBUG_MODE=true on a
throwaway database.
"""
import io
import os
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).parent.parent / ".env")
API = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or "http://127.0.0.1:8001").rstrip("/") + "/api"
BASE = API[: -len("/api")]
TASHKENT = {"lat": 41.3111, "lng": 69.2797}


def _db():
    return MongoClient(os.environ.get("MONGO_URL") or "mongodb://localhost:27017")[os.environ.get("DB_NAME") or "bookloop_test"]


def _h(u):
    return {"Authorization": f"Bearer {u['token']}"}


def _user(name=None, email=None, password="test1234"):
    tag = uuid.uuid4().hex[:10]
    email = email or f"TEST_priv_{tag}@bookloop.com"
    r = requests.post(f"{API}/auth/register/dev-instant", json={"email": email, "password": password, "name": name or f"Priv {tag}"})
    assert r.status_code == 200, r.text
    d = r.json()
    return {"token": d["session_token"], "id": d["user"]["user_id"], "email": email, "name": d["user"]["name"], "tag": tag}


def _get(u, path, **params):
    return requests.get(f"{API}{path}", params=params, headers=_h(u))


def _post(u, path, body=None):
    return requests.post(f"{API}{path}", json=body, headers=_h(u))


def _put(u, path, body=None):
    return requests.put(f"{API}{path}", json=body, headers=_h(u))


def _code(r):
    d = r.json().get("detail")
    return d.get("code") if isinstance(d, dict) else None


def _loc(u, lat, lng, **kw):
    r = _put(u, "/users/me/location", {"lat": lat, "lng": lng, "city": "Tashkent", **kw})
    assert r.status_code == 200, r.text
    return r.json()["user"]


def _book(u, title=None, **kw):
    r = _post(u, "/books", {"title": title or f"TEST book {uuid.uuid4().hex[:6]}", "author": "T", **kw})
    assert r.status_code == 200, r.text
    return r.json()["book"]


def _open_chat(a, b):
    r = _post(a, "/swaps", {"receiver_id": b["id"]})
    assert r.status_code == 200, r.text
    sid = r.json()["swap"]["id"]
    assert _post(b, f"/swaps/{sid}/allow-chat").status_code == 200
    return sid


def _completed_swap(a, b):
    ba, bb = _book(a)["id"], _book(b)["id"]
    r = _post(a, "/swaps", {"receiver_id": b["id"], "offered_book_id": ba, "requested_book_id": bb})
    assert r.status_code == 200, r.text
    sid = r.json()["swap"]["id"]
    assert _post(b, f"/swaps/{sid}/accept").status_code == 200
    assert _post(a, f"/swaps/{sid}/complete").status_code == 200
    assert _post(b, f"/swaps/{sid}/complete").status_code == 200
    return sid


def _jpeg_with_gps():
    Image = pytest.importorskip("PIL.Image")
    exif = Image.Exif()
    exif[0x0110] = "PhoneModel 12"
    exif.get_ifd(0x8825).update({1: "N", 2: (41.0, 18.0, 40.0), 3: "E", 4: (69.0, 16.0, 47.0)})
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (200, 100, 50)).save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


def _upload(u, data=None, name="p.jpg"):
    data = data or _jpeg_with_gps()
    return requests.post(f"{API}/upload", files={"file": (name, io.BytesIO(data), "image/jpeg")}, headers=_h(u))


def _keys_anywhere(obj, keys):
    """Every (key, value) pair in a JSON tree whose key is in `keys`."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys:
                out.append((k, v))
            out += _keys_anywhere(v, keys)
    elif isinstance(obj, list):
        for x in obj:
            out += _keys_anywhere(x, keys)
    return out


# ====================================================================== location privacy
class TestLocationPrivacy:
    @pytest.fixture(scope="class")
    def pair(self):
        victim, viewer = _user(), _user()
        _loc(victim, 41.31234, 69.28765, neighborhood="Chilonzor")
        _loc(viewer, 41.33, 69.30)
        _book(victim, f"TEST loc {victim['tag']}")
        return victim, viewer

    def test_no_coordinates_of_others_in_any_response(self, pair):
        victim, viewer = pair
        assert _put(viewer, f"/favorites/{victim['id']}").status_code == 200
        responses = [
            _get(viewer, f"/users/{victim['id']}"), _get(viewer, "/discover/people", search=victim["tag"]),
            _get(viewer, "/discover/books", search=victim["tag"]), _get(viewer, "/favorites"),
            _get(viewer, "/map/clusters"), _get(viewer, "/wishlist"),
        ]
        for r in responses:
            assert r.status_code == 200, r.url
            leaked = [(k, v) for k, v in _keys_anywhere(r.json(), {"lat", "lng", "location_updated_at", "email"}) if v is not None]
            # Map clusters carry a ~1 km rounded center of an AREA, never a person's position.
            if "/map/clusters" in r.url:
                leaked = [(k, v) for k, v in leaked if round(v, 2) != v]
            assert not leaked, (r.url, leaked)

    def test_distances_are_whole_km_with_a_1_km_floor(self, pair):
        victim, viewer = pair
        d = _get(viewer, f"/users/{victim['id']}").json()["user"]["distance_km"]
        assert d == int(d) and d >= 1
        # Even from the victim's exact position, the answer is only "within 1 km".
        spy = _user()
        _loc(spy, 41.31234, 69.28765)
        assert _get(spy, f"/users/{victim['id']}").json()["user"]["distance_km"] == 1.0

    def test_triangulation_cannot_find_a_position_inside_the_cell(self):
        # Two victims in the same ~1 km cell, ~400 m apart: from anywhere, they look identical.
        v1, v2, spy = _user(), _user(), _user()
        _loc(v1, 41.3112, 69.2711)
        _loc(v2, 41.3148, 69.2759)
        for lat, lng in ((41.30, 69.20), (41.40, 69.29), (41.31, 69.40), (41.2, 69.1), (41.3149, 69.2701)):
            _loc(spy, lat, lng)
            d1 = _get(spy, f"/users/{v1['id']}").json()["user"]["distance_km"]
            d2 = _get(spy, f"/users/{v2['id']}").json()["user"]["distance_km"]
            assert d1 == d2, (lat, lng, d1, d2)

    def test_distance_filter_uses_the_same_coarse_distance(self, pair):
        victim, viewer = pair
        spy = _user()
        _loc(spy, 41.3125, 69.2880)  # a few hundred meters away
        ids = [p["user_id"] for p in _get(spy, "/discover/people", search=victim["tag"], max_distance="1").json()["people"]]
        assert victim["id"] in ids

    def test_own_location_stored_at_reduced_precision(self):
        u = _user()
        me = _loc(u, 41.312345, 69.287654)
        assert me["lat"] == 41.312 and me["lng"] == 69.288  # ~110 m: the reader's own map only

    def test_location_updates_are_rate_limited(self):
        u = _user()
        codes = [_put(u, "/users/me/location", {"lat": 41.3, "lng": 69.2}).status_code for _ in range(31)]
        assert codes[:30] == [200] * 30 and codes[30] == 429


# ====================================================================== photos + image urls
class TestUploadsAndImages:
    def test_gps_and_device_metadata_removed_from_uploads(self):
        u = _user()
        raw = _jpeg_with_gps()
        assert b"Exif" in raw and b"PhoneModel" in raw
        r = _upload(u, raw)
        assert r.status_code == 200, r.text
        stored = requests.get(f"{BASE}{r.json()['url']}", headers=_h(u))
        assert stored.status_code == 200
        assert b"Exif" not in stored.content and b"PhoneModel" not in stored.content
        assert stored.content[:3] == b"\xff\xd8\xff"

    def test_heic_with_exif_refused_because_it_cannot_be_removed(self):
        u = _user()
        heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 200
        r = _upload(u, heic + b"Exif\x00\x00MM\x00*GPSLatitude", "p.heic")
        assert r.status_code == 400 and _code(r) == "unsupported_image"
        assert _upload(u, heic, "p.heic").status_code == 200  # nothing to leak: accepted

    def test_files_need_a_session_or_a_valid_signature(self):
        u = _user()
        url = _upload(u).json()["url"].split("?", 1)[0]  # API responses carry a signed URL; probe the bare path
        assert requests.get(f"{BASE}{url}").status_code == 401
        assert requests.get(f"{BASE}{url}?exp=9999999999&sig=forged").status_code == 403

    def test_chat_images_must_be_the_senders_own_uploads(self):
        a, b, c = _user(), _user(), _user()
        sid = _open_chat(a, b)
        for bad in ("https://evil.example/pixel.png", "http://192.168.1.1/x.jpg",
                    _upload(c).json()["url"], "/api/files/bookloop/uploads/" + a["id"] + "/../" + c["id"] + "/x.jpg"):
            r = _post(a, f"/swaps/{sid}/messages", {"image_url": bad})
            assert r.status_code == 400 and _code(r) == "invalid_image_url", bad
        own = _upload(a).json()["url"]
        assert _post(a, f"/swaps/{sid}/messages", {"image_url": own}).status_code == 200

    def test_avatar_and_cover_urls_are_restricted(self):
        u = _user()
        assert _code(_put(u, "/users/me", {"avatar_url": "https://evil.example/a.png"})) == "invalid_image_url"
        assert _put(u, "/users/me", {"avatar_url": _upload(u).json()["url"]}).status_code == 200
        assert _code(_post(u, "/books", {"title": "X", "cover_url": "https://evil.example/c.png"})) == "invalid_image_url"
        ok = _post(u, "/books", {"title": "X", "cover_url": "https://covers.openlibrary.org/b/id/1-M.jpg"})
        assert ok.status_code == 200
        # A stored value may be sent back unchanged (older app builds always resend the whole form).
        bid = ok.json()["book"]["id"]
        assert _put(u, f"/books/{bid}", {"title": "Y", "cover_url": "https://covers.openlibrary.org/b/id/1-M.jpg"}).status_code == 200


# ====================================================================== limits
class TestLimits:
    def test_oversized_fields_rejected(self):
        u = _user()
        assert _put(u, "/users/me", {"bio": "x" * 5000}).status_code == 422
        assert _put(u, "/users/me", {"name": "x" * 500}).status_code == 422
        assert _post(u, "/books", {"title": "x" * 5000}).status_code == 422
        a, b = _user(), _user()
        sid = _open_chat(a, b)
        assert _post(a, f"/swaps/{sid}/messages", {"text": "x" * 50000}).status_code == 422

    def test_login_locks_after_repeated_wrong_passwords(self):
        u = _user(password="Correct1pass")
        for _ in range(10):
            assert requests.post(f"{API}/auth/login", json={"email": u["email"], "password": "wrong"}).status_code == 401
        r = requests.post(f"{API}/auth/login", json={"email": u["email"], "password": "Correct1pass"})
        assert r.status_code == 429 and _code(r) == "too_many_attempts"
        other = _user(password="Correct1pass")  # other accounts are unaffected
        assert requests.post(f"{API}/auth/login", json={"email": other["email"], "password": "Correct1pass"}).status_code == 200

    def test_successful_logins_never_count_toward_the_lock(self):
        u = _user(password="Correct1pass")
        for _ in range(15):
            assert requests.post(f"{API}/auth/login", json={"email": u["email"], "password": "Correct1pass"}).status_code == 200

    def test_chat_messages_rate_limited(self):
        a, b = _user(), _user()
        sid = _open_chat(a, b)
        codes = [_post(a, f"/swaps/{sid}/messages", {"text": f"m{i}"}).status_code for i in range(31)]
        assert codes[:30] == [200] * 30 and codes[30] == 429
        assert _post(b, f"/swaps/{sid}/messages", {"text": "still fine"}).status_code == 200  # per sender


# ====================================================================== blocking
class TestBlocking:
    @pytest.fixture()
    def ab(self):
        a, b = _user(), _user()
        for u in (a, b):
            _loc(u, 41.3111, 69.2797, neighborhood=f"Blk {a['tag']}")
        _book(a, f"TEST blk {a['tag']}")
        bb = _book(b, f"TEST blk {b['tag']}")
        return a, b, bb

    def _visible(self, viewer, other):
        return {
            "profile": _get(viewer, f"/users/{other['id']}").status_code == 200,
            "people": other["id"] in [p["user_id"] for p in _get(viewer, "/discover/people", search=other["tag"]).json()["people"]],
            "books": any(x["owner_id"] == other["id"] for x in _get(viewer, "/discover/books", search=other["tag"]).json()["books"]),
        }

    def test_block_hides_both_ways_everywhere_and_unblock_restores(self, ab):
        a, b, bb = ab
        assert all(self._visible(a, b).values()) and all(self._visible(b, a).values())
        assert _put(a, f"/blocks/{b['id']}").status_code == 200
        assert not any(self._visible(a, b).values()) and not any(self._visible(b, a).values())
        assert _get(a, f"/books/detail/{bb['id']}").status_code == 404
        clusters = _get(a, "/map/clusters").json()["clusters"]
        assert all(c["neighborhood"] != f"Blk {a['tag']}" or c["people_count"] == 1 for c in clusters)
        assert [x["user_id"] for x in _get(a, "/blocks").json()["blocked"]] == [b["id"]]
        assert _get(b, "/blocks").json()["blocked"] == []  # who blocked you is never revealed
        assert _delete(a, f"/blocks/{b['id']}").status_code == 200
        assert all(self._visible(a, b).values()) and all(self._visible(b, a).values())

    def test_block_stops_every_interaction(self, ab):
        a, b, _ = ab
        sid = _open_chat(a, b)
        assert _put(a, f"/favorites/{b['id']}").status_code == 200
        assert _put(b, f"/blocks/{a['id']}").status_code == 200
        swap = _get(a, f"/swaps/{sid}").json()
        assert swap["swap"]["status"] == "cancelled"  # history stays readable
        assert any(m.get("key") == "swapChat.sys.closed" for m in swap["messages"])
        for who in (a, b):
            r = _post(who, f"/swaps/{sid}/messages", {"text": "hi"})
            assert r.status_code in (403, 409), r.text
        assert _post(a, "/swaps", {"receiver_id": b["id"]}).status_code == 404
        assert _get(a, f"/swaps/with/{b['id']}").json()["can_propose"] is False
        assert b["id"] not in [f["user_id"] for f in _get(a, "/favorites").json()["favorites"]]
        assert _put(a, f"/favorites/{b['id']}").status_code == 404

    def test_completed_swap_chat_is_closed_by_a_block(self):
        a, b = _user(), _user()
        sid = _completed_swap(a, b)
        assert _post(a, f"/swaps/{sid}/messages", {"text": "after"}).status_code == 200
        _put(a, f"/blocks/{b['id']}")
        r = _post(b, f"/swaps/{sid}/messages", {"text": "hello?"})
        assert r.status_code == 403 and _code(r) == "blocked"

    def test_cannot_block_self(self):
        u = _user()
        assert _put(u, f"/blocks/{u['id']}").status_code == 400


def _delete(u, path):
    return requests.delete(f"{API}{path}", headers=_h(u))


# ====================================================================== reports
class TestReports:
    def test_report_each_kind_of_content(self):
        a, b = _user(), _user()
        sid = _completed_swap(a, b)
        msg = _post(b, f"/swaps/{sid}/messages", {"text": "rude message"}).json()["message"]
        _post(b, f"/swaps/{sid}/rate", {"stars": 1, "review": "bad review"})
        review = _get(a, f"/users/{a['id']}").json()["reviews"][0]
        book = _book(b)
        for t, tid in (("user", b["id"]), ("book", book["id"]), ("message", msg["id"]), ("swap", sid), ("review", review["id"])):
            r = _post(a, "/reports", {"target_type": t, "target_id": tid, "reason": "harassment", "details": "x"})
            assert r.status_code == 200, (t, r.text)
            stored = _db().reports.find_one({"id": r.json()["report_id"]})
            assert stored["reporter_id"] == a["id"] and stored["target_user_id"] == b["id"] and stored["status"] == "open"
        assert _db().reports.find_one({"target_id": msg["id"]})["snapshot"]["text"] == "rude message"

    def test_cannot_report_own_or_invisible_content(self):
        a, b, c = _user(), _user(), _user()
        sid = _open_chat(b, c)
        m = _post(b, f"/swaps/{sid}/messages", {"text": "private"}).json()["message"]
        assert _post(a, "/reports", {"target_type": "message", "target_id": m["id"], "reason": "spam"}).status_code == 404
        assert _post(a, "/reports", {"target_type": "swap", "target_id": sid, "reason": "spam"}).status_code == 404
        assert _post(a, "/reports", {"target_type": "user", "target_id": a["id"], "reason": "spam"}).status_code == 404
        assert _post(a, "/reports", {"target_type": "book", "target_id": _book(a)["id"], "reason": "spam"}).status_code == 404
        assert _post(a, "/reports", {"target_type": "user", "target_id": "user_nope", "reason": "spam"}).status_code == 404
        assert _post(a, "/reports", {"target_type": "user", "target_id": b["id"], "reason": "made-up"}).status_code == 422

    def test_duplicate_report_is_not_stored_twice_and_reports_are_rate_limited(self):
        a = _user()
        targets = [_user() for _ in range(21)]
        first = _post(a, "/reports", {"target_type": "user", "target_id": targets[0]["id"], "reason": "spam"}).json()["report_id"]
        again = _post(a, "/reports", {"target_type": "user", "target_id": targets[0]["id"], "reason": "spam"}).json()["report_id"]
        assert first == again
        codes = [_post(a, "/reports", {"target_type": "user", "target_id": t["id"], "reason": "spam"}).status_code for t in targets[1:]]
        assert codes[:19] == [200] * 19 and codes[19] == 429


# ====================================================================== account deletion
class TestAccountDeletion:
    def test_requires_explicit_confirmation(self):
        u = _user()
        assert _post(u, "/users/me/delete", {"confirm": False}).status_code == 400
        assert _post(u, "/users/me/delete", {}).status_code == 422
        assert _get(u, "/auth/me").status_code == 200

    def test_deletion_removes_identity_everywhere(self):
        gone = _user(name="Zarina Deletable")
        other, fan = _user(), _user()
        _loc(gone, **TASHKENT)
        avatar = _upload(gone).json()["url"]
        _put(gone, "/users/me", {"avatar_url": avatar, "bio": "my bio"})
        book = _book(gone, f"TEST del {gone['tag']}")
        done = _completed_swap(gone, other)
        _post(gone, f"/swaps/{done}/rate", {"stars": 5, "review": "lovely"})
        _post(other, f"/swaps/{done}/rate", {"stars": 4, "review": "about gone"})
        open_sid = _open_chat(other, gone)
        photo = _upload(gone).json()["url"]
        _post(gone, f"/swaps/{open_sid}/messages", {"image_url": photo})
        _put(fan, f"/favorites/{gone['id']}")
        _post(gone, "/push/register", {"token": f"ExponentPushToken[{gone['tag']}]", "platform": "ios"})

        assert _post(gone, "/users/me/delete", {"confirm": True}).status_code == 200

        # Signed out everywhere, cannot sign in, profile and listings gone.
        assert _get(gone, "/auth/me").status_code == 401
        assert requests.post(f"{API}/auth/login", json={"email": gone["email"], "password": "test1234"}).status_code == 401
        assert _get(other, f"/users/{gone['id']}").status_code == 404
        assert _get(other, f"/books/detail/{book['id']}").status_code == 404
        assert not _get(other, "/discover/books", search=gone["tag"]).json()["books"]
        assert gone["id"] not in [p["user_id"] for p in _get(other, "/discover/people", search="Zarina").json()["people"]]
        assert _get(fan, "/favorites").json()["favorites"] == []
        # Their photos are deleted from storage.
        for url in (avatar, photo):
            assert requests.get(f"{BASE}{url}", headers=_h(other)).status_code == 404
        # The other reader keeps their history, without the deleted reader's identity.
        swaps = _get(other, "/swaps").json()
        listed = [s for bucket in swaps.values() if isinstance(bucket, list) for s in bucket if s["id"] in (done, open_sid)]
        assert listed and all(s["other_user"].get("is_deleted") and s["other_user"]["name"] == "" for s in listed)
        conv = _get(other, f"/swaps/{open_sid}").json()
        assert conv["swap"]["status"] == "cancelled"
        assert any(m.get("key") == "swapChat.sys.accountDeleted" for m in conv["messages"])
        assert "Zarina" not in str(conv) and "Zarina" not in str(_get(other, f"/swaps/{done}").json())
        assert all(not m.get("image_url") for m in conv["messages"])
        # The review they wrote stays for the reviewed reader, anonymously; the one about them is gone.
        reviews = _get(other, f"/users/{other['id']}").json()["reviews"]
        mine = [r for r in reviews if r["review"] == "lovely"]
        assert mine and mine[0]["rater_id"] is None and "Zarina" not in str(mine[0])
        assert _db().ratings.count_documents({"ratee_id": gone["id"]}) == 0
        doc = _db().users.find_one({"user_id": gone["id"]})
        for field in ("name", "bio", "avatar_url", "lat", "lng", "city", "password_hash"):
            assert not doc.get(field), field
        assert gone["email"].lower() not in str(doc).lower()
        assert _db().push_tokens.count_documents({"user_id": gone["id"]}) == 0
        assert _db().user_sessions.count_documents({"user_id": gone["id"]}) == 0
        # The email address is free again: the person can come back with a fresh account.
        again = _user(email=gone["email"])
        assert again["id"] != gone["id"]


# ====================================================================== cross-user access (IDOR)
class TestCrossUserAccess:
    def test_cannot_reach_another_readers_private_resources_by_id(self):
        a, b, c = _user(), _user(), _user()
        sid = _open_chat(a, b)
        a_book = _book(a)
        assert _get(c, f"/swaps/{sid}").status_code == 404
        assert _post(c, f"/swaps/{sid}/messages", {"text": "x"}).status_code == 404
        for action in ("accept", "decline", "cancel", "complete", "read", "allow-chat"):
            assert _post(c, f"/swaps/{sid}/{action}").status_code == 404, action
        assert _post(c, f"/swaps/{sid}/rate", {"stars": 1}).status_code == 404
        assert _put(c, f"/books/{a_book['id']}", {"title": "hijacked"}).status_code == 404
        assert _delete(c, f"/books/{a_book['id']}").status_code == 404
        assert _get(a, f"/books/detail/{a_book['id']}").json()["book"]["title"] == a_book["title"]

    def test_private_profile_fields_only_for_the_owner(self):
        a, b = _user(), _user()
        _loc(a, **TASHKENT)
        other_view = _get(b, f"/users/{a['id']}").json()["user"]
        for k in ("email", "lat", "lng", "notifications_enabled", "location_updated_at"):
            assert k not in other_view, k
        assert _get(a, f"/users/{a['id']}").json()["user"]["email"] == a["email"].lower()

    def test_push_tokens_cannot_be_removed_by_another_reader(self):
        a, b = _user(), _user()
        token = f"ExponentPushToken[{a['tag']}]"
        assert _post(a, "/push/register", {"token": token}).status_code == 200
        _post(b, "/push/unregister", {"token": token})
        assert _db().push_tokens.find_one({"token": token})["user_id"] == a["id"]

    def test_notifications_and_favorites_are_per_reader(self):
        a, b, c = _user(), _user(), _user()
        _put(a, f"/favorites/{c['id']}")
        assert _get(b, "/favorites").json()["favorites"] == []
        _post(b, "/swaps", {"receiver_id": a["id"]})
        feed_c = _get(c, "/notifications/feed").json()
        assert all(i.get("actor", {}).get("user_id") != b["id"] for i in feed_c.get("items", []))


# ====================================================================== reviews
class TestReviewAnonymity:
    def test_anonymous_off_by_default_and_on_hides_identity(self):
        a, b, c, d = _user(), _user(), _user(), _user()
        s1 = _completed_swap(a, b)
        assert _post(a, f"/swaps/{s1}/rate", {"stars": 5, "review": "named"}).status_code == 200
        s2 = _completed_swap(c, b)
        assert _post(c, f"/swaps/{s2}/rate", {"stars": 4, "review": "hidden", "is_anonymous": True}).status_code == 200
        reviews = {r["review"]: r for r in _get(d, f"/users/{b['id']}").json()["reviews"]}
        assert reviews["named"]["rater_id"] == a["id"] and reviews["named"]["is_anonymous"] is False
        assert reviews["hidden"]["rater_id"] is None and c["id"] not in str(reviews["hidden"])

    def test_no_review_without_a_completed_swap_or_twice_or_of_self(self):
        a, b, c = _user(), _user(), _user()
        sid = _open_chat(a, c)  # an open (not completed) swap cannot be reviewed
        assert _post(a, f"/swaps/{sid}/rate", {"stars": 5}).status_code == 400
        done = _completed_swap(a, b)
        assert _post(a, f"/swaps/{done}/rate", {"stars": 5}).status_code == 200
        assert _post(a, f"/swaps/{done}/rate", {"stars": 1}).status_code == 400
        assert _post(a, "/swaps", {"receiver_id": a["id"]}).status_code == 400  # no swap with yourself
