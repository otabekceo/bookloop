import os
import re
import json
import asyncio
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
# Sign in with Apple: the app's bundle id(s) — the `aud` of Apple identity tokens. Unset = Apple
# sign-in is off (the app hides the button). APPLE_KEYS_URL can point tests at a fake Apple.
APPLE_AUDIENCES = tuple(a.strip() for a in (os.environ.get("APPLE_BUNDLE_IDS") or "").split(",") if a.strip())
APPLE_KEYS_URL = os.environ.get("APPLE_KEYS_URL") or "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"
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
    # Push notifications opt-in (Profile -> Notifications). Missing on older accounts = off.
    notifications_enabled: Optional[bool] = None


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
    # Optional note shown on the proposal. NOT a chat message and never unlocks chat.
    note: Optional[str] = Field(default=None, max_length=2000)
    # Older app builds send a first chat line as `message`; it is stored as the note instead.
    message: Optional[str] = Field(default=None, max_length=2000)
    # Both or neither: the books of the initial proposal.
    offered_book_id: Optional[str] = None
    requested_book_id: Optional[str] = None


class ReadBody(BaseModel):
    # The newest message the app has shown; everything up to it counts as read. Omitted = up to now.
    message_id: Optional[str] = None


class PushTokenBody(BaseModel):
    token: str = Field(max_length=200)
    platform: Optional[str] = Field(default=None, max_length=20)


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
    review: Optional[str] = Field(default=None, max_length=1000)
    # The reviewer's choice. Off unless they deliberately turn it on; older app builds never send it.
    is_anonymous: bool = False


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
                "notifications_enabled": bool(u.get("notifications_enabled", False)),
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
    return {"google": _google_configured(), "apple": _apple_configured()}


@api.get("/auth/google/start")
async def google_start(request: Request, redirect: str):
    if not _google_configured():
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")
    if not _allowed_app_redirect(redirect):
        raise HTTPException(status_code=400, detail="Redirect URL not allowed")
    return RedirectResponse(await _google_auth_url(request, redirect), status_code=302)


async def _google_auth_url(request: Request, redirect: str, link_user_id: Optional[str] = None) -> str:
    state = secrets.token_urlsafe(32)
    await db.oauth_states.insert_one({"state": state, "redirect": redirect, "link_user_id": link_user_id,
                                      "created_at": now_utc()})
    callback = (PUBLIC_BACKEND_URL or str(request.base_url).rstrip("/")) + "/api/auth/google/callback"
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": callback,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


class LinkStartBody(BaseModel):
    redirect: str = Field(max_length=500)


@api.post("/auth/google/link")
async def google_link_start(body: LinkStartBody, request: Request, user: dict = Depends(get_current_user)):
    """Settings -> Account -> Link Google. Returns the Google URL for the app to open; the signed-in
    account is bound to the single-use OAuth state server-side, so the browser needs no credentials."""
    if not _google_configured():
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")
    if not _allowed_app_redirect(body.redirect):
        raise HTTPException(status_code=400, detail="Redirect URL not allowed")
    return {"url": await _google_auth_url(request, body.redirect, link_user_id=user["user_id"])}


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
    if rec.get("link_user_id"):
        # Linking Google to the account that started this from Settings — never a sign-in.
        if not claims or not claims.get("sub"):
            return _app_redirect(redirect, link_error="failed")
        err = await _link_identity(rec["link_user_id"], "google", claims["sub"], claims["email"].lower(), True)
        return _app_redirect(redirect, **({"link_error": err} if err else {"linked": "google"}))
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
    """The account for a verified Google identity: the one it is linked to; else the account with the
    same Google-verified email (then the identity is linked to it); else a new account."""
    email = claims["email"].lower()
    name = claims.get("name") or email.split("@")[0]
    picture = claims.get("picture")
    sub = claims.get("sub")
    if sub:
        linked = await _identity_user("google", sub)
        if linked is not None:
            return linked or None  # {} = linked to a deleted account
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
    if sub:
        await _link_identity(uid, "google", sub, email, True)
    return user


# --- Provider identities + account linking -----------------------------------------------------------
# auth_identities: one document per (provider, subject) — the provider's stable user id, never an email —
# pointing at one BookLoop user, at most one per provider per user. Emails are stored for display only.
# Sign-in looks the identity up first. Only when it is unknown does a provider-VERIFIED email (verified by
# Google/Apple server-side, never taken from the app) find an existing account, exactly as Google sign-in
# already did before identities were stored.
AUTH_PROVIDERS = ("google", "apple")


async def _identity_user(provider: str, subject: str) -> Optional[dict]:
    """None = identity unknown; {} = linked to an account that no longer exists; else the user."""
    ident = await db.auth_identities.find_one({"provider": provider, "subject": subject}, {"_id": 0})
    if not ident:
        return None
    user = await db.users.find_one({"user_id": ident["user_id"], "deleted_at": None}, {"_id": 0})
    if user:
        await db.auth_identities.update_one({"provider": provider, "subject": subject},
                                            {"$set": {"last_login_at": now_utc()}})
    return user or {}


async def _link_identity(user_id: str, provider: str, subject: str, email: Optional[str], email_verified: bool,
                         private_email: bool = False) -> Optional[str]:
    """Links the identity to the user. Returns an error code, or None when linked (or already was)."""
    existing = await db.auth_identities.find_one({"provider": provider, "subject": subject}, {"_id": 0})
    if existing:
        return None if existing["user_id"] == user_id else "identity_in_use"
    if await db.auth_identities.find_one({"user_id": user_id, "provider": provider}, {"_id": 1}):
        return "provider_already_linked"  # a different Google/Apple account is already linked
    try:
        await db.auth_identities.insert_one({
            "provider": provider, "subject": subject, "user_id": user_id,
            "email": email, "email_verified": bool(email_verified), "private_email": private_email,
            "created_at": now_utc(), "last_login_at": now_utc(),
        })
    except DuplicateKeyError:  # a concurrent link won the race
        again = await db.auth_identities.find_one({"provider": provider, "subject": subject}, {"_id": 0})
        return None if again and again["user_id"] == user_id else "identity_in_use"
    return None


def _apple_configured() -> bool:
    if not APPLE_AUDIENCES:
        return False
    try:
        import jwt  # noqa: F401  (PyJWT[crypto]; Apple sign-in stays off without it)
        return True
    except ImportError:
        return False


_apple_keys: dict = {"at": 0.0, "keys": {}}


