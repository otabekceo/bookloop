"""BookLoop v2.2: review anonymity (R-Y), the proposal book picker data (Z), Sign in with Apple and
provider-identity linking, and legacy reviews.

Same requirements as test_bookloop_api.py: a backend started with OTP_DEBUG_MODE=true on a throwaway
database. The Apple tests additionally need the backend pointed at a fake Apple, e.g.:
    APPLE_BUNDLE_IDS=com.otabekceo.bookloop APPLE_KEYS_URL=http://127.0.0.1:8098/keys
and FAKE_APPLE_PORT=8098 for the tests (they host the fake Apple key set there).
"""
import os
import json
import time
import uuid
import hashlib
import threading
from pathlib import Path
from datetime import datetime, timezone

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).parent.parent / ".env")
API = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or "http://127.0.0.1:8001").rstrip("/") + "/api"


def _db():
    return MongoClient(os.environ.get("MONGO_URL") or "mongodb://localhost:27017")[os.environ.get("DB_NAME") or "bookloop_test"]


def _h(u):
    return {"Authorization": f"Bearer {u['token']}"}


def _user(label="U", email=None, password="test1234"):
    email = email or f"TEST_v22r_{uuid.uuid4().hex[:10]}@bookloop.com"
    r = requests.post(f"{API}/auth/register/dev-instant", json={"email": email, "password": password, "name": f"V22 {label}"})
    assert r.status_code == 200, r.text
    d = r.json()
    return {"token": d["session_token"], "id": d["user"]["user_id"], "name": d["user"]["name"], "email": email}


def _post(u, path, body=None):
    return requests.post(f"{API}{path}", json=body, headers=_h(u))


def _book(u, title="Book"):
    r = requests.post(f"{API}/books", json={"title": f"TEST {title}", "author": "T"}, headers=_h(u))
    assert r.status_code == 200, r.text
    return r.json()["book"]["id"]


def _completed_swap(a, b):
    ba, bb = _book(a, "A"), _book(b, "B")
    r = _post(a, "/swaps", {"receiver_id": b["id"], "offered_book_id": ba, "requested_book_id": bb})
    assert r.status_code == 200, r.text
    sid = r.json()["swap"]["id"]
    assert _post(b, f"/swaps/{sid}/accept").status_code == 200
    assert _post(a, f"/swaps/{sid}/complete").status_code == 200
    assert _post(b, f"/swaps/{sid}/complete").status_code == 200
    return sid


def _profile(viewer, uid):
    r = requests.get(f"{API}/users/{uid}", headers=_h(viewer))
    assert r.status_code == 200, r.text
    return r.json()


def _no_trace_of(obj, *needles):
    """No needle appears anywhere in the JSON (keys or values)."""
    blob = json.dumps(obj)
    for n in needles:
        if n:
            assert n not in blob, f"{n!r} leaked in {blob[:400]}"


