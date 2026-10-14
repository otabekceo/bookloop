"""Security audit part 3 (checklist items 21-30): regression tests for the issues found and fixed, plus the
server-side guarantees the audit verified (uploads, path traversal, SSRF, OTP, sessions, rate limits,
CORS, test-only accounts).

Same requirements as test_bookloop_api.py: a backend started with OTP_DEBUG_MODE=true on a throwaway
database, with this process's DB_NAME pointing at the same database. TestTestOnlyAccounts additionally
needs a second backend WITHOUT OTP_DEBUG_MODE on that database (NODEBUG_BACKEND_URL); it skips otherwise.
"""
import ast
import concurrent.futures as cf
import http.client
import io
import os
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import bcrypt
import pytest
import requests
from dotenv import load_dotenv
from PIL import Image
from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

load_dotenv(Path(__file__).parent.parent / ".env")
API = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or "http://127.0.0.1:8001").rstrip("/") + "/api"
SERVER_SOURCE = Path(__file__).resolve().parents[1] / "server.py"


def _db():
    return MongoClient(os.environ.get("MONGO_URL") or "mongodb://localhost:27017")[os.environ.get("DB_NAME") or "bookloop_test"]


def _h(u):
    return {"Authorization": f"Bearer {u['token']}"}


def _user(label="sec3", password="Test1234!"):
    email = f"TEST_{label}_{uuid.uuid4().hex[:10]}@bookloop.com"
    r = requests.post(f"{API}/auth/register/dev-instant", json={"email": email, "password": password, "name": "Sec3"})
    assert r.status_code == 200, r.text
    d = r.json()
    return {"token": d["session_token"], "id": d["user"]["user_id"], "email": email, "password": password}


def _image(fmt="PNG"):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 120, 200)).save(buf, fmt)
    return buf.getvalue()


def _upload(u, data, filename="photo.png", content_type="image/png"):
    return requests.post(f"{API}/upload", files={"file": (filename, data, content_type)}, headers=_h(u))


def _burst(fn, n):
    with cf.ThreadPoolExecutor(n) as ex:
        return Counter(f.result() for f in [ex.submit(fn, i) for i in range(n)])


def _raw_get(path, headers=None):
    """GET with the path sent exactly as written (no client-side dot-segment removal or re-encoding)."""
    p = urlparse(API)
    conn = http.client.HTTPConnection(p.hostname, p.port or 80, timeout=15)
    try:
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def u1():
    return _user("sec3a")


@pytest.fixture(scope="module")
def u2():
    return _user("sec3b")