async def _apple_public_key(kid: str):
    """Apple's signing key by id, from its JWKS: cached for an hour, refetched early (at most once a
    minute) when Apple starts signing with a key we haven't seen."""
    import jwt
    age = time.time() - _apple_keys["at"]
    if age > 3600 or (kid not in _apple_keys["keys"] and age > 60):
        async with httpx.AsyncClient(timeout=10) as hc:
            resp = await hc.get(APPLE_KEYS_URL)
        resp.raise_for_status()
        _apple_keys["keys"] = {k["kid"]: jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(k)) for k in resp.json()["keys"]}
        _apple_keys["at"] = time.time()
    return _apple_keys["keys"].get(kid)


async def _apple_claims(identity_token: str, nonce_id: str) -> Optional[dict]:
    """Verifies an Apple identity token: Apple's signature, issuer, our bundle id as audience, expiry,
    and the single-use nonce this server issued (the token carries its SHA-256)."""
    import jwt
    rec = await db.apple_nonces.find_one_and_delete({"id": nonce_id}) if nonce_id else None
    if not rec or (now_utc() - _aware(rec["created_at"])) > timedelta(minutes=10):
        return None
    try:
        kid = jwt.get_unverified_header(identity_token).get("kid")
        key = await _apple_public_key(kid) if kid else None
        if key is None:
            return None
        claims = jwt.decode(identity_token, key, algorithms=["RS256"], audience=list(APPLE_AUDIENCES),
                            issuer=APPLE_ISSUER, options={"require": ["exp", "iat", "sub", "aud", "iss"]})
    except Exception as e:
        logger.warning(f"Apple identity token rejected: {e!r}")
        return None
    expected = hashlib.sha256(rec["nonce"].encode()).hexdigest()
    if not hmac.compare_digest(str(claims.get("nonce") or ""), expected):
        return None
    return claims


class AppleAuthBody(BaseModel):
    identity_token: str = Field(max_length=5000)
    nonce_id: str = Field(max_length=100)
    # Apple gives the name to the app only on the very first authorization, never inside the token.
    # It is only a display-name suggestion for a NEW account — never used to identify anyone.
    given_name: Optional[str] = Field(default=None, max_length=60)
    family_name: Optional[str] = Field(default=None, max_length=60)


def _apple_email(claims: dict):
    """(email, verified, private relay) from the verified token. A private-relay address is a real,
    verified, forwarding address — but it can never match an existing BookLoop email."""
    email = (claims.get("email") or "").strip().lower() or None
    verified = claims.get("email_verified") in (True, "true")
    private = claims.get("is_private_email") in (True, "true") or bool(email and email.endswith("@privaterelay.appleid.com"))
    return email, verified, private


@api.post("/auth/apple/nonce")
async def apple_nonce():
    """A single-use nonce: the app passes its SHA-256 to Apple, and Apple signs it into the token."""
    if not _apple_configured():
        raise HTTPException(status_code=503, detail="Sign in with Apple is not configured")
    rec = {"id": secrets.token_urlsafe(16), "nonce": secrets.token_urlsafe(32), "created_at": now_utc()}
    await db.apple_nonces.insert_one(dict(rec))
    return {"nonce_id": rec["id"], "nonce": rec["nonce"]}


@api.post("/auth/apple")
async def apple_sign_in(body: AppleAuthBody):
    if not _apple_configured():
        raise HTTPException(status_code=503, detail="Sign in with Apple is not configured")
    claims = await _apple_claims(body.identity_token, body.nonce_id)
    if not claims:
        raise _coded(401, "apple_failed", "Sign in with Apple could not be verified")
    sub = claims["sub"]
    user = await _identity_user("apple", sub)
    if user == {}:
        raise _coded(401, "apple_failed", "This account no longer exists")
    email, verified, private = _apple_email(claims)
    if user is None and email and verified and not private:
        # Same rule as Google: an Apple-verified email finds the existing account with that email.
        found = await db.users.find_one({"email": email}, {"_id": 0})
        if found and found.get("deleted_at"):
            raise _coded(401, "apple_failed", "This account no longer exists")
        user = found
    if user is None:
        if not email or not verified:
            # Apple omits the email only when this Apple ID authorized BookLoop before; without it no
            # account can be created (it would have no contact address).
            raise _coded(400, "apple_email_missing", "Apple did not share an email address")
        name = " ".join(p.strip() for p in (body.given_name, body.family_name) if p and p.strip())[:60]
        user = _build_user_doc(email, name or "Reader", None, None, onboarding_completed=False)
        try:
            await db.users.insert_one(user)
        except DuplicateKeyError:
            raise _coded(409, "apple_failed", "Could not create the account — please try again")
        user.pop("_id", None)
    err = await _link_identity(user["user_id"], "apple", sub, email, verified, private)
    if err:
        raise _coded(409, err, "This Apple ID can't be used with this account")
    token = await create_session(user["user_id"])
    return {"session_token": token, "user": public_user(user, private=True)}


@api.post("/auth/apple/link")
async def apple_link(body: AppleAuthBody, user: dict = Depends(get_current_user)):
    """Settings -> Account -> Link Apple, for the signed-in account (no email matching involved)."""
    if not _apple_configured():
        raise HTTPException(status_code=503, detail="Sign in with Apple is not configured")
    claims = await _apple_claims(body.identity_token, body.nonce_id)
    if not claims:
        raise _coded(401, "apple_failed", "Sign in with Apple could not be verified")
    email, verified, private = _apple_email(claims)
    err = await _link_identity(user["user_id"], "apple", claims["sub"], email, verified, private)
    if err:
        raise _coded(409, err, "This Apple ID can't be linked to this account")
    return await account_identities(user)


@api.get("/auth/identities")
async def account_identities(user: dict = Depends(get_current_user)):
    """Settings -> Account: the ways this account can sign in."""
    idents = {i["provider"]: i for i in await db.auth_identities.find({"user_id": user["user_id"]}, {"_id": 0}).to_list(10)}
    return {
        "email": user.get("email"),
        "password": bool(user.get("password_hash")),
        "providers": {
            p: {"linked": p in idents, "email": idents[p].get("email") if p in idents else None,
                "private_email": bool(idents[p].get("private_email")) if p in idents else False}
            for p in AUTH_PROVIDERS
        },
        "available": {"google": _google_configured(), "apple": _apple_configured()},
    }