# ---------------------------------------------------------------- R-Y: reviews
class TestReviews:
    def test_v_default_review_is_identified(self):
        a, b = _user("Rev A"), _user("Rev B")
        sid = _completed_swap(a, b)
        assert _post(a, f"/swaps/{sid}/rate", {"stars": 5, "review": "Great swap"}).status_code == 200
        stored = _db().ratings.find_one({"swap_id": sid})
        assert stored["is_anonymous"] is False and stored["rater_id"] == a["id"]

    def test_t_identified_review_exposes_only_public_identity(self):
        a, b = _user("Rev A"), _user("Rev B")
        sid = _completed_swap(a, b)
        assert _post(a, f"/swaps/{sid}/rate", {"stars": 4, "review": "Nice", "is_anonymous": False}).status_code == 200
        for viewer in (b, _user("Stranger")):
            rev = _profile(viewer, b["id"])["reviews"][0]
            assert rev["is_anonymous"] is False
            assert rev["rater_id"] == a["id"] and rev["rater_name"] == a["name"]
            assert set(rev) == {"id", "stars", "review", "is_anonymous", "rater_id", "rater_name", "rater_avatar", "created_at"}
            _no_trace_of(rev, a["email"], a["email"].lower(), sid)

    def test_u_r_s_anonymous_review_stored_with_reviewer_but_never_exposed(self):
        a, b = _user("Anon A"), _user("Anon B")
        sid = _completed_swap(a, b)
        assert _post(a, f"/swaps/{sid}/rate", {"stars": 5, "review": "Lovely", "is_anonymous": True}).status_code == 200
        # R: the server still knows exactly who wrote it
        stored = _db().ratings.find_one({"swap_id": sid})
        assert stored["is_anonymous"] is True and stored["rater_id"] == a["id"] and stored["ratee_id"] == b["id"]
        # S: nobody — not the recipient, not a stranger — gets anything identifying
        for viewer in (b, _user("Stranger")):
            rev = _profile(viewer, b["id"])["reviews"][0]
            assert rev["is_anonymous"] is True and rev["stars"] == 5 and rev["review"] == "Lovely"
            assert rev["rater_id"] is None and rev["rater_avatar"] is None and rev["rater_name"] == "BookLoop member"
            assert rev["created_at"].endswith("T12:00:00+00:00")  # the day only, not the exact time
            _no_trace_of(rev, a["id"], a["name"], a["email"], sid)

    def test_y_anonymous_reviewer_not_discoverable_elsewhere(self):
        a, b = _user("Anon A"), _user("Anon B")
        sid = _completed_swap(a, b)
        assert _post(a, f"/swaps/{sid}/rate", {"stars": 3, "review": "ok", "is_anonymous": True}).status_code == 200
        rid = _db().ratings.find_one({"swap_id": sid})["id"]
        # The swap itself no longer tells B whether/when A reviewed.
        # (B's own profile lists the review itself — test S checks it carries nothing about the reviewer.)
        for path in (f"/swaps/{sid}", "/swaps", "/notifications", "/notifications/feed", "/auth/me"):
            r = requests.get(f"{API}{path}", headers=_h(b))
            assert r.status_code == 200, path
            _no_trace_of(r.json(), rid)
            if path == f"/swaps/{sid}":
                assert "requester_rated" not in r.json()["swap"]  # B is the receiver: only their own flag
        # A's own profile does not list reviews A wrote.
        _no_trace_of(_profile(b, a["id"])["reviews"], rid, "ok")

    def test_w_stranger_cannot_review(self):
        a, b = _user("A"), _user("B")
        sid = _completed_swap(a, b)
        c = _user("Stranger")
        r = _post(c, f"/swaps/{sid}/rate", {"stars": 1, "review": "bad", "is_anonymous": True})
        assert r.status_code in (403, 404)
        assert _db().ratings.count_documents({"swap_id": sid}) == 0

    def test_review_requires_completed_swap(self):
        a, b = _user("A"), _user("B")
        r = _post(a, "/swaps", {"receiver_id": b["id"]})
        sid = r.json()["swap"]["id"]
        assert _post(a, f"/swaps/{sid}/rate", {"stars": 5, "is_anonymous": True}).status_code == 400

    def test_cannot_review_self(self):
        a = _user("Self")
        assert _post(a, "/swaps", {"receiver_id": a["id"]}).status_code == 400  # no swap with yourself -> no review

    def test_x_duplicate_review_blocked_even_when_switching_anonymity(self):
        a, b = _user("A"), _user("B")
        sid = _completed_swap(a, b)
        assert _post(a, f"/swaps/{sid}/rate", {"stars": 5, "is_anonymous": False}).status_code == 200
        assert _post(a, f"/swaps/{sid}/rate", {"stars": 1, "is_anonymous": True}).status_code == 400
        assert _db().ratings.count_documents({"swap_id": sid, "rater_id": a["id"]}) == 1
        # the other direction is its own review
        assert _post(b, f"/swaps/{sid}/rate", {"stars": 4, "is_anonymous": True}).status_code == 200

    def test_review_text_is_sanitized_and_limited(self):
        a, b = _user("A"), _user("B")
        sid = _completed_swap(a, b)
        assert _post(a, f"/swaps/{sid}/rate", {"stars": 5, "review": "x" * 1001}).status_code == 422
        assert _post(a, f"/swaps/{sid}/rate", {"stars": 5, "review": " hi‮​\n\n\n\nthere "}).status_code == 200
        assert _db().ratings.find_one({"swap_id": sid})["review"] == "hi\n\nthere"

    def test_legacy_review_without_flag_stays_identified(self):
        a, b = _user("Old A"), _user("Old B")
        _db().ratings.insert_one({"id": f"rate_legacy_{uuid.uuid4().hex[:8]}", "swap_id": "swap_legacy", "rater_id": a["id"],
                                  "ratee_id": b["id"], "stars": 4, "review": "old one", "created_at": datetime.now(timezone.utc)})
        rev = _profile(b, b["id"])["reviews"][0]
        assert rev["is_anonymous"] is False and rev["rater_name"] == a["name"] and rev["rater_id"] == a["id"]

    def test_rating_average_counts_anonymous_reviews(self):
        a, b = _user("A"), _user("B")
        sid = _completed_swap(a, b)
        assert _post(a, f"/swaps/{sid}/rate", {"stars": 4, "is_anonymous": True}).status_code == 200
        u = _profile(a, b["id"])["user"]
        assert u["rating"] == 4.0 and u["rating_count"] == 1


