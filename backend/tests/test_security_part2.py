"""Security audit part 2 (checklist items 12, 15, 16, 18, 19, 20): regression tests for the issues found
and fixed, plus server-side guarantees the audit verified. Item 11 is covered by test_log_redaction.py.

Same requirements as test_bookloop_api.py: a backend started with OTP_DEBUG_MODE=true on a throwaway
database (the email test at the top needs no backend).
"""
import io
import json
import os
import sys
import time
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from email_provider import otp_email_body  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env")
API = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or "http://127.0.0.1:8001").rstrip("/") + "/api"
BASE = API[: -len("/api")]
EVIL = "https://evil.example"


# ====================================================================== 19: HTML injection in the OTP email
def test_otp_email_never_carries_markup_or_extra_lines_from_the_name():
    # Anyone can request a code for anyone's address, choosing the name the email greets.
    name = '<a href="https://evil.example/verify">Verify your account</a><img src=x>\n\nYour code is 000000'
    _subject, html, text = otp_email_body(name, "123456")
    assert "<a " not in html and "<img" not in html and "evil.example/verify\">" not in html
    assert "&lt;a href=&quot;https://evil.example/verify&quot;&gt;" in html
    assert "\n\nYour code is 000000" not in text and "Your BookLoop verification code is: 123456" in text
    greeting = text.split("\n", 1)[0]
    assert greeting.startswith("Hi <a href=") and greeting.endswith("Your code is 000000,")  # one plain line


def test_otp_email_keeps_multilingual_names_and_has_a_fallback():
    for name in ("Oʻtabek", "Отабек", "أوتابك", "Niccolò"):
        _s, html, text = otp_email_body(name, "123456")
        assert f"Hi {name}," in text and f"Hi {name}," in html
    assert otp_email_body("  \n ", "123456")[2].startswith("Hi there,")


# ====================================================================== helpers (live backend)
def _h(u):
    return {"Authorization": f"Bearer {u['token']}"}


def _user():
    email = f"TEST_sec2_{uuid.uuid4().hex[:10]}@bookloop.com"
    r = requests.post(f"{API}/auth/register/dev-instant", json={"email": email, "password": "Test1234!", "name": "Sec2"})
    assert r.status_code == 200, r.text
    d = r.json()
    return {"token": d["session_token"], "id": d["user"]["user_id"], "email": email}


def _book(u, **kw):
    r = requests.post(f"{API}/books", json={"title": f"TEST sec2 {uuid.uuid4().hex[:6]}", **kw}, headers=_h(u))
    assert r.status_code == 200, r.text
    return r.json()["book"]


def _active_swap(a, b):
    ba, bb = _book(a)["id"], _book(b)["id"]
    r = requests.post(f"{API}/swaps", json={"receiver_id": b["id"], "offered_book_id": ba, "requested_book_id": bb}, headers=_h(a))
    assert r.status_code == 200, r.text
    sid = r.json()["swap"]["id"]
    assert requests.post(f"{API}/swaps/{sid}/accept", headers=_h(b)).status_code == 200
    return sid, ba, bb