@api.delete("/auth/identities/{provider}")
async def unlink_identity(provider: str, user: dict = Depends(get_current_user)):
    """Unlinks a provider from the signed-in account — never its last way to sign in."""
    if provider not in AUTH_PROVIDERS:
        raise HTTPException(status_code=404, detail="Not found")
    ident = await db.auth_identities.find_one({"user_id": user["user_id"], "provider": provider}, {"_id": 1})
    if not ident:
        raise HTTPException(status_code=404, detail="Not linked")
    others = await db.auth_identities.count_documents({"user_id": user["user_id"], "provider": {"$ne": provider}})
    if not user.get("password_hash") and others == 0:
        raise _coded(409, "last_login_method", "This is your only way to sign in — it can't be removed")
    await db.auth_identities.delete_one({"_id": ident["_id"]})
    return await account_identities(user)


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


ANONYMOUS_REVIEWER_NAME = "BookLoop member"


async def public_reviews(ratings: List[dict]) -> List[dict]:
    """The only shape in which reviews leave the server. Identified reviews carry the reviewer's public
    name/avatar/id; anonymous ones carry nothing about the reviewer at all (no id, name, avatar, swap,
    or exact time — only the day), so no endpoint can reveal who wrote them. Reviews from before
    is_anonymous existed were always shown with the reviewer's name, and still are."""
    ids = list({r["rater_id"] for r in ratings if not r.get("is_anonymous") and r.get("rater_id")})
    raters = {u["user_id"]: u for u in await db.users.find(
        {"user_id": {"$in": ids}}, {"_id": 0, "user_id": 1, "name": 1, "avatar_url": 1, "deleted_at": 1}).to_list(None)}
    out = []
    for r in ratings:
        created = _aware(r.get("created_at"))
        item = {"id": r["id"], "stars": r["stars"], "review": r.get("review") or "",
                "is_anonymous": bool(r.get("is_anonymous"))}
        if item["is_anonymous"]:
            # English fallback for older app builds that print rater_name; new builds localize it.
            item.update({"rater_id": None, "rater_name": ANONYMOUS_REVIEWER_NAME, "rater_avatar": None,
                         "created_at": f"{created.date().isoformat()}T12:00:00+00:00" if created else None})
        else:
            rater = raters.get(r["rater_id"])
            gone = not rater or rater.get("deleted_at")
            item.update({"rater_id": None if gone else r["rater_id"],
                         "rater_name": "Someone" if gone else rater.get("name", ""),
                         "rater_avatar": None if gone else rater.get("avatar_url"),
                         "created_at": created.isoformat() if created else None})
        out.append(item)
    return out


@api.get("/users/{user_id}")
async def get_user(user_id: str, user: dict = Depends(get_current_user)):
    u = await db.users.find_one({"user_id": user_id, "deleted_at": None}, {"_id": 0})
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    ratings = await public_reviews(
        await db.ratings.find({"ratee_id": user_id}, {"_id": 0}).sort("created_at", -1).to_list(50)
    )
    pu = public_user(u, private=(user_id == user["user_id"]))
    pu["distance_km"] = user_distance_km(user, u)
    books = await _books_for(user_id)
    pu.update(match_info(user, u, {b["genre"] for b in books if b["status"] == "Available"}))
    pu["is_favorite"] = bool(await db.favorites.find_one({"user_id": user["user_id"], "target_id": user_id}, {"_id": 1}))
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
    favs = {
        f["target_id"]
        for f in await db.favorites.find({"user_id": user["user_id"]}, {"_id": 0, "target_id": 1}).to_list(None)
    }
    result = []
    for p, dist in candidates:
        pu = public_user(p)
        pu["is_favorite"] = p["user_id"] in favs
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
OPEN_ACTIVE_STATUSES = ("accepted", "active")
CLOSED_STATUSES = ("completed", "declined", "cancelled")
# Statuses in which the recipient has accepted the swap itself (chat is always open in these).
ACCEPTED_STATUSES = ("accepted", "active", "completed")
# Messages people type (as opposed to system/proposal messages, which are events). Only these count
# as unread chat messages and only these are gated by the chat lock.
CHAT_MESSAGE_TYPES = ("text", "image")

# --- Chat access -------------------------------------------------------------------------------
# Swap status and chat access are separate things. The one rule set, used by every endpoint:
#   accepted / active / completed           -> open (completing a swap never closes its chat)
#   pending, recipient allowed chat         -> open ("Accept to continue chatting" or accepting)
#   pending, the two readers met before     -> open
#   pending otherwise                       -> locked: the requester waits, the recipient decides
#   declined / cancelled                    -> open only if the two readers met before, else closed
# "Met before" = at least one swap between them that the recipient accepted (active/completed, or
# accepted and later cancelled). Favorites, notes and proposals never create chat access.
CHAT_OPEN = "open"
CHAT_LOCKED_WAITING = "locked_waiting"  # I sent the request; the recipient has not accepted yet
CHAT_LOCKED_NEEDS_YOU = "locked_needs_you"  # I received it; chat opens when I accept
CHAT_CLOSED = "closed"


def _pair_query(a: str, b: str) -> dict:
    return {"$or": [{"requester_id": a, "receiver_id": b}, {"requester_id": b, "receiver_id": a}]}


def _other_id(s: dict, uid: str) -> str:
    return s["receiver_id"] if s["requester_id"] == uid else s["requester_id"]


def _read_field(s: dict, uid: str) -> str:
    return "requester_read_at" if s["requester_id"] == uid else "receiver_read_at"


async def _pair_has_met(a: str, b: str) -> bool:
    pair = _pair_query(a, b)
    accepted = {"$or": [{"status": {"$in": list(ACCEPTED_STATUSES)}}, {"accepted_at": {"$exists": True, "$ne": None}}]}
    if await db.swaps.find_one({"$and": [pair, accepted]}, {"_id": 1}):
        return True
    # Swaps accepted and later cancelled before v2.2 have no accepted_at; their chat still holds the
    # "Swap confirmed" system message written at acceptance.
    legacy = [s["id"] for s in await db.swaps.find(
        {"$and": [pair, {"status": "cancelled", "accepted_at": {"$exists": False}}]}, {"_id": 0, "id": 1}
    ).to_list(200)]
    if legacy and await db.messages.find_one({"swap_id": {"$in": legacy}, "key": "swapChat.sys.confirmed"}, {"_id": 1}):
        return True
    return False