# ---------------------------------------------------------------- Z: the proposal picker gets every book
class TestProposalBooks:
    def test_z_every_available_book_is_returned_and_the_last_one_can_be_proposed(self):
        a, b = _user("Many A"), _user("Many B")
        mine = [_book(a, f"Mine {i:02d}") for i in range(22)]
        theirs = [_book(b, f"Theirs {i:02d}") for i in range(9)]
        shelf = requests.get(f"{API}/books?status=Available", headers=_h(a)).json()["books"]
        assert {x["id"] for x in shelf} >= set(mine)
        prof = _profile(a, b["id"])["books"]
        assert {x["id"] for x in prof} >= set(theirs)
        # The oldest book — the one furthest down/along any picker — is a valid choice.
        r = _post(a, "/swaps", {"receiver_id": b["id"], "offered_book_id": mine[0], "requested_book_id": theirs[0]})
        assert r.status_code == 200, r.text
        sid = r.json()["swap"]["id"]
        assert r.json()["swap"]["active_proposal"]["offered_book_id"] == mine[0]
        d = requests.get(f"{API}/swaps/{sid}", headers=_h(a)).json()
        assert len(d["my_books"]) == 22 and len(d["their_books"]) == 9


# ---------------------------------------------------------------- Apple + identities
FAKE_APPLE_PORT = os.environ.get("FAKE_APPLE_PORT")
# TEST-ONLY signing key for the fake Apple key set below. It signs nothing real and no service trusts it.
FAKE_APPLE_TEST_KEY = """-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQCRxEICIRITxVKy
B06WvsGT0y7jMG7BFi4jT0ZcCbxjSlA3PxwsBMZTAhFCb1dCjQPYRfThdr4O0nEI
+gW/tqWjiQISwyNyxh9uT/XBr7ec5adOchE0HaA1vwq3EflTS7OnWbx164j9KaiA
YJeADL8Li259gtDSHShQNua0Uk20cPAVPbGKaVt9mwYPseih6h4JJtLPACmJQB7o
MMQIuqTuX/PNkcB5JHhMNPfPKZKujT8/28YlRoaZEa3DWdHyOmjm8F17HTRoxYfD
jrTl14fyjUwZ6G0ntxjLa8UXrQo2qJabEX55aCwAuwKFKSfY38A3nr35ngnTeeYF
eAfYkX5vAgMBAAECggEAAOXCsFw7teSaovYTENkXvHeCD4VPOgVoiOHlRbdBSmq5
gJByo5vqOA0hsEf7Y6CO98aGuDrkpQsTs7d5xrylpAtPYGHqFyiy4smWebzec+Yq
u5ikorgf8MrQfzxP10yh+mKg0zvgQ10MMkJ1sN69yYZTObZKOxutXFQr3o0Foe58
PDhJMljYXbOvjBZtdZ4OVepo6RuAMxC5yIrDxRcSnwZCCq3S0yMvnI5AkEyNwYg+
+sSwvDVyaGO1U+YeTV/SYdIXv7AYZkYZBdwCL5fWQTSjKx1p0dM1MNMzfZzUmHKz
T0ts5al5TYo9yy3hmpn9OM7xtwhN/vK+bTDBre652QKBgQDEw3bpc2K0jiU4DFx1
WJVgSy9/PeEsV82EWeiKbmkkml0JffTBexbsFQAKyvEZt48wgVpBBugNctPunChI
0arP05ioeWoQYXqMrDnWAgiTGZmSVq+9yHXT3n2Zx0uYCrkD3nNZBEsgtRUsHs+d
4sysm5kjj6tj89vg5ux36idatwKBgQC9pnhPbJF0ZDPK9fYN3VadSPOfybfnnevB
CBcIgOROmKc2NLfOjlEKBkBcBO4e7jeOCCV2EHU4bcFRD7A4Ujx53Nu96HCb5aDn
7h3C3PJuSapwvXjEWSOGfUBfHoxxRU91UTdESoJ7z/Gl+hs0RZAv5dIAv+rHDMvT
VEVU/vUiCQKBgBDGJRYDyy9fStb7Xsp2Mc3Suwra6J5SIegdjhB3FymSq0nK3hm8
d5jKtYU2BdXOazuUW3BduVykJGMcFucWULyE6me6agtZdAwYBpoy9C4uKYRrNXI1
a64TMpvwfIZKq7kjtqupDQqNxW9MdSCdnBs+7JA2iELuIBmNNi2G3bIDAoGBAJkU
2mAh3nb4uuXFC8u9McDbterwxEVtL3MsuIrgn2jhx3b1rrhIIBnbWe/s+th0qNs4
sCRQvI3D6hsocrHDj5U+NRFhoiP0gqEzj0pPVlC3naBLEERNP2O7jk45Nlvn+m9U
EyxIiHKwMvoqm/Lhg7Vl6NE6GKR7K7t1dlFZSz0hAoGAN3/sCdZNShgB3ybwNx8p
T6tDEujKB8hKI1CX1hzSAkV8gsCoqcl56lxUzM/bqioamANmxKaG2Y0hI2qYVy9Y
X+EX7dvuuoFkdGS6WBXnwMSUgmNZwpeRgiuhHDEeAhURT6nxBn5iH7AXA7nc7+gS
dkQrhXOuFfwKIBNu1QUQzj4=
-----END PRIVATE KEY-----
"""
BUNDLE = "com.otabekceo.bookloop"