# ====================================================================== 12: error details
class TestErrorDetails:
    def test_api_schema_and_docs_are_not_served(self):
        for path in ("/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"):
            assert requests.get(BASE + path).status_code == 404, path

    def test_validation_errors_do_not_echo_input_or_library_details(self):
        secret = "Sup3r-Secret-Passw0rd!"
        r = requests.post(f"{API}/auth/login", json={"email": "not-an-email", "password": secret})
        assert r.status_code == 422
        assert secret not in r.text and "pydantic" not in r.text and "errors.pydantic.dev" not in r.text
        detail = r.json()["detail"]
        assert detail and all(set(e) == {"type", "loc", "msg"} for e in detail)
        assert detail[0]["loc"] == ["body", "email"] and detail[0]["msg"]  # the app shows detail[0].msg

        r = requests.post(f"{API}/auth/login", json={"email": "x@example.com"})  # whole body was echoed before
        assert r.status_code == 422 and "x@example.com" not in r.text
        assert r.json()["detail"] == [{"type": "missing", "loc": ["body", "password"], "msg": "Field required"}]

    def test_malformed_json_gives_a_plain_422(self):
        r = requests.post(f"{API}/auth/login", data='{"email": "x@example.com", "password": ', headers={"Content-Type": "application/json"})
        assert r.status_code == 422
        assert r.json()["detail"][0]["type"] == "json_invalid" and "ctx" not in r.text and "Expecting" not in r.text

    def test_common_failures_are_short_and_structured(self):
        u = _user()
        for r, status in ((requests.get(f"{API}/auth/me", headers={"Authorization": "Bearer nope"}), 401),
                          (requests.get(f"{API}/books/detail/book_doesnotexist", headers=_h(u)), 404),
                          (requests.get(f"{API}/swaps/swap_doesnotexist", headers=_h(u)), 404),
                          (requests.get(f"{API}/files/bookloop/uploads/x/none.jpg", params={"exp": 1, "sig": "00"}), 403),
                          (requests.post(f"{API}/upload", files={"file": ("x.txt", b"hello", "text/plain")}, headers=_h(u)), 400)):
            assert r.status_code == status and isinstance(r.json()["detail"], (str, dict))
            for leak in ("Traceback", "File \"", "site-packages", "pymongo", "motor", "bookloop\\backend", "/backend/"):
                assert leak not in r.text


# ====================================================================== 16: request size and input bounds
class TestRequestBodyLimits:
    def test_oversized_json_body_is_refused_before_it_is_read(self):
        body = json.dumps({"email": "x@example.com", "password": "A" * (2 * 1024 * 1024)})
        r = requests.post(f"{API}/auth/login", data=body, headers={"Content-Type": "application/json", "Origin": EVIL})
        assert r.status_code == 413 and r.json() == {"detail": "Request body too large"}
        assert r.headers.get("access-control-allow-origin") == "*"  # still readable by a web client

    def test_oversized_streamed_body_is_refused(self):
        def chunks():
            yield b'{"email":"x@example.com","password":"'
            for _ in range(3):
                yield b"A" * (1024 * 1024)
            yield b'"}'
        r = requests.post(f"{API}/auth/login", data=chunks(), headers={"Content-Type": "application/json"})
        assert r.status_code == 413

    def test_oversized_upload_is_refused_and_normal_bodies_pass(self):
        u = _user()
        big = b"\xff\xd8\xff" + b"0" * (8 * 1024 * 1024 + 128 * 1024)
        r = requests.post(f"{API}/upload", files={"file": ("big.jpg", io.BytesIO(big), "image/jpeg")}, headers=_h(u))
        assert r.status_code == 413
        r = requests.put(f"{API}/users/me", json={"bio": "ä" * 1000, "name": "Normal size"}, headers=_h(u))
        assert r.status_code == 200, r.text


class TestInputBounds:
    def test_profile_list_entries_are_bounded(self):
        u = _user()
        r = requests.put(f"{API}/users/me", json={"genres": ["G" * 100_000]}, headers=_h(u))
        assert r.status_code == 422
        r = requests.put(f"{API}/users/me", json={"reading_interests": ["x" * 81]}, headers=_h(u))
        assert r.status_code == 422
        r = requests.put(f"{API}/users/me", json={"languages": ["L" * 80] * 20}, headers=_h(u))
        assert r.status_code == 200

    def test_multilingual_profile_values_round_trip(self):
        u = _user()
        langs = ["Oʻzbekcha", "Русский", "العربية", "Italiano", "English"]
        r = requests.put(f"{API}/users/me", json={"languages": langs, "name": "Отабек Oʻtabek أوتابك", "bio": "Libri e книги 📚"}, headers=_h(u))
        assert r.status_code == 200, r.text
        me = r.json()["user"]
        assert me["languages"] == langs and me["name"] == "Отабек Oʻtabek أوتابك" and me["bio"] == "Libri e книги 📚"

    @pytest.mark.parametrize("value", ["nan", "inf", "-inf", "-5", "abc"])
    def test_wishlist_distance_must_be_a_real_radius(self, value):
        u = _user()
        assert requests.get(f"{API}/wishlist", params={"max_distance": value}, headers=_h(u)).status_code == 422

    def test_wishlist_default_and_valid_radius(self):
        u = _user()
        assert requests.get(f"{API}/wishlist", headers=_h(u)).status_code == 200
        assert requests.get(f"{API}/wishlist", params={"max_distance": "0"}, headers=_h(u)).status_code == 200
        assert requests.get(f"{API}/wishlist", params={"max_distance": "12.5"}, headers=_h(u)).status_code == 200