async def _chat_state(s: dict, uid: str) -> str:
    status = s["status"]
    if status in ACCEPTED_STATUSES:
        return CHAT_OPEN
    if status == "pending":
        if s.get("chat_accepted_at"):
            return CHAT_OPEN
        # Pending swaps from before v2.2: if the recipient already took part in the conversation
        # (the old app let both sides chat), they consented — keep that conversation open.
        if await db.messages.find_one(
            {"swap_id": s["id"], "sender_id": s["receiver_id"], "type": {"$in": [*CHAT_MESSAGE_TYPES, "proposal"]}},
            {"_id": 1},
        ):
            return CHAT_OPEN
        if await _pair_has_met(s["requester_id"], s["receiver_id"]):
            return CHAT_OPEN
        return CHAT_LOCKED_NEEDS_YOU if uid == s["receiver_id"] else CHAT_LOCKED_WAITING
    if await _pair_has_met(s["requester_id"], s["receiver_id"]):
        return CHAT_OPEN
    return CHAT_CLOSED


def _coded(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"message": message, "code": code})


# --- Proposal note -------------------------------------------------------------------------------
NOTE_MAX_CHARS = 280
REVIEW_MAX_CHARS = 1000
_NOTE_CONTROL = re.compile(r"[\u0000-\u0008\u000b-\u001f\u007f​-‏‪-‮⁦-⁩﻿]")