@pytest.fixture(scope="module")
def fake_apple():
    if not FAKE_APPLE_PORT:
        pytest.skip("FAKE_APPLE_PORT not set (backend not pointed at a fake Apple)")
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from cryptography.hazmat.primitives import serialization
    # A fixed, test-only key: xdist may run this module's classes on two workers, each hosting the fake
    # key set, and the backend caches the key set — every copy must serve the same key.
    key = serialization.load_pem_private_key(FAKE_APPLE_TEST_KEY.encode(), password=None)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "test-kid", "alg": "RS256", "use": "sig"})
    body = json.dumps({"keys": [jwk]}).encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", int(FAKE_APPLE_PORT)), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def token(sub, email=None, nonce_hash=None, aud=BUNDLE, iss="https://appleid.apple.com", private=False,
              signer=None, exp_in=600):
        now = int(time.time())
        claims = {"iss": iss, "aud": aud, "sub": sub, "iat": now, "exp": now + exp_in, "nonce": nonce_hash}
        if email:
            claims.update({"email": email, "email_verified": "true", "is_private_email": "true" if private else "false"})
        return jwt.encode(claims, signer or key, algorithm="RS256", headers={"kid": "test-kid"})

    token.other_key = other_key
    yield token
    srv.shutdown()


def _nonce():
    r = requests.post(f"{API}/auth/apple/nonce")
    assert r.status_code == 200, r.text
    d = r.json()
    return d["nonce_id"], hashlib.sha256(d["nonce"].encode()).hexdigest()