# ====================================================================== 21: file uploads
class TestFileUploads:
    def test_type_comes_from_the_bytes_not_the_name_or_header(self, u1):
        # PNG bytes announced as an HTML page: stored and served as a PNG.
        r = _upload(u1, _image("PNG"), "page.html", "text/html")
        assert r.status_code == 200, r.text
        assert r.json()["path"].endswith(".png")
        got = requests.get(f"{API}/files/{r.json()['path']}", headers=_h(u1))
        assert got.status_code == 200 and got.headers["content-type"] == "image/png"
        assert got.headers["x-content-type-options"] == "nosniff"

    @pytest.mark.parametrize("name, ctype, data", [
        ("x.svg", "image/svg+xml", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
        ("x.html", "text/html", b"<html><script>alert(1)</script></html>"),
        ("x.png", "image/png", b"<?php system($_GET['c']); ?>"),
        ("x.jpg", "image/jpeg", b"MZ\x90\x00\x03" + b"\x00" * 64),  # Windows executable
        ("x.gif", "image/gif", b"GIF89a\x01\x00\x01\x00"),             # not an accepted type
        ("x.png", "image/png", b""),
    ])
    def test_non_images_are_refused_whatever_they_claim(self, u1, name, ctype, data):
        assert _upload(u1, data, name, ctype).status_code == 400

    def test_polyglot_is_only_ever_served_as_an_image(self, u1):
        # A JPEG signature followed by markup: never served with a type a browser would render as a page.
        poly = b"\xff\xd8\xff\xfe\x00\x10<html><script>alert(document.domain)</script></html>" + b"\x00" * 32
        r = _upload(u1, poly, "x.html", "text/html")
        if r.status_code == 200:
            got = requests.get(f"{API}/files/{r.json()['path']}", headers=_h(u1))
            assert got.headers["content-type"] == "image/jpeg"
            assert got.headers["x-content-type-options"] == "nosniff"
            assert "content-disposition" not in {k.lower() for k in got.headers}
        else:
            assert r.status_code == 400

    def test_upload_needs_a_session(self):
        assert requests.post(f"{API}/upload", files={"file": ("a.png", _image(), "image/png")}).status_code == 401

    def test_file_access_needs_a_valid_signature_or_session(self, u1):
        path = _upload(u1, _image()).json()["path"]
        url = f"{API}/files/{path}"
        assert requests.get(url).status_code == 401
        assert requests.get(url, params={"exp": int(time.time()) + 3600, "sig": "0" * 64}).status_code == 403
        # Every API response signs the file URLs it contains; take a signed one from a new book.
        book = requests.post(f"{API}/books", json={"title": "TEST sec3 cover", "cover_url": f"/api/files/{path}"},
                             headers=_h(u1))
        assert book.status_code == 200, book.text
        cover = book.json()["book"]["cover_url"]
        q = dict(p.split("=", 1) for p in urlparse(cover).query.split("&"))
        assert requests.get(url, params=q).status_code == 200
        # The signature is bound to its path and expiry.
        assert requests.get(url, params={"exp": int(q["exp"]) + 3600, "sig": q["sig"]}).status_code == 403
        other = _upload(u1, _image()).json()["path"]
        assert requests.get(f"{API}/files/{other}", params=q).status_code == 403
        assert requests.get(url, params={"exp": int(time.time()) - 10, "sig": q["sig"]}).status_code == 403


# ====================================================================== 22: path traversal
class TestPathTraversal:
    @pytest.mark.parametrize("path", [
        "/api/files/../server.py",
        "/api/files/..%2fserver.py",
        "/api/files/%2e%2e/%2e%2e/backend/server.py",
        "/api/files/%252e%252e%252fserver.py",
        "/api/files/bookloop/uploads/../../server.py",
        "/api/files/bookloop/uploads/..%2f..%2f.env",
        "/api/files/..%5c..%5cserver.py",
        "/api/files/%2fetc%2fpasswd",
        "/api/files//etc/passwd",
        "/api/files/C:%5cWindows%5cwin.ini",
        "/api/files/bookloop/uploads/x.jpg%00.png",
        "/api/files/.env",
        "/api/files/bookloop/uploads",
    ])
    def test_files_outside_the_upload_records_are_never_served(self, u1, path):
        status, body = _raw_get(path, _h(u1))
        assert status in (400, 404), (path, status)
        assert b"MONGO_URL" not in body and b"import " not in body and b"root:" not in body

    def test_image_urls_cannot_point_into_another_readers_folder(self, u1, u2):
        victim = _upload(u2, _image()).json()["path"]           # bookloop/uploads/<u2>/<file>
        tail = victim.split("/", 3)[3]
        for crafted in (f"/api/files/bookloop/uploads/{u1['id']}/../{u2['id']}/{tail}",
                        f"/api/files/bookloop/uploads/{u1['id']}/..%2f{u2['id']}/{tail}",
                        f"/api/files/bookloop/uploads/{u2['id']}/{tail}"):
            r = requests.put(f"{API}/users/me", json={"avatar_url": crafted}, headers=_h(u1))
            if r.status_code == 200:
                # Accepted as an odd name inside the reader's own folder: its signed URL never loads the file.
                signed = r.json()["user"]["avatar_url"]
                assert requests.get(f"{API.rsplit('/api', 1)[0]}{signed}").status_code in (403, 404)
            else:
                assert r.status_code == 400, crafted


# ====================================================================== 23: SSRF
class TestServerSideRequests:
    @pytest.mark.parametrize("url", [
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://127.0.0.1:8001/api/",
        "http://localhost/x.jpg",
        "http://10.0.0.1/x.jpg",
        "file:///etc/passwd",
        "gopher://127.0.0.1:27017/",
        "//evil.example/x.jpg",
        "https://covers.openlibrary.org.evil.example/x.jpg",
        "https://covers.openlibrary.org@evil.example/x.jpg",
        "http://covers.openlibrary.org/b/id/1-M.jpg",
    ])
    def test_image_urls_are_never_arbitrary_addresses(self, u1, url):
        r = requests.post(f"{API}/books", json={"title": "TEST sec3 ssrf", "cover_url": url}, headers=_h(u1))
        assert r.status_code == 400, (url, r.status_code)
        r = requests.put(f"{API}/users/me", json={"avatar_url": url}, headers=_h(u1))
        assert r.status_code == 400, (url, r.status_code)

    def test_outbound_http_never_follows_redirects(self):
        # httpx follows no redirects unless asked to; a metadata/sign-in provider answering with a redirect to an
        # internal address must not be followed.
        tree = ast.parse(SERVER_SOURCE.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    assert kw.arg != "follow_redirects", f"server.py:{node.lineno} enables redirects"


# ====================================================================== 24: OTP verification
def _pending_otp():
    email = f"otp_{uuid.uuid4().hex[:10]}@example.com"
    r = requests.post(f"{API}/auth/register/request-otp", json={"name": "Otp", "email": email})
    assert r.status_code == 200, r.text
    return email, r.json()["debug_otp"]


def _verify(email, code):
    return requests.post(f"{API}/auth/register/verify-otp", json={"email": email, "code": code})


class TestOtpVerification:
    def test_parallel_wrong_guesses_are_capped_at_five(self):
        email, real = _pending_otp()
        wrong = lambda i: _verify(email, f"{(int(real) + 1 + i) % 10 ** 6:06d}").status_code  # noqa: E731
        counts = _burst(wrong, 30)
        assert counts[400] <= 5 and counts[400] + counts[429] == 30, counts
        # Even the right code is refused once the attempts are used up.
        assert _verify(email, real).status_code == 429

    def test_a_code_is_redeemed_once_even_in_parallel(self):
        email, code = _pending_otp()
        counts = _burst(lambda i: _verify(email, code).status_code, 10)
        assert counts[200] == 1, counts
        assert _verify(email, code).status_code == 400  # replay

    def test_verification_token_is_single_use_in_parallel(self):
        email, code = _pending_otp()
        token = _verify(email, code).json()["verification_token"]
        complete = lambda i: requests.post(f"{API}/auth/register/complete", json={  # noqa: E731
            "email": email, "password": "Str0ng!Pass", "verification_token": token}).status_code
        counts = _burst(complete, 6)
        assert counts[200] == 1, counts

    def test_right_code_after_wrong_ones_still_works(self):
        email, code = _pending_otp()
        for i in range(4):
            assert _verify(email, f"{(int(code) + 1 + i) % 10 ** 6:06d}").status_code == 400
        assert _verify(email, code).status_code == 200

    def test_codes_are_six_random_digits_and_never_stored_in_clear(self):
        email, code = _pending_otp()
        assert len(code) == 6 and code.isdigit()
        doc = _db().pending_registrations.find_one({"email": email})
        assert code not in str(doc) and doc["otp_hash"].startswith("$2")


# ====================================================================== 25: sessions
class TestSessions:
    def test_tokens_are_long_random_and_opaque(self):
        a, b = _user(), _user()
        for t in (a["token"], b["token"]):
            assert len(t) >= 64 and "." not in t  # not a JWT; nothing to decode or forge
        assert a["token"] != b["token"]

    def test_logout_revokes_only_that_session(self):
        u = _user()
        second = requests.post(f"{API}/auth/login", json={"email": u["email"], "password": u["password"]}).json()
        assert requests.post(f"{API}/auth/logout", headers=_h(u)).status_code == 200
        assert requests.get(f"{API}/auth/me", headers=_h(u)).status_code == 401
        assert requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {second['session_token']}"}).status_code == 200

    def test_expired_session_is_refused(self):
        u = _user()
        _db().user_sessions.update_one({"session_token": u["token"]},
                                       {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=5)}})
        assert requests.get(f"{API}/auth/me", headers=_h(u)).status_code == 401

    def test_account_deletion_revokes_every_session(self):
        u = _user()
        second = requests.post(f"{API}/auth/login", json={"email": u["email"], "password": u["password"]}).json()
        assert requests.post(f"{API}/users/me/delete", json={"confirm": True}, headers=_h(u)).status_code == 200
        for tok in (u["token"], second["session_token"]):
            assert requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
        assert requests.post(f"{API}/auth/login", json={"email": u["email"], "password": u["password"]}).status_code == 401

    @pytest.mark.parametrize("header", ["Bearer ", "Bearer null", "Bearer undefined", "Basic abc", "bearer abc",
                                        'Bearer {"$ne": null}'])
    def test_malformed_tokens_are_refused(self, header):
        assert requests.get(f"{API}/auth/me", headers={"Authorization": header}).status_code == 401