def clean_note_text(raw: Optional[str], limit: int) -> str:
    """User-written free text shown to other users (proposal notes, reviews): control, invisible and
    bidi-override characters removed, at most two consecutive line breaks, trimmed, cut at `limit`."""
    text = _NOTE_CONTROL.sub("", (raw or "").replace("\r\n", "\n").replace("\r", "\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:limit]


def clean_note(raw: Optional[str]) -> Optional[str]:
    """The optional note on a proposal: plain text, trimmed, control/invisible/bidi-override
    characters removed, at most two consecutive line breaks, NOTE_MAX_CHARS characters. Empty -> None."""
    if raw is None:
        return None
    text = clean_note_text(raw, len(raw))
    if not text:
        return None
    if len(text) > NOTE_MAX_CHARS:
        raise _coded(400, "note_too_long", f"The note can be at most {NOTE_MAX_CHARS} characters")
    return text


# --- Anti-spam limits on new swap requests --------------------------------------------------------
# Counted from the swaps collection itself (no extra state). "Unanswered" = still pending without the
# recipient accepting chat, or declined — i.e. requests nobody wanted.
REQUESTS_PER_HOUR = int(os.environ.get("SWAP_REQUESTS_PER_HOUR") or 20)
UNANSWERED_REQUESTS_PER_DAY = int(os.environ.get("SWAP_UNANSWERED_PER_DAY") or 10)
PAIR_REQUESTS_PER_DAY = 3
DECLINE_COOLDOWN_HOURS = 24
# While the chat is locked, the requester may change their proposal only this many times in total.
LOCKED_PROPOSALS_MAX = 3


async def _check_request_limits(requester_id: str, receiver_id: str) -> None:
    now = now_utc()
    day_ago, hour_ago = now - timedelta(days=1), now - timedelta(hours=1)
    mine = {"requester_id": requester_id}
    if await db.swaps.count_documents({**mine, "created_at": {"$gte": hour_ago}}) >= REQUESTS_PER_HOUR:
        raise _coded(429, "rate_limited", "Too many swap requests — please try again later")
    unanswered = await db.swaps.count_documents({
        **mine, "created_at": {"$gte": day_ago},
        "$or": [{"status": "pending", "chat_accepted_at": None}, {"status": "declined"}],
    })
    if unanswered >= UNANSWERED_REQUESTS_PER_DAY:
        raise _coded(429, "rate_limited", "Too many unanswered swap requests — please try again later")
    pair = {"requester_id": requester_id, "receiver_id": receiver_id, "created_at": {"$gte": day_ago}}
    if await db.swaps.find_one({**pair, "status": "declined",
                                "updated_at": {"$gte": now - timedelta(hours=DECLINE_COOLDOWN_HOURS)}}, {"_id": 1}):
        raise _coded(429, "recently_declined", "This reader recently declined your request — try again later")
    if await db.swaps.count_documents(pair) >= PAIR_REQUESTS_PER_DAY:
        raise _coded(429, "rate_limited", "Too many requests to this reader today — please try again later")


# --- Notification events + push ------------------------------------------------------------------
# Events are stored per recipient in `notifications`; new chat messages are NOT stored there (the
# unread state of a conversation is derived from its messages and the reader's read marker), so a
# message is never counted twice.
EVENT_TYPES = ("proposal_received", "proposal_accepted", "proposal_declined", "proposal_cancelled", "chat_accepted")

# Push texts, localized by the recipient's preferred language (English fallback). {name} = the actor.
PUSH_TEXT = {
    "en": {"proposal_received": "New swap proposal from {name}", "proposal_accepted": "{name} accepted your swap",
           "proposal_declined": "{name} declined the swap", "proposal_cancelled": "{name} cancelled the swap",
           "chat_accepted": "{name} accepted your request — you can chat now", "message": "New message from {name}",
           "photo": "📷 Photo"},
    "uz": {"proposal_received": "{name}dan yangi almashuv taklifi", "proposal_accepted": "{name} almashuvingizni qabul qildi",
           "proposal_declined": "{name} almashuvni rad etdi", "proposal_cancelled": "{name} almashuvni bekor qildi",
           "chat_accepted": "{name} so‘rovingizni qabul qildi — endi yozishishingiz mumkin",
           "message": "{name}dan yangi xabar", "photo": "📷 Rasm"},
    "ru": {"proposal_received": "Новое предложение обмена от {name}", "proposal_accepted": "{name} принял(а) ваш обмен",
           "proposal_declined": "{name} отклонил(а) обмен", "proposal_cancelled": "{name} отменил(а) обмен",
           "chat_accepted": "{name} принял(а) ваш запрос — теперь можно переписываться",
           "message": "Новое сообщение от {name}", "photo": "📷 Фото"},
    "it": {"proposal_received": "Nuova proposta di scambio da {name}", "proposal_accepted": "{name} ha accettato il tuo scambio",
           "proposal_declined": "{name} ha rifiutato lo scambio", "proposal_cancelled": "{name} ha annullato lo scambio",
           "chat_accepted": "{name} ha accettato la tua richiesta — ora potete chattare",
           "message": "Nuovo messaggio da {name}", "photo": "📷 Foto"},
    "ar": {"proposal_received": "عرض تبادل جديد من {name}", "proposal_accepted": "قبل {name} التبادل",
           "proposal_declined": "رفض {name} التبادل", "proposal_cancelled": "ألغى {name} التبادل",
           "chat_accepted": "قبل {name} طلبك — يمكنكما الدردشة الآن", "message": "رسالة جديدة من {name}",
           "photo": "📷 صورة"},
}
EXPO_PUSH_URL = os.environ.get("EXPO_PUSH_URL") or "https://exp.host/--/api/v2/push/send"
PUSH_TOKEN_RE = re.compile(r"^(ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_\-]{8,120}\]$")


def _push_text(lang: Optional[str], kind: str, name: str) -> str:
    table = PUSH_TEXT.get(lang or "en") or PUSH_TEXT["en"]
    return (table.get(kind) or PUSH_TEXT["en"][kind]).format(name=name)


async def _deliver_push(messages: List[dict]) -> None:
    """Sends through the Expo push service. Tokens Expo reports as no longer registered are removed.
    With OTP_DEBUG_MODE (test runs) nothing leaves the machine: pushes are recorded in push_debug."""
    if OTP_DEBUG_MODE:
        await db.push_debug.insert_many([{**m, "created_at": now_utc()} for m in messages])
        return
    try:
        async with httpx.AsyncClient(timeout=10) as hc:
            resp = await hc.post(EXPO_PUSH_URL, json=messages, headers={"Accept": "application/json"})
        tickets = resp.json().get("data") if resp.status_code == 200 else None
        if not isinstance(tickets, list):
            logger.warning(f"Expo push rejected: HTTP {resp.status_code} {resp.text[:300]}")
            return
        for m, ticket in zip(messages, tickets):
            if ticket.get("status") == "error":
                err = (ticket.get("details") or {}).get("error")
                logger.warning(f"Expo push error for a token: {err or ticket.get('message')}")
                if err == "DeviceNotRegistered":
                    await db.push_tokens.delete_one({"token": m["to"]})
    except Exception as e:  # push is best-effort; never fail the request that triggered it
        logger.warning(f"Expo push failed: {e!r}")


async def _push(user_id: str, kind: str, actor_name: str, data: dict, body: Optional[str] = None) -> None:
    """Push to the user's registered devices — only if they turned Notifications on."""
    u = await db.users.find_one({"user_id": user_id, "deleted_at": None},
                                {"_id": 0, "notifications_enabled": 1, "preferred_language": 1})
    if not u or not u.get("notifications_enabled"):
        return
    tokens = await db.push_tokens.find({"user_id": user_id}, {"_id": 0, "token": 1}).to_list(20)
    if not tokens:
        return
    title = _push_text(u.get("preferred_language"), kind, actor_name)
    msgs = [{"to": t["token"], "title": title, "body": body or "", "sound": "default", "data": data,
             "channelId": "default", "user_id": user_id, "kind": kind} for t in tokens]
    # Only the Expo fields go over the wire; user_id/kind are kept for the debug record.
    if OTP_DEBUG_MODE:
        await _deliver_push(msgs)
    else:
        asyncio.create_task(_deliver_push([{k: v for k, v in m.items() if k not in ("user_id", "kind")} for m in msgs]))


async def _notify(user_id: str, etype: str, swap: dict, actor: dict) -> None:
    """Record an event for the recipient (deduplicated per swap+type while unread) and push it."""
    now = now_utc()
    existing = await db.notifications.find_one_and_update(
        {"user_id": user_id, "type": etype, "swap_id": swap["id"], "read_at": None},
        {"$set": {"created_at": now, "actor_id": actor["user_id"]}},
    )
    if not existing:
        await db.notifications.insert_one({
            "id": new_id("ntf"), "user_id": user_id, "type": etype, "swap_id": swap["id"],
            "actor_id": actor["user_id"], "created_at": now, "read_at": None,
        })
    await _push(user_id, etype, actor.get("name", ""), {"swap_id": swap["id"], "type": etype, "url": f"/swap/{swap['id']}"})


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
    updates = {"updated_at": now_utc(), "last_message": last,
               "last_message_key": last_key, "last_message_params": last_params}
    if mtype in CHAT_MESSAGE_TYPES and sender_id:
        # Unread tracking: when the conversation last got a real chat message, and the sender has
        # obviously read everything up to their own message.
        updates["last_chat_at"] = msg["created_at"]
        swap = await db.swaps.find_one({"id": swap_id}, {"_id": 0, "requester_id": 1, "receiver_id": 1})
        if swap and sender_id in (swap["requester_id"], swap["receiver_id"]):
            updates[_read_field(swap, sender_id)] = msg["created_at"]
    await db.swaps.update_one({"id": swap_id}, {"$set": updates})
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
        # The requester's optional note — part of the proposal, not a chat message.
        "note": s.get("note"),
        "chat_accepted": bool(s.get("chat_accepted_at")),
    }


async def _swap_with_meta(s: dict, me_id: str) -> dict:
    cs = clean_swap(s)
    other_id = s["receiver_id"] if s["requester_id"] == me_id else s["requester_id"]
    other = await db.users.find_one({"user_id": other_id}, {"_id": 0})
    cs["other_user"] = public_user(other) if other else {}
    cs["is_requester"] = s["requester_id"] == me_id
    # Only my own "rated" flag: whether the other reader reviewed me (and so when) isn't mine to know —
    # otherwise an anonymous review could be matched to the swap it came from.
    cs.pop("receiver_rated" if cs["is_requester"] else "requester_rated", None)
    return cs


@api.post("/swaps")
async def create_swap(body: SwapCreate, user: dict = Depends(get_current_user)):
    """A new swap proposal: optional books + optional note. It never opens the chat — the recipient
    has to accept first (see _chat_state)."""
    uid = user["user_id"]
    if body.receiver_id == uid:
        raise HTTPException(status_code=400, detail="Cannot swap with yourself")
    receiver = await db.users.find_one({"user_id": body.receiver_id, "deleted_at": None})
    if not receiver:
        raise HTTPException(status_code=404, detail="User not found")
    existing = await db.swaps.find_one(
        {"$and": [_pair_query(uid, body.receiver_id), {"status": {"$in": ["pending", *OPEN_ACTIVE_STATUSES]}}]}
    )
    if existing:  # one open swap per pair: hand back the existing one, never a duplicate
        return {"swap": await _swap_with_meta(existing, uid), "existing": True}
    # "Currently exchanging" OFF: nobody can start a new swap with this reader (existing swaps and
    # conversations are untouched).
    if not receiver.get("is_exchanging", True):
        raise _coded(403, "not_exchanging", "This reader is not currently accepting exchanges")
    note = clean_note(body.note if body.note is not None else body.message)
    if bool(body.offered_book_id) != bool(body.requested_book_id):
        raise HTTPException(status_code=400, detail="Choose both books, or neither")
    offered = requested = None
    if body.offered_book_id:
        offered = await db.books.find_one(
            {"id": body.offered_book_id, "owner_id": uid, "status": "Available", "deleted_at": None}, {"_id": 0})
        requested = await db.books.find_one(
            {"id": body.requested_book_id, "owner_id": body.receiver_id, "status": "Available", "deleted_at": None}, {"_id": 0})
        if not offered:
            raise HTTPException(status_code=400, detail="Offered book must be one of your available books")
        if not requested:
            raise HTTPException(status_code=400, detail="Requested book must be an available book of the other reader")
    await _check_request_limits(uid, body.receiver_id)
    now = now_utc()
    swap = {
        "id": new_id("swap"),
        "requester_id": uid,
        "receiver_id": body.receiver_id,
        "status": "pending",
        "requester_completed": False,
        "receiver_completed": False,
        "requester_rated": False,
        "receiver_rated": False,
        "active_proposal": None,
        "note": note,
        "chat_accepted_at": None,
        "last_message": "",
        "created_at": now,
        "updated_at": now,
    }
    await db.swaps.insert_one(dict(swap))
    await _add_message(swap["id"], None, "system", f"{user['name']} is interested in exchanging books.",
                       key="swapChat.sys.interested", params={"name": user["name"]})
    if offered:
        proposal = {"proposer_id": uid, "offered_book_id": offered["id"], "requested_book_id": requested["id"]}
        await db.swaps.update_one({"id": swap["id"]}, {"$set": {"active_proposal": proposal}})
        swap["active_proposal"] = proposal
        await _add_message(swap["id"], uid, "proposal", f"Proposed a swap: '{offered['title']}' ⇄ '{requested['title']}'",
                           proposal, key="swapChat.sys.proposed",
                           params={"offered": offered["title"], "requested": requested["title"]})
    await _notify(body.receiver_id, "proposal_received", swap, user)
    return {"swap": await _swap_with_meta(swap, uid), "existing": False}


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Mongo hands datetimes back naive (they are stored in UTC)."""
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


# Unread tracking started when v2.2 first ran against this database (stored once in app_settings).
# Conversations nobody has opened since then count only messages newer than that, so upgrading never
# floods anyone with years of "unread" history.
_unread_since: datetime = datetime(2026, 1, 1, tzinfo=timezone.utc)


async def load_unread_since() -> None:
    global _unread_since
    try:
        await db.app_settings.insert_one({"_id": "unread_tracking_since", "value": now_utc()})
    except DuplicateKeyError:
        pass
    doc = await db.app_settings.find_one({"_id": "unread_tracking_since"})
    _unread_since = _aware(doc["value"])


async def _unread_counts(swaps: List[dict], uid: str) -> dict:
    """swap id -> number of chat messages from the other reader the user hasn't read. Only
    conversations with chat activity after the user's read marker are queried."""
    out = {}
    for s in swaps:
        read_at = _aware(s.get(_read_field(s, uid))) or _unread_since
        last_chat = _aware(s.get("last_chat_at"))
        if not last_chat or last_chat <= read_at:
            continue
        n = await db.messages.count_documents({
            "swap_id": s["id"], "sender_id": _other_id(s, uid), "type": {"$in": list(CHAT_MESSAGE_TYPES)},
            "created_at": {"$gt": read_at},
        })
        if n:
            out[s["id"]] = n
    return out


@api.get("/swaps")
async def list_swaps(user: dict = Depends(get_current_user)):
    uid = user["user_id"]
    swaps = await db.swaps.find(
        {"$or": [{"requester_id": uid}, {"receiver_id": uid}]}, {"_id": 0}
    ).sort("updated_at", -1).to_list(300)
    other_ids = {s["receiver_id"] if s["requester_id"] == uid else s["requester_id"] for s in swaps}
    others = {u["user_id"]: u for u in await db.users.find({"user_id": {"$in": list(other_ids)}}, {"_id": 0}).to_list(None)}
    unread = await _unread_counts(swaps, uid)
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
        meta.pop("receiver_rated" if meta["is_requester"] else "requester_rated", None)  # see _swap_with_meta
        meta["unread_count"] = unread.get(s["id"], 0)
        status = s["status"]
        if status == "pending":
            buckets["incoming" if s["receiver_id"] == uid else "outgoing"].append(meta)
        elif status in OPEN_ACTIVE_STATUSES:
            buckets["active"].append(meta)
        elif status in CLOSED_STATUSES:
            buckets["completed"].append(meta)
    return buckets


@api.get("/swaps/with/{user_id}")
async def swap_with_user(user_id: str, user: dict = Depends(get_current_user)):
    """The caller's open swap with this reader (pending/accepted/active), if any, and their most
    recent swap of any status, plus what the caller may do next: `can_propose` (the reader is
    currently exchanging) and whether the latest conversation is still open to chat in."""
    uid = user["user_id"]
    pair = _pair_query(uid, user_id)
    open_swap = await db.swaps.find_one({"$and": [pair, {"status": {"$in": ["pending", *OPEN_ACTIVE_STATUSES]}}]}, {"_id": 0})
    latest = await db.swaps.find_one(pair, {"_id": 0}, sort=[("updated_at", -1)])
    other = await db.users.find_one({"user_id": user_id, "deleted_at": None}, {"_id": 0, "is_exchanging": 1})
    return {
        "open": await _swap_with_meta(open_swap, uid) if open_swap else None,
        "latest": await _swap_with_meta(latest, uid) if latest else None,
        "can_propose": bool(other) and user_id != uid and other.get("is_exchanging", True),
        "latest_chat_open": bool(latest) and await _chat_state(latest, uid) == CHAT_OPEN,
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
    state = await _chat_state(s, user["user_id"])
    # `chat` tells the app what to show instead of (or with) the composer; the server enforces it anyway.
    meta["chat"] = {"state": state, "can_send": state == CHAT_OPEN}
    meta["unread_count"] = (await _unread_counts([s], user["user_id"])).get(swap_id, 0)
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
    state = await _chat_state(s, user["user_id"])
    if state == CHAT_CLOSED:
        _require_status(s, *ACCEPTED_STATUSES, "pending")  # -> 409 "Swap is declined; action not allowed"
    if state in (CHAT_LOCKED_WAITING, CHAT_LOCKED_NEEDS_YOU):
        raise _coded(403, "chat_locked", "Chat opens once the other reader accepts the request")
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
    other_id = _other_id(s, user["user_id"])
    other = await db.users.find_one({"user_id": other_id}, {"_id": 0, "preferred_language": 1})
    preview = body.text.strip()[:140] if mtype == "text" else _push_text((other or {}).get("preferred_language"), "photo", "")
    await _push(other_id, "message", user.get("name", ""), {"swap_id": swap_id, "type": "message", "url": f"/swap/{swap_id}"}, preview)
    msg["created_at"] = msg["created_at"].isoformat()
    return {"message": msg}


@api.post("/swaps/{swap_id}/propose")
async def propose(swap_id: str, body: ProposeBody, user: dict = Depends(get_current_user)):
    s = await _participant_swap(swap_id, user)
    _require_status(s, "pending")
    other_id = s["receiver_id"] if s["requester_id"] == user["user_id"] else s["requester_id"]
    # A proposal is structured (two books), never free text — but each one still reaches the other
    # reader, so a requester whose request hasn't been accepted can only revise it a few times.
    if user["user_id"] == s["requester_id"] and await _chat_state(s, user["user_id"]) == CHAT_LOCKED_WAITING:
        sent = await db.messages.count_documents({"swap_id": swap_id, "sender_id": user["user_id"], "type": "proposal"})
        if sent >= LOCKED_PROPOSALS_MAX:
            raise _coded(429, "proposal_limit", "Wait for the other reader to respond before changing the proposal again")
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
    await _notify(other_id, "proposal_received", s, user)
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
    now = now_utc()
    res = await db.swaps.update_one(
        {"id": swap_id, "status": "pending"},
        # accepted_at marks the pair as having "met": their chats stay open even if this is cancelled.
        {"$set": {"status": "active", "accepted_at": now, "chat_accepted_at": s.get("chat_accepted_at") or now}},
    )
    if res.matched_count == 0:
        await _release_books(claimed)
        raise HTTPException(status_code=409, detail="Swap is no longer pending")
    await _add_message(swap_id, user["user_id"], "system", "Swap confirmed! Arrange to meet locally.",
                       key="swapChat.sys.confirmed")
    await _notify(_other_id(s, user["user_id"]), "proposal_accepted", s, user)
    return {"ok": True}


@api.post("/swaps/{swap_id}/allow-chat")
async def allow_chat(swap_id: str, user: dict = Depends(get_current_user)):
    """"Accept to continue chatting": the recipient opens the conversation without accepting the
    books yet. Only the recipient can do this, only while the request is pending."""
    s = await _participant_swap(swap_id, user)
    if user["user_id"] != s["receiver_id"]:
        raise _coded(403, "only_recipient", "Only the reader who received the request can accept it")
    _require_status(s, "pending")
    if s.get("chat_accepted_at"):
        return {"ok": True, "already": True}
    res = await db.swaps.update_one(
        {"id": swap_id, "status": "pending", "chat_accepted_at": None}, {"$set": {"chat_accepted_at": now_utc()}}
    )
    if res.modified_count == 0:
        return {"ok": True, "already": True}
    await _add_message(swap_id, user["user_id"], "system", f"{user['name']} accepted the request. You can chat now.",
                       key="swapChat.sys.chatAccepted", params={"name": user["name"]})
    await _notify(s["requester_id"], "chat_accepted", s, user)
    return {"ok": True, "already": False}


@api.post("/swaps/{swap_id}/read")
async def mark_swap_read(swap_id: str, body: Optional[ReadBody] = None, user: dict = Depends(get_current_user)):
    """The reader has seen this conversation up to `message_id` (or up to now). Also marks the
    swap's notification events read. Never moves the marker backwards."""
    s = await _participant_swap(swap_id, user)
    upto = now_utc()
    if body and body.message_id:
        anchor = await db.messages.find_one({"id": body.message_id, "swap_id": swap_id}, {"_id": 0, "created_at": 1})
        if not anchor:
            raise HTTPException(status_code=404, detail="Message not found")
        upto = _aware(anchor["created_at"])
    field = _read_field(s, user["user_id"])
    await db.swaps.update_one(
        {"id": swap_id, "$or": [{field: None}, {field: {"$lt": upto}}]}, {"$set": {field: upto}}
    )
    await db.notifications.update_many(
        {"user_id": user["user_id"], "swap_id": swap_id, "read_at": None}, {"$set": {"read_at": now_utc()}}
    )
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
    await _notify(_other_id(s, user["user_id"]), "proposal_declined", s, user)
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
    await _notify(_other_id(s, user["user_id"]), "proposal_cancelled", s, user)
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
            # Always stored, anonymous or not: anonymity only hides the reviewer from other users
            # (eligibility, duplicate prevention and moderation still need to know who wrote it).
            "rater_id": user["user_id"],
            "ratee_id": ratee_id,
            "stars": stars,
            "review": clean_note_text(body.review, REVIEW_MAX_CHARS),
            "is_anonymous": bool(body.is_anonymous),
            "created_at": now_utc(),
        }
    )
    await _recompute_rating(ratee_id)
    return {"ok": True}