def _apple(fake, sub, email=None, path="/auth/apple", headers=None, **kw):
    nid, nh = _nonce()
    tok = fake(sub, email=email, nonce_hash=kw.pop("nonce_hash", nh), **kw)
    return requests.post(f"{API}{path}", json={"identity_token": tok, "nonce_id": nid, "given_name": "Ada", "family_name": "Apple"},
                         headers=headers or {})


def _sub():
    return f"000123.{uuid.uuid4().hex}.0101"


class TestAppleSignIn:
    def test_providers_reports_apple(self, fake_apple):
        assert requests.get(f"{API}/auth/providers").json()["apple"] is True

    def test_new_account_then_same_account_without_email(self, fake_apple):
        sub, email = _sub(), f"apple_{uuid.uuid4().hex[:8]}@privaterelay.appleid.com"
        r = _apple(fake_apple, sub, email, private=True)
        assert r.status_code == 200, r.text
        u = r.json()["user"]
        assert u["email"] == email and u["name"] == "Ada Apple"
        # Later sign-ins: Apple sends no email and no name — still the same account.
        r2 = _apple(fake_apple, sub)
        assert r2.status_code == 200 and r2.json()["user"]["user_id"] == u["user_id"]

    def test_no_email_and_unknown_identity_is_rejected(self, fake_apple):
        r = _apple(fake_apple, _sub())
        assert r.status_code == 400 and r.json()["detail"]["code"] == "apple_email_missing"

    def test_verified_email_links_existing_password_account(self, fake_apple):
        a = _user("Pw")
        email = a["email"].lower()
        r = _apple(fake_apple, _sub(), email)
        assert r.status_code == 200 and r.json()["user"]["user_id"] == a["id"]
        assert requests.post(f"{API}/auth/login", json={"email": email, "password": "test1234"}).status_code == 200

    def test_private_relay_never_matches_by_email(self, fake_apple):
        r = _apple(fake_apple, _sub(), f"x{uuid.uuid4().hex[:6]}@privaterelay.appleid.com", private=True)
        assert r.status_code == 200
        assert r.json()["user"]["onboarding_completed"] is False  # a new account

    @pytest.mark.parametrize("bad", ["aud", "iss", "sig", "expired", "nonce", "reused_nonce"])
    def test_forged_or_replayed_tokens_are_rejected(self, fake_apple, bad):
        sub, email = _sub(), f"f_{uuid.uuid4().hex[:8]}@example.com"
        nid, nh = _nonce()
        kw = {"aud": "com.evil.app"} if bad == "aud" else {"iss": "https://evil.example"} if bad == "iss" else \
            {"signer": fake_apple.other_key} if bad == "sig" else {"exp_in": -60} if bad == "expired" else {}
        tok = fake_apple(sub, email=email, nonce_hash="0" * 64 if bad == "nonce" else nh, **kw)
        body = {"identity_token": tok, "nonce_id": nid}
        if bad == "reused_nonce":
            assert requests.post(f"{API}/auth/apple", json=body).status_code == 200
        r = requests.post(f"{API}/auth/apple", json=body)
        assert r.status_code == 401, r.text
        if bad != "reused_nonce":
            assert _db().users.find_one({"email": email}) is None

    def test_client_supplied_identity_fields_are_ignored(self, fake_apple):
        a = _user("Victim")
        # The token's verified email is the attacker's; extra JSON fields claiming the victim do nothing.
        nid, nh = _nonce()
        tok = fake_apple(_sub(), email=f"att_{uuid.uuid4().hex[:6]}@example.com", nonce_hash=nh)
        r = requests.post(f"{API}/auth/apple", json={"identity_token": tok, "nonce_id": nid, "email": a["email"], "user_id": a["id"]})
        assert r.status_code == 200 and r.json()["user"]["user_id"] != a["id"]


