"""Native Google sign-in (Android Credential Manager): POST /auth/google/nonce, /auth/google/native and
/auth/google/native/link, against a fake Google key set.

Same requirements as test_bookloop_api.py (a backend started with OTP_DEBUG_MODE=true on a throwaway
database), plus the backend pointed at the fake Google keys, e.g.:
    GOOGLE_CLIENT_ID=test-client-id GOOGLE_CLIENT_SECRET=test-secret
    GOOGLE_ANDROID_CLIENT_IDS=test-android-client GOOGLE_CERTS_URL=http://127.0.0.1:8097/certs
and FAKE_GOOGLE_CERTS_PORT=8097 for the tests (they host the fake key set there).
"""
import os
import json
import time
import uuid
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).parent.parent / ".env")
API = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or "http://127.0.0.1:8001").rstrip("/") + "/api"
FAKE_GOOGLE_CERTS_PORT = os.environ.get("FAKE_GOOGLE_CERTS_PORT")
WEB_CLIENT, ANDROID_CLIENT = "test-client-id", "test-android-client"
# TEST-ONLY signing key for the fake Google key set below. It signs nothing real and no service trusts it.
# Fixed (not generated per run) because the backend caches the key set by key id.
FAKE_GOOGLE_TEST_KEY = """-----BEGIN PRIVATE KEY-----
MIIEvwIBADANBgkqhkiG9w0BAQEFAASCBKkwggSlAgEAAoIBAQDUQ4qx7RCwp9bI
QPTFszvWsHtC1CP7E/hpg/7GTCoxfazi0jQGbXkYrMN9q2ZTdHszR2Gs3JicL7hn
5FOvuVpthay4KTZxnnKfX4t20S/kfJsGBbDRm0Qv6yuz/0QWGIRYAmK0+tPAbDrZ
QhHBY15Ed60ZOl4FWBChfzFYCvoOnBELoFGaEx4l3F1a0VcfiQMrUvGpLNb7O1kz
ucl244fPU8gbdnFVIDyu2XV3uB4/PdlEyJ97+NMHNCs9Uc/FPlwK+jmkCNYfwqCr
mek2zjnbxYxreP2KPUIOuzulVwS3g57cEAob/tfJOdDbMJqtt/j1EC9S0uzFSQ5s
D9iBNmVnAgMBAAECggEAZM0AZ/d3FhVWe7nRI7O2poEfwABDaAA3V1BuJJhV7zPJ
7ts5BpZlTBD7M0o6qUYnHZZ37aHCvWUyEvBMx6XjqiAxgjFcH//JWiMC9XVo435D
FkyMiDXOX100X2HJYR7adnsHif+VoIslNThZezentCcLJnBf5vTHzRG8ip2H23hn
acFAQLOEvFSBFL2bW+J8YtYZUkkW2XawqA+cmS9hp4wFe2476OtLhvof2fbT8eFP
GYdITuVivsLG2ygUWy7sJyTnkiGlXFmKAdbwdKcmgQAh9zx42nQDX3gcyj4PlKz3
51u3hI/fUhyBvRqSbxyn9elBH18bRGjO5yjYQQdvSQKBgQDvjHpBjaLfGIn80C1h
bQ9EP789rMaR5l0gXUf5V2Yh6BWrVHtvwYNzVqyPYQB/LQCyY+41rmIwdVIaFidN
yS5gE9TkXod/v6OvElswmiAsVdb8aSmo8EnmhnPfHprq7Y2xqVadgAZN17tE118s
ixOqRcPTXagP+s13vatQ5A2NWQKBgQDi113QwvL2m04piQS7jxlFI13wg8GE3LD2
yMvR4ov8xvB80LBhjig/SM3/b2ENXbbrSzCo3fKM5wvV3zanFoSFbjCVec9Nrlvu
7z1UHp5CKl8+qMiMwPcuBC9CJvlZht5Tpy07ocmH3dQT2dUPvcuz/X4oDrbRuSvk
hzdnr/twvwKBgQCfZQIUjIJLoLBwoLURltgJucEu8MBQZNCisG8r4pP7C8sfbd+U
xVcKcCnyXcFO3ksQaJ4g6iA7zlULNqKAh9Vr6MZIDv5qasInxDcZZ/Twi026Nt+Z
ogoKcny3gk3i9J7MELmpo36OXXYmoIibww8axcE/65ngyCnf+zz7XkQGQQKBgQCd
0CtKg3Y4rs9wAg0y11lDSlmebGuDnW3YItX5zNyLHXRNwXoURKwnauU1oi0o+Vu6
0nKl6NNHx63qOwJwuC+2DHTsaPQaHQWEcxh9n1eGvKfKle4ATncoomymXyr2Z+fg
1tuXMvVb+q4CqXebHfXL4L2V7LavrV2l/ZREJKCPTQKBgQCF2aWzSEAQRpVIaZsg
6KnkYxi4Afuzab2+EuypTJZHYMD9bkXCxvu/oAyANSBRcjaGsNYodWJKF1xwrXEZ
76uNVB+Vh3S7/UfgdBoSBwzWPXkfyBYAv1k5+KEHueRg6LlYjl9CTROMwgLy11Ym
6C1w2zu/R+5kvrFKsDnWRvH0Sw==
-----END PRIVATE KEY-----
"""