# ----------------------------------------------------------------------------
# Notifications: stored events (proposal received/accepted/declined/cancelled, chat accepted) plus
# unread chat messages derived per conversation. Swap status and message notifications are separate:
# a completed swap's conversation still produces unread messages.
# ----------------------------------------------------------------------------
async def _my_swaps(uid: str) -> List[dict]:
    return await db.swaps.find({"$or": [{"requester_id": uid}, {"receiver_id": uid}]}, {"_id": 0}).to_list(500)


@api.get("/notifications")
async def notifications(user: dict = Depends(get_current_user)):
    """Counters for badges. `unread_total` drives the bell; the older fields stay for older app builds."""
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
    unread_messages = sum((await _unread_counts(await _my_swaps(uid), uid)).values())
    unread_events = await db.notifications.count_documents({"user_id": uid, "read_at": None})
    return {
        "incoming_requests": incoming, "active_swaps": active, "to_rate": to_rate,
        # Older builds show their bell dot for `total`; new messages are included so they see them too.
        "total": incoming + to_rate + unread_messages,
        "unread_messages": unread_messages, "unread_events": unread_events,
        "unread_total": unread_messages + unread_events,
    }


@api.get("/notifications/feed")
async def notification_feed(user: dict = Depends(get_current_user)):
    """The notification center: one row per conversation with unread messages, then events, newest
    first. Reading the feed does not mark anything read."""
    uid = user["user_id"]
    swaps = await _my_swaps(uid)
    by_id = {s["id"]: s for s in swaps}
    unread = await _unread_counts(swaps, uid)
    events = await db.notifications.find({"user_id": uid}, {"_id": 0}).sort("created_at", -1).to_list(50)
    actor_ids = {e["actor_id"] for e in events} | {_other_id(by_id[sid], uid) for sid in unread}
    actors = {u["user_id"]: u for u in await db.users.find({"user_id": {"$in": list(actor_ids)}}, {"_id": 0}).to_list(None)}

    def actor(aid):
        a = actors.get(aid)
        return {"user_id": aid, "name": a.get("name", ""), "avatar_url": a.get("avatar_url")} if a else {"user_id": aid, "name": "", "avatar_url": None}

    items = []
    for sid, n in unread.items():
        s = by_id[sid]
        items.append({
            "id": f"msg_{sid}", "kind": "message", "type": "message", "swap_id": sid, "swap_status": s["status"],
            "actor": actor(_other_id(s, uid)), "unread_count": n, "read": False,
            "last_message": s.get("last_message", ""), "last_message_key": s.get("last_message_key"),
            "last_message_params": s.get("last_message_params") or {},
            "created_at": _aware(s.get("last_chat_at")).isoformat(),
        })
    for e in events:
        s = by_id.get(e["swap_id"])
        items.append({
            "id": e["id"], "kind": "event", "type": e["type"], "swap_id": e["swap_id"],
            "swap_status": s["status"] if s else None, "actor": actor(e["actor_id"]),
            "read": e.get("read_at") is not None, "created_at": _aware(e["created_at"]).isoformat(),
        })
    items.sort(key=lambda i: i["created_at"], reverse=True)
    unread_events = sum(1 for e in events if e.get("read_at") is None)
    return {"items": items, "unread_total": sum(unread.values()) + unread_events}


