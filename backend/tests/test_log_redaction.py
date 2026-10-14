"""Unit tests for log_redaction.py: no credential reaches a log line (security audit item 11). All values
below are synthetic and only shaped like the real thing. No backend needed."""
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from log_redaction import MASK, RedactingFilter, install, redact  # noqa: E402

SIG = "3a8b81d76fa6" + "0" * 52
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.c2lnbmF0dXJlLXZhbHVl"


@pytest.mark.parametrize("line, secret", [
    (f'127.0.0.1:1 - "GET /api/files/bookloop/uploads/u/x.jpg?exp=1791824400&sig={SIG} HTTP/1.1" 200', SIG),
    ('"GET /api/auth/google/callback?state=Zm9vYmFyc3RhdGU&code=4/0AQSTgQ-fake-code HTTP/1.1" 302', "4/0AQSTgQ-fake-code"),
    ('"GET /api/auth/google/callback?state=Zm9vYmFyc3RhdGU&code=x HTTP/1.1" 302', "Zm9vYmFyc3RhdGU"),
    ("redirect bookloop://?session_id=c2Vzc2lvbi1pZC12YWx1ZQ&attempt=abc", "c2Vzc2lvbi1pZC12YWx1ZQ"),
    ("Authorization: Bearer 0123456789abcdef0123456789abcdef", "0123456789abcdef0123456789abcdef"),
    ('Expo push error for a token: "ExponentPushToken[AbCdEf123456]" is not registered', "AbCdEf123456"),
    (f"Google ID token rejected: {JWT}", JWT),
    ("MONGO_URL=mongodb+srv://bookloop:hunter2pass@cluster0.example.mongodb.net/db", "hunter2pass"),
    ("Resend send failed: key re_AbCd1234_EfGh5678IjKl rejected", "re_AbCd1234_EfGh5678IjKl"),
    ("client secret GOCSPX-fakeFakeFake123", "GOCSPX-fakeFakeFake123"),
    ("-----BEGIN PRIVATE KEY-----\nMIIfake\n-----END PRIVATE KEY-----", "MIIfake"),
    ('{"email": "a@example.com", "password": "Hunter2!x"}', "Hunter2!x"),
    ("code_verifier=dmVyaWZpZXItdmFsdWUtdmVyaWZpZXItdmFsdWUtdmVyaWZp", "dmVyaWZpZXItdmFsdWUtdmVyaWZpZXItdmFsdWUtdmVyaWZp"),
    ('{"debug_otp": "287043"}', "287043"),
])
def test_secret_shaped_values_are_masked(line, secret):
    out = redact(line)
    assert secret not in out and MASK in out


def test_useful_diagnostics_survive():
    line = '127.0.0.1:1 - "GET /api/files/bookloop/uploads/u/x.jpg?exp=1791824400&sig=abc HTTP/1.1" 200'
    out = redact(line)
    # Path, expiry, status and the PKCE challenge (public by design) stay readable.
    assert "/api/files/bookloop/uploads/u/x.jpg?exp=1791824400&sig=" in out and out.endswith("200")
    start = "GET /api/auth/google/start?redirect=bookloop%3A%2F%2F&code_challenge=abcDEF123&code_challenge_method=S256"
    assert redact(start) == start
    for plain in ("Search tokens built for 12 books/readers", "Password does not meet requirements",
                  "Demo community in sync: 10 readers, 35 swaps, 51 reviews"):
        assert redact(plain) == plain


def test_redaction_is_idempotent():
    once = redact(f"x?sig={SIG}&code=abc Bearer tok123 {JWT}")
    assert redact(once) == once


def _record(msg, args=(), exc_info=None):
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, msg, args, exc_info)


def test_filter_redacts_uvicorn_access_args_and_keeps_their_types():
    # uvicorn's access log: '%s - "%s %s HTTP/%s" %d' with the full URL as an argument.
    rec = _record('%s - "%s %s HTTP/%s" %d', ("127.0.0.1:5", "GET", f"/api/files/a.jpg?exp=1&sig={SIG}", "1.1", 200))
    assert RedactingFilter().filter(rec) is True
    assert rec.args[4] == 200 and SIG not in rec.getMessage() and MASK in rec.getMessage()


def test_filter_redacts_message_and_traceback():
    try:
        raise RuntimeError(f"cannot connect to mongodb://admin:hunter2pass@db.internal:27017 with {JWT}")
    except RuntimeError:
        rec = _record("Exception in ASGI application Bearer abcdef123456", exc_info=sys.exc_info())
    RedactingFilter().filter(rec)
    text = logging.Formatter().format(rec)
    assert "hunter2pass" not in text and JWT not in text and "abcdef123456" not in text
    assert "RuntimeError" in text  # the traceback itself is kept


def test_install_covers_uvicorn_and_app_loggers_once():
    install()
    install()
    for name in ("bookloop", "bookloop.email", "uvicorn.access", "uvicorn.error", "httpx"):
        assert sum(isinstance(f, RedactingFilter) for f in logging.getLogger(name).filters) == 1