# ====================================================================== 15: enforced by the server, not the app
class TestServerSideAuthorization:
    def test_deleting_someone_elses_book_reveals_nothing(self):
        a, b, c = _user(), _user(), _user()
        _sid, ba, _bb = _active_swap(a, b)
        free = _book(a)["id"]
        for bid in (ba, free, "book_doesnotexist"):
            r = requests.delete(f"{API}/books/{bid}", headers=_h(c))
            assert r.status_code == 404 and r.json()["detail"] == "Book not found", bid
        # The owner still gets the real answer, and nothing was deleted by the stranger.
        assert requests.delete(f"{API}/books/{ba}", headers=_h(a)).status_code == 409
        assert requests.get(f"{API}/books/detail/{free}", headers=_h(a)).status_code == 200
        assert requests.delete(f"{API}/books/{free}", headers=_h(a)).status_code == 200
        assert requests.delete(f"{API}/books/{free}", headers=_h(a)).status_code == 200  # still idempotent
        assert requests.get(f"{API}/books/detail/{free}", headers=_h(a)).status_code == 404

    def test_server_controlled_profile_fields_cannot_be_set(self):
        u = _user()
        r = requests.put(f"{API}/users/me", json={
            "rating": 5, "rating_count": 99, "swaps_count": 99, "email": "evil@example.com", "user_id": "user_evil",
            "password_hash": "x", "seed": True, "test_account": False, "deleted_at": "2020-01-01", "lat": 1.0,
            "lng": 2.0, "location_updated_at": "2020-01-01", "city": "Fake", "google_sub": "x",
        }, headers=_h(u))
        assert r.status_code == 200
        me = requests.get(f"{API}/auth/me", headers=_h(u)).json()["user"]
        assert (me["user_id"], me["email"], me["rating"], me["rating_count"], me["swaps_count"]) == (u["id"], u["email"].lower(), 0.0, 0, 0)
        assert me["lat"] is None and me["city"] is None
        assert requests.post(f"{API}/auth/login", json={"email": u["email"], "password": "Test1234!"}).status_code == 200

    def test_server_controlled_book_and_swap_fields_cannot_be_set(self):
        a, b = _user(), _user()
        r = requests.post(f"{API}/books", json={"title": "TEST owner spoof", "owner_id": b["id"], "id": "book_mine", "deleted_at": "x"}, headers=_h(a))
        book = r.json()["book"]
        assert book["owner_id"] == a["id"] and book["id"] != "book_mine"
        r = requests.put(f"{API}/books/{book['id']}", json={"owner_id": b["id"], "title": "TEST renamed"}, headers=_h(a))
        assert r.status_code == 200 and r.json()["book"]["owner_id"] == a["id"]
        assert requests.put(f"{API}/books/{book['id']}", json={"title": "TEST stolen"}, headers=_h(b)).status_code == 404

        r = requests.post(f"{API}/swaps", json={"receiver_id": b["id"], "status": "completed", "chat_accepted_at": "2020-01-01",
                                                "requester_id": b["id"]}, headers=_h(a))
        swap = r.json()["swap"]
        assert swap["status"] == "pending" and swap["requester_id"] == a["id"] and swap["chat_accepted"] is False
        r = requests.post(f"{API}/swaps/{swap['id']}/messages", json={"text": "hi", "sender_id": b["id"], "type": "system"}, headers=_h(a))
        assert r.status_code == 403  # chat is still locked: the requester can't unlock it by sending fields

    def test_actions_reserved_to_the_other_side_are_refused(self):
        a, b, c = _user(), _user(), _user()
        r = requests.post(f"{API}/swaps", json={"receiver_id": b["id"]}, headers=_h(a))
        sid = r.json()["swap"]["id"]
        assert requests.post(f"{API}/swaps/{sid}/allow-chat", headers=_h(a)).status_code == 403  # only the recipient
        for action in ("allow-chat", "accept", "decline", "cancel", "complete", "read"):
            assert requests.post(f"{API}/swaps/{sid}/{action}", headers=_h(c)).status_code == 404, action
        assert requests.post(f"{API}/swaps/{sid}/rate", json={"stars": 5}, headers=_h(c)).status_code == 404