class TestAccountLinking:
    def test_link_apple_to_signed_in_account_and_list(self, fake_apple):
        a = _user("Linker")
        sub = _sub()
        r = _apple(fake_apple, sub, f"l_{uuid.uuid4().hex[:6]}@privaterelay.appleid.com", path="/auth/apple/link", headers=_h(a), private=True)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["password"] is True and d["providers"]["apple"]["linked"] is True and d["providers"]["apple"]["private_email"] is True
        # Signing in with that Apple ID now opens THIS account, not a new one.
        r2 = _apple(fake_apple, sub)
        assert r2.status_code == 200 and r2.json()["user"]["user_id"] == a["id"]

    def test_identity_linked_elsewhere_cannot_be_taken(self, fake_apple):
        owner, attacker = _user("Owner"), _user("Attacker")
        sub = _sub()
        assert _apple(fake_apple, sub, f"o_{uuid.uuid4().hex[:6]}@example.com", path="/auth/apple/link", headers=_h(owner)).status_code == 200
        r = _apple(fake_apple, sub, path="/auth/apple/link", headers=_h(attacker))
        assert r.status_code == 409 and r.json()["detail"]["code"] == "identity_in_use"
        assert _db().auth_identities.find_one({"provider": "apple", "subject": sub})["user_id"] == owner["id"]

    def test_second_apple_id_cannot_be_linked(self, fake_apple):
        a = _user("Two")
        assert _apple(fake_apple, _sub(), "t1@example.com", path="/auth/apple/link", headers=_h(a)).status_code == 200
        r = _apple(fake_apple, _sub(), "t2@example.com", path="/auth/apple/link", headers=_h(a))
        assert r.status_code == 409 and r.json()["detail"]["code"] == "provider_already_linked"

    def test_link_requires_auth(self, fake_apple):
        assert _apple(fake_apple, _sub(), "n@example.com", path="/auth/apple/link").status_code == 401

    def test_unlink_keeps_last_login_method(self, fake_apple):
        # Apple-only account: its only way in can't be removed.
        r = _apple(fake_apple, _sub(), f"only_{uuid.uuid4().hex[:6]}@privaterelay.appleid.com", private=True)
        u = {"token": r.json()["session_token"]}
        d = requests.delete(f"{API}/auth/identities/apple", headers=_h(u))
        assert d.status_code == 409 and d.json()["detail"]["code"] == "last_login_method"
        # Password account: Apple can be unlinked.
        a = _user("Unlink")
        assert _apple(fake_apple, _sub(), "u@example.com", path="/auth/apple/link", headers=_h(a)).status_code == 200
        d = requests.delete(f"{API}/auth/identities/apple", headers=_h(a))
        assert d.status_code == 200 and d.json()["providers"]["apple"]["linked"] is False

    def test_unlink_only_touches_own_identities(self, fake_apple):
        owner, other = _user("Own"), _user("Other")
        sub = _sub()
        assert _apple(fake_apple, sub, "own@example.com", path="/auth/apple/link", headers=_h(owner)).status_code == 200
        assert requests.delete(f"{API}/auth/identities/apple", headers=_h(other)).status_code == 404
        assert _db().auth_identities.find_one({"provider": "apple", "subject": sub}) is not None


class TestIdentitiesWithoutProviders:
    def test_identities_endpoint_for_password_account(self):
        a = _user("Plain")
        d = requests.get(f"{API}/auth/identities", headers=_h(a)).json()
        assert d["password"] is True and d["email"] == a["email"].lower()
        assert d["providers"]["google"]["linked"] is False and d["providers"]["apple"]["linked"] is False
        assert requests.get(f"{API}/auth/identities").status_code == 401
        assert requests.delete(f"{API}/auth/identities/google", headers=_h(a)).status_code == 404
        assert requests.delete(f"{API}/auth/identities/facebook", headers=_h(a)).status_code == 404