# ====================================================================== 28: rate limits
class TestLoginLimits:
    def test_parallel_wrong_passwords_are_capped_at_ten(self):
        u = _user()
        counts = _burst(lambda i: requests.post(f"{API}/auth/login",
                                                json={"email": u["email"], "password": f"wrong{i}"}).status_code, 30)
        assert counts[401] <= 10 and counts[401] + counts[429] == 30, counts
        # At most 10 wrong passwords are ever checked in the window: the rest of the allowance, then the lock
        # (which also refuses the right password).
        for i in range(10 - counts[401]):
            assert requests.post(f"{API}/auth/login", json={"email": u["email"], "password": f"late{i}"}).status_code == 401
        assert requests.post(f"{API}/auth/login", json={"email": u["email"], "password": u["password"]}).status_code == 429

    def test_successful_logins_do_not_count(self):
        u = _user()
        for _ in range(12):
            assert requests.post(f"{API}/auth/login", json={"email": u["email"], "password": u["password"]}).status_code == 200
        for i in range(9):
            assert requests.post(f"{API}/auth/login", json={"email": u["email"], "password": f"x{i}"}).status_code == 401
        assert requests.post(f"{API}/auth/login", json={"email": u["email"], "password": u["password"]}).status_code == 200

    def test_spoofed_forwarding_headers_do_not_reset_the_limit(self):
        u = _user()
        for i in range(10):
            requests.post(f"{API}/auth/login", json={"email": u["email"], "password": f"x{i}"},
                          headers={"X-Forwarded-For": f"203.0.113.{i}", "X-Real-IP": f"198.51.100.{i}"})
        r = requests.post(f"{API}/auth/login", json={"email": u["email"], "password": u["password"]},
                          headers={"X-Forwarded-For": "192.0.2.77", "X-Real-IP": "192.0.2.78"})
        assert r.status_code == 429