# ====================================================================== 18: NoSQL injection
OPERATOR_PAYLOADS = [
    ("post", "/auth/login", {"email": "{victim}", "password": {"$ne": None}}),
    ("post", "/auth/login", {"email": {"$ne": None}, "password": "x"}),
    ("post", "/auth/login", {"email": {"$regex": ".*"}, "password": {"$gt": ""}}),
    ("post", "/auth/register/verify-otp", {"email": "{victim}", "code": {"$ne": ""}}),
    ("post", "/auth/register/complete", {"email": "{victim}", "password": "Aa1!aaaa", "verification_token": {"$ne": ""}}),
    ("post", "/auth/register/resend-otp", {"email": {"$ne": None}}),
    ("post", "/auth/session", {"session_id": {"$ne": ""}, "code_verifier": "a" * 43}),
    ("post", "/auth/google/link/complete", {"link_id": {"$ne": ""}, "code_verifier": "a" * 43}),
    ("post", "/auth/google/native", {"id_token": "x", "nonce_id": {"$ne": ""}}),
    ("post", "/auth/apple", {"identity_token": "x", "nonce_id": {"$gt": ""}}),
    ("post", "/swaps", {"receiver_id": {"$ne": "x"}}),
    ("post", "/swaps", {"receiver_id": "{other}", "offered_book_id": {"$ne": ""}, "requested_book_id": {"$ne": ""}}),
    ("post", "/reports", {"target_type": "user", "target_id": {"$regex": ".*"}, "reason": "spam"}),
    ("post", "/reports", {"target_type": {"$ne": "x"}, "target_id": "x", "reason": "spam"}),
    ("put", "/users/me", {"genres": {"$where": "sleep(1000)"}}),
    ("put", "/users/me", {"name": {"$set": {"rating": 5}}}),
    ("post", "/push/register", {"token": {"$ne": ""}}),
    ("post", "/push/unregister", {"token": {"$ne": ""}}),
    ("post", "/users/me/delete", {"confirm": {"$ne": False}}),
]


@pytest.fixture(scope="module")
def ab():
    return _user(), _user()


class TestNoSqlInjection:
    @pytest.mark.parametrize("method, path, body", OPERATOR_PAYLOADS)
    def test_operator_objects_are_rejected_where_strings_are_expected(self, ab, method, path, body):
        a, b = ab
        raw = json.dumps(body).replace('"{victim}"', json.dumps(b["email"])).replace('"{other}"', json.dumps(b["id"]))
        r = requests.request(method.upper(), f"{API}{path}", data=raw, headers={**_h(a), "Content-Type": "application/json"})
        assert r.status_code == 422, (path, r.status_code, r.text[:200])
        assert "session_token" not in r.text

    def test_operator_shaped_keys_in_a_body_change_nothing(self, ab):
        a, _b = ab
        r = requests.put(f"{API}/users/me", json={"$set": {"rating": 5}, "rating.$": 1, "$inc": {"swaps_count": 9}, "bio": "ok"}, headers=_h(a))
        assert r.status_code == 200
        me = r.json()["user"]
        assert me["rating"] == 0.0 and me["swaps_count"] == 0 and me["bio"] == "ok"

    def test_message_id_operator_cannot_move_the_read_marker(self, ab):
        a, b = ab
        sid, _x, _y = _active_swap(a, b)
        assert requests.post(f"{API}/swaps/{sid}/read", json={"message_id": {"$gt": ""}}, headers=_h(a)).status_code == 422

    def test_query_string_operators_and_regex_are_literal(self, ab):
        a, b = ab
        _book(b, title="Zzyzx literal regex test")
        everything = [x["id"] for x in requests.get(f"{API}/discover/books", headers=_h(a)).json()["books"]]
        assert everything
        # Bracket syntax is not parsed into an operator: the filter is simply absent.
        r = requests.get(f"{API}/discover/books", params={"genre[$ne]": "Fiction"}, headers=_h(a))
        assert r.status_code == 200 and [x["id"] for x in r.json()["books"]] == everything
        # Regex metacharacters are text (and only punctuation here): they never match "everything".
        for q in (".*", "^.*$", ".+", "\\", "()", "[]", "|"):
            for path, key in (("/discover/books", "books"), ("/discover/people", "people")):
                r = requests.get(f"{API}{path}", params={"search": q}, headers=_h(a))
                assert r.status_code == 200 and r.json()[key] == [], (path, q)
        for q in ("$where", "'; return true; //", '{"$gt": ""}'):
            assert requests.get(f"{API}/discover/books", params={"search": q}, headers=_h(a)).status_code == 200
        r = requests.get(f"{API}/discover/books", params={"search": "Zzyzx"}, headers=_h(a))
        assert [x["title"] for x in r.json()["books"]] == ["Zzyzx literal regex test"]

    def test_catastrophic_regex_input_is_cheap(self, ab):
        a, _b = ab
        start = time.monotonic()
        for path in ("/discover/books", "/discover/people"):
            assert requests.get(f"{API}{path}", params={"search": "(a+)+$" * 50 + "!" * 30}, headers=_h(a)).status_code == 200
        assert time.monotonic() - start < 10

    def test_password_login_cannot_be_bypassed_with_type_confusion(self, ab):
        _a, b = ab
        for pw in (None, True, 0, [], ["Test1234!"], {"$ne": "x"}):
            r = requests.post(f"{API}/auth/login", json={"email": b["email"], "password": pw})
            assert r.status_code == 422, pw
        assert requests.post(f"{API}/auth/login", json={"email": b["email"], "password": "Test1234!"}).status_code == 200