@api.post("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str, user: dict = Depends(get_current_user)):
    res = await db.notifications.update_one(
        {"id": notification_id, "user_id": user["user_id"]}, {"$set": {"read_at": now_utc()}}
    )
    if res.matched_count == 0:  # someone else's (or no such) notification: never reveal which
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}


@api.post("/notifications/read-all")
async def mark_all_notifications_read(user: dict = Depends(get_current_user)):
    """Explicit "mark all as read": every event and every conversation, up to now."""
    uid, now = user["user_id"], now_utc()
    await db.notifications.update_many({"user_id": uid, "read_at": None}, {"$set": {"read_at": now}})
    await db.swaps.update_many({"requester_id": uid}, {"$set": {"requester_read_at": now}})
    await db.swaps.update_many({"receiver_id": uid}, {"$set": {"receiver_read_at": now}})
    return {"ok": True}


# ----------------------------------------------------------------------------
# Push tokens (Expo). A token belongs to one device; signing in on that device moves it to that user.
# ----------------------------------------------------------------------------
@api.post("/push/register")
async def register_push_token(body: PushTokenBody, user: dict = Depends(get_current_user)):
    token = body.token.strip()
    if not PUSH_TOKEN_RE.match(token):
        raise HTTPException(status_code=400, detail="Invalid push token")
    await db.push_tokens.update_one(
        {"token": token},
        {"$set": {"user_id": user["user_id"], "platform": (body.platform or "")[:20], "updated_at": now_utc()},
         "$setOnInsert": {"created_at": now_utc()}},
        upsert=True,
    )
    return {"ok": True}