# ====================================================================== 27: CORS preflight
class TestCorsPreflight:
    def test_preflight_allows_bearer_header_without_credentials(self):
        r = requests.options(f"{API}/auth/me", headers={
            "Origin": "https://evil.example", "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization"})
        assert r.status_code == 200
        assert r.headers.get("access-control-allow-origin") == "*"
        assert "access-control-allow-credentials" not in {k.lower() for k in r.headers}


# ====================================================================== 30: publicly known test credentials
NODEBUG_API = (os.environ.get("NODEBUG_BACKEND_URL") or "").rstrip("/")


@pytest.mark.skipif(not NODEBUG_API, reason="needs a backend without OTP_DEBUG_MODE (NODEBUG_BACKEND_URL)")
class TestTestOnlyAccounts:
    def _account(self, email, password, **extra):
        users = _db().users
        if users.find_one({"email": email}):
            return  # e.g. test@bookloop.com made by the test suite on this database (test_account, same password)
        users.insert_one({"user_id": f"user_{uuid.uuid4().hex[:16]}", "email": email, "name": "T",
                          "password_hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
                          "deleted_at": None, "created_at": datetime.now(timezone.utc), **extra})

    def _login(self, email, password):
        return requests.post(f"{NODEBUG_API}/api/auth/login", json={"email": email, "password": password})

    def test_documented_test_account_cannot_sign_in(self):
        self._account("test@bookloop.com", "test1234")
        assert self._login("test@bookloop.com", "test1234").status_code == 401

    def test_test_suite_accounts_cannot_sign_in(self):
        email = f"TEST_flag_{uuid.uuid4().hex[:8]}@bookloop.com".lower()
        self._account(email, "test1234", test_account=True)
        assert self._login(email, "test1234").status_code == 401

    def test_real_accounts_still_sign_in(self):
        email = f"reader_{uuid.uuid4().hex[:8]}@example.com"
        self._account(email, "Str0ng!Pass")
        assert self._login(email, "Str0ng!Pass").status_code == 200


# ====================================================================== 28: verification emails across addresses
OTP_CAP_API = (os.environ.get("OTP_CAP_BACKEND_URL") or "").rstrip("/")
OTP_CAP = int(os.environ.get("OTP_CAP_LIMIT") or 0)


@pytest.mark.skipif(not (OTP_CAP_API and OTP_CAP), reason="needs a backend on a fresh database started with "
                    "OTP_DEBUG_MODE=true OTP_EMAILS_PER_HOUR=<n> (OTP_CAP_BACKEND_URL, OTP_CAP_LIMIT=<n>)")
def test_verification_emails_are_capped_across_all_addresses():
    # A different address every time: the per-address cooldown never applies, the global cap does.
    def request(i):
        r = requests.post(f"{OTP_CAP_API}/api/auth/register/request-otp",
                          json={"name": "Cap", "email": f"cap_{uuid.uuid4().hex[:10]}@example.com"})
        return r.status_code, (r.json().get("detail") or {}).get("code") if r.status_code == 429 else None

    first = [request(i) for i in range(OTP_CAP)]
    assert all(s == 200 for s, _ in first), first
    assert request(0) == (429, "rate_limited")
