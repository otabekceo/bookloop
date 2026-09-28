import os
import re
import json
import time
import hmac
import uuid
import math
import base64
import hashlib
import secrets
import logging
from pathlib import Path
from urllib.parse import urlencode, urlparse
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import bcrypt
import httpx
from fastapi import FastAPI, APIRouter, Depends, HTTPException, Header, Request, UploadFile, File, Query
from fastapi.responses import Response, JSONResponse, RedirectResponse
from fastapi.concurrency import run_in_threadpool
from pymongo.errors import DuplicateKeyError
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr

from email_provider import get_email_provider, otp_email_body, EmailNotConfiguredError, EmailSendError

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

# ----------------------------------------------------------------------------
# Config / DB
# ----------------------------------------------------------------------------
mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

# Google sign-in uses our own Google Cloud OAuth "Web application" client (see backend/.env.example).
# The GOOGLE_*_URL overrides exist so tests can point the backend at a fake Google.
GOOGLE_CLIENT_ID = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
GOOGLE_CLIENT_SECRET = (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()
GOOGLE_AUTH_URL = os.environ.get("GOOGLE_AUTH_URL") or "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = os.environ.get("GOOGLE_TOKEN_URL") or "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
# Public https address of this API, used to build the redirect URI registered in Google Cloud.
# Falls back to the address the request arrived on (fine locally, wrong behind a proxy).
PUBLIC_BACKEND_URL = (os.environ.get("PUBLIC_BACKEND_URL") or "").strip().rstrip("/")
# Where the app may be sent back to after Google: custom URL schemes of the app (Expo Go uses exp://),
# plus web origins. http://localhost and http://127.0.0.1 are always allowed for development.
APP_URL_SCHEMES = tuple(
    s.strip().lower() for s in (os.environ.get("APP_URL_SCHEMES") or "exp,exps,bookloop").split(",") if s.strip()
)
APP_REDIRECT_ORIGINS = tuple(
    o.strip().rstrip("/") for o in (os.environ.get("APP_REDIRECT_ORIGINS") or "").split(",") if o.strip()
)

# Uploaded images live on local disk under STORAGE_DIR (default: backend/uploads). In production
# point STORAGE_DIR at a persistent volume, and back it up together with the database.
STORAGE_DIR = Path(os.environ.get("STORAGE_DIR") or (ROOT_DIR / "uploads")).resolve()
APP_NAME = "bookloop"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("bookloop")

FILE_PREFIX = "/api/files/"
FILE_URL_TTL_HOURS = 24
_file_secret: bytes = b""


async def load_file_secret() -> None:
    """Key for signing file URLs: FILE_SIGNING_SECRET if set, else one generated once and kept in Mongo."""
    global _file_secret
    env = os.environ.get("FILE_SIGNING_SECRET")
    if env:
        _file_secret = env.encode()
        return
    doc = await db.app_settings.find_one({"_id": "file_signing_secret"})
    if not doc:
        try:
            await db.app_settings.insert_one({"_id": "file_signing_secret", "value": secrets.token_hex(32)})
        except DuplicateKeyError:
            pass
        doc = await db.app_settings.find_one({"_id": "file_signing_secret"})
    _file_secret = doc["value"].encode()


def _file_sig(path: str, exp: int) -> str:
    return hmac.new(_file_secret, f"{path}:{exp}".encode(), hashlib.sha256).hexdigest()


def sign_file_url(url: str) -> str:
    """Append a time-limited signature to an internal file URL. Any existing query is replaced.
    `exp` is bucketed by the hour so a URL stays identical (cache-friendly) within the hour."""
    base = url.split("?", 1)[0]
    exp = (int(time.time()) // 3600 + FILE_URL_TTL_HOURS + 1) * 3600
    return f"{base}?exp={exp}&sig={_file_sig(base[len(FILE_PREFIX):], exp)}"


def _valid_file_sig(path: str, exp: Optional[int], sig: Optional[str]) -> bool:
    if exp is None or not sig or exp < int(time.time()):
        return False
    return hmac.compare_digest(sig, _file_sig(path, exp))


def _sign_tree(o):
    if isinstance(o, str):
        return sign_file_url(o) if o.startswith(FILE_PREFIX) else o
    if isinstance(o, list):
        return [_sign_tree(x) for x in o]
    if isinstance(o, dict):
        return {k: _sign_tree(v) for k, v in o.items()}
    return o


class SignedJSONResponse(JSONResponse):
    """Every /api JSON response gets its internal file URLs signed on the way out, so images
    only load for people who were given the URL by an authenticated request."""

    def render(self, content) -> bytes:
        return super().render(_sign_tree(content))


app = FastAPI()
api = APIRouter(prefix="/api", default_response_class=SignedJSONResponse)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


# ----------------------------------------------------------------------------
# File storage (local disk). Sync helpers, called through run_in_threadpool.
# The logical object path ("bookloop/uploads/<user>/<id>.<ext>") is what the database and the
# /api/files URLs use, so swapping this for S3 later only means rewriting these three functions.
# ----------------------------------------------------------------------------
def _storage_file(path: str) -> Path:
    """Map a logical object path to a file inside STORAGE_DIR; refuse anything that escapes it."""
    target = (STORAGE_DIR / path).resolve()
    if STORAGE_DIR != target and STORAGE_DIR not in target.parents:
        raise FileNotFoundError(path)
    return target


def init_storage() -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def put_object(path: str, data: bytes) -> None:
    target = _storage_file(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file and rename so a reader never sees a half-written image.
    tmp = target.with_name(target.name + f".{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def get_object(path: str) -> bytes:
    return _storage_file(path).read_bytes()


# ----------------------------------------------------------------------------
# Geo helpers
# ----------------------------------------------------------------------------
def haversine_km(lat1, lng1, lat2, lng2) -> float:
    if None in (lat1, lng1, lat2, lng2):
        return 999.0
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(r * 2 * math.asin(math.sqrt(a)), 1)


UNKNOWN_DISTANCE_KM = 999.0
# Privacy floor: readers closer than this are reported as exactly this far, so the API never reveals
# a finer proximity than "within 100 m" (the app renders this value as that phrase).
MIN_REPORTED_DISTANCE_KM = 0.1


def has_location(u: Optional[dict]) -> bool:
    """True only for a location that actually came from the user's device (location_updated_at is
    set by PUT /users/me/location), or for the seeded demo community, which has fixed coordinates.
    Accounts created before device location existed still carry a hardcoded Messina default in
    lat/lng/city/neighborhood; that default is ignored everywhere rather than treated as real."""
    if not u or u.get("lat") is None or u.get("lng") is None:
        return False
    return bool(u.get("location_updated_at") or u.get("seed"))


def user_distance_km(a: Optional[dict], b: Optional[dict]) -> float:
    """Distance between two users' real locations; UNKNOWN_DISTANCE_KM when either has none."""
    if not has_location(a) or not has_location(b):
        return UNKNOWN_DISTANCE_KM
    return max(MIN_REPORTED_DISTANCE_KM, haversine_km(a["lat"], a["lng"], b["lat"], b["lng"]))


# ----------------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------------
SUPPORTED_LANGUAGES = ("uz", "en", "ru", "it", "ar")


class RegisterBody(BaseModel):
    email: EmailStr
    password: str
    name: str
    preferred_language: Optional[str] = None


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class SessionBody(BaseModel):
    session_id: str


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    bio: Optional[str] = None
    # city / neighborhood / lat / lng are deliberately NOT here: location only comes from the device via
    # PUT /users/me/location. Older app builds still send them on save; pydantic drops unknown fields.
    avatar_url: Optional[str] = None
    genres: Optional[List[str]] = None
    languages: Optional[List[str]] = None
    reading_interests: Optional[List[str]] = None
    is_exchanging: Optional[bool] = None
    preferred_language: Optional[str] = None
    # Set to true by the Welcome screen's "Start exploring" tap — never shown again after that.
    onboarding_completed: Optional[bool] = None


class LocationUpdate(BaseModel):
    """The device's current position plus the area names the device's reverse geocoder derived from it.
    Area names are optional: when the geocoder has no neighborhood, the app sends only the city."""
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    city: Optional[str] = Field(default=None, max_length=80)
    neighborhood: Optional[str] = Field(default=None, max_length=80)
    country: Optional[str] = Field(default=None, max_length=80)


class BookBody(BaseModel):
    title: str
    author: str = ""
    cover_url: Optional[str] = None
    condition: str = "Good"
    language: str = "English"
    genre: str = "Fiction"
    status: str = "Available"
    isbn: Optional[str] = None


BOOK_STATUSES = ("Available", "Reserved", "Swapped")


class BookUpdate(BaseModel):
    """Partial update: only the fields the client sends are changed."""
    title: Optional[str] = None
    author: Optional[str] = None
    cover_url: Optional[str] = None
    condition: Optional[str] = None
    language: Optional[str] = None
    genre: Optional[str] = None
    status: Optional[str] = None
    isbn: Optional[str] = None


class SwapCreate(BaseModel):
    receiver_id: str
    message: Optional[str] = None


class ProposeBody(BaseModel):
    offered_book_id: str
    requested_book_id: str


class MessageBody(BaseModel):
    text: str = ""
    image_url: Optional[str] = None
    # Chosen by the app for each message it sends. A retry of the same send (slow network, app
    # resent after a timeout) carries the same id and gets the original message back instead of a
    # duplicate.
    client_id: Optional[str] = Field(default=None, max_length=64)


class RateBody(BaseModel):
    stars: int
    review: Optional[str] = None


# ----------------------------------------------------------------------------
# Serialization helpers
# ----------------------------------------------------------------------------
# Swap streak badges — friendly milestones for completed swaps.
BADGES = [
    {"id": "first_loop", "label": "First Loop", "threshold": 1, "blurb": "Completed your first swap"},
    {"id": "regular", "label": "Loop Regular", "threshold": 3, "blurb": "3 swaps completed"},
    {"id": "bookworm", "label": "Bookworm", "threshold": 5, "blurb": "5 swaps completed"},
    {"id": "legend", "label": "Loop Legend", "threshold": 10, "blurb": "10 swaps completed"},
]

READING_INTERESTS = [
    "Short reads", "Classics", "Bestsellers", "University texts", "Italian authors",
    "Book club picks", "Thrillers", "Poetry", "Graphic novels", "Non-fiction deep dives",
]


def compute_badges(swaps_count: int) -> List[dict]:
    return [{**b, "earned": swaps_count >= b["threshold"]} for b in BADGES]


def earned_badge_ids(swaps_count: int) -> List[str]:
    return [b["id"] for b in BADGES if swaps_count >= b["threshold"]]


def match_info(me: dict, other: dict, shelf_genres: Optional[set] = None) -> dict:
    """Soft match between two readers: shared genres (profile + shelf), languages, interests."""
    my_g = set(me.get("genres") or [])
    their_g = set(other.get("genres") or []) | (shelf_genres or set())
    shared_g = sorted(my_g & their_g)
    shared_l = sorted(set(me.get("languages") or []) & set(other.get("languages") or []))
    shared_i = sorted(set(me.get("reading_interests") or []) & set(other.get("reading_interests") or []))
    score = 2 * len(shared_g) + len(shared_l) + len(shared_i) + (1 if other.get("is_exchanging", True) else 0)
    return {
        "shared_genres": shared_g,
        "shared_languages": shared_l,
        "shared_interests": shared_i,
        "match_score": score if shared_g else 0,
    }


def public_user(u: dict, private: bool = False) -> dict:
    """Serialize a user. Email and exact coordinates are only included for the
    user's own account (private=True); other readers only get neighborhood-level info
    plus a server-computed distance."""
    if not u:
        return {}
    swaps = u.get("swaps_count", 0)
    located = has_location(u)
    out = {
        "reading_interests": u.get("reading_interests", []),
        "badges": compute_badges(swaps),
        "user_id": u["user_id"],
        "name": u.get("name", ""),
        "avatar_url": u.get("avatar_url"),
        "bio": u.get("bio", ""),
        # Area labels only once a real location exists (see has_location); otherwise null, which the
        # app shows as "location not set" instead of a made-up place.
        "city": u.get("city") if located else None,
        "neighborhood": u.get("neighborhood") if located else None,
        "genres": u.get("genres", []),
        "languages": u.get("languages", []),
        "is_exchanging": u.get("is_exchanging", True),
        "rating": round(u.get("rating", 0.0), 1),
        "rating_count": u.get("rating_count", 0),
        "swaps_count": u.get("swaps_count", 0),
        "preferred_language": u.get("preferred_language"),
    }
    if private:
        out.update(
            {
                "email": u.get("email", ""),
                "lat": u.get("lat") if located else None,
                "lng": u.get("lng") if located else None,
                "location_updated_at": u["location_updated_at"].isoformat() if u.get("location_updated_at") else None,
                # Missing on any account that predates this field (defaults True: never re-surface the
                # welcome screen for an existing user — startup's backfill_onboarding_state() also sets
                # this explicitly, this default is just extra safety before/around that).
                "onboarding_completed": u.get("onboarding_completed", True),
            }
        )
    return out


def clean_book(b: dict) -> dict:
    return {
        "id": b["id"],
        "owner_id": b["owner_id"],
        "title": b["title"],
        "author": b.get("author", ""),
        "cover_url": b.get("cover_url"),
        "condition": b.get("condition", "Good"),
        "language": b.get("language", "English"),
        "genre": b.get("genre", "Fiction"),
        "status": b.get("status", "Available"),
        "isbn": b.get("isbn"),
    }


# ----------------------------------------------------------------------------
# Auth
# ----------------------------------------------------------------------------
def hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_pw(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except Exception:
        return False


async def create_session(user_id: str) -> str:
    token = uuid.uuid4().hex + uuid.uuid4().hex
    await db.user_sessions.insert_one(
        {
            "session_token": token,
            "user_id": user_id,
            "created_at": now_utc(),
            "expires_at": now_utc() + timedelta(days=7),
        }
    )
    return token


async def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ", 1)[1].strip()
    session = await db.user_sessions.find_one({"session_token": token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    exp = session.get("expires_at")
    if exp is not None:
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < now_utc():
            raise HTTPException(status_code=401, detail="Session expired")
    user = await db.users.find_one({"user_id": session["user_id"], "deleted_at": None}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


# --- Email-verified registration (OTP) ---------------------------------------
# Flow: request-otp (send a code) -> verify-otp (redeem it for a short-lived verification_token) ->
# complete (password + that token -> the account is actually created). No step trusts the client:
# "email verified" only ever means "holds a verification_token this server issued and hasn't expired
# or been redeemed yet" — never a boolean the frontend sends.
OTP_LENGTH = 6
OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN_SECONDS = 60
OTP_MAX_SENDS = 10  # absolute cap per pending registration — defense in depth beyond the cooldown
EMAIL_VERIFICATION_TTL_MINUTES = 15
PASSWORD_MIN_LENGTH = 8
# Debug/test only: echoes OTP codes in API responses and enables the instant-registration test
# endpoint below. NEVER true on a backend reachable from the internet — see backend/.env.example.
OTP_DEBUG_MODE = (os.environ.get("OTP_DEBUG_MODE") or "").strip().lower() in ("1", "true", "yes")
if OTP_DEBUG_MODE:
    logger.warning(
        "OTP_DEBUG_MODE is ON: OTP codes are echoed in API responses and /auth/register/dev-instant "
        "is enabled. Never run with this on a backend reachable from the internet."
    )


class RequestOtpBody(BaseModel):
    name: str
    email: EmailStr
    preferred_language: Optional[str] = None


class ResendOtpBody(BaseModel):
    email: EmailStr


class VerifyOtpBody(BaseModel):
    email: EmailStr
    code: str


class CompleteRegistrationBody(BaseModel):
    email: EmailStr
    password: str
    verification_token: str


def _otp_error(status: int, code: str, message: str) -> HTTPException:
    """A structured error the frontend can map to a translated message via `code`, with `message`
    (English) as the fallback for anything that doesn't recognize the code."""
    return HTTPException(status_code=status, detail={"message": message, "code": code})


def validate_password(pw: str) -> List[str]:
    """Returns the unmet requirement codes (empty = valid). This — not the frontend's live checklist —
    is the actual policy: the frontend mirrors these same rules only for UX, never as the source of truth."""
    violations = []
    if len(pw) < PASSWORD_MIN_LENGTH:
        violations.append("too_short")
    if not re.search(r"[A-Z]", pw):
        violations.append("no_upper")
    if not re.search(r"[a-z]", pw):
        violations.append("no_lower")
    if not re.search(r"[0-9]", pw):
        violations.append("no_number")
    if not re.search(r"[^A-Za-z0-9]", pw):
        violations.append("no_special")
    return violations


def _generate_otp() -> str:
    return f"{secrets.randbelow(10 ** OTP_LENGTH):0{OTP_LENGTH}d}"


def _build_user_doc(
    email: str, name: str, password_hash: Optional[str], preferred_language: Optional[str], onboarding_completed: bool
) -> dict:
    return {
        "user_id": new_id("user"),
        "email": email.lower(),
        "password_hash": password_hash,
        "name": name,
        "avatar_url": None,
        "bio": "",
        # No location until the device reports one (PUT /users/me/location) — never a made-up default.
        "city": None,
        "neighborhood": None,
        "lat": None,
        "lng": None,
        "genres": [],
        "languages": ["Italian", "English"],
        "is_exchanging": True,
        "rating": 0.0,
        "rating_count": 0,
        "swaps_count": 0,
        # The language picked on the first-run screen; unknown values are ignored, not an error.
        "preferred_language": preferred_language if preferred_language in SUPPORTED_LANGUAGES else None,
        # New accounts get the one-time welcome screen; backfilled True for every account that
        # existed before this field did (see backfill_onboarding_state).
        "onboarding_completed": onboarding_completed,
        "created_at": now_utc(),
        "deleted_at": None,
    }


async def _issue_otp(email: str, name: Optional[str], preferred_language: Optional[str]) -> dict:
    """Creates/refreshes the pending registration with a fresh OTP and emails it. Enforces the resend
    cooldown and an absolute send cap; shared by request-otp and resend-otp so both are rate-limited
    the same way (calling request-otp again is not a way around the resend cooldown)."""
    email = email.lower()
    existing = await db.pending_registrations.find_one({"email": email})
    now = now_utc()
    if existing:
        last_sent = existing.get("last_otp_sent_at")
        if last_sent and last_sent.tzinfo is None:
            last_sent = last_sent.replace(tzinfo=timezone.utc)
        if last_sent and (now - last_sent) < timedelta(seconds=OTP_RESEND_COOLDOWN_SECONDS):
            wait = OTP_RESEND_COOLDOWN_SECONDS - int((now - last_sent).total_seconds())
            raise _otp_error(429, "rate_limited", f"Please wait {max(wait, 1)}s before requesting another code")
        if (existing.get("send_count") or 0) >= OTP_MAX_SENDS:
            raise _otp_error(429, "rate_limited", "Too many codes requested for this email — try again later")

    code = _generate_otp()
    name = (name or (existing or {}).get("name") or "").strip()
    preferred_language = preferred_language or (existing or {}).get("preferred_language")
    await db.pending_registrations.update_one(
        {"email": email},
        {
            "$set": {
                "name": name,
                "preferred_language": preferred_language if preferred_language in SUPPORTED_LANGUAGES else None,
                "otp_hash": hash_pw(code),  # never store the plaintext code
                "otp_expires_at": now + timedelta(minutes=OTP_TTL_MINUTES),
                "otp_verified": False,
                "attempt_count": 0,
            },
            "$setOnInsert": {"created_at": now},
            "$currentDate": {"last_otp_sent_at": True},
            "$inc": {"send_count": 1},
        },
        upsert=True,
    )

    provider = get_email_provider()
    subject, html, text = otp_email_body(name or "there", code)
    try:
        await provider.send(to=email, subject=subject, html=html, text=text)
    except EmailNotConfiguredError as e:
        if not OTP_DEBUG_MODE:
            raise HTTPException(status_code=503, detail=str(e))
        logger.warning(f"OTP email not sent (no provider configured) — continuing because OTP_DEBUG_MODE is on")
    except EmailSendError as e:
        raise HTTPException(status_code=502, detail="Could not send the verification email — please try again") from e

    result = {"ok": True, "expires_in_seconds": OTP_TTL_MINUTES * 60, "resend_after_seconds": OTP_RESEND_COOLDOWN_SECONDS}
    if OTP_DEBUG_MODE:
        result["debug_otp"] = code  # never included unless OTP_DEBUG_MODE is explicitly on
    return result


@api.post("/auth/register/request-otp")
async def request_otp(body: RequestOtpBody):
    # Handled the same way whether or not the email is taken, up to this one clear rejection — matches
    # the existing plain /auth/login "Invalid email or password" precedent of not being fully silent.
    existing_user = await db.users.find_one({"email": body.email.lower(), "deleted_at": None})
    if existing_user:
        raise _otp_error(409, "email_registered", "An account with this email already exists")
    return await _issue_otp(body.email, body.name.strip(), body.preferred_language)


@api.post("/auth/register/resend-otp")
async def resend_otp(body: ResendOtpBody):
    email = body.email.lower()
    pending = await db.pending_registrations.find_one({"email": email})
    if not pending:
        raise _otp_error(400, "otp_expired", "Start registration again")
    return await _issue_otp(email, None, None)


@api.post("/auth/register/verify-otp")
async def verify_otp(body: VerifyOtpBody):
    email = body.email.lower()
    pending = await db.pending_registrations.find_one({"email": email})
    if not pending or not pending.get("otp_hash"):
        raise _otp_error(400, "otp_expired", "Verification code expired — request a new one")
    exp = pending.get("otp_expires_at")
    if exp and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if not exp or exp < now_utc():
        raise _otp_error(400, "otp_expired", "Verification code expired — request a new one")
    if (pending.get("attempt_count") or 0) >= OTP_MAX_ATTEMPTS:
        raise _otp_error(429, "too_many_attempts", "Too many incorrect attempts — request a new code")
    if not verify_pw(body.code.strip(), pending["otp_hash"]):
        await db.pending_registrations.update_one({"email": email}, {"$inc": {"attempt_count": 1}})
        raise _otp_error(400, "invalid_otp", "Incorrect verification code")

    # One-time use: a fresh, server-issued token proves verification for the next step. The OTP hash
    # is cleared so it can never be replayed even within its expiry window.
    token = secrets.token_urlsafe(32)
    await db.email_verifications.insert_one({"token": token, "email": email, "created_at": now_utc()})
    await db.pending_registrations.update_one({"email": email}, {"$set": {"otp_hash": None, "otp_verified": True}})
    return {"verification_token": token, "expires_in_seconds": EMAIL_VERIFICATION_TTL_MINUTES * 60}


@api.post("/auth/register/complete")
async def complete_registration(body: CompleteRegistrationBody):
    email = body.email.lower()

    # Validate everything BEFORE consuming the one-time token: a rejected password must never burn
    # it, or a user who simply mistypes their password gets sent all the way back through OTP just to
    # retry. bcrypt only uses the first 72 bytes and (v5+) refuses longer input, so check that too.
    if len(body.password.encode()) > 72:
        raise HTTPException(status_code=400, detail="Password is too long (maximum 72 bytes)")
    violations = validate_password(body.password)
    if violations:
        raise HTTPException(
            status_code=400,
            detail={"message": "Password does not meet requirements", "code": "weak_password", "violations": violations},
        )

    # Read-only check first — still valid? If not, no need to touch (or waste) the token at all.
    rec = await db.email_verifications.find_one({"token": body.verification_token, "email": email})
    if not rec or (now_utc() - rec["created_at"].replace(tzinfo=timezone.utc)) > timedelta(minutes=EMAIL_VERIFICATION_TTL_MINUTES):
        raise _otp_error(401, "verification_expired", "Email verification expired — please verify again")

    pending = await db.pending_registrations.find_one({"email": email})
    if not pending or not pending.get("otp_verified"):
        raise _otp_error(401, "verification_expired", "Email verification expired — please verify again")

    # Re-check: another request could have completed registration for this email in between.
    if await db.users.find_one({"email": email}):
        raise _otp_error(409, "email_registered", "An account with this email already exists")

    # Everything checks out — NOW consume the token atomically. Two concurrent completes racing here
    # can never both win: only one find_one_and_delete call actually finds (and deletes) it.
    rec = await db.email_verifications.find_one_and_delete({"token": body.verification_token, "email": email})
    if not rec:
        raise _otp_error(401, "verification_expired", "Email verification expired — please verify again")

    user = _build_user_doc(
        email, pending.get("name") or "", hash_pw(body.password), pending.get("preferred_language"), onboarding_completed=False
    )
    await db.users.insert_one(user)
    await db.pending_registrations.delete_one({"email": email})
    token = await create_session(user["user_id"])
    return {"session_token": token, "user": public_user(user, private=True)}


if OTP_DEBUG_MODE:
    # Test-only: mirrors the OLD instant-registration contract (email+password+name -> immediate
    # account+session) so the existing test suite's fixtures keep working unchanged, without any
    # OTP-bypass endpoint existing in a normal run — this route is simply absent (true 404) whenever
    # OTP_DEBUG_MODE is off, which is every environment except an explicit local test run.
    @api.post("/auth/register/dev-instant")
    async def register_dev_instant(body: RegisterBody):
        if len(body.password.encode()) > 72:
            raise HTTPException(status_code=400, detail="Password is too long (maximum 72 bytes)")
        if await db.users.find_one({"email": body.email.lower()}):
            raise HTTPException(status_code=400, detail="Email already registered")
        user = _build_user_doc(body.email, body.name, hash_pw(body.password), body.preferred_language, onboarding_completed=True)
        # Marks accounts made by the test suite so they can always be told apart from real sign-ups.
        user["test_account"] = True
        await db.users.insert_one(user)
        token = await create_session(user["user_id"])
        return {"session_token": token, "user": public_user(user, private=True)}


@api.post("/auth/login")
async def login(body: LoginBody):
    user = await db.users.find_one({"email": body.email.lower(), "deleted_at": None})
    if not user or not user.get("password_hash") or not verify_pw(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = await create_session(user["user_id"])
    return {"session_token": token, "user": public_user(user, private=True)}


# --- Google sign-in -----------------------------------------------------------
# Flow: the app opens /auth/google/start in a browser -> Google -> /auth/google/callback (this API
# exchanges the code with our client secret and finds/creates the user) -> back to the app with a
# one-time `session_id` -> the app POSTs it to /auth/session and receives a normal session token.
def _google_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def _allowed_app_redirect(url: str) -> bool:
    """Only send the one-time login id back to the app itself, never to an arbitrary site."""
    try:
        p = urlparse(url)
    except ValueError:
        return False
    if p.scheme in APP_URL_SCHEMES:
        return True
    if p.scheme in ("http", "https") and p.hostname and p.username is None:
        if f"{p.scheme}://{p.netloc}" in APP_REDIRECT_ORIGINS:
            return True
        if p.scheme == "http" and p.hostname in ("localhost", "127.0.0.1"):
            return True
    return False


def _app_redirect(url: str, **params) -> RedirectResponse:
    return RedirectResponse(url + ("&" if "?" in url else "?") + urlencode(params), status_code=302)


def _google_claims(tok: dict) -> Optional[dict]:
    """Claims of the id_token Google returned from its token endpoint over TLS. It was obtained with our
    client secret, so the signature needn't be re-verified, but audience, issuer, expiry and a verified
    email are still enforced."""
    try:
        payload = tok["id_token"].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if claims.get("aud") != GOOGLE_CLIENT_ID or claims.get("iss") not in GOOGLE_ISSUERS:
            return None
        if int(claims.get("exp", 0)) < time.time():
            return None
        if not claims.get("email") or claims.get("email_verified") not in (True, "true"):
            return None
        return claims
    except Exception:
        return None


@api.get("/auth/providers")
async def auth_providers():
    return {"google": _google_configured()}


@api.get("/auth/google/start")
async def google_start(request: Request, redirect: str):
    if not _google_configured():
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")
    if not _allowed_app_redirect(redirect):
        raise HTTPException(status_code=400, detail="Redirect URL not allowed")
    state = secrets.token_urlsafe(32)
    await db.oauth_states.insert_one({"state": state, "redirect": redirect, "created_at": now_utc()})
    callback = (PUBLIC_BACKEND_URL or str(request.base_url).rstrip("/")) + "/api/auth/google/callback"
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": callback,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(params)}", status_code=302)


@api.get("/auth/google/callback")
async def google_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    # `state` is single-use and short-lived; without a valid one this isn't a sign-in we started.
    rec = await db.oauth_states.find_one_and_delete({"state": state}) if state else None
    if not rec or (now_utc() - rec["created_at"].replace(tzinfo=timezone.utc)) > timedelta(minutes=10):
        raise HTTPException(status_code=400, detail="Invalid or expired sign-in attempt")
    redirect = rec["redirect"]
    if error or not code:
        return _app_redirect(redirect, google_error=error or "cancelled")
    callback = (PUBLIC_BACKEND_URL or str(request.base_url).rstrip("/")) + "/api/auth/google/callback"
    try:
        async with httpx.AsyncClient(timeout=20) as hc:
            resp = await hc.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": callback,
                    "grant_type": "authorization_code",
                },
            )
        tok = resp.json() if resp.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"Google token exchange failed: {e!r}")
        tok = None
    claims = _google_claims(tok) if tok else None
    user = await _google_user(claims) if claims else None
    if not user:
        return _app_redirect(redirect, google_error="failed")
    sid = secrets.token_urlsafe(32)
    await db.google_logins.insert_one({"sid": sid, "user_id": user["user_id"], "created_at": now_utc()})
    return _app_redirect(redirect, session_id=sid)


@api.post("/auth/session")
async def google_session(body: SessionBody):
    """Redeem the one-time id from a completed Google sign-in for a normal app session."""
    rec = await db.google_logins.find_one_and_delete({"sid": body.session_id})
    if not rec or (now_utc() - rec["created_at"].replace(tzinfo=timezone.utc)) > timedelta(minutes=2):
        raise HTTPException(status_code=401, detail="Invalid session")
    user = await db.users.find_one({"user_id": rec["user_id"], "deleted_at": None}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid session")
    token = await create_session(user["user_id"])
    return {"session_token": token, "user": public_user(user, private=True)}


async def _google_user(claims: dict) -> Optional[dict]:
    """Find the account for a verified Google email, or create it."""
    email = claims["email"].lower()
    name = claims.get("name") or email.split("@")[0]
    picture = claims.get("picture")
    user = await db.users.find_one({"email": email})
    if user:
        if user.get("deleted_at"):
            return None
        uid = user["user_id"]
        if picture and not user.get("avatar_url"):
            await db.users.update_one({"user_id": uid}, {"$set": {"avatar_url": picture}})
            user["avatar_url"] = picture
    else:
        uid = new_id("user")
        user = {
            "user_id": uid,
            "email": email,
            "password_hash": None,
            "name": name,
            "avatar_url": picture,
            "bio": "",
            "city": None,
            "neighborhood": None,
            "lat": None,
            "lng": None,
            "genres": [],
            "languages": ["Italian", "English"],
            "is_exchanging": True,
            "rating": 0.0,
            "rating_count": 0,
            "swaps_count": 0,
            "preferred_language": None,
            # First-time Google sign-up is also a first-time registration: same one-time welcome
            # screen as email/password signup gets (this only sets the field on the brand-new account
            # — the Google sign-in flow itself is unchanged).
            "onboarding_completed": False,
            "created_at": now_utc(),
            "deleted_at": None,
        }
        await db.users.insert_one(user)
    return user


@api.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return {"user": public_user(user, private=True)}


@api.post("/auth/logout")
async def logout(authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1].strip()
        await db.user_sessions.delete_one({"session_token": token})
    return {"ok": True}


# ----------------------------------------------------------------------------
# Profile
# ----------------------------------------------------------------------------
@api.put("/users/me")
async def update_me(body: ProfileUpdate, user: dict = Depends(get_current_user)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if "preferred_language" in updates and updates["preferred_language"] not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail="Unsupported language")
    if updates:
        await db.users.update_one({"user_id": user["user_id"]}, {"$set": updates})
    fresh = await db.users.find_one({"user_id": user["user_id"]}, {"_id": 0})
    return {"user": public_user(fresh, private=True)}


def _clean_place(v: Optional[str]) -> Optional[str]:
    v = (v or "").strip()
    return v or None


@api.put("/users/me/location")
async def update_my_location(body: LocationUpdate, user: dict = Depends(get_current_user)):
    """The only way a user's location changes: the app sends the device position after the user
    granted location permission. Coordinates are stored at ~11 m precision (4 decimals) and are never
    returned to other users — they only ever see an area name and a distance."""
    neighborhood = _clean_place(body.neighborhood)
    city = _clean_place(body.city)
    if neighborhood and city and neighborhood.casefold() == city.casefold():
        neighborhood = None  # the geocoder echoed the city: don't show "Tashkent, Tashkent"
    updates = {
        "lat": round(body.lat, 4),
        "lng": round(body.lng, 4),
        "city": city,
        "neighborhood": neighborhood,
        "country": _clean_place(body.country),
        "location_updated_at": now_utc(),
    }
    await db.users.update_one({"user_id": user["user_id"]}, {"$set": updates})
    fresh = await db.users.find_one({"user_id": user["user_id"]}, {"_id": 0})
    return {"user": public_user(fresh, private=True)}


async def _books_for(owner_id: str, only_available: bool = False):
    q = {"owner_id": owner_id, "deleted_at": None}
    if only_available:
        q["status"] = "Available"
    books = await db.books.find(q, {"_id": 0}).sort("created_at", -1).to_list(200)
    return [clean_book(b) for b in books]


@api.get("/users/{user_id}")
async def get_user(user_id: str, user: dict = Depends(get_current_user)):
    u = await db.users.find_one({"user_id": user_id, "deleted_at": None}, {"_id": 0})
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    ratings = await db.ratings.find({"ratee_id": user_id}, {"_id": 0}).sort("created_at", -1).to_list(50)
    for r in ratings:
        rater = await db.users.find_one({"user_id": r["rater_id"]}, {"_id": 0})
        r["rater_name"] = rater.get("name") if rater else "Someone"
        r["rater_avatar"] = rater.get("avatar_url") if rater else None
    pu = public_user(u, private=(user_id == user["user_id"]))
    pu["distance_km"] = user_distance_km(user, u)
    books = await _books_for(user_id)
    pu.update(match_info(user, u, {b["genre"] for b in books if b["status"] == "Available"}))
    return {"user": pu, "books": books, "reviews": ratings}


# ----------------------------------------------------------------------------
# Books
# ----------------------------------------------------------------------------
@api.get("/books")
async def my_books(status: Optional[str] = None, user: dict = Depends(get_current_user)):
    q = {"owner_id": user["user_id"], "deleted_at": None}
    if status and status != "All":
        q["status"] = status
    books = await db.books.find(q, {"_id": 0}).sort("created_at", -1).to_list(300)
    return {"books": [clean_book(b) for b in books]}


@api.post("/books")
async def add_book(body: BookBody, user: dict = Depends(get_current_user)):
    book = {
        "id": new_id("book"),
        "owner_id": user["user_id"],
        "title": body.title,
        "author": body.author,
        "cover_url": body.cover_url,
        "condition": body.condition,
        "language": body.language,
        "genre": body.genre,
        "isbn": body.isbn,
        "status": body.status if body.status in ("Available", "Reserved", "Swapped") else "Available",
        "created_at": now_utc(),
        "deleted_at": None,
    }
    await db.books.insert_one(book)
    return {"book": clean_book(book)}


async def _book_in_active_swap(book_id: str) -> bool:
    """True while an accepted/active swap holds this book in its proposal."""
    return bool(
        await db.swaps.find_one(
            {
                "status": {"$in": ["accepted", "active"]},
                "$or": [
                    {"active_proposal.offered_book_id": book_id},
                    {"active_proposal.requested_book_id": book_id},
                ],
            },
            {"_id": 1},
        )
    )


@api.put("/books/{book_id}")
async def update_book(book_id: str, body: BookUpdate, user: dict = Depends(get_current_user)):
    book = await db.books.find_one({"id": book_id, "owner_id": user["user_id"], "deleted_at": None})
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    # Partial update: fields the client didn't send keep their stored value.
    updates = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None or k == "cover_url"}
    if "title" in updates and not updates["title"].strip():
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    if "status" in updates:
        if updates["status"] not in BOOK_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status")
        if updates["status"] == book.get("status"):
            updates.pop("status")
        elif await _book_in_active_swap(book_id):
            raise HTTPException(status_code=409, detail="This book is part of an active swap; cancel the swap first")
    if updates:
        await db.books.update_one({"id": book_id}, {"$set": updates})
    fresh = await db.books.find_one({"id": book_id}, {"_id": 0})
    return {"book": clean_book(fresh)}


@api.get("/books/detail/{book_id}")
async def get_book(book_id: str, user: dict = Depends(get_current_user)):
    b = await db.books.find_one({"id": book_id, "deleted_at": None}, {"_id": 0})
    if not b:
        raise HTTPException(status_code=404, detail="Book not found")
    owner = await db.users.find_one({"user_id": b["owner_id"], "deleted_at": None}, {"_id": 0})
    ownerp = public_user(owner) if owner else {}
    if owner:
        ownerp["distance_km"] = user_distance_km(user, owner)
    is_owner = b["owner_id"] == user["user_id"]
    wanted_by = 0
    if is_owner and b.get("status") == "Available":
        demand = await _demand_for_books(user, [b])
        wanted_by = demand["books"].get(b["id"], 0)
    return {"book": clean_book(b), "owner": ownerp, "is_owner": is_owner, "wanted_by": wanted_by}


DEMAND_RADIUS_KM = 25.0


async def _nearby_readers(user: dict) -> List[dict]:
    """Other exchanging readers within the demand radius (or same city when no coords)."""
    people = await db.users.find(
        {"deleted_at": None, "is_exchanging": True, "user_id": {"$ne": user["user_id"]}}, {"_id": 0}
    ).to_list(500)
    out = []
    for p in people:
        dist = user_distance_km(user, p)
        if dist <= DEMAND_RADIUS_KM or dist == UNKNOWN_DISTANCE_KM:
            p["distance_km"] = dist
            out.append(p)
    return out


async def _demand_for_books(user: dict, books: List[dict]) -> dict:
    """How many nearby exchanging readers want each of my books (by genre + language preference)."""
    readers = await _nearby_readers(user)
    per_book: dict = {}
    interested_ids = set()
    for b in books:
        if b.get("status") != "Available":
            continue
        n = 0
        for r in readers:
            if b.get("genre") in (r.get("genres") or []):
                langs = r.get("languages") or []
                if not langs or b.get("language") in langs:
                    n += 1
                    interested_ids.add(r["user_id"])
        per_book[b["id"]] = n
    return {"books": per_book, "total_readers": len(interested_ids)}


@api.get("/books/demand")
async def books_demand(user: dict = Depends(get_current_user)):
    books = await db.books.find({"owner_id": user["user_id"], "deleted_at": None}, {"_id": 0}).to_list(300)
    return await _demand_for_books(user, books)


LANG_CODE_MAP = {
    "eng": "English", "ita": "Italian", "spa": "Spanish", "fre": "French",
    "fra": "French", "ger": "German", "deu": "German", "ara": "Arabic", "por": "Portuguese",
    "rus": "Russian", "uzb": "Uzbek",
}


def _ol_cover(cover_i, isbn_list):
    if cover_i:
        return f"https://covers.openlibrary.org/b/id/{cover_i}-M.jpg"
    if isbn_list:
        return f"https://covers.openlibrary.org/b/isbn/{isbn_list[0]}-M.jpg"
    return None


@api.get("/books/search")
async def book_search(q: str, user: dict = Depends(get_current_user)):
    if not q or len(q.strip()) < 2:
        return {"results": []}
    url = "https://openlibrary.org/search.json"
    params = {"q": q, "limit": 20, "fields": "title,author_name,isbn,cover_i,language"}
    try:
        async with httpx.AsyncClient(timeout=12) as hc:
            resp = await hc.get(url, params=params, headers={"User-Agent": "BookLoop/1.0"})
        docs = resp.json().get("docs", []) if resp.status_code == 200 else []
    except Exception:
        docs = []
    results = []
    for d in docs:
        isbns = d.get("isbn") or []
        langs = d.get("language") or []
        results.append({
            "title": d.get("title", "Untitled"),
            "author": ", ".join((d.get("author_name") or [])[:2]),
            "cover_url": _ol_cover(d.get("cover_i"), isbns),
            "isbn": isbns[0] if isbns else None,
            "language": LANG_CODE_MAP.get(langs[0], "English") if langs else "English",
        })
    return {"results": results}


@api.get("/books/isbn/{isbn}")
async def book_isbn(isbn: str, user: dict = Depends(get_current_user)):
    url = "https://openlibrary.org/search.json"
    try:
        async with httpx.AsyncClient(timeout=12) as hc:
            resp = await hc.get(url, params={"isbn": isbn, "fields": "title,author_name,isbn,cover_i,language"}, headers={"User-Agent": "BookLoop/1.0"})
        docs = resp.json().get("docs", []) if resp.status_code == 200 else []
    except Exception:
        docs = []
    if not docs:
        return {"result": {"title": "", "author": "", "isbn": isbn, "language": "English", "cover_url": f"https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg"}}
    d = docs[0]
    isbns = d.get("isbn") or [isbn]
    langs = d.get("language") or []
    return {"result": {
        "title": d.get("title", ""),
        "author": ", ".join((d.get("author_name") or [])[:2]),
        "cover_url": _ol_cover(d.get("cover_i"), isbns),
        "isbn": isbn,
        "language": LANG_CODE_MAP.get(langs[0], "English") if langs else "English",
    }}


@api.delete("/books/{book_id}")
async def delete_book(book_id: str, user: dict = Depends(get_current_user)):
    if await _book_in_active_swap(book_id):
        raise HTTPException(status_code=409, detail="This book is part of an active swap; cancel the swap first")
    res = await db.books.update_one(
        {"id": book_id, "owner_id": user["user_id"]}, {"$set": {"deleted_at": now_utc()}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Book not found")
    return {"ok": True}


# ----------------------------------------------------------------------------
# Discover
# ----------------------------------------------------------------------------
DISCOVER_CANDIDATE_LIMIT = 5000
DISCOVER_RESULT_LIMIT = 200


@api.get("/discover/people")
async def discover_people(
    search: Optional[str] = None,
    genre: Optional[str] = None,
    language: Optional[str] = None,
    exchanging: bool = False,
    max_distance: Optional[float] = None,
    user: dict = Depends(get_current_user),
):
    q = {"deleted_at": None, "user_id": {"$ne": user["user_id"]}}
    if exchanging:
        q["is_exchanging"] = True
    if genre and genre != "All":
        q["genres"] = genre
    if language and language != "All":
        q["languages"] = language
    if search:
        q["name"] = {"$regex": re.escape(search), "$options": "i"}
    # Filter by distance and rank over ALL matching readers before truncating; capping the query
    # first returned an arbitrary 200 and could drop the nearest readers entirely.
    people = await db.users.find(q, {"_id": 0}).to_list(DISCOVER_CANDIDATE_LIMIT)
    candidates = []
    for p in people:
        dist = user_distance_km(user, p)
        if max_distance is not None and dist > max_distance and dist != UNKNOWN_DISTANCE_KM:
            continue
        candidates.append((p, dist))
    shelves: dict = {}
    if candidates:
        books = await db.books.find(
            {"owner_id": {"$in": [p["user_id"] for p, _ in candidates]}, "status": "Available", "deleted_at": None},
            {"_id": 0},
        ).sort("created_at", -1).to_list(None)
        for b in books:
            shelves.setdefault(b["owner_id"], []).append(clean_book(b))
    result = []
    for p, dist in candidates:
        pu = public_user(p)
        pu["distance_km"] = dist
        pu["books"] = shelves.get(p["user_id"], [])[:6]
        pu["available_count"] = len(pu["books"])
        pu.update(match_info(user, p, {b["genre"] for b in pu["books"]}))
        result.append(pu)
    # Genre matches first, then closest.
    result.sort(key=lambda x: (-x["match_score"], x["distance_km"]))
    result = result[:DISCOVER_RESULT_LIMIT]
    top_matches = [p for p in result if p["match_score"] > 0][:6]
    return {"people": result, "top_matches": top_matches}


@api.get("/discover/books")
async def discover_books(
    search: Optional[str] = None,
    genre: Optional[str] = None,
    language: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    q = {"deleted_at": None, "status": "Available", "owner_id": {"$ne": user["user_id"]}}
    if genre and genre != "All":
        q["genre"] = genre
    if language and language != "All":
        q["language"] = language
    if search:
        pattern = re.escape(search)
        q["$or"] = [
            {"title": {"$regex": pattern, "$options": "i"}},
            {"author": {"$regex": pattern, "$options": "i"}},
        ]
    books = await db.books.find(q, {"_id": 0}).sort("created_at", -1).to_list(200)
    out = []
    for b in books:
        owner = await db.users.find_one({"user_id": b["owner_id"]}, {"_id": 0})
        cb = clean_book(b)
        cb["owner_name"] = owner.get("name") if owner else ""
        cb["owner_avatar"] = owner.get("avatar_url") if owner else None
        cb["distance_km"] = user_distance_km(user, owner)
        out.append(cb)
    return {"books": out}


GENRE_LIST = ["Fiction", "Psychology", "Business", "History", "Biography", "Self-development", "Romance", "Fantasy", "Philosophy", "Science"]


# ----------------------------------------------------------------------------
# Wishlist — soft preferences (genres / languages / interests) surfacing
# nearby books & readers. No exact-title matching in V1.
# ----------------------------------------------------------------------------
@api.get("/wishlist")
async def wishlist(max_distance: float = 25.0, user: dict = Depends(get_current_user)):
    my_genres = set(user.get("genres") or [])
    my_langs = set(user.get("languages") or [])
    prefs = {
        "genres": sorted(my_genres),
        "languages": sorted(my_langs),
        "reading_interests": user.get("reading_interests", []),
        "options": {"reading_interests": READING_INTERESTS},
    }
    if not my_genres:
        return {"preferences": prefs, "books": [], "people": [], "needs_setup": True}

    others = await db.users.find({"deleted_at": None, "user_id": {"$ne": user["user_id"]}}, {"_id": 0}).to_list(500)
    owners: dict = {}
    for o in others:
        dist = user_distance_km(user, o)
        if dist > max_distance and dist != UNKNOWN_DISTANCE_KM:
            continue
        o["distance_km"] = dist
        owners[o["user_id"]] = o

    books = await db.books.find(
        {"deleted_at": None, "status": "Available", "owner_id": {"$in": list(owners.keys())}, "genre": {"$in": list(my_genres)}},
        {"_id": 0},
    ).to_list(500)
    out_books = []
    for b in books:
        o = owners[b["owner_id"]]
        score = 2 + (1 if not my_langs or b.get("language") in my_langs else 0) + (1 if o.get("is_exchanging", True) else 0)
        cb = clean_book(b)
        cb.update({
            "owner_name": o.get("name", ""),
            "owner_avatar": o.get("avatar_url"),
            "owner_exchanging": o.get("is_exchanging", True),
            "distance_km": o["distance_km"],
            "match_score": score,
        })
        out_books.append(cb)
    out_books.sort(key=lambda x: (-x["match_score"], x["distance_km"]))

    out_people = []
    for o in owners.values():
        shelf = {b["genre"] for b in books if b["owner_id"] == o["user_id"]}
        mi = match_info(user, o, shelf)
        if mi["match_score"] <= 0:
            continue
        pu = public_user(o)
        pu["distance_km"] = o["distance_km"]
        pu["available_count"] = len([b for b in books if b["owner_id"] == o["user_id"]])
        pu.update(mi)
        out_people.append(pu)
    out_people.sort(key=lambda x: (-x["match_score"], x["distance_km"]))
    return {"preferences": prefs, "books": out_books[:40], "people": out_people[:20], "needs_setup": False}


# ----------------------------------------------------------------------------
# Map
# ----------------------------------------------------------------------------
# Fixed coordinates of the seeded demo community's neighborhoods (demo data only — real users'
# locations come from their devices).
NEIGHBORHOODS = {
    "Centro": (38.1938, 15.5540),
    "University Area": (38.2490, 15.5560),
    "Annunziata": (38.2470, 15.5470),
    "Giostra": (38.2100, 15.5470),
    "Tremestieri": (38.1200, 15.5200),
    "Provinciale": (38.1850, 15.5450),
}

# Cluster centers are rounded to 2 decimals (~1 km) so a small cluster never pinpoints a reader.
CLUSTER_CENTER_DECIMALS = 2


@api.get("/map/clusters")
async def map_clusters(user: dict = Depends(get_current_user)):
    """Exchanging readers grouped by the area their device reported (neighborhood, else city).
    Readers without a real location are left off the map. Totals count readers within
    DEMAND_RADIUS_KM of the viewer when the viewer's own location is known, otherwise everyone."""
    people = await db.users.find({"is_exchanging": True, "deleted_at": None}, {"_id": 0}).to_list(5000)
    groups: dict = {}
    for p in people:
        if not has_location(p):
            continue
        city = p.get("city") or ""
        label = p.get("neighborhood") or city
        if not label:
            continue
        g = groups.setdefault((city.casefold(), label.casefold()), {"label": label, "city": city or None, "people": []})
        g["people"].append(p)

    viewer_located = has_location(user)
    clusters = []
    total_people = 0
    total_books = 0
    for g in groups.values():
        members = g["people"]
        ids = [m["user_id"] for m in members]
        bcount = await db.books.count_documents({"owner_id": {"$in": ids}, "status": "Available", "deleted_at": None})
        genre_counts: dict = {}
        for m in members:
            for genre in m.get("genres", []):
                genre_counts[genre] = genre_counts.get(genre, 0) + 1
        top = sorted(genre_counts.items(), key=lambda x: -x[1])[:4]
        lat = round(sum(m["lat"] for m in members) / len(members), CLUSTER_CENTER_DECIMALS)
        lng = round(sum(m["lng"] for m in members) / len(members), CLUSTER_CENTER_DECIMALS)
        cluster_id = hashlib.sha1(f"{g['city']}|{g['label']}".casefold().encode()).hexdigest()[:12]
        clusters.append(
            {
                "id": cluster_id,
                "neighborhood": g["label"],
                "city": g["city"],
                "lat": lat,
                "lng": lng,
                "people_count": len(members),
                "books_count": bcount,
                "top_genres": [name for name, _ in top],
            }
        )
        nearby = [m for m in members if m["user_id"] != user["user_id"]]
        if viewer_located:
            nearby = [m for m in nearby if user_distance_km(user, m) <= DEMAND_RADIUS_KM]
        if nearby:
            total_people += len(nearby)
            total_books += await db.books.count_documents(
                {"owner_id": {"$in": [m["user_id"] for m in nearby]}, "status": "Available", "deleted_at": None}
            )
    clusters.sort(key=lambda c: -c["people_count"])
    return {"clusters": clusters, "total_active": total_people, "total_books": total_books}


# ----------------------------------------------------------------------------
# Swaps
# ----------------------------------------------------------------------------
async def _add_message(
    swap_id: str,
    sender_id: Optional[str],
    mtype: str,
    text: str = "",
    proposal: dict = None,
    image_url: str = None,
    key: Optional[str] = None,
    params: Optional[dict] = None,
    client_id: Optional[str] = None,
):
    """Store a chat message. System/proposal messages also carry an i18n `key` + `params` so each
    client renders them in its own language; `text` stays as the English fallback for old clients
    and for rows written before keys existed."""
    msg = {
        "id": new_id("msg"),
        "swap_id": swap_id,
        "sender_id": sender_id,
        "type": mtype,
        "text": text,
        "key": key,
        "params": params or {},
        "proposal": proposal,
        "image_url": image_url,
        "created_at": now_utc(),
    }
    if client_id:
        msg["client_id"] = client_id
    await db.messages.insert_one(dict(msg))
    if mtype == "image":
        last, last_key, last_params = "📷 Photo", "swapChat.sys.photo", {}
    elif key:
        last, last_key, last_params = text, key, params or {}
    else:
        last, last_key, last_params = (text or mtype), None, {}
    await db.swaps.update_one(
        {"id": swap_id},
        {"$set": {"updated_at": now_utc(), "last_message": last,
                  "last_message_key": last_key, "last_message_params": last_params}},
    )
    return msg


def clean_swap(s: dict) -> dict:
    return {
        "id": s["id"],
        "requester_id": s["requester_id"],
        "receiver_id": s["receiver_id"],
        "status": s["status"],
        "last_message": s.get("last_message", ""),
        "last_message_key": s.get("last_message_key"),
        "last_message_params": s.get("last_message_params") or {},
        "requester_completed": s.get("requester_completed", False),
        "receiver_completed": s.get("receiver_completed", False),
        "requester_rated": s.get("requester_rated", False),
        "receiver_rated": s.get("receiver_rated", False),
        "active_proposal": dict(s["active_proposal"]) if s.get("active_proposal") else None,
        "updated_at": s.get("updated_at").isoformat() if s.get("updated_at") else None,
    }


async def _swap_with_meta(s: dict, me_id: str) -> dict:
    cs = clean_swap(s)
    other_id = s["receiver_id"] if s["requester_id"] == me_id else s["requester_id"]
    other = await db.users.find_one({"user_id": other_id}, {"_id": 0})
    cs["other_user"] = public_user(other) if other else {}
    cs["is_requester"] = s["requester_id"] == me_id
    return cs


@api.post("/swaps")
async def create_swap(body: SwapCreate, user: dict = Depends(get_current_user)):
    if body.receiver_id == user["user_id"]:
        raise HTTPException(status_code=400, detail="Cannot swap with yourself")
    receiver = await db.users.find_one({"user_id": body.receiver_id, "deleted_at": None})
    if not receiver:
        raise HTTPException(status_code=404, detail="User not found")
    existing = await db.swaps.find_one(
        {
            "$or": [
                {"requester_id": user["user_id"], "receiver_id": body.receiver_id},
                {"requester_id": body.receiver_id, "receiver_id": user["user_id"]},
            ],
            "status": {"$in": ["pending", "accepted", "active"]},
        }
    )
    if existing:
        return {"swap": await _swap_with_meta(existing, user["user_id"]), "existing": True}
    swap = {
        "id": new_id("swap"),
        "requester_id": user["user_id"],
        "receiver_id": body.receiver_id,
        "status": "pending",
        "requester_completed": False,
        "receiver_completed": False,
        "requester_rated": False,
        "receiver_rated": False,
        "active_proposal": None,
        "last_message": "",
        "created_at": now_utc(),
        "updated_at": now_utc(),
    }
    await db.swaps.insert_one(swap)
    await _add_message(swap["id"], None, "system", f"{user['name']} is interested in exchanging books.",
                       key="swapChat.sys.interested", params={"name": user["name"]})
    if body.message:
        await _add_message(swap["id"], user["user_id"], "text", body.message)
    return {"swap": await _swap_with_meta(swap, user["user_id"]), "existing": False}


@api.get("/swaps")
async def list_swaps(user: dict = Depends(get_current_user)):
    uid = user["user_id"]
    swaps = await db.swaps.find(
        {"$or": [{"requester_id": uid}, {"receiver_id": uid}]}, {"_id": 0}
    ).sort("updated_at", -1).to_list(300)
    other_ids = {s["receiver_id"] if s["requester_id"] == uid else s["requester_id"] for s in swaps}
    others = {u["user_id"]: u for u in await db.users.find({"user_id": {"$in": list(other_ids)}}, {"_id": 0}).to_list(None)}
    # Every swap lands in exactly one bucket, decided by its status alone:
    #   pending            -> incoming (I received it) / outgoing (I sent it)
    #   accepted / active  -> active
    #   completed, declined, cancelled -> completed (the "Done" history; each row shows its status)
    buckets = {"incoming": [], "outgoing": [], "active": [], "completed": []}
    for s in swaps:
        meta = clean_swap(s)
        other = others.get(s["receiver_id"] if s["requester_id"] == uid else s["requester_id"])
        meta["other_user"] = public_user(other) if other else {}
        meta["is_requester"] = s["requester_id"] == uid
        status = s["status"]
        if status == "pending":
            buckets["incoming" if s["receiver_id"] == uid else "outgoing"].append(meta)
        elif status in OPEN_ACTIVE_STATUSES:
            buckets["active"].append(meta)
        elif status in CLOSED_STATUSES:
            buckets["completed"].append(meta)
    return buckets


OPEN_ACTIVE_STATUSES = ("accepted", "active")
CLOSED_STATUSES = ("completed", "declined", "cancelled")


@api.get("/swaps/with/{user_id}")
async def swap_with_user(user_id: str, user: dict = Depends(get_current_user)):
    """The caller's open swap with this reader (pending/accepted/active), if any, and their most
    recent swap of any status. Lets the app open an existing conversation instead of silently
    starting a new request after a swap has finished."""
    uid = user["user_id"]
    pair = {"$or": [{"requester_id": uid, "receiver_id": user_id}, {"requester_id": user_id, "receiver_id": uid}]}
    open_swap = await db.swaps.find_one({**pair, "status": {"$in": ["pending", *OPEN_ACTIVE_STATUSES]}}, {"_id": 0})
    latest = await db.swaps.find_one(pair, {"_id": 0}, sort=[("updated_at", -1)])
    return {
        "open": await _swap_with_meta(open_swap, uid) if open_swap else None,
        "latest": await _swap_with_meta(latest, uid) if latest else None,
    }


async def _proposal_books(proposals: List[dict]) -> None:
    """Attach the two books of each proposal as `offered` / `requested` (what the chat renders), with
    one query for all of them. A book that no longer exists at all becomes None ("Book unavailable");
    a soft-deleted one keeps its title, since the proposal really was about that book."""
    ids = {p.get(k) for p in proposals for k in ("offered_book_id", "requested_book_id") if p.get(k)}
    books = {b["id"]: clean_book(b) for b in await db.books.find({"id": {"$in": list(ids)}}, {"_id": 0}).to_list(None)} if ids else {}
    for p in proposals:
        p["offered"] = books.get(p.get("offered_book_id"))
        p["requested"] = books.get(p.get("requested_book_id"))


@api.get("/swaps/{swap_id}")
async def get_swap(swap_id: str, after: Optional[str] = None, user: dict = Depends(get_current_user)):
    """The conversation. With `after=<message id>` (the app's polling), only messages newer than that
    one are returned, plus the swap itself; `full` tells the app whether `messages` is the whole
    conversation or just the new tail."""
    s = await db.swaps.find_one({"id": swap_id}, {"_id": 0})
    if not s or user["user_id"] not in (s["requester_id"], s["receiver_id"]):
        raise HTTPException(status_code=404, detail="Swap not found")
    meta = await _swap_with_meta(s, user["user_id"])
    q: dict = {"swap_id": swap_id}
    full = True
    if after:
        anchor = await db.messages.find_one({"id": after, "swap_id": swap_id}, {"_id": 0, "created_at": 1})
        if anchor:
            # $gte, not $gt: messages written in the same millisecond as the anchor must not be skipped.
            # The anchor itself comes back too; the app merges by message id.
            q["created_at"] = {"$gte": anchor["created_at"]}
            full = False
    msgs = await db.messages.find(q, {"_id": 0}).sort("created_at", 1).to_list(500)
    for m in msgs:
        m["created_at"] = m["created_at"].isoformat() if m.get("created_at") else None
    await _proposal_books([m["proposal"] for m in msgs if m.get("proposal")])
    if meta.get("active_proposal"):
        await _proposal_books([meta["active_proposal"]])
    out = {"swap": meta, "messages": msgs, "full": full}
    # Shelves are only needed to make a proposal (pending swaps), and not on every poll.
    if full:
        other_id = s["receiver_id"] if s["requester_id"] == user["user_id"] else s["requester_id"]
        if s["status"] == "pending":
            out["my_books"] = await _books_for(user["user_id"], only_available=True)
            out["their_books"] = await _books_for(other_id, only_available=True)
        else:
            out["my_books"], out["their_books"] = [], []
    return out


async def _participant_swap(swap_id: str, user: dict) -> dict:
    """Load a swap the caller takes part in, or 404 (never reveal other people's swaps)."""
    s = await db.swaps.find_one({"id": swap_id})
    if not s or user["user_id"] not in (s["requester_id"], s["receiver_id"]):
        raise HTTPException(status_code=404, detail="Swap not found")
    return s


def _require_status(s: dict, *allowed: str) -> None:
    if s["status"] not in allowed:
        raise HTTPException(status_code=409, detail=f"Swap is {s['status']}; action not allowed")


async def _set_proposal_books(proposal: Optional[dict], from_status: str, to_status: str) -> None:
    if not proposal:
        return
    for bid in (proposal.get("offered_book_id"), proposal.get("requested_book_id")):
        if bid:
            await db.books.update_one({"id": bid, "status": from_status}, {"$set": {"status": to_status}})


async def _release_books(book_ids: List[str]) -> None:
    for bid in book_ids:
        await db.books.update_one({"id": bid, "status": "Reserved"}, {"$set": {"status": "Available"}})


@api.post("/swaps/{swap_id}/messages")
async def send_message(swap_id: str, body: MessageBody, user: dict = Depends(get_current_user)):
    s = await _participant_swap(swap_id, user)
    _require_status(s, "pending", "accepted", "active", "completed")
    if not body.text.strip() and not body.image_url:
        raise HTTPException(status_code=400, detail="Empty message")
    if body.client_id:
        prior = await db.messages.find_one(
            {"swap_id": swap_id, "sender_id": user["user_id"], "client_id": body.client_id}, {"_id": 0}
        )
        if prior:  # the same send retried: return the original, never store it twice
            prior["created_at"] = prior["created_at"].isoformat()
            return {"message": prior, "duplicate": True}
    mtype = "image" if body.image_url else "text"
    msg = await _add_message(swap_id, user["user_id"], mtype, body.text, image_url=body.image_url, client_id=body.client_id)
    msg["created_at"] = msg["created_at"].isoformat()
    return {"message": msg}


@api.post("/swaps/{swap_id}/propose")
async def propose(swap_id: str, body: ProposeBody, user: dict = Depends(get_current_user)):
    s = await _participant_swap(swap_id, user)
    _require_status(s, "pending")
    other_id = s["receiver_id"] if s["requester_id"] == user["user_id"] else s["requester_id"]
    offered = await db.books.find_one(
        {"id": body.offered_book_id, "owner_id": user["user_id"], "status": "Available", "deleted_at": None}, {"_id": 0}
    )
    requested = await db.books.find_one(
        {"id": body.requested_book_id, "owner_id": other_id, "status": "Available", "deleted_at": None}, {"_id": 0}
    )
    if not offered:
        raise HTTPException(status_code=400, detail="Offered book must be one of your available books")
    if not requested:
        raise HTTPException(status_code=400, detail="Requested book must be an available book of the other reader")
    proposal = {
        "proposer_id": user["user_id"],
        "offered_book_id": body.offered_book_id,
        "requested_book_id": body.requested_book_id,
    }
    # Status-guarded so a concurrent accept/decline/cancel can't be overwritten.
    res = await db.swaps.update_one({"id": swap_id, "status": "pending"}, {"$set": {"active_proposal": proposal}})
    if res.matched_count == 0:
        raise HTTPException(status_code=409, detail="Swap is no longer pending")
    text = f"Proposed a swap: '{offered['title']}' ⇄ '{requested['title']}'"
    await _add_message(swap_id, user["user_id"], "proposal", text, proposal, key="swapChat.sys.proposed",
                       params={"offered": offered["title"], "requested": requested["title"]})
    return {"ok": True}


@api.post("/swaps/{swap_id}/accept")
async def accept_swap(swap_id: str, user: dict = Depends(get_current_user)):
    s = await _participant_swap(swap_id, user)
    _require_status(s, "pending")
    proposal = s.get("active_proposal")
    if not proposal:
        raise HTTPException(status_code=400, detail="No proposal to accept")
    if proposal.get("proposer_id") == user["user_id"]:
        raise HTTPException(status_code=403, detail="You cannot accept your own proposal")
    # Claim each book with a conditional update (Available -> Reserved). Exactly one of
    # several swaps racing for the same book can win it; the loser rolls back and gets 409.
    claimed: List[str] = []
    for bid in (proposal["offered_book_id"], proposal["requested_book_id"]):
        got = await db.books.update_one(
            {"id": bid, "status": "Available", "deleted_at": None}, {"$set": {"status": "Reserved"}}
        )
        if got.modified_count == 0:
            await _release_books(claimed)
            raise HTTPException(status_code=409, detail="One of the proposed books is no longer available")
        claimed.append(bid)
    res = await db.swaps.update_one({"id": swap_id, "status": "pending"}, {"$set": {"status": "active"}})
    if res.matched_count == 0:
        await _release_books(claimed)
        raise HTTPException(status_code=409, detail="Swap is no longer pending")
    await _add_message(swap_id, user["user_id"], "system", "Swap confirmed! Arrange to meet locally.",
                       key="swapChat.sys.confirmed")
    return {"ok": True}


@api.post("/swaps/{swap_id}/decline")
async def decline_swap(swap_id: str, user: dict = Depends(get_current_user)):
    s = await _participant_swap(swap_id, user)
    _require_status(s, "pending")
    proposal = s.get("active_proposal")
    if proposal and proposal.get("proposer_id") == user["user_id"]:
        raise HTTPException(status_code=403, detail="Use cancel to withdraw your own proposal")
    res = await db.swaps.update_one({"id": swap_id, "status": "pending"}, {"$set": {"status": "declined"}})
    if res.matched_count == 0:
        raise HTTPException(status_code=409, detail="Swap is no longer pending")
    await _add_message(swap_id, user["user_id"], "system", f"{user['name']} declined the swap.",
                       key="swapChat.sys.declined", params={"name": user["name"]})
    return {"ok": True}


@api.post("/swaps/{swap_id}/cancel")
async def cancel_swap(swap_id: str, user: dict = Depends(get_current_user)):
    s = await _participant_swap(swap_id, user)
    _require_status(s, "pending", "accepted", "active")
    res = await db.swaps.update_one(
        {"id": swap_id, "status": {"$in": ["pending", "accepted", "active"]}}, {"$set": {"status": "cancelled"}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=409, detail="Swap can no longer be cancelled")
    # Books are only reserved once the swap is confirmed; a pending swap must not
    # release a book that a different swap may have reserved.
    if s["status"] in ("accepted", "active"):
        await _set_proposal_books(s.get("active_proposal"), "Reserved", "Available")
    await _add_message(swap_id, user["user_id"], "system", f"{user['name']} cancelled the swap.",
                       key="swapChat.sys.cancelled", params={"name": user["name"]})
    return {"ok": True}


async def _recompute_rating(user_id: str):
    ratings = await db.ratings.find({"ratee_id": user_id}).to_list(1000)
    if not ratings:
        return
    avg = sum(r["stars"] for r in ratings) / len(ratings)
    await db.users.update_one({"user_id": user_id}, {"$set": {"rating": round(avg, 1), "rating_count": len(ratings)}})


@api.post("/swaps/{swap_id}/complete")
async def complete_swap(swap_id: str, user: dict = Depends(get_current_user)):
    s = await _participant_swap(swap_id, user)
    _require_status(s, "active")
    field = "requester_completed" if s["requester_id"] == user["user_id"] else "receiver_completed"
    await db.swaps.update_one({"id": swap_id, "status": "active"}, {"$set": {field: True}})
    new_badges = []
    # Atomic transition: only the request that flips active -> completed awards
    # swaps_count, so concurrent confirmations can't double-count.
    flipped = await db.swaps.update_one(
        {"id": swap_id, "status": "active", "requester_completed": True, "receiver_completed": True},
        {"$set": {"status": "completed"}},
    )
    if flipped.modified_count == 1:
        for uid in (s["requester_id"], s["receiver_id"]):
            u = await db.users.find_one({"user_id": uid}, {"_id": 0})
            before = earned_badge_ids(u.get("swaps_count", 0)) if u else []
            await db.users.update_one({"user_id": uid}, {"$inc": {"swaps_count": 1}})
            after = earned_badge_ids((u.get("swaps_count", 0) if u else 0) + 1)
            unlocked = [b for b in BADGES if b["id"] in after and b["id"] not in before]
            for b in unlocked:
                await _add_message(swap_id, None, "system", f"🏅 {u.get('name', 'Someone')} unlocked the {b['label']} badge!",
                                   key="swapChat.sys.badgeUnlocked",
                                   params={"name": u.get("name", ""), "badge": b["id"]})
            if uid == user["user_id"]:
                new_badges = unlocked
        await _set_proposal_books(s.get("active_proposal"), "Reserved", "Swapped")
        await _add_message(swap_id, None, "system", "Swap completed! Leave a rating.", key="swapChat.sys.completed")
    else:
        await _add_message(swap_id, user["user_id"], "system", f"{user['name']} marked the swap complete.",
                           key="swapChat.sys.markedComplete", params={"name": user["name"]})
    return {"ok": True, "new_badges": new_badges}


@api.post("/swaps/{swap_id}/rate")
async def rate_swap(swap_id: str, body: RateBody, user: dict = Depends(get_current_user)):
    s = await _participant_swap(swap_id, user)
    if s["status"] != "completed":
        raise HTTPException(status_code=400, detail="Swap not completed yet")
    ratee_id = s["receiver_id"] if s["requester_id"] == user["user_id"] else s["requester_id"]
    field = "requester_rated" if s["requester_id"] == user["user_id"] else "receiver_rated"
    # Claim the "rated" flag atomically first so a double-submit can't create two ratings.
    claimed = await db.swaps.update_one({"id": swap_id, field: {"$ne": True}}, {"$set": {field: True}})
    if claimed.modified_count == 0:
        raise HTTPException(status_code=400, detail="Already rated")
    stars = max(1, min(5, body.stars))
    await db.ratings.insert_one(
        {
            "id": new_id("rate"),
            "swap_id": swap_id,
            "rater_id": user["user_id"],
            "ratee_id": ratee_id,
            "stars": stars,
            "review": body.review or "",
            "created_at": now_utc(),
        }
    )
    await _recompute_rating(ratee_id)
    return {"ok": True}


# ----------------------------------------------------------------------------
# Notifications (derived)
# ----------------------------------------------------------------------------
@api.get("/notifications")
async def notifications(user: dict = Depends(get_current_user)):
    uid = user["user_id"]
    incoming = await db.swaps.count_documents({"receiver_id": uid, "status": "pending"})
    active = await db.swaps.count_documents(
        {"$or": [{"requester_id": uid}, {"receiver_id": uid}], "status": "active"}
    )
    to_rate = await db.swaps.count_documents(
        {"requester_id": uid, "status": "completed", "requester_rated": False}
    ) + await db.swaps.count_documents(
        {"receiver_id": uid, "status": "completed", "receiver_rated": False}
    )
    return {"incoming_requests": incoming, "active_swaps": active, "to_rate": to_rate, "total": incoming + to_rate}


# ----------------------------------------------------------------------------
# Uploads
# ----------------------------------------------------------------------------
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


def _sniff_image(data: bytes) -> Optional[tuple]:
    """Return (extension, content_type) if the bytes look like a supported image."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg", "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"hevc"):
        return "heic", "image/heic"
    return None


@api.post("/upload")
async def upload(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image too large (max 8 MB)")
    kind = _sniff_image(data)
    if not kind:
        raise HTTPException(status_code=400, detail="Unsupported file type; upload a JPEG, PNG, WebP or HEIC image")
    # Type/extension come from the file's actual bytes, not the client-supplied headers.
    ext, ctype = kind
    path = f"{APP_NAME}/uploads/{user['user_id']}/{uuid.uuid4().hex}.{ext}"
    try:
        await run_in_threadpool(put_object, path, data)
    except OSError as e:
        logger.error(f"Upload write failed for {path}: {e!r}")
        raise HTTPException(status_code=500, detail="Could not store the image")
    await db.uploads.insert_one(
        {"path": path, "owner_id": user["user_id"], "content_type": ctype, "created_at": now_utc()}
    )
    return {"path": path, "url": f"/api/files/{path}"}


@api.get("/files/{path:path}")
async def files(
    path: str,
    exp: Optional[int] = None,
    sig: Optional[str] = None,
    authorization: Optional[str] = Header(None),
):
    # Access needs either a valid signed URL (what the app receives in API responses) or a session token.
    if not _valid_file_sig(path, exp, sig):
        if sig is not None:
            raise HTTPException(status_code=403, detail="Invalid or expired file link")
        await get_current_user(authorization)  # raises 401 when missing/invalid
    rec = await db.uploads.find_one({"path": path})
    if not rec:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        content = await run_in_threadpool(get_object, path)
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        raise HTTPException(status_code=404, detail="Not found")
    return Response(
        content=content,
        media_type=rec.get("content_type") or "application/octet-stream",
        # File names are random and never reused, so the bytes behind a URL never change.
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


@api.get("/")
async def root():
    return {"message": "BookLoop API", "genres": GENRE_LIST}


app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    await load_file_secret()
    await db.users.create_index("email", unique=True)
    await db.users.create_index("user_id", unique=True)
    await db.user_sessions.create_index("session_token", unique=True)
    await db.user_sessions.create_index("expires_at", expireAfterSeconds=0)
    # Short-lived Google sign-in records expire on their own (the code also checks their age).
    await db.oauth_states.create_index("state", unique=True)
    await db.oauth_states.create_index("created_at", expireAfterSeconds=600)
    await db.google_logins.create_index("sid", unique=True)
    await db.google_logins.create_index("created_at", expireAfterSeconds=120)
    # Registration OTP: pending registrations self-clean after a day of inactivity (a user who never
    # finishes signing up); email verification tokens are single-use and expire fast either way, but
    # the TTL index also clears any left stranded by a crash between verify-otp and complete.
    await db.pending_registrations.create_index("email", unique=True)
    await db.pending_registrations.create_index("created_at", expireAfterSeconds=86400)
    await db.email_verifications.create_index("token", unique=True)
    await db.email_verifications.create_index("created_at", expireAfterSeconds=EMAIL_VERIFICATION_TTL_MINUTES * 60)
    await db.books.create_index("owner_id")
    await db.swaps.create_index("requester_id")
    await db.swaps.create_index("receiver_id")
    # Chat loads and polls read one swap's messages in time order.
    await db.messages.create_index([("swap_id", 1), ("created_at", 1)])
    await run_in_threadpool(init_storage)
    logger.info(f"File storage: {STORAGE_DIR}")
    try:
        await sync_demo_community()
        await backfill_onboarding_state()
    except Exception as e:
        logger.warning(f"Seed failed: {e}")


@app.on_event("shutdown")
async def shutdown():
    client.close()


# ----------------------------------------------------------------------------
# Demo community (seeded Messina readers)
# ----------------------------------------------------------------------------
# Demo accounts are identified by ALL of: seed=True, an "@demo.bookloop" email and no password (nobody
# can sign in as them). Real sign-ups never get any of these. Everything the demo community owns —
# books, completed swaps and reviews — also carries seed=True, and demo swaps/reviews only ever
# involve two demo users, so real users never receive fake reviews.
DEMO_EMAIL_DOMAIN = "@demo.bookloop"


def cover(isbn: str) -> str:
    return f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg"


def _demo_email(name: str) -> str:
    first, second = name.split()[:2]
    return f"{first.lower()}.{second.lower()}{DEMO_EMAIL_DOMAIN}"


def _demo_jitter(name: str) -> tuple:
    """Stable small offset (±0.005°) so demo readers in one neighborhood don't share a point."""
    h = hashlib.sha1(name.encode()).digest()
    return ((h[0] % 20 - 10) / 2000.0, (h[1] % 20 - 10) / 2000.0)


_FEM = "https://images.unsplash.com/photo-1514355315815-2b64b0216b14?crop=entropy&cs=srgb&fm=jpg&w=400&q=80"
_MALE = "https://images.unsplash.com/photo-1525457136159-8878648a7ad0?crop=entropy&cs=srgb&fm=jpg&w=400&q=80"
_FEM2 = "https://images.unsplash.com/photo-1544005313-94ddf0286df2?crop=entropy&cs=srgb&fm=jpg&w=400&q=80"
_MALE2 = "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?crop=entropy&cs=srgb&fm=jpg&w=400&q=80"
_FEM3 = "https://images.unsplash.com/photo-1534528741775-53994a69daeb?crop=entropy&cs=srgb&fm=jpg&w=400&q=80"

# Exactly 10 demo readers. "swaps" is the number of completed swaps each one has; the swaps
# themselves are generated between demo readers (see _demo_swap_pairs), and reviews only come from
# those swaps, so a demo reader's review count can never exceed their completed swaps.
DEMO_USERS = [
    {
        "name": "Maria Rossi", "avatar": _FEM, "nb": "Centro", "swaps": 12,
        "genres": ["Fiction", "Psychology", "History"], "langs": ["Italian", "English"],
        "interests": ["Thrillers", "Classics", "Book club picks"],
        "bio": "Literature student. Love a good psychological thriller and Sunday reading in the park.",
        "books": [
            ("Atomic Habits", "James Clear", "9780735211292", "Self-development", "English", "Good"),
            ("Sapiens", "Yuval Noah Harari", "9780062316097", "History", "English", "Like New"),
            ("Norwegian Wood", "Haruki Murakami", "9780375704024", "Fiction", "English", "Good"),
            ("The Silent Patient", "Alex Michaelides", "9781250301697", "Fiction", "English", "Good"),
        ],
    },
    {
        "name": "Luca Bianchi", "avatar": _MALE, "nb": "University Area", "swaps": 8,
        "genres": ["Business", "Science", "Self-development"], "langs": ["Italian", "English"],
        "interests": ["University texts", "Non-fiction deep dives", "Bestsellers"],
        "bio": "Engineering @ UniMe. Trading business & science reads for good fiction.",
        "books": [
            ("Thinking, Fast and Slow", "Daniel Kahneman", "9780374533557", "Psychology", "English", "Good"),
            ("Dune", "Frank Herbert", "9780441013593", "Fantasy", "English", "Acceptable"),
            ("Zero to One", "Peter Thiel", "9780804139298", "Business", "English", "Like New"),
        ],
    },
    {
        "name": "Laura Conti", "avatar": _FEM2, "nb": "Annunziata", "swaps": 14,
        "genres": ["Fantasy", "Romance", "Fiction"], "langs": ["Italian", "English", "Spanish"],
        "interests": ["Bestsellers", "Book club picks", "Short reads"],
        "bio": "Fantasy addict & Erasmus mentor. Always up for a book chat over coffee.",
        "books": [
            ("Harry Potter and the Sorcerer's Stone", "J.K. Rowling", "9780590353427", "Fantasy", "English", "Good"),
            ("The Alchemist", "Paulo Coelho", "9780061122415", "Fiction", "English", "Good"),
            ("1984", "George Orwell", "9780451524935", "Fiction", "English", "Like New"),
            ("The Midnight Library", "Matt Haig", "9780525559474", "Fiction", "English", "Good"),
        ],
    },
    {
        "name": "Ahmed Hassan", "avatar": _MALE2, "nb": "Giostra", "swaps": 6,
        "genres": ["History", "Philosophy", "Biography"], "langs": ["English", "Arabic"],
        "interests": ["Classics", "Non-fiction deep dives", "Poetry"],
        "bio": "History buff & PhD candidate. Big on biographies and philosophy.",
        "books": [
            ("Man's Search for Meaning", "Viktor Frankl", "9780807014271", "Philosophy", "English", "Good"),
            ("Educated", "Tara Westover", "9780399590504", "Biography", "English", "Like New"),
            ("Meditations", "Marcus Aurelius", "9780140449334", "Philosophy", "English", "Acceptable"),
        ],
    },
    {
        "name": "Giulia Marino", "avatar": _FEM3, "nb": "Centro", "swaps": 5,
        "genres": ["Romance", "Fiction", "Psychology"], "langs": ["Italian"],
        "interests": ["Italian authors", "Bestsellers", "Short reads"],
        "bio": "Romance & contemporary fiction lover. Messina born and raised.",
        "books": [
            ("It Ends with Us", "Colleen Hoover", "9781501110368", "Romance", "English", "Good"),
            ("Where the Crawdads Sing", "Delia Owens", "9780735219090", "Fiction", "English", "Good"),
        ],
    },
    {
        "name": "Marco De Luca", "avatar": _MALE, "nb": "University Area", "swaps": 9,
        "genres": ["Science", "Business", "History"], "langs": ["Italian", "English"],
        "interests": ["University texts", "Non-fiction deep dives"],
        "bio": "Physics student. Trading science and business books near campus.",
        "books": [
            ("A Brief History of Time", "Stephen Hawking", "9780553380163", "Science", "English", "Good"),
            ("The Lean Startup", "Eric Ries", "9780307887894", "Business", "English", "Like New"),
        ],
    },
    {
        "name": "Sofia Greco", "avatar": _FEM, "nb": "Annunziata", "swaps": 7,
        "genres": ["Fiction", "Fantasy", "Self-development"], "langs": ["Italian", "English", "French"],
        "interests": ["Italian authors", "Classics", "Graphic novels"],
        "bio": "Erasmus from France. Building my Italian one novel at a time.",
        "books": [
            ("The Name of the Wind", "Patrick Rothfuss", "9780756404741", "Fantasy", "English", "Good"),
            ("Deep Work", "Cal Newport", "9781455586691", "Self-development", "English", "Good"),
        ],
    },
    {
        "name": "Antonio Ferrara", "avatar": _MALE2, "nb": "Provinciale", "swaps": 4,
        "genres": ["History", "Biography"], "langs": ["Italian"],
        "interests": ["Classics", "Italian authors", "Poetry"],
        "bio": "Retired teacher with a big home library to share.",
        "books": [
            ("Steve Jobs", "Walter Isaacson", "9781451648539", "Biography", "English", "Good"),
            ("Guns, Germs, and Steel", "Jared Diamond", "9780393317558", "History", "English", "Acceptable"),
        ],
    },
    {
        "name": "Elena Russo", "avatar": _FEM2, "nb": "Tremestieri", "swaps": 3,
        "genres": ["Fiction", "Philosophy", "Romance"], "langs": ["Italian", "English"],
        "interests": ["Short reads", "Poetry", "Book club picks"],
        "bio": "Nurse on night shifts — short novels and poetry keep me company.",
        "books": [
            ("The Little Prince", "Antoine de Saint-Exupéry", "9780156012195", "Fiction", "English", "Good"),
            ("Pride and Prejudice", "Jane Austen", "9780141439518", "Romance", "English", "Acceptable"),
        ],
    },
    {
        "name": "Davide Romano", "avatar": _MALE, "nb": "Centro", "swaps": 2,
        "genres": ["Science", "Fantasy", "Self-development"], "langs": ["Italian", "English"],
        "interests": ["Bestsellers", "Graphic novels"],
        "bio": "New to Messina and to book swapping. Sci-fi first, everything else second.",
        "books": [
            ("The Martian", "Andy Weir", "9780553418026", "Science", "English", "Like New"),
            ("Ready Player One", "Ernest Cline", "9780307887443", "Fantasy", "English", "Good"),
        ],
    },
]

DEMO_REVIEWS = [
    "Lovely swap — the book was exactly as described.",
    "Friendly and on time. Would swap again!",
    "Great recommendations over coffee, thanks!",
    "Book in even better condition than listed.",
    "Easy to arrange and very kind.",
    "Quick replies and a smooth meetup.",
    "Such a good read, thank you for bringing it.",
    "Punctual, polite and a true book lover.",
    "Second swap with them, just as easy as the first.",
    "Wrapped the book carefully — really appreciated it.",
]


def _demo_swap_pairs() -> List[tuple]:
    """Deterministically pairs demo readers into completed swaps so each ends up with exactly the
    number of swaps in DEMO_USERS: repeatedly give the reader with the most swaps left a partner from
    those with the most left, preferring partners they have swapped with least."""
    remaining = {d["name"]: d["swaps"] for d in DEMO_USERS}
    assert sum(remaining.values()) % 2 == 0, "demo swap counts must sum to an even number"
    order = {d["name"]: i for i, d in enumerate(DEMO_USERS)}
    together: dict = {}
    pairs = []
    while any(remaining.values()):
        a = max(remaining, key=lambda n: (remaining[n], -order[n]))
        others = [n for n in remaining if n != a and remaining[n] > 0]
        b = max(others, key=lambda n: (remaining[n], -together.get(frozenset((a, n)), 0), -order[n]))
        pairs.append((a, b))
        together[frozenset((a, b))] = together.get(frozenset((a, b)), 0) + 1
        remaining[a] -= 1
        remaining[b] -= 1
    return pairs


async def sync_demo_community():
    """Idempotently brings the demo community to exactly DEMO_USERS: creates missing demo readers,
    removes demo readers beyond those 10, and (re)builds their completed swaps and reviews so every
    count shown in the app is backed by real records. Only documents with seed=True are touched."""
    wanted = {_demo_email(d["name"]): d for d in DEMO_USERS}
    demo_filter = {"seed": True, "email": {"$regex": re.escape(DEMO_EMAIL_DOMAIN) + "$"}, "password_hash": None}

    # 1. Demo readers beyond the 10: remove them and the demo data they own.
    extras = await db.users.find({**demo_filter, "email": {"$regex": demo_filter["email"]["$regex"], "$nin": list(wanted)}}, {"_id": 0}).to_list(10000)
    for extra in extras:
        uid = extra["user_id"]
        await db.books.delete_many({"owner_id": uid, "seed": True})
        await db.ratings.delete_many({"seed": True, "$or": [{"rater_id": uid}, {"ratee_id": uid}]})
        await db.swaps.delete_many({"seed": True, "$or": [{"requester_id": uid}, {"receiver_id": uid}]})
        await db.users.delete_one({"user_id": uid, "seed": True, "password_hash": None})
        logger.info(f"Removed extra demo reader {extra.get('name')} <{extra.get('email')}>")

    # 2. The 10 demo readers and their shelves.
    ids: dict = {}
    for email, d in wanted.items():
        existing = await db.users.find_one({"email": email}, {"_id": 0})
        if existing and not existing.get("seed"):
            logger.warning(f"Skipping demo reader {email}: a non-demo account uses that email")
            continue
        profile = {
            "name": d["name"], "avatar_url": d["avatar"], "bio": d["bio"], "city": "Messina",
            "neighborhood": d["nb"], "genres": d["genres"], "languages": d["langs"],
            "reading_interests": d["interests"], "is_exchanging": True,
        }
        if existing:
            uid = existing["user_id"]
            await db.users.update_one({"user_id": uid, "seed": True}, {"$set": profile})
        else:
            uid = new_id("user")
            lat, lng = NEIGHBORHOODS[d["nb"]]
            dlat, dlng = _demo_jitter(d["name"])
            await db.users.insert_one({
                "user_id": uid, "email": email, "password_hash": None, **profile,
                "lat": lat + dlat, "lng": lng + dlng, "rating": 0.0, "rating_count": 0, "swaps_count": 0,
                "onboarding_completed": True, "created_at": now_utc(), "deleted_at": None, "seed": True,
            })
        ids[d["name"]] = uid
        for (title, author, isbn, genre, lang, cond) in d["books"]:
            if not await db.books.find_one({"owner_id": uid, "title": title, "seed": True}):
                await db.books.insert_one({
                    "id": new_id("book"), "owner_id": uid, "title": title, "author": author,
                    "cover_url": cover(isbn), "condition": cond, "language": lang, "genre": genre,
                    "status": "Available", "created_at": now_utc(), "deleted_at": None, "seed": True,
                })

    # 3. Completed swaps between demo readers, and the reviews those swaps produced.
    now = now_utc()
    swap_ids, rating_ids = [], []
    for i, (a, b) in enumerate(_demo_swap_pairs()):
        if a not in ids or b not in ids:
            continue
        swap_id = f"swap_demo_{i:03d}"
        requester_rates = i % 4 != 3
        receiver_rates = i % 3 != 2
        done_at = now - timedelta(days=4 + i * 5)
        swap_ids.append(swap_id)
        await db.swaps.update_one(
            {"id": swap_id},
            {
                "$set": {
                    "requester_id": ids[a], "receiver_id": ids[b], "status": "completed",
                    "requester_completed": True, "receiver_completed": True,
                    "requester_rated": requester_rates, "receiver_rated": receiver_rates,
                    "active_proposal": None, "last_message": "", "seed": True,
                },
                "$setOnInsert": {"created_at": done_at - timedelta(days=2), "updated_at": done_at},
            },
            upsert=True,
        )
        for direction, (rater, ratee, rates) in enumerate(((a, b, requester_rates), (b, a, receiver_rates))):
            if not rates:
                continue
            rating_id = f"rate_demo_{i:03d}_{direction}"
            rating_ids.append(rating_id)
            await db.ratings.update_one(
                {"id": rating_id},
                {
                    "$set": {
                        "swap_id": swap_id, "rater_id": ids[rater], "ratee_id": ids[ratee],
                        "stars": 4 if (i * 7 + direction) % 5 == 0 else 5,
                        "review": DEMO_REVIEWS[(i * 3 + direction) % len(DEMO_REVIEWS)], "seed": True,
                    },
                    "$setOnInsert": {"created_at": done_at + timedelta(hours=6 + direction * 5)},
                },
                upsert=True,
            )
    await db.swaps.delete_many({"seed": True, "id": {"$regex": "^swap_demo_", "$nin": swap_ids}})
    await db.ratings.delete_many({"seed": True, "id": {"$regex": "^rate_demo_", "$nin": rating_ids}})

    # 4. Counts come from the records above, never from hardcoded numbers.
    for name, uid in ids.items():
        swaps = await db.swaps.count_documents(
            {"status": "completed", "$or": [{"requester_id": uid}, {"receiver_id": uid}]}
        )
        received = await db.ratings.find({"ratee_id": uid}, {"_id": 0, "stars": 1}).to_list(1000)
        avg = round(sum(r["stars"] for r in received) / len(received), 1) if received else 0.0
        if len(received) > swaps:
            logger.warning(f"Demo reader {name} has {len(received)} reviews but {swaps} completed swaps")
        await db.users.update_one(
            {"user_id": uid, "seed": True},
            {"$set": {"swaps_count": swaps, "rating_count": len(received), "rating": avg}},
        )
    logger.info(f"Demo community in sync: {len(ids)} readers, {len(swap_ids)} swaps, {len(rating_ids)} reviews")


async def backfill_onboarding_state():
    """Every account that existed before the welcome-screen feature shipped is marked already
    onboarded, so no existing user is ever suddenly shown the new-user welcome screen."""
    await db.users.update_many(
        {"onboarding_completed": {"$exists": False}}, {"$set": {"onboarding_completed": True}}
    )