@api.post("/push/unregister")
async def unregister_push_token(body: PushTokenBody, user: dict = Depends(get_current_user)):
    await db.push_tokens.delete_one({"token": body.token.strip(), "user_id": user["user_id"]})
    return {"ok": True}


# ----------------------------------------------------------------------------
# Favorites: private bookmarks of other readers. Stored as (user_id, target_id) pairs only. Adding one
# notifies nobody and grants nothing (no chat access, no swap).
# ----------------------------------------------------------------------------
@api.get("/favorites")
async def list_favorites(user: dict = Depends(get_current_user)):
    favs = await db.favorites.find({"user_id": user["user_id"]}, {"_id": 0}).sort("created_at", -1).to_list(500)
    ids = [f["target_id"] for f in favs]
    people = {u["user_id"]: u for u in await db.users.find({"user_id": {"$in": ids}, "deleted_at": None}, {"_id": 0}).to_list(None)}
    counts: dict = {}
    for b in await db.books.find({"owner_id": {"$in": ids}, "status": "Available", "deleted_at": None},
                                 {"_id": 0, "owner_id": 1}).to_list(None):
        counts[b["owner_id"]] = counts.get(b["owner_id"], 0) + 1
    out = []
    for tid in ids:
        p = people.get(tid)
        if not p:  # deleted account: its favorite simply stops showing
            continue
        pu = public_user(p)
        pu["distance_km"] = user_distance_km(user, p)
        pu["available_count"] = counts.get(tid, 0)
        pu["is_favorite"] = True
        out.append(pu)
    return {"favorites": out}


@api.put("/favorites/{target_id}")
async def add_favorite(target_id: str, user: dict = Depends(get_current_user)):
    if target_id == user["user_id"]:
        raise _coded(400, "cannot_favorite_self", "You can't favorite yourself")
    if not await db.users.find_one({"user_id": target_id, "deleted_at": None}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="User not found")
    # Upsert on the unique (user_id, target_id) pair: repeating the call never creates a duplicate.
    try:
        await db.favorites.update_one(
            {"user_id": user["user_id"], "target_id": target_id},
            {"$setOnInsert": {"created_at": now_utc()}},
            upsert=True,
        )
    except DuplicateKeyError:  # two taps racing: the other one already inserted it
        pass
    return {"ok": True, "is_favorite": True}


@api.delete("/favorites/{target_id}")
async def remove_favorite(target_id: str, user: dict = Depends(get_current_user)):
    await db.favorites.delete_one({"user_id": user["user_id"], "target_id": target_id})
    return {"ok": True, "is_favorite": False}


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
    # v2.2 provider identities (additive; legacy Google accounts get theirs on their next Google sign-in).
    await db.auth_identities.create_index([("provider", 1), ("subject", 1)], unique=True)
    await db.auth_identities.create_index([("user_id", 1), ("provider", 1)], unique=True)
    await db.apple_nonces.create_index("id", unique=True)
    await db.apple_nonces.create_index("created_at", expireAfterSeconds=600)
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
    # v2.2 — all additive: new collections and indexes only; no existing document is rewritten.
    await db.favorites.create_index([("user_id", 1), ("target_id", 1)], unique=True)
    await db.notifications.create_index("id", unique=True)
    await db.notifications.create_index([("user_id", 1), ("created_at", -1)])
    await db.notifications.create_index([("user_id", 1), ("swap_id", 1), ("read_at", 1)])
    await db.push_tokens.create_index("token", unique=True)
    await db.push_tokens.create_index("user_id")
    await db.swaps.create_index([("requester_id", 1), ("created_at", -1)])
    await load_unread_since()
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