# ====================================================================== 20: CSRF / CORS
class TestCrossSiteRequests:
    def test_cors_never_reflects_an_origin_with_credentials(self):
        r = requests.options(f"{API}/users/me", headers={"Origin": EVIL, "Access-Control-Request-Method": "PUT",
                                                         "Access-Control-Request-Headers": "authorization,content-type"})
        assert r.status_code == 200
        assert r.headers.get("access-control-allow-origin") == "*"
        assert r.headers.get("access-control-allow-credentials") is None
        assert "authorization" in (r.headers.get("access-control-allow-headers") or "").lower()  # web app still works
        r = requests.get(f"{API}/", headers={"Origin": EVIL})
        assert r.headers.get("access-control-allow-origin") == "*" and r.headers.get("access-control-allow-credentials") is None
        # With a Cookie header Starlette names the origin instead of "*", but without Allow-Credentials a
        # browser never lets a page read a response to a request that carried cookies.
        r = requests.get(f"{API}/", headers={"Origin": EVIL, "Cookie": "session=x"})
        assert r.headers.get("access-control-allow-credentials") is None

    def test_no_ambient_credentials_authenticate_a_request(self):
        u = _user()
        for headers, data in (({"Content-Type": "text/plain", "Origin": EVIL}, '{"bio": "csrf"}'),
                              ({"Content-Type": "application/x-www-form-urlencoded", "Origin": EVIL}, "bio=csrf"),
                              ({"Content-Type": "application/json", "Cookie": f"session_token={u['token']}"}, '{"bio": "csrf"}')):
            assert requests.put(f"{API}/users/me", data=data, headers=headers).status_code == 401
        assert requests.post(f"{API}/users/me/delete", data='{"confirm": true}',
                             headers={"Content-Type": "application/json", "Cookie": f"session_token={u['token']}"}).status_code == 401
        assert requests.get(f"{API}/auth/me", headers=_h(u)).json()["user"]["bio"] == ""

    def test_no_endpoint_sets_cookies(self):
        u = _user()
        r = requests.post(f"{API}/auth/login", json={"email": u["email"], "password": "Test1234!"})
        assert r.status_code == 200 and "set-cookie" not in {k.lower() for k in r.headers}

    def test_bearer_request_with_text_plain_body_is_not_parsed_as_json(self):
        # A cross-site "simple" request can't carry a bearer token anyway; even with one, a non-JSON
        # content type is never parsed as the JSON body.
        u = _user()
        r = requests.put(f"{API}/users/me", data='{"bio": "x"}', headers={**_h(u), "Content-Type": "text/plain"})
        assert r.status_code == 422