def _db():
    return MongoClient(os.environ.get("MONGO_URL") or "mongodb://localhost:27017")[os.environ.get("DB_NAME") or "bookloop_test"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _user(email=None):
    email = email or f"n_{uuid.uuid4().hex[:8]}@example.com"
    r = requests.post(f"{API}/auth/register/dev-instant", json={"email": email, "password": "test1234", "name": "Nat"})
    assert r.status_code == 200, r.text
    return r.json()


def _events(user_id):
    return [(e["event"], e["provider"]) for e in _db().security_events.find({"user_id": user_id}).sort("created_at", 1)]


@pytest.fixture(scope="module")
def fake_google_keys():
    if not FAKE_GOOGLE_CERTS_PORT:
        pytest.skip("FAKE_GOOGLE_CERTS_PORT not set (backend not pointed at a fake Google key set)")
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from http.server import BaseHTTPRequestHandler, HTTPServer

    key = serialization.load_pem_private_key(FAKE_GOOGLE_TEST_KEY.encode(), password=None)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "google-test-kid", "alg": "RS256", "use": "sig"})
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

    srv = HTTPServer(("127.0.0.1", int(FAKE_GOOGLE_CERTS_PORT)), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def token(email, nonce, sub=None, aud=WEB_CLIENT, azp=ANDROID_CLIENT, iss="https://accounts.google.com",
              verified=True, signer=None, exp_in=600, hd=None, iat_in=0):
        now = int(time.time())
        claims = {"iss": iss, "aud": aud, "sub": sub or ("nsub-" + email.lower()), "iat": now + iat_in, "exp": now + exp_in,
                  "nonce": nonce, "email": email, "email_verified": verified, "name": "Nina Native",
                  "picture": "https://example.com/n.png"}
        if azp is not None:
            claims["azp"] = azp
        if hd or email.lower().endswith("@example.com"):  # example.com plays a Workspace domain
            claims["hd"] = hd or "example.com"
        return jwt.encode(claims, signer or key, algorithm="RS256", headers={"kid": "google-test-kid"})

    token.other_key = other_key
    yield token
    srv.shutdown()


def _nonce():
    r = requests.post(f"{API}/auth/google/nonce")
    assert r.status_code == 200, r.text
    d = r.json()
    return d["nonce_id"], d["nonce"]


def _native(fake, email, path="/auth/google/native", headers=None, **kw):
    nid, nonce = _nonce()
    tok = fake(email, kw.pop("nonce", nonce), **kw)
    return requests.post(f"{API}{path}", json={"id_token": tok, "nonce_id": nid}, headers=headers or {})


class TestNativeGoogle:
    # ---- sign-in
    def test_providers_reports_native_google(self, fake_google_keys):
        assert requests.get(f"{API}/auth/providers").json()["google_native"] is True

    def test_new_account_then_same_account(self, fake_google_keys):
        email = f"nn_{uuid.uuid4().hex[:8]}@gmail.com"
        r = _native(fake_google_keys, email, headers={"X-App-Version": "2.3.0", "X-App-Platform": "android"})
        assert r.status_code == 200, r.text
        u = r.json()["user"]
        assert u["email"] == email and u["name"] == "Nina Native" and u["onboarding_completed"] is False
        me = requests.get(f"{API}/auth/me", headers=_h(r.json()["session_token"]))
        assert me.status_code == 200 and me.json()["user"]["user_id"] == u["user_id"]
        r2 = _native(fake_google_keys, email)
        assert r2.status_code == 200 and r2.json()["user"]["user_id"] == u["user_id"]
        doc = _db().users.find_one({"user_id": u["user_id"]})
        assert doc["google_signin_via"] == "google_native"
        ident = _db().auth_identities.find_one({"provider": "google", "subject": "nsub-" + email})
        assert ident and ident["user_id"] == u["user_id"]

    @pytest.mark.parametrize("bad", ["aud", "azp", "no_azp", "iss", "sig", "expired", "unverified", "nonce"])
    def test_forged_tokens_are_rejected(self, fake_google_keys, bad):
        email = f"nf_{uuid.uuid4().hex[:8]}@gmail.com"
        kw = {"aud": {"aud": "someone-else"}, "azp": {"azp": "com.evil.app-client"}, "no_azp": {"azp": None},
              "iss": {"iss": "https://evil.example"}, "sig": {"signer": fake_google_keys.other_key},
              "expired": {"exp_in": -60}, "unverified": {"verified": False}, "nonce": {"nonce": "not-our-nonce"}}[bad]
        r = _native(fake_google_keys, email, **kw)
        assert r.status_code == 401 and r.json()["detail"]["code"] == "google_failed", (bad, r.text)
        assert _db().users.find_one({"email": email}) is None

    def test_small_clock_skew_is_tolerated(self, fake_google_keys):
        # Google's clock a little ahead of ours: still valid. Far ahead: not.
        assert _native(fake_google_keys, f"sk_{uuid.uuid4().hex[:8]}@gmail.com", iat_in=30).status_code == 200
        r = _native(fake_google_keys, f"sk_{uuid.uuid4().hex[:8]}@gmail.com", iat_in=300)
        assert r.status_code == 401 and r.json()["detail"]["code"] == "google_failed"

    def test_concurrent_first_sign_ins_never_error(self, fake_google_keys):
        # Two devices signing up with the same new Google account at once: one account, no 500s, and a
        # retry of any request that lost the race gets into that same account.
        email = f"cc_{uuid.uuid4().hex[:8]}@gmail.com"
        prepared = []
        for _ in range(6):
            nid, nonce = _nonce()
            prepared.append({"id_token": fake_google_keys(email, nonce), "nonce_id": nid})
        barrier, results, lock = threading.Barrier(len(prepared)), [], threading.Lock()

        def run(body):
            barrier.wait()
            r = requests.post(f"{API}/auth/google/native", json=body)
            with lock:
                results.append(r)

        threads = [threading.Thread(target=run, args=(b,)) for b in prepared]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert all(r.status_code in (200, 401) for r in results), [r.status_code for r in results]
        assert any(r.status_code == 200 for r in results)
        assert _db().users.count_documents({"email": email}) == 1
        retry = _native(fake_google_keys, email)
        assert retry.status_code == 200
        assert {r.json()["user"]["user_id"] for r in results if r.status_code == 200} == {retry.json()["user"]["user_id"]}
        assert _events(retry.json()["user"]["user_id"]) == []  # a new account: no "first sign-in" notice

    def test_nonce_is_single_use_required_and_expires(self, fake_google_keys):
        email = f"nr_{uuid.uuid4().hex[:8]}@gmail.com"
        nid, nonce = _nonce()
        body = {"id_token": fake_google_keys(email, nonce), "nonce_id": nid}
        assert requests.post(f"{API}/auth/google/native", json=body).status_code == 200
        assert requests.post(f"{API}/auth/google/native", json=body).status_code == 401  # replay
        assert requests.post(f"{API}/auth/google/native", json={**body, "nonce_id": "unknown"}).status_code == 401
        nid2, nonce2 = _nonce()
        _db().google_nonces.update_one({"id": nid2}, {"$set": {"created_at": datetime.now(timezone.utc) - timedelta(minutes=11)}})
        r = requests.post(f"{API}/auth/google/native", json={"id_token": fake_google_keys(email, nonce2), "nonce_id": nid2})
        assert r.status_code == 401

    def test_a_failed_attempt_burns_its_nonce(self, fake_google_keys):
        email = f"nb_{uuid.uuid4().hex[:8]}@gmail.com"
        nid, nonce = _nonce()
        bad = fake_google_keys(email, nonce, signer=fake_google_keys.other_key)
        assert requests.post(f"{API}/auth/google/native", json={"id_token": bad, "nonce_id": nid}).status_code == 401
        good = fake_google_keys(email, nonce)
        assert requests.post(f"{API}/auth/google/native", json={"id_token": good, "nonce_id": nid}).status_code == 401

    def test_email_rules_match_the_browser_flow(self, fake_google_keys):
        # Untrusted address (consumer Google account on a non-Gmail domain, no `hd`): no existing account opens.
        untrusted = f"nu_{uuid.uuid4().hex[:8]}@example.org"
        acct = _user(untrusted)
        r = _native(fake_google_keys, untrusted)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "link_required"
        # Gmail: opens the existing account and is reported.
        gmail = f"ng_{uuid.uuid4().hex[:8]}@gmail.com"
        acct = _user(gmail)
        r = _native(fake_google_keys, gmail)
        assert r.status_code == 200 and r.json()["user"]["user_id"] == acct["user"]["user_id"]
        assert _events(acct["user"]["user_id"]) == [("signin_linked", "google")]
        # Another Google account on the same address: refused.
        r = _native(fake_google_keys, gmail, sub="nsub-other-" + gmail)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "account_mismatch"

    # ---- linking
    def test_link_any_email_to_the_signed_in_account(self, fake_google_keys):
        u = _user()
        g_email = f"nl_{uuid.uuid4().hex[:8]}@gmail.com"
        r = _native(fake_google_keys, g_email, path="/auth/google/native/link", headers=_h(u["session_token"]))
        assert r.status_code == 200, r.text
        assert r.json()["providers"]["google"] == {"linked": True, "email": g_email, "private_email": False}
        assert _events(u["user"]["user_id"]) == [("linked", "google")]
        r2 = _native(fake_google_keys, g_email)
        assert r2.status_code == 200 and r2.json()["user"]["user_id"] == u["user"]["user_id"]

    def test_link_conflicts_and_auth(self, fake_google_keys):
        owner_email = f"no_{uuid.uuid4().hex[:8]}@gmail.com"
        assert _native(fake_google_keys, owner_email).status_code == 200
        other = _user()
        r = _native(fake_google_keys, owner_email, path="/auth/google/native/link", headers=_h(other["session_token"]))
        assert r.status_code == 409 and r.json()["detail"]["code"] == "identity_in_use"
        assert _native(fake_google_keys, f"x_{uuid.uuid4().hex[:6]}@gmail.com", path="/auth/google/native/link",
                       headers=_h(other["session_token"])).status_code == 200
        r = _native(fake_google_keys, f"y_{uuid.uuid4().hex[:6]}@gmail.com", path="/auth/google/native/link",
                    headers=_h(other["session_token"]))
        assert r.status_code == 409 and r.json()["detail"]["code"] == "provider_already_linked"
        assert _events(other["user"]["user_id"]) == [("linked", "google")]
        assert _native(fake_google_keys, f"z_{uuid.uuid4().hex[:6]}@gmail.com", path="/auth/google/native/link").status_code == 401

    def test_forged_token_cannot_link(self, fake_google_keys):
        u = _user()
        r = _native(fake_google_keys, f"nfl_{uuid.uuid4().hex[:8]}@gmail.com", path="/auth/google/native/link",
                    headers=_h(u["session_token"]), azp="com.evil.app-client")
        assert r.status_code == 401
        assert _events(u["user"]["user_id"]) == []
