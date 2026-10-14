"""Masks credentials in log records before any handler writes them.

Hosting logs (Railway) are read by more people, and kept longer, than the database. They must never hold
anything that grants access: signed file links (`sig=`), OAuth codes/state and one-time ids in request URLs
(uvicorn's access log prints full URLs), bearer/session tokens, Google/Apple ID tokens, Expo push tokens
(anyone holding one can push to that phone), API keys, database credentials, passwords and OTP codes.

Pure functions plus a logging.Filter; unit-tested without a server (tests/test_log_redaction.py).
"""
import logging
import re

MASK = "[REDACTED]"

_PATTERNS = (
    # Credentials in URL query strings: signed file links, OAuth code/state, one-time ids, tokens.
    (re.compile(r"([?&](?:sig|code|state|session_id|link_id|token|id_token|access_token|code_verifier|nonce)=)"
                r"[^&\s\"'#]+", re.I), r"\1" + MASK),
    (re.compile(r"\b(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.I), r"\1" + MASK),
    (re.compile(r"\b(ExponentPushToken|ExpoPushToken)\[[^\]\s]*\]"), r"\1[" + MASK + "]"),
    # JWTs: Google / Apple ID tokens.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+"), MASK),
    (re.compile(r"\b(mongodb(?:\+srv)?://)[^@/\s]+@"), r"\1" + MASK + "@"),
    (re.compile(r"\bre_[A-Za-z0-9]{6,}_[A-Za-z0-9_]{6,}"), MASK),  # Resend API keys
    (re.compile(r"\bGOCSPX-[A-Za-z0-9_-]+"), MASK),  # Google OAuth client secrets
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), MASK),
    # key=value / "key": "value" pairs naming a secret.
    (re.compile(r"""((?:password|client_secret|code_verifier|verification_token|session_token|id_token"""
                r"""|identity_token|debug_otp|api_key)["']?\s*[:=]\s*["']?)[^"'\s,&}]+""", re.I), r"\1" + MASK),
)


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    """Redacts the message, its %-style string arguments (uvicorn's access log passes the URL that way,
    and needs its non-string arguments untouched) and the formatted traceback."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: redact(v) if isinstance(v, str) else v for k, v in record.args.items()}
        if record.exc_info and not record.exc_text:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        return True


# Loggers whose records do not all pass through the root handlers (uvicorn's have their own handlers).
LOGGERS = ("bookloop", "bookloop.email", "httpx", "uvicorn", "uvicorn.error", "uvicorn.access")


def install() -> None:
    """Idempotent: attaches the filter to the root logger's handlers and to LOGGERS."""
    targets = [*logging.getLogger().handlers, *(logging.getLogger(n) for n in LOGGERS)]
    for t in targets:
        if not any(isinstance(f, RedactingFilter) for f in t.filters):
            t.addFilter(RedactingFilter())