def test_installed_filter_masks_records_on_the_app_logger(caplog):
    install()
    with caplog.at_level(logging.INFO, logger="bookloop"):
        logging.getLogger("bookloop").warning("Expo push error for a token: ExponentPushToken[AbCdEf123456]")
    assert "AbCdEf123456" not in caplog.text and MASK in caplog.text


# --- Uvicorn's production access log (Railway runs `uvicorn server:app`) --------------------------------------
# uvicorn configures logging from LOGGING_CONFIG before it imports server.py, which then calls install(); the
# access line is built by uvicorn.logging.AccessFormatter from the record's args (client, method, full path with
# the raw query string, http version, status).

FAKE_SIG = "FAKE_TEST_SIGNATURE"


@pytest.fixture
def uvicorn_logging():
    """Applies uvicorn's own logging config, captures the access handler's output, restores state after."""
    import copy
    import io
    import logging.config

    from uvicorn.config import LOGGING_CONFIG

    names = ("uvicorn", "uvicorn.error", "uvicorn.access")
    saved = {n: (list(logging.getLogger(n).handlers), list(logging.getLogger(n).filters),
                 logging.getLogger(n).propagate, logging.getLogger(n).level) for n in names}
    logging.config.dictConfig(copy.deepcopy(LOGGING_CONFIG))
    install()
    out = io.StringIO()
    access = logging.getLogger("uvicorn.access")
    access.handlers[0].setStream(out)
    yield access, out
    for n, (handlers, filters, propagate, level) in saved.items():
        lg = logging.getLogger(n)
        lg.handlers, lg.filters, lg.propagate = handlers, filters, propagate
        lg.setLevel(level)


def _access_line(access, out, full_path, status=200):
    # The exact call uvicorn 0.25 makes in protocols/http/{h11,httptools}_impl.py.
    access.info('%s - "%s %s HTTP/%s" %d', "100.64.0.2:41234", "GET", full_path, "1.1", status)
    return out.getvalue()


def test_uvicorn_access_log_masks_file_signature(uvicorn_logging):
    access, out = uvicorn_logging
    text = _access_line(access, out, f"/api/files/bookloop/uploads/u1/a.jpg?exp=1791824400&sig={FAKE_SIG}")
    assert FAKE_SIG not in text
    assert f'"GET /api/files/bookloop/uploads/u1/a.jpg?exp=1791824400&sig={MASK} HTTP/1.1" 200' in text


@pytest.mark.parametrize("query", [
    f"sig={FAKE_SIG}&exp=1791824400",                 # signature first
    f"exp=1&SIG={FAKE_SIG}",                          # case variant
    f"exp=1&sig=other&sig={FAKE_SIG}",                # repeated parameter
    f"exp=1&sig={FAKE_SIG}%2Bpadded%3D",             # percent-encoded characters inside the value
    f"state=s&code={FAKE_SIG}&scope=openid",          # Google OAuth callback
    f"state={FAKE_SIG}&code=c",
    f"session_id={FAKE_SIG}&attempt=a",
    f"link_id={FAKE_SIG}",
    f"access_token={FAKE_SIG}",
    f"token={FAKE_SIG}",
    f"id_token={FAKE_SIG}",
    f"code_verifier={FAKE_SIG}",
    f"nonce={FAKE_SIG}",
])
def test_uvicorn_access_log_masks_credential_query_params(uvicorn_logging, query):
    access, out = uvicorn_logging
    text = _access_line(access, out, f"/api/x?{query}", 302)
    assert FAKE_SIG not in text and MASK in text


def test_uvicorn_access_log_keeps_non_secret_params(uvicorn_logging):
    access, out = uvicorn_logging
    text = _access_line(access, out, "/api/discover/people?search=Maria&genre=Fiction&max_distance=25")
    assert "/api/discover/people?search=Maria&genre=Fiction&max_distance=25 HTTP/1.1" in text and MASK not in text


def test_live_uvicorn_server_access_log_masks_signature(uvicorn_logging):
    """End to end through a real uvicorn server and HTTP request (h11), not a hand-built record."""
    import asyncio
    import threading

    import httpx
    import uvicorn

    access, out = uvicorn_logging

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"ok"})

    # log_config=None keeps the configuration (and filter) the fixture installed, as in production.
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None, lifespan="off"))
    thread = threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            threading.Event().wait(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        r = httpx.get(f"http://127.0.0.1:{port}/api/files/bookloop/uploads/u1/a.jpg?exp=1791824400&sig={FAKE_SIG}")
        assert r.status_code == 200
    finally:
        server.should_exit = True
        thread.join(10)
    text = out.getvalue()
    assert "/api/files/bookloop/uploads/u1/a.jpg?exp=1791824400&sig=" in text
    assert FAKE_SIG not in text and MASK in text
