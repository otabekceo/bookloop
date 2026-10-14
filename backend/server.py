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
from typing import Annotated, List, Literal, Optional

import bcrypt
import httpx
from fastapi import FastAPI, APIRouter, Depends, HTTPException, Header, Request, UploadFile, File, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response, JSONResponse, RedirectResponse
from fastapi.concurrency import run_in_threadpool
from pymongo import UpdateOne
from pymongo.errors import DuplicateKeyError
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr

from email_provider import (get_email_provider, otp_email_body, security_email_body, EmailNotConfiguredError,
                            EmailSendError)
import log_redaction
import oauth_redirects
import search_text
from image_meta import UnsupportedImage, strip_metadata

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
# Native Google sign-in (Android Credential Manager): Google signs ID tokens for our web client
# (GOOGLE_CLIENT_ID = `aud`) only for apps registered as one of these Android OAuth clients (`azp`),
# i.e. our package name + signing certificate. Unset = native Google sign-in is off.
GOOGLE_ANDROID_CLIENT_IDS = tuple(
    c.strip() for c in (os.environ.get("GOOGLE_ANDROID_CLIENT_IDS") or "").split(",") if c.strip()
)
GOOGLE_CERTS_URL = os.environ.get("GOOGLE_CERTS_URL") or "https://www.googleapis.com/oauth2/v3/certs"
# Public https address of this API, used to build the redirect URI registered in Google Cloud.
# Falls back to the address the request arrived on (fine locally, wrong behind a proxy).
PUBLIC_BACKEND_URL = (os.environ.get("PUBLIC_BACKEND_URL") or "").strip().rstrip("/")
# Sign in with Apple: the app's bundle id(s) — the `aud` of Apple identity tokens. Unset = Apple
# sign-in is off (the app hides the button). APPLE_KEYS_URL can point tests at a fake Apple.
APPLE_AUDIENCES = tuple(a.strip() for a in (os.environ.get("APPLE_BUNDLE_IDS") or "").split(",") if a.strip())
APPLE_KEYS_URL = os.environ.get("APPLE_KEYS_URL") or "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"
# Where the app may be sent back to after Google: only the app's exact return screens under its own URL
# scheme(s) and configured web origins (see oauth_redirects.py). ALLOW_DEV_REDIRECTS additionally allows
# Expo Go (exp://), development builds and http://localhost — for local development only.
APP_URL_SCHEMES = tuple(
    s.strip().lower() for s in (os.environ.get("APP_URL_SCHEMES") or "bookloop").split(",") if s.strip()
)
APP_REDIRECT_ORIGINS = tuple(
    o.strip().rstrip("/") for o in (os.environ.get("APP_REDIRECT_ORIGINS") or "").split(",") if o.strip()
)
ALLOW_DEV_REDIRECTS = (os.environ.get("ALLOW_DEV_REDIRECTS") or "").strip().lower() in ("1", "true", "yes")
# Kill switch for the browser Google flow returning to the app's URL scheme(s). Any app can claim a
# custom scheme, so once the app signs in natively this is turned off ("0") and the browser flow only
# returns to APP_REDIRECT_ORIGINS (web). On by default while older app builds still use it.
GOOGLE_WEB_FLOW_APP_SCHEMES = (os.environ.get("GOOGLE_WEB_FLOW_APP_SCHEMES") or "1").strip().lower() in ("1", "true", "yes")

# Uploaded images live on local disk under STORAGE_DIR (default: backend/uploads). In production
# point STORAGE_DIR at a persistent volume, and back it up together with the database.
STORAGE_DIR = Path(os.environ.get("STORAGE_DIR") or (ROOT_DIR / "uploads")).resolve()
APP_NAME = "bookloop"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("bookloop")
# Hosting logs are read by more people and kept longer than the database: credentials, signed file links,
# OAuth codes and push tokens are masked in every log line (see log_redaction.py), uvicorn's access log
# (full request URLs) included.
log_redaction.install()

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


# /docs, /redoc and /openapi.json describe every route and model of the API; served only when
# ENABLE_API_DOCS=1 (local development), never by default.
API_DOCS = (os.environ.get("ENABLE_API_DOCS") or "").strip().lower() in ("1", "true", "yes")
app = FastAPI(openapi_url="/openapi.json" if API_DOCS else None)
api = APIRouter(prefix="/api", default_response_class=SignedJSONResponse)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """422 saying which field is wrong and why — without echoing the rejected input back (it can be a
    password or a whole request body) or library details (pydantic's version and documentation links)."""
    return JSONResponse(status_code=422, content={"detail": [
        {"type": e.get("type"), "loc": list(e.get("loc") or ()), "msg": e.get("msg")} for e in exc.errors()
    ]})


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


# ----------------------------------------------------------------------------
# Rate limiting: one small `rate_events` collection (TTL-expired after 2 days) shared by every
# limited action, so limits hold across restarts and multiple server processes.
# ----------------------------------------------------------------------------
async def _rate_count(key: str, window: timedelta) -> int:
    return await db.rate_events.count_documents({"key": key, "at": {"$gte": now_utc() - window}})


async def _rate_record(key: str) -> None:
    await db.rate_events.insert_one({"key": key, "at": now_utc()})


async def _rate_limit(key: str, limit: int, window: timedelta) -> None:
    """Count this action under `key`; 429 `rate_limited` once `limit` were made within `window`."""
    if await _rate_count(key, window) >= limit:
        raise _coded(429, "rate_limited", "Too many requests — please try again later")
    await _rate_record(key)


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


def delete_object(path: str) -> None:
    target = _storage_file(path)
    if target.is_file():
        target.unlink()


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
# Location privacy. Another reader's position never leaves the server, and the distance to them is
# deliberately coarse, because a client can claim any position of its own (PUT /users/me/location)
# and could otherwise triangulate someone from a few exact distances:
#  * distances are measured to the CENTER of the ~1 km grid cell the other reader is in, so even
#    perfect triangulation only finds that cell, never a home;
#  * they are reported in whole km, and anything under 1 km as exactly 1 km ("within 1 km");
#  * distance filters use the same coarse value, so they cannot be used to probe finer.
# Stored coordinates are kept at ~110 m (3 decimals): enough for the reader's own map, no more.
MIN_REPORTED_DISTANCE_KM = 1.0
PUBLIC_CELL_DEG = 0.01
LOCATION_STORE_DECIMALS = 3


def _cell_center(lat: float, lng: float) -> tuple:
    return ((math.floor(lat / PUBLIC_CELL_DEG) + 0.5) * PUBLIC_CELL_DEG,
            (math.floor(lng / PUBLIC_CELL_DEG) + 0.5) * PUBLIC_CELL_DEG)


def public_distance_km(km: float) -> float:
    return max(MIN_REPORTED_DISTANCE_KM, float(round(km)))


def has_location(u: Optional[dict]) -> bool:
    """True only for a location that actually came from the user's device (location_updated_at is
    set by PUT /users/me/location), or for the seeded demo community, which has fixed coordinates.
    Accounts created before device location existed still carry a hardcoded Messina default in
    lat/lng/city/neighborhood; that default is ignored everywhere rather than treated as real."""
    if not u or u.get("lat") is None or u.get("lng") is None:
        return False
    return bool(u.get("location_updated_at") or u.get("seed"))


def user_distance_km(a: Optional[dict], b: Optional[dict]) -> float:
    """Coarse distance from viewer `a` to reader `b` (see "Location privacy" above);
    UNKNOWN_DISTANCE_KM when either has no real location. Always call it viewer-first."""
    if not has_location(a) or not has_location(b):
        return UNKNOWN_DISTANCE_KM
    blat, blng = _cell_center(b["lat"], b["lng"])
    return public_distance_km(haversine_km(a["lat"], a["lng"], blat, blng))


# ----------------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------------
SUPPORTED_LANGUAGES = ("uz", "en", "ru", "it", "ar")
# Upper bounds on free text, so no field can be used to store or relay arbitrary amounts of data.
NAME_MAX_CHARS = 80
BIO_MAX_CHARS = 1000
TITLE_MAX_CHARS = 300
AUTHOR_MAX_CHARS = 200
MESSAGE_MAX_CHARS = 4000
URL_MAX_CHARS = 1000
# One entry of a profile list (a genre, language or reading interest); the app offers fixed values.
ProfileTag = Annotated[str, Field(max_length=NAME_MAX_CHARS)]


class RegisterBody(BaseModel):
    email: EmailStr
    password: str
    name: str = Field(max_length=NAME_MAX_CHARS)
    preferred_language: Optional[str] = None


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class ProfileUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=NAME_MAX_CHARS)
    bio: Optional[str] = Field(default=None, max_length=BIO_MAX_CHARS)
    # city / neighborhood / lat / lng are deliberately NOT here: location only comes from the device via
    # PUT /users/me/location. Older app builds still send them on save; pydantic drops unknown fields.
    avatar_url: Optional[str] = Field(default=None, max_length=URL_MAX_CHARS)
    genres: Optional[List[ProfileTag]] = Field(default=None, max_length=30)
    languages: Optional[List[ProfileTag]] = Field(default=None, max_length=20)
    reading_interests: Optional[List[ProfileTag]] = Field(default=None, max_length=30)
    preferred_language: Optional[str] = Field(default=None, max_length=10)
    is_exchanging: Optional[bool] = None
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
    title: str = Field(max_length=TITLE_MAX_CHARS)
    author: str = Field(default="", max_length=AUTHOR_MAX_CHARS)
    cover_url: Optional[str] = Field(default=None, max_length=URL_MAX_CHARS)
    condition: str = Field(default="Good", max_length=40)
    language: str = Field(default="English", max_length=40)
    genre: str = Field(default="Fiction", max_length=40)
    status: str = Field(default="Available", max_length=20)
    isbn: Optional[str] = Field(default=None, max_length=20)


BOOK_STATUSES = ("Available", "Reserved", "Swapped")


class BookUpdate(BaseModel):
    """Partial update: only the fields the client sends are changed."""
    title: Optional[str] = Field(default=None, max_length=TITLE_MAX_CHARS)
    author: Optional[str] = Field(default=None, max_length=AUTHOR_MAX_CHARS)
    cover_url: Optional[str] = Field(default=None, max_length=URL_MAX_CHARS)
    condition: Optional[str] = Field(default=None, max_length=40)
    language: Optional[str] = Field(default=None, max_length=40)
    genre: Optional[str] = Field(default=None, max_length=40)
    status: Optional[str] = Field(default=None, max_length=20)
    isbn: Optional[str] = Field(default=None, max_length=20)


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
    text: str = Field(default="", max_length=MESSAGE_MAX_CHARS)
    image_url: Optional[str] = Field(default=None, max_length=URL_MAX_CHARS)
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
    if u.get("deleted_at"):
        # A deleted account (seen in someone's swap history): no name, photo or anything else.
        return {"user_id": u["user_id"], "name": "", "avatar_url": None, "bio": "", "city": None,
                "neighborhood": None, "genres": [], "languages": [], "reading_interests": [], "badges": [],
                "is_exchanging": False, "rating": 0.0, "rating_count": 0, "swaps_count": 0,
                "preferred_language": None, "is_deleted": True}
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


# Images other readers' apps will load. An arbitrary URL there would let anyone make every viewer's
# phone contact a server of their choosing (a tracking pixel that reveals IP addresses and when the
# image was seen) or show content that never went through the upload checks. So: the caller's own
# uploads only — plus Open Library covers for books — or the value already stored (e.g. the Google
# profile photo an account was created with).
COVER_URL_PREFIXES = ("https://covers.openlibrary.org/",)


def _url_base(url: Optional[str]) -> str:
    return (url or "").split("?", 1)[0].strip()


def checked_image_url(url: Optional[str], owner_id: str, current: Optional[str] = None,
                      allow_covers: bool = False) -> Optional[str]:
    """The URL to store (signature query removed), or a 400 `invalid_image_url`."""
    base = _url_base(url)
    if not base:
        return None
    if current and base == _url_base(current):
        return current
    if base.startswith(f"{FILE_PREFIX}{APP_NAME}/uploads/{owner_id}/") and "/../" not in base:
        return base
    if allow_covers and base.startswith(COVER_URL_PREFIXES):
        return base
    raise _coded(400, "invalid_image_url", "Images must be uploaded through BookLoop")


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
# Cap on verification emails per hour across ALL addresses: anyone can request a code for any address, so
# without it a script could send BookLoop emails to unlimited addresses (spam under our domain, the email
# provider's quota used up). Set it above the expected sign-ups per hour, below the provider's quota.
OTP_EMAILS_PER_HOUR = int(os.environ.get("OTP_EMAILS_PER_HOUR") or 60)
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
    name: str = Field(max_length=NAME_MAX_CHARS)
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
        **_name_search_fields(name),
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
    await _rate_limit("otp-email:all", OTP_EMAILS_PER_HOUR, timedelta(hours=1))

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
            # The how-to-configure text (env var names, debug switches) is for the operator's log only.
            logger.error(f"OTP email not sent: {e}")
            raise HTTPException(status_code=503, detail="Email is temporarily unavailable — please try again later")
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
    # Every attempt is counted before the code is checked, in one atomic step with the limit: parallel
    # guesses can't all read the same attempt_count and each be checked.
    counted = await db.pending_registrations.update_one(
        {"email": email, "otp_hash": pending["otp_hash"], "attempt_count": {"$not": {"$gte": OTP_MAX_ATTEMPTS}}},
        {"$inc": {"attempt_count": 1}},
    )
    if not counted.modified_count:
        raise _otp_error(429, "too_many_attempts", "Too many incorrect attempts — request a new code")
    if not verify_pw(body.code.strip(), pending["otp_hash"]):
        raise _otp_error(400, "invalid_otp", "Incorrect verification code")

    # One-time use: a fresh, server-issued token proves verification for the next step. The OTP hash
    # is cleared first, atomically, so the code is redeemed once even by parallel requests.
    redeemed = await db.pending_registrations.update_one(
        {"email": email, "otp_hash": pending["otp_hash"]}, {"$set": {"otp_hash": None, "otp_verified": True}})
    if not redeemed.modified_count:
        raise _otp_error(400, "otp_expired", "Verification code expired — request a new one")
    token = secrets.token_urlsafe(32)
    await db.email_verifications.insert_one({"token": token, "email": email, "created_at": now_utc()})
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


LOGIN_FAILURES_ALLOWED = 10
LOGIN_FAILURE_WINDOW = timedelta(minutes=15)
# Accounts whose passwords are public: the shared test account documented in this (public) repository, and
# every account the test suite made (test_account, only created by /auth/register/dev-instant). They sign in
# only on a test server (OTP_DEBUG_MODE), never on one reachable from the internet.
PUBLIC_TEST_EMAILS = ("test@bookloop.com",)


def _test_only_account(user: dict) -> bool:
    return not OTP_DEBUG_MODE and bool(user.get("test_account") or user.get("email") in PUBLIC_TEST_EMAILS)


@api.post("/auth/login")
async def login(body: LoginBody):
    # Password guessing: after 10 wrong passwords for an email within 15 minutes, that email is
    # locked for the rest of the window (successful logins never count).
    key = f"login-fail:{body.email.lower()}"
    too_many = _coded(429, "too_many_attempts", "Too many sign-in attempts — please try again in a few minutes")
    if await _rate_count(key, LOGIN_FAILURE_WINDOW) >= LOGIN_FAILURES_ALLOWED:
        raise too_many
    # The attempt is recorded before the password is checked (and removed again if it was right), so
    # parallel guesses can't all pass the count above and each be checked.
    attempt = (await db.rate_events.insert_one({"key": key, "at": now_utc()})).inserted_id
    if await _rate_count(key, LOGIN_FAILURE_WINDOW) > LOGIN_FAILURES_ALLOWED:
        await db.rate_events.delete_one({"_id": attempt})
        raise too_many
    user = await db.users.find_one({"email": body.email.lower(), "deleted_at": None})
    if (not user or not user.get("password_hash") or _test_only_account(user)
            or not verify_pw(body.password, user["password_hash"])):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    await db.rate_events.delete_one({"_id": attempt})
    token = await create_session(user["user_id"])
    return {"session_token": token, "user": public_user(user, private=True)}


# --- Google sign-in -----------------------------------------------------------
# Flow: the app opens /auth/google/start in a browser -> Google -> /auth/google/callback (this API
# exchanges the code with our client secret and finds/creates the user) -> back to the app with a
# one-time `session_id` -> the app POSTs it to /auth/session and receives a normal session token.
#
# Binding to the app that started it (PKCE-style, RFC 7636 S256): the app keeps a random
# `code_verifier` to itself and sends only its SHA-256 (`code_challenge`) to /start. The one-time
# session_id only turns into a session together with that verifier, so a session_id that reaches anyone
# else (another app claiming the URL scheme, a crafted redirect, a shared link) is useless to them.
# Linking Google in Settings works the same way: the callback only parks the Google identity; the app
# that started the link completes it with its verifier while signed in as the same account.
#
# Every redirect back to the app carries `attempt` (the first characters of the challenge — not a
# secret, the challenge already went through the browser) so the app can tell which of its own sign-in
# attempts an incoming link belongs to, and ignore links it did not start.
OAUTH_STATE_TTL = timedelta(minutes=10)
GOOGLE_LOGIN_TTL = timedelta(minutes=2)
GOOGLE_LINK_TTL = timedelta(minutes=5)
PKCE_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")  # base64url(SHA-256), unpadded
PKCE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")  # RFC 7636 section 4.1
ATTEMPT_CHARS = 16
# Fixed reasons a sign-in redirect may carry (anything else becomes "failed"):
#   link_required    - the Google email matches an existing account, but it isn't an address Google
#                      vouches for (see _google_email_trusted): sign in with the password, then link.
#   account_mismatch - that account already has a different Google account linked.
GOOGLE_ERRORS = ("cancelled", "failed", "link_required", "account_mismatch")

if ALLOW_DEV_REDIRECTS:
    logger.warning(
        "ALLOW_DEV_REDIRECTS is ON: Google sign-in may return to exp://, development builds and "
        "http://localhost. Never run with this on a backend reachable from the internet."
    )


def _google_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def _google_native_configured() -> bool:
    if not (GOOGLE_CLIENT_ID and GOOGLE_ANDROID_CLIENT_IDS):
        return False
    try:
        import jwt  # noqa: F401  (PyJWT[crypto], as for Apple)
        return True
    except ImportError:
        return False


def _allowed_app_redirect(url: str) -> bool:
    """Only send the one-time login id back to the app itself, never to an arbitrary site."""
    return oauth_redirects.allowed_app_redirect(url, schemes=APP_URL_SCHEMES if GOOGLE_WEB_FLOW_APP_SCHEMES else (),
                                                origins=APP_REDIRECT_ORIGINS, dev=ALLOW_DEV_REDIRECTS)


def _is_app_scheme_redirect(url: str) -> bool:
    """A return address that any app on the phone could claim (bookloop://, exp://), unlike a web origin."""
    return urlparse(url).scheme not in ("http", "https")


def _pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _require_pkce(challenge: Optional[str], method: Optional[str]) -> str:
    if method != "S256" or not challenge or not PKCE_CHALLENGE_RE.match(challenge):
        raise _coded(400, "pkce_required", "Update the app to sign in with Google")
    return challenge


def _valid_verifier(verifier: Optional[str]) -> bool:
    return bool(verifier and PKCE_VERIFIER_RE.match(verifier))


def _fresh_since(ttl: timedelta) -> dict:
    """Mongo filter on created_at: records older than `ttl` are never redeemed, whether or not the TTL
    index has removed them yet (it only runs about once a minute)."""
    return {"$gte": now_utc() - ttl}


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
    return {"google": _google_configured(), "google_native": _google_native_configured(), "apple": _apple_configured()}


@api.get("/auth/google/start")
async def google_start(request: Request, redirect: str, code_challenge: Optional[str] = None,
                       code_challenge_method: Optional[str] = None):
    if not _google_configured():
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")
    if not _allowed_app_redirect(redirect):
        raise HTTPException(status_code=400, detail="Redirect URL not allowed")
    challenge = _require_pkce(code_challenge, code_challenge_method)
    return RedirectResponse(await _google_auth_url(request, redirect, challenge), status_code=302)


async def _google_auth_url(request: Request, redirect: str, code_challenge: str,
                           link_user_id: Optional[str] = None) -> str:
    state = secrets.token_urlsafe(32)
    await db.oauth_states.insert_one({"state": state, "redirect": redirect, "link_user_id": link_user_id,
                                      "code_challenge": code_challenge, "created_at": now_utc()})
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
    code_challenge: Optional[str] = Field(default=None, max_length=64)
    code_challenge_method: Optional[str] = Field(default=None, max_length=10)


@api.post("/auth/google/link")
async def google_link_start(body: LinkStartBody, request: Request, user: dict = Depends(get_current_user)):
    """Settings -> Account -> Link Google. Returns the Google URL for the app to open. The signed-in
    account and the app's code_challenge are bound to the single-use OAuth state server-side."""
    if not _google_configured():
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")
    if not _allowed_app_redirect(body.redirect):
        raise HTTPException(status_code=400, detail="Redirect URL not allowed")
    challenge = _require_pkce(body.code_challenge, body.code_challenge_method)
    return {"url": await _google_auth_url(request, body.redirect, challenge, link_user_id=user["user_id"])}


@api.get("/auth/google/callback")
async def google_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    # `state` is single-use and short-lived; without a valid one this isn't a sign-in we started.
    rec = await db.oauth_states.find_one_and_delete({"state": state}) if state else None
    if (not rec or not rec.get("code_challenge")
            or now_utc() - _aware(rec["created_at"]) > OAUTH_STATE_TTL):
        raise HTTPException(status_code=400, detail="Invalid or expired sign-in attempt")
    redirect = rec["redirect"]
    challenge = rec["code_challenge"]
    attempt = challenge[:ATTEMPT_CHARS]
    linking = bool(rec.get("link_user_id"))

    def fail(reason: str) -> RedirectResponse:
        # Fixed values only: nothing Google (or whoever crafted the callback URL) sent is reflected.
        if linking:
            return _app_redirect(redirect, link_error="failed", attempt=attempt)
        return _app_redirect(redirect, google_error=reason if reason in GOOGLE_ERRORS else "failed", attempt=attempt)

    if error or not code:
        return fail("cancelled")
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
    if linking:
        # Linking Google to the account that started this from Settings — never a sign-in, and not
        # linked yet: the app completes it (POST /auth/google/link/complete) with its verifier.
        if not claims or not claims.get("sub"):
            return fail("failed")
        lid = secrets.token_urlsafe(32)
        await db.google_link_tickets.insert_one({
            "lid": lid, "user_id": rec["link_user_id"], "subject": claims["sub"],
            "email": claims["email"].lower(), "code_challenge": challenge, "created_at": now_utc(),
            "app_scheme": _is_app_scheme_redirect(redirect),
        })
        return _app_redirect(redirect, link_id=lid, attempt=attempt)
    try:
        user = await _google_user(claims) if claims else None
    except GoogleRefused as e:
        return fail(e.code)
    if not user:
        return fail("failed")
    sid = secrets.token_urlsafe(32)
    await db.google_logins.insert_one({"sid": sid, "user_id": user["user_id"], "code_challenge": challenge,
                                       "created_at": now_utc()})
    return _app_redirect(redirect, session_id=sid, attempt=attempt)


# Which app build each account uses: X-App-Version / X-App-Platform (no header = a build from before
# native Google sign-in), plus how its last Google sign-in ran. Decides when GOOGLE_WEB_FLOW_APP_SCHEMES
# can be switched off; scripts/client_versions.py summarizes it.
CLIENT_HEADER_RE = re.compile(r"^[0-9A-Za-z._+-]{1,32}$")
CLIENT_SEEN_REFRESH = timedelta(hours=12)


async def _record_client(user: dict, request: Request, google_signin: Optional[str] = None) -> None:
    def header(name: str) -> Optional[str]:
        v = (request.headers.get(name) or "").strip()
        return v if CLIENT_HEADER_RE.match(v) else None

    now = now_utc()
    fields = {"client_version": header("x-app-version"), "client_platform": header("x-app-platform")}
    seen = user.get("client_seen_at")
    if (not google_signin and all(user.get(k) == v for k, v in fields.items())
            and seen and now - _aware(seen) < CLIENT_SEEN_REFRESH):
        return
    fields["client_seen_at"] = now
    if google_signin:
        fields.update(google_signin_via=google_signin, google_signin_at=now)
    await db.users.update_one({"user_id": user["user_id"]}, {"$set": fields})


class SessionBody(BaseModel):
    session_id: str = Field(max_length=100)
    code_verifier: Optional[str] = Field(default=None, max_length=128)


@api.post("/auth/session")
async def google_session(body: SessionBody, request: Request):
    """Redeem the one-time id from a completed Google sign-in, together with the verifier of the app
    that started it, for a normal app session."""
    if not _valid_verifier(body.code_verifier):
        raise _coded(400, "pkce_required", "Update the app to sign in with Google")
    # One atomic step: only the matching verifier, only while fresh, only once. A wrong verifier does
    # not consume the id, so someone holding a stolen id cannot even spoil the real sign-in.
    rec = await db.google_logins.find_one_and_delete({
        "sid": body.session_id, "code_challenge": _pkce_challenge(body.code_verifier),
        "created_at": _fresh_since(GOOGLE_LOGIN_TTL),
    })
    if not rec:
        raise HTTPException(status_code=401, detail="Invalid session")
    user = await db.users.find_one({"user_id": rec["user_id"], "deleted_at": None}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid session")
    token = await create_session(user["user_id"])
    await _record_client(user, request, "google_web")
    return {"session_token": token, "user": public_user(user, private=True)}


class LinkCompleteBody(BaseModel):
    link_id: str = Field(max_length=100)
    code_verifier: Optional[str] = Field(default=None, max_length=128)


@api.post("/auth/google/link/complete")
async def google_link_complete(body: LinkCompleteBody, user: dict = Depends(get_current_user)):
    """Finishes Settings -> Link Google: only for the account that started it, only with the verifier
    of the app that started it, only once and only while fresh. Conflicts (Google account already
    linked elsewhere, another Google account already linked here) are decided at this moment, against
    the unique indexes on auth_identities, not when the browser came back."""
    if not _valid_verifier(body.code_verifier):
        raise _coded(400, "pkce_required", "Update the app to link Google")
    rec = await db.google_link_tickets.find_one_and_delete({
        "lid": body.link_id, "user_id": user["user_id"], "code_challenge": _pkce_challenge(body.code_verifier),
        "created_at": _fresh_since(GOOGLE_LINK_TTL),
    })
    if not rec:
        raise _coded(400, "link_invalid", "This link request is invalid or has expired — please try again")
    # Started from the app's URL scheme: another app on the phone could have started it (with its own
    # verifier and its own signed-in account) and had a victim pick their Google account. Until the app
    # links natively, only a Google account with this account's own email can be linked this way.
    if rec.get("app_scheme") and (rec.get("email") or "") != (user.get("email") or "").lower():
        raise _coded(409, "google_email_mismatch", "Only a Google account with this account's email can be linked")
    err = await _link_from_settings(user, "google", rec["subject"], rec.get("email"), True)
    if err:
        raise _coded(409, err, "This Google account can't be linked to this account")
    return await account_identities(user)


class GoogleRefused(Exception):
    """A verified Google identity that must not open the account its email points at. `code` is one of
    GOOGLE_ERRORS."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


GOOGLE_CONSUMER_DOMAINS = ("gmail.com", "googlemail.com")


def _google_email_trusted(claims: dict, email: str) -> bool:
    """Whether Google's `email` identifies the person well enough to open an EXISTING account by email.
    Google's guidance: only for Gmail addresses and Google Workspace accounts (`hd` claim). Any other
    address was verified when the Google account was created and may belong to someone else by now."""
    return email.rsplit("@", 1)[-1] in GOOGLE_CONSUMER_DOMAINS or bool(claims.get("hd"))


async def _google_user(claims: dict) -> Optional[dict]:
    """The account for a verified Google identity: the one it is linked to; else the account with the
    same Google-verified email (then the identity is linked to it) — only when Google vouches for the
    address and the account has no other Google account linked, else GoogleRefused; else a new account."""
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
        # Accounts from before auth_identities kept Google's id on the user record (google_sub): that
        # proves it's the same Google account whatever the address.
        legacy_same = bool(sub) and user.get("google_sub") == sub
        if not legacy_same and not _google_email_trusted(claims, email):
            raise GoogleRefused("link_required")
        # Another Google account linked here shares the email (e.g. a recycled Workspace address). The
        # same Google account can appear here too, linked a moment ago by a concurrent sign-in.
        other = {"user_id": uid, "provider": "google", **({"subject": {"$ne": sub}} if sub else {})}
        if await db.auth_identities.find_one(other, {"_id": 1}):
            raise GoogleRefused("account_mismatch")
        if sub:
            had_other_login = await _has_other_login(user, "google")
            status = await _link_identity_status(uid, "google", sub, email, True)
            if status not in ("linked", "already"):  # a concurrent link of another account won the race
                raise GoogleRefused("account_mismatch" if status == "provider_already_linked" else "failed")
            if status == "linked" and had_other_login:
                await _security_notice(user, "signin_linked", "google", email)
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
            **_name_search_fields(name),
        }
        try:
            await db.users.insert_one(user)
        except DuplicateKeyError:
            # A concurrent first sign-in with the same email created the account a moment ago: this one
            # fails cleanly and the retry finds that account.
            raise GoogleRefused("failed")
        if sub:
            await _link_identity(uid, "google", sub, email, True)
    return user


# --- Native Google sign-in (Android Credential Manager) ------------------------------------------------
# The app asks Android for a Google ID token for our web client (serverClientId = GOOGLE_CLIENT_ID) with a
# nonce from /auth/google/nonce, and posts it here. Google issues such a token only to an app registered as
# one of GOOGLE_ANDROID_CLIENT_IDS (package name + signing certificate) — so, unlike the browser flow's
# bookloop:// return, no other app on the phone can obtain one — and the single-use nonce stops replays.
# The account rules are the browser flow's (_google_user); linking needs no email match here.
GOOGLE_NONCE_TTL = timedelta(minutes=10)
GOOGLE_TOKEN_LEEWAY_S = 60


@api.post("/auth/google/nonce")
async def google_nonce():
    """A single-use nonce for one native Google sign-in; Google puts it into the ID token as is."""
    if not _google_native_configured():
        raise HTTPException(status_code=503, detail="Native Google sign-in is not configured")
    rec = {"id": secrets.token_urlsafe(16), "nonce": secrets.token_urlsafe(32), "created_at": now_utc()}
    await db.google_nonces.insert_one(dict(rec))
    return {"nonce_id": rec["id"], "nonce": rec["nonce"]}


async def _google_native_claims(id_token: str, nonce_id: str) -> Optional[dict]:
    """Verifies a Google ID token from the app: Google's signature, issuer, our web client as audience,
    one of our Android clients as the authorized party, expiry, a verified email, and this server's
    single-use nonce (consumed whatever the outcome)."""
    import jwt
    rec = await db.google_nonces.find_one_and_delete(
        {"id": nonce_id, "created_at": _fresh_since(GOOGLE_NONCE_TTL)}) if nonce_id else None
    if not rec:
        return None
    try:
        kid = jwt.get_unverified_header(id_token).get("kid")
        key = await _jwks_key(_google_keys, GOOGLE_CERTS_URL, kid) if kid else None
        if key is None:
            return None
        # Leeway: Google's clock and ours differ by a few seconds, and PyJWT rejects an `iat` that is
        # even one second ahead.
        claims = jwt.decode(id_token, key, algorithms=["RS256"], audience=GOOGLE_CLIENT_ID,
                            leeway=GOOGLE_TOKEN_LEEWAY_S, options={"require": ["exp", "iat", "sub", "aud", "iss"]})
    except Exception as e:
        logger.warning(f"Google ID token rejected: {e!r}")
        return None
    if claims.get("iss") not in GOOGLE_ISSUERS or claims.get("azp") not in GOOGLE_ANDROID_CLIENT_IDS:
        return None
    if not hmac.compare_digest(str(claims.get("nonce") or ""), rec["nonce"]):
        return None
    if not claims.get("email") or claims.get("email_verified") not in (True, "true"):
        return None
    return claims


class GoogleNativeBody(BaseModel):
    id_token: str = Field(max_length=5000)
    nonce_id: str = Field(max_length=100)


GOOGLE_REFUSALS = {
    "link_required": "Sign in with your password, then link Google in Profile -> Account",
    "account_mismatch": "This email's BookLoop account is linked to a different Google account",
}


@api.post("/auth/google/native")
async def google_native_sign_in(body: GoogleNativeBody, request: Request):
    if not _google_native_configured():
        raise HTTPException(status_code=503, detail="Native Google sign-in is not configured")
    claims = await _google_native_claims(body.id_token, body.nonce_id)
    if not claims:
        raise _coded(401, "google_failed", "Google sign-in could not be verified")
    try:
        user = await _google_user(claims)
    except GoogleRefused as e:
        if e.code in GOOGLE_REFUSALS:
            raise _coded(409, e.code, GOOGLE_REFUSALS[e.code])
        raise _coded(401, "google_failed", "Google sign-in could not be verified")
    if not user:
        raise _coded(401, "google_failed", "This account no longer exists")
    user.pop("_id", None)
    token = await create_session(user["user_id"])
    await _record_client(user, request, "google_native")
    return {"session_token": token, "user": public_user(user, private=True)}


@api.post("/auth/google/native/link")
async def google_native_link(body: GoogleNativeBody, user: dict = Depends(get_current_user)):
    """Settings -> Account -> Link Google, natively: links the Google account in the token to the
    signed-in account (any email: the token can only come from our own app)."""
    if not _google_native_configured():
        raise HTTPException(status_code=503, detail="Native Google sign-in is not configured")
    claims = await _google_native_claims(body.id_token, body.nonce_id)
    if not claims:
        raise _coded(401, "google_failed", "Google sign-in could not be verified")
    err = await _link_from_settings(user, "google", claims["sub"], claims["email"].lower(), True)
    if err:
        raise _coded(409, err, "This Google account can't be linked to this account")
    return await account_identities(user)


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
    status = await _link_identity_status(user_id, provider, subject, email, email_verified, private_email)
    return None if status in ("linked", "already") else status


async def _link_identity_status(user_id: str, provider: str, subject: str, email: Optional[str],
                                email_verified: bool, private_email: bool = False) -> str:
    """"linked" (by this call), "already" (linked to this user before, or by a concurrent call), or an
    error code: "identity_in_use" / "provider_already_linked"."""
    existing = await db.auth_identities.find_one({"provider": provider, "subject": subject}, {"_id": 0})
    if existing:
        return "already" if existing["user_id"] == user_id else "identity_in_use"
    mine = await db.auth_identities.find_one({"user_id": user_id, "provider": provider}, {"_id": 0, "subject": 1})
    if mine:  # the same account linked by a concurrent call a moment ago, or a different one
        return "already" if mine["subject"] == subject else "provider_already_linked"
    try:
        await db.auth_identities.insert_one({
            "provider": provider, "subject": subject, "user_id": user_id,
            "email": email, "email_verified": bool(email_verified), "private_email": private_email,
            "created_at": now_utc(), "last_login_at": now_utc(),
        })
    except DuplicateKeyError:
        # A concurrent link won the race. The unique indexes ((provider, subject) and (user_id, provider))
        # decided it; report which one this request lost on.
        again = await db.auth_identities.find_one({"provider": provider, "subject": subject}, {"_id": 0})
        if again:
            return "already" if again["user_id"] == user_id else "identity_in_use"
        return "provider_already_linked"
    return "linked"


async def _has_other_login(user: dict, provider: str) -> bool:
    """Whether the account already had a way in besides `provider` (a password or another provider). Only
    then is a sign-in that attaches `provider` by email news to its owner: an account without one was
    created by a sign-in with this very identity (possibly a concurrent one, or before identities were
    stored)."""
    if user.get("password_hash"):
        return True
    return bool(await db.auth_identities.find_one({"user_id": user["user_id"], "provider": {"$ne": provider}}, {"_id": 1}))


async def _link_from_settings(user: dict, provider: str, subject: str, email: Optional[str], email_verified: bool,
                              private_email: bool = False) -> Optional[str]:
    """Settings -> Account -> Link: _link_identity, plus a security notice when this call linked something new."""
    status = await _link_identity_status(user["user_id"], provider, subject, email, email_verified, private_email)
    if status == "linked":
        await _security_notice(user, "linked", provider, email)
    return None if status in ("linked", "already") else status


# --- Security notices ----------------------------------------------------------------------------------
# Every change to how an account signs in (a provider linked from Settings, linked automatically by a
# first sign-in with a matching email, or unlinked) is recorded in security_events (kept 180 days, deleted
# with the account) and emailed to the account's address, so a change the owner didn't make is noticed.
SECURITY_EVENT_TTL = timedelta(days=180)
SECURITY_EVENTS = ("linked", "signin_linked", "unlinked")


async def _security_notice(user: dict, event: str, provider: str, provider_email: Optional[str]) -> None:
    """Best-effort: never fails the request that triggered it."""
    try:
        await db.security_events.insert_one({"user_id": user["user_id"], "event": event, "provider": provider,
                                             "provider_email": provider_email, "created_at": now_utc()})
    except Exception as e:
        logger.warning(f"security event not recorded: {e!r}")
    to = user.get("email")
    if to and not to.endswith("@deleted.invalid"):
        _spawn(_send_security_email(to, user.get("name") or "", event, provider, provider_email))


async def _send_security_email(to: str, name: str, event: str, provider: str, provider_email: Optional[str]) -> None:
    subject, html, text = security_email_body(name, event, provider, provider_email)
    try:
        await get_email_provider().send(to=to, subject=subject, html=html, text=text)
    except EmailNotConfiguredError:
        logger.info(f"security email ({event}) not sent: no email provider configured")
    except Exception as e:
        logger.warning(f"security email ({event}) failed: {e!r}")


def _apple_configured() -> bool:
    if not APPLE_AUDIENCES:
        return False
    try:
        import jwt  # noqa: F401  (PyJWT[crypto]; Apple sign-in stays off without it)
        return True
    except ImportError:
        return False


_apple_keys: dict = {"at": 0.0, "keys": {}}
_google_keys: dict = {"at": 0.0, "keys": {}}


async def _jwks_key(cache: dict, url: str, kid: str):
    """A provider's signing key by id, from its JWKS: cached for an hour, refetched early (at most once a
    minute) when the provider starts signing with a key we haven't seen."""
    import jwt
    age = time.time() - cache["at"]
    if age > 3600 or (kid not in cache["keys"] and age > 60):
        async with httpx.AsyncClient(timeout=10) as hc:
            resp = await hc.get(url)
        resp.raise_for_status()
        cache["keys"] = {k["kid"]: jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(k)) for k in resp.json()["keys"]}
        cache["at"] = time.time()
    return cache["keys"].get(kid)


async def _apple_public_key(kid: str):
    return await _jwks_key(_apple_keys, APPLE_KEYS_URL, kid)


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
    matched_by_email = False
    if user is None and email and verified and not private:
        # Same rule as Google: an Apple-verified email finds the existing account with that email.
        found = await db.users.find_one({"email": email}, {"_id": 0})
        if found and found.get("deleted_at"):
            raise _coded(401, "apple_failed", "This account no longer exists")
        user, matched_by_email = found, found is not None
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
    had_other_login = matched_by_email and await _has_other_login(user, "apple")
    status = await _link_identity_status(user["user_id"], "apple", sub, email, verified, private)
    if status not in ("linked", "already"):
        raise _coded(409, status, "This Apple ID can't be used with this account")
    if had_other_login and status == "linked":
        await _security_notice(user, "signin_linked", "apple", email)
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
    err = await _link_from_settings(user, "apple", claims["sub"], email, verified, private)
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
    ident = await db.auth_identities.find_one({"user_id": user["user_id"], "provider": provider}, {"_id": 1, "email": 1})
    if not ident:
        raise HTTPException(status_code=404, detail="Not linked")
    others = await db.auth_identities.count_documents({"user_id": user["user_id"], "provider": {"$ne": provider}})
    if not user.get("password_hash") and others == 0:
        raise _coded(409, "last_login_method", "This is your only way to sign in — it can't be removed")
    if (await db.auth_identities.delete_one({"_id": ident["_id"]})).deleted_count:
        await _security_notice(user, "unlinked", provider, ident.get("email"))
    return await account_identities(user)


@api.get("/auth/me")
async def me(request: Request, user: dict = Depends(get_current_user)):
    await _record_client(user, request)
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
    if "avatar_url" in updates:
        updates["avatar_url"] = checked_image_url(updates["avatar_url"], user["user_id"], user.get("avatar_url"))
    if "name" in updates:
        if not updates["name"].strip():
            raise HTTPException(status_code=400, detail="Name cannot be empty")
        updates.update(_name_search_fields(updates["name"]))
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
    granted location permission. Coordinates are stored at ~110 m precision (3 decimals) and are never
    returned to other users — they only ever see an area name and a coarse distance."""
    await _rate_limit(f"location:{user['user_id']}", 30, timedelta(hours=1))
    neighborhood = _clean_place(body.neighborhood)
    city = _clean_place(body.city)
    if neighborhood and city and neighborhood.casefold() == city.casefold():
        neighborhood = None  # the geocoder echoed the city: don't show "Tashkent, Tashkent"
    updates = {
        "lat": round(body.lat, LOCATION_STORE_DECIMALS),
        "lng": round(body.lng, LOCATION_STORE_DECIMALS),
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
    if not u or await _is_blocked_pair(user["user_id"], user_id):
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
# Text search (see search_text.py) — books by title/author/aliases, readers by name
# ----------------------------------------------------------------------------
def _name_search_fields(name: Optional[str]) -> dict:
    """Search representation of a reader's name (users.search_tokens / search_v)."""
    return {"search_tokens": search_text.index_tokens([name]), "search_v": search_text.SEARCH_VERSION}


TEXT_SEARCH_CANDIDATE_LIMIT = 3000


async def _ranked_matches(coll, base_q: dict, search: str, slim: dict, legacy_fields: List[str],
                          score, sort_newest: bool = True) -> Optional[List[tuple]]:
    """[(score, _id)] of the documents allowed by `base_q` whose text matches `search`, best first
    (ties newest first when `sort_newest`). None when the query has nothing searchable in it.

    `base_q` is the caller's eligibility filter and is always ANDed in: text matching only narrows
    and ranks. Candidates come from anchored-prefix lookups on the indexed `search_tokens`, plus a
    plain-text branch for documents the backfill has not reached yet; when that finds nothing, a
    second pass looks words up by their ending (a typo in the first letters, e.g. "Fostoevsky")."""
    prefixes = search_text.candidate_prefixes(search)
    if not prefixes:
        return None
    raw = re.escape(search.strip())
    legacy = {"search_v": {"$exists": False}, "$or": [{f: {"$regex": raw, "$options": "i"}} for f in legacy_fields]}
    passes = [
        [{"search_tokens": {"$regex": "^" + re.escape(p)}} for p in prefixes] + [legacy],
        [{"search_tokens": {"$regex": "^" + re.escape(p)}} for p in search_text.reverse_prefixes(search)],
    ]
    for ors in passes:
        if not ors:
            continue
        cursor = coll.find({"$and": [base_q, {"$or": ors}]}, slim)
        if sort_newest:
            cursor = cursor.sort("created_at", -1)
        scored = [(score(d), d["_id"]) for d in await cursor.to_list(TEXT_SEARCH_CANDIDATE_LIMIT)]
        hits = sorted((sd for sd in scored if sd[0] > 0), key=lambda sd: -sd[0])  # stable: ties keep order
        if hits:
            return hits
    return []


# ----------------------------------------------------------------------------
# Book search representation (see search_text.py)
# ----------------------------------------------------------------------------
# Stored on each book next to the untouched title/author:
#   search_tokens  normalized words + cross-script keys of title, author and aliases (indexed)
#   search_v       search_text.SEARCH_VERSION the tokens were built with
#   aliases        [{kind: "title"|"author", value, source: "openlibrary", ref: work key, lang?}] -
#                  only from verified metadata, never generated; user-entered values stay in title/author
#   aliases_status "pending" | "done" | "none" (no verified match) | "error" (retried later)
def _search_fields(book: dict) -> dict:
    texts = [book.get("title"), book.get("author"), *(a.get("value") for a in book.get("aliases") or [])]
    return {"search_tokens": search_text.index_tokens(texts), "search_v": search_text.SEARCH_VERSION}


# Alias lookups (background only; never on a search request). Two sources, both verified:
#   OpenLibrary — the work behind the ISBN/title: original title, edition titles, author spellings
#   Wikidata    — the work's curated title in each BookLoop language, its authors' names
# Every response is cached in `metadata_cache` (30 days), so popular books are looked up once.
METADATA_ENRICH = (os.environ.get("METADATA_ENRICH") or "1").strip().lower() not in ("0", "false", "no")
# Wikimedia rejects requests without contact details in the User-Agent; Wikidata is used only when
# METADATA_CONTACT (an email or URL for the operators of this server) is configured.
METADATA_CONTACT = (os.environ.get("METADATA_CONTACT") or "").strip()
WIKIDATA_URL = os.environ.get("WIKIDATA_URL") or "https://www.wikidata.org/w/api.php"
METADATA_SPACING_S = float(os.environ.get("METADATA_SPACING_S") or 0.5)
METADATA_CACHE_TTL = timedelta(days=30)
MAX_ALIASES = 80
ALIAS_RETRY_AFTER = timedelta(days=1)
_meta_gates: dict = {}  # one request at a time per host, spaced out (polite + rate-safe)
_bg_tasks: set = set()


def _spawn(coro) -> None:
    """Fire-and-forget task, kept referenced until it finishes."""
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)


def _metadata_user_agent() -> str:
    return f"BookLoop/1.0 (book search metadata; contact: {METADATA_CONTACT})" if METADATA_CONTACT else "BookLoop/1.0"


async def _meta_get(url: str, params: dict, shrink=None) -> dict:
    """GET a metadata JSON document through the cache. `shrink` trims a response to what BookLoop
    uses before it is cached (edition lists and Wikidata entities are large)."""
    key = hashlib.sha1(f"{url}?{urlencode(sorted(params.items()))}".encode()).hexdigest()
    hit = await db.metadata_cache.find_one({"key": key}, {"_id": 0, "data": 1})
    if hit:
        return hit["data"]
    gate = _meta_gates.setdefault(urlparse(url).netloc, asyncio.Semaphore(1))
    async with gate:
        try:
            async with httpx.AsyncClient(timeout=15) as hc:
                resp = await hc.get(url, params=params, headers={"User-Agent": _metadata_user_agent()})
            resp.raise_for_status()
            data = resp.json()
        finally:
            await asyncio.sleep(METADATA_SPACING_S)
    if shrink:
        data = shrink(data)
    await db.metadata_cache.update_one({"key": key}, {"$set": {"data": data, "fetched_at": now_utc()}}, upsert=True)
    return data


def _shrink_editions(data: dict) -> dict:
    return {"entries": [{"title": e.get("title"), "languages": e.get("languages") or []} for e in data.get("entries") or []]}


def _shrink_entities(data: dict) -> dict:
    out = {}
    for qid, e in (data.get("entities") or {}).items():
        out[qid] = {"id": e.get("id") or qid, "labels": e.get("labels") or {}, "aliases": e.get("aliases") or {},
                    "claims": {"P50": (e.get("claims") or {}).get("P50") or []}}
    return {"entities": out}


async def _openlibrary_aliases(book: dict) -> list:
    """Aliases from the OpenLibrary work this book is verified to be: by ISBN (an exact edition), or
    by title+author, where the stored title/author must equal the work's (search_text.same_work).
    Returns [] when no work is verified; raises on network errors so the book is retried later."""
    fields = "key,title,author_name,author_alternative_name"
    isbn = re.sub(r"[^0-9Xx]", "", book.get("isbn") or "")
    if isbn:
        params = {"isbn": isbn, "fields": fields, "limit": 1}
    else:
        params = {"title": book.get("title") or "", "fields": fields, "limit": 3}
        if (book.get("author") or "").strip():
            params["author"] = book["author"]
    docs = (await _meta_get(f"{OPENLIBRARY_URL}/search.json", params)).get("docs") or []
    for doc in docs:
        key = doc.get("key") or ""
        if not key.startswith("/works/"):
            continue
        editions = (await _meta_get(f"{OPENLIBRARY_URL}{key}/editions.json", {"limit": 500}, _shrink_editions)).get("entries") or []
        title, author = book.get("title") or "", book.get("author") or ""
        if isbn or search_text.same_work(title, author, doc, [e.get("title") or "" for e in editions]):
            return search_text.aliases_from_openlibrary(title, author, doc, editions)
    return []


async def _wikidata_entities(ids: List[str], claims: bool) -> List[dict]:
    out = []
    for i in range(0, len(ids), 50):  # wbgetentities takes up to 50 ids
        data = await _meta_get(WIKIDATA_URL, {
            "action": "wbgetentities", "ids": "|".join(ids[i:i + 50]), "format": "json",
            "props": "labels|aliases" + ("|claims" if claims else ""), "languages": "|".join(search_text.WIKIDATA_LANGS),
        }, _shrink_entities)
        out += list((data.get("entities") or {}).values())
    return out


async def _wikidata_aliases(book: dict) -> list:
    """Per-language titles and author names from the Wikidata item this book is verified to be
    (search_text.wikidata_match). [] when Wikidata is not configured or nothing is verified."""
    if not METADATA_CONTACT:
        return []
    title, author = book.get("title") or "", book.get("author") or ""
    if not search_text.normalize(title):
        return []
    lang = search_text.BOOK_LANGUAGE_CODES.get(book.get("language") or "", "en")
    ids: List[str] = []
    for code in dict.fromkeys([lang, "en"]):
        r = await _meta_get(WIKIDATA_URL, {"action": "wbsearchentities", "search": title, "language": code,
                                            "type": "item", "limit": 7, "format": "json"})
        ids += [x["id"] for x in r.get("search") or [] if x.get("id") and x["id"] not in ids]
    if not ids:
        return []
    works = await _wikidata_entities(ids, claims=True)
    author_ids = sorted({a for w in works for a in search_text.wikidata_author_ids(w)})
    authors = {e["id"]: e for e in await _wikidata_entities(author_ids, claims=False)} if author_ids else {}
    work = search_text.wikidata_match(title, author, works, authors)
    return search_text.aliases_from_wikidata(title, author, work, authors) if work else []


async def _enrich_book(book_id: str) -> None:
    """Fetch and store a book's metadata aliases, then rebuild its search tokens. Writes only if the
    title/author/isbn are unchanged since the fetch started (an edit meanwhile re-queues the book).
    A source that fails marks the book "error" (retried later) but keeps what the other one found."""
    if not METADATA_ENRICH:
        return
    book = await db.books.find_one({"id": book_id, "deleted_at": None}, {"_id": 0})
    if not book:
        return
    same = {"id": book_id, "title": book.get("title"), "author": book.get("author"), "isbn": book.get("isbn")}
    results = await asyncio.gather(_wikidata_aliases(book), _openlibrary_aliases(book), return_exceptions=True)
    failed = [r for r in results if isinstance(r, Exception)]
    for r in failed:
        logger.info(f"alias lookup failed for {book_id}: {r!r}")
    # Wikidata first: its per-language labels are the curated titles; OpenLibrary fills in the rest.
    merged = search_text.merge_aliases(*(r for r in results if not isinstance(r, Exception)))
    aliases = sorted(merged, key=lambda a: a["kind"] != "title")[:MAX_ALIASES]
    book["aliases"] = aliases
    status = "error" if failed else "done" if aliases else "none"
    await db.books.update_one(same, {"$set": {
        "aliases": aliases, "aliases_status": status, "aliases_checked_at": now_utc(), **_search_fields(book),
    }})


async def backfill_search_tokens() -> int:
    """Give every book (and every reader's name) the current search representation (local work
    only, no network). Idempotent and versioned: run at each startup, it touches only documents
    whose search_v is missing/outdated."""
    done = 0
    stale = {"search_v": {"$ne": search_text.SEARCH_VERSION}}
    jobs = (
        (db.books, {"_id": 1, "title": 1, "author": 1, "aliases": 1}, _search_fields),
        (db.users, {"_id": 1, "name": 1}, lambda u: _name_search_fields(u.get("name"))),
    )
    for coll, fields, build in jobs:
        while True:
            batch = await coll.find(stale, fields).to_list(500)
            if not batch:
                break
            # By _id: always indexed, so a batch costs 500 point updates rather than 500 collection scans.
            await coll.bulk_write([UpdateOne({"_id": d["_id"]}, {"$set": build(d)}) for d in batch], ordered=False)
            done += len(batch)
    return done


async def backfill_aliases() -> None:
    """Slowly look up aliases for books that never had them (existing books, failed lookups), one
    OpenLibrary request at a time. Runs in the background; search works meanwhile, just without them."""
    while METADATA_ENRICH:
        due = {"deleted_at": None, "$or": [
            {"aliases_status": {"$exists": False}},
            {"aliases_status": "pending", "aliases_checked_at": {"$exists": False}},
            {"aliases_status": "error", "aliases_checked_at": {"$lt": now_utc() - ALIAS_RETRY_AFTER}},
        ]}
        batch = await db.books.find(due, {"_id": 0, "id": 1}).sort("created_at", -1).to_list(50)
        if not batch:
            return
        for b in batch:
            await _enrich_book(b["id"])
            # A lookup that could not be stored (book edited meanwhile) must not be picked again forever;
            # the edit already queued its own lookup.
            await db.books.update_one({"id": b["id"], "aliases_checked_at": {"$exists": False}},
                                      {"$set": {"aliases_status": "pending", "aliases_checked_at": now_utc()}})


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
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    book = {
        "id": new_id("book"),
        "owner_id": user["user_id"],
        "title": body.title,
        "author": body.author,
        "cover_url": checked_image_url(body.cover_url, user["user_id"], allow_covers=True),
        "condition": body.condition,
        "language": body.language,
        "genre": body.genre,
        "isbn": body.isbn,
        "status": body.status if body.status in ("Available", "Reserved", "Swapped") else "Available",
        "created_at": now_utc(),
        "deleted_at": None,
        "aliases": [],
        "aliases_status": "pending",
    }
    book.update(_search_fields(book))
    await db.books.insert_one(book)
    _spawn(_enrich_book(book["id"]))
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
    if "cover_url" in updates:
        updates["cover_url"] = checked_image_url(updates["cover_url"], user["user_id"], book.get("cover_url"), allow_covers=True)
    if "status" in updates:
        if updates["status"] not in BOOK_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status")
        if updates["status"] == book.get("status"):
            updates.pop("status")
        elif await _book_in_active_swap(book_id):
            raise HTTPException(status_code=409, detail="This book is part of an active swap; cancel the swap first")
    identity_changed = any(k in updates and updates[k] != book.get(k) for k in ("title", "author", "isbn"))
    if identity_changed:
        # Aliases belonged to the old title/author/isbn: drop them, re-index, look them up again.
        updates.update({"aliases": [], "aliases_status": "pending"})
        updates.update(_search_fields({**book, **updates}))
    if updates:
        await db.books.update_one({"id": book_id}, {"$set": updates})
    if identity_changed:
        _spawn(_enrich_book(book_id))
    fresh = await db.books.find_one({"id": book_id}, {"_id": 0})
    return {"book": clean_book(fresh)}


@api.get("/books/detail/{book_id}")
async def get_book(book_id: str, user: dict = Depends(get_current_user)):
    b = await db.books.find_one({"id": book_id, "deleted_at": None}, {"_id": 0})
    if not b:
        raise HTTPException(status_code=404, detail="Book not found")
    if await _is_blocked_pair(user["user_id"], b["owner_id"]):
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


# OPENLIBRARY_URL exists so tests can point the backend at a fake OpenLibrary.
OPENLIBRARY_URL = (os.environ.get("OPENLIBRARY_URL") or "https://openlibrary.org").rstrip("/")

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
    url = f"{OPENLIBRARY_URL}/search.json"
    # OpenLibrary indexes many Latin spellings of names but finds little for a Cyrillic query, so a
    # Cyrillic query is also sent in Latin transliteration and the two result lists are merged.
    queries = [q]
    latin = search_text.transliterate(q)
    if latin and latin != search_text.normalize(q):
        queries.append(latin)

    async def fetch(query):
        params = {"q": query, "limit": 20, "fields": "title,author_name,author_alternative_name,isbn,cover_i,language"}
        try:
            async with httpx.AsyncClient(timeout=12) as hc:
                resp = await hc.get(url, params=params, headers={"User-Agent": "BookLoop/1.0"})
            return resp.json().get("docs", []) if resp.status_code == 200 else []
        except Exception:
            return []

    seen, docs = set(), []
    for d in [d for batch in await asyncio.gather(*map(fetch, queries)) for d in batch]:
        k = (search_text.normalize(d.get("title")), search_text.normalize(", ".join(d.get("author_name") or [])))
        if k not in seen:
            seen.add(k)
            docs.append(d)

    def relevance(d):
        names = (d.get("author_name") or []) + (d.get("author_alternative_name") or [])
        return search_text.score_book(q, {"title": d.get("title"), "author": "", "aliases": [{"value": n} for n in names]})

    # Our own matching first (any script/transliteration); OpenLibrary's order breaks ties and keeps
    # results it matched on fields we do not see (subjects, descriptions…).
    docs = sorted(docs, key=lambda d: -relevance(d))[:20]
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
    url = f"{OPENLIBRARY_URL}/search.json"
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
    # Ownership first: for anyone else's book the answer is always 404, never whether it is in a swap.
    if not await db.books.find_one({"id": book_id, "owner_id": user["user_id"]}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Book not found")
    if await _book_in_active_swap(book_id):
        raise HTTPException(status_code=409, detail="This book is part of an active swap; cancel the swap first")
    await db.books.update_one({"id": book_id, "owner_id": user["user_id"]}, {"$set": {"deleted_at": now_utc()}})
    return {"ok": True}


# ----------------------------------------------------------------------------
# Discover
# ----------------------------------------------------------------------------
DISCOVER_CANDIDATE_LIMIT = 5000
DISCOVER_RESULT_LIMIT = 200
# `max_distance=all` (or leaving max_distance out) means no distance limit at all. It is a distinct
# state, not a large number, so an "All" filter can never be confused with a real radius.
MAX_DISTANCE_ALL = "all"


def _parse_max_distance(raw: Optional[str]) -> Optional[float]:
    """None = no distance limit (absent, empty, or "all"); otherwise a finite, non-negative km radius."""
    if raw is None or raw.strip() == "" or raw.strip().casefold() == MAX_DISTANCE_ALL:
        return None
    try:
        km = float(raw)
    except ValueError:
        km = float("nan")
    if not math.isfinite(km) or km < 0:
        raise HTTPException(status_code=422, detail="max_distance must be a non-negative number of km or 'all'")
    return km


@api.get("/discover/people")
async def discover_people(
    search: Optional[str] = None,
    genre: Optional[str] = None,
    language: Optional[str] = None,
    exchanging: bool = False,
    max_distance: Optional[str] = None,
    cluster: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """`cluster` (an id from GET /map/clusters) scopes results to the readers of that map area. The
    reader chose the area explicitly, so the distance filter does not apply to it: an area 2,000 km
    away still lists its readers. Every other filter and eligibility rule applies as usual."""
    radius = None if cluster else _parse_max_distance(max_distance)
    q = {"deleted_at": None, "user_id": {"$nin": [user["user_id"], *await _blocked_ids(user["user_id"])]}}
    if exchanging:
        q["is_exchanging"] = True
    if genre and genre != "All":
        q["genres"] = genre
    if language and language != "All":
        q["languages"] = language
    if cluster:
        # Narrow to the area in the query itself, so the candidate cap below can never drop its readers.
        areas = await _cluster_area_filters(cluster)
        if not areas:
            return {"people": [], "top_matches": []}
        q["$or"] = areas
    text_score: dict = {}
    if search and search.strip():
        # Names match like book titles: any script, transliteration variants, typos (search_text).
        ranked = await _ranked_matches(
            db.users, q, search, {"_id": 1, "name": 1}, ["name"],
            lambda u: search_text.score_book(search, {"title": u.get("name")}), sort_newest=False,
        )
        if not ranked:
            return {"people": [], "top_matches": []}
        text_score = {oid: sc for sc, oid in ranked}
        q = {**q, "_id": {"$in": list(text_score)}}
    # Filter by distance and rank over ALL matching readers before truncating; capping the query
    # first returned an arbitrary 200 and could drop the nearest readers entirely.
    people = await db.users.find(q).to_list(DISCOVER_CANDIDATE_LIMIT)
    candidates = []
    for p in people:
        p["_text_score"] = text_score.get(p.pop("_id"), 0)
        if cluster and _cluster_id_of(p) != cluster:
            continue
        dist = user_distance_km(user, p)
        if radius is not None and dist > radius and dist != UNKNOWN_DISTANCE_KM:
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
        result.append((p["_text_score"], pu))
    # Best name match first (when searching), then genre matches, then closest.
    result.sort(key=lambda x: (-x[0], -x[1]["match_score"], x[1]["distance_km"]))
    result = [pu for _, pu in result]
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
    q = {"deleted_at": None, "status": "Available", "owner_id": {"$nin": [user["user_id"], *await _blocked_ids(user["user_id"])]}}
    if genre and genre != "All":
        q["genre"] = genre
    if language and language != "All":
        q["language"] = language
    # `q` above is the eligibility filter and stays exactly as without a search; the text match only
    # narrows and ranks what that filter already allows.
    if search and search.strip():
        ranked = await _ranked_matches(
            db.books, q, search, {"_id": 1, "title": 1, "author": 1, "aliases.value": 1}, ["title", "author"],
            lambda b: search_text.score_book(search, b),
        )
        if not ranked:  # no match, or nothing searchable left (e.g. only punctuation)
            return {"books": []}
        # Full documents only for the top results (by the indexed _id, re-applying the same filter).
        top = [oid for _, oid in ranked][:200]
        full = {b["_id"]: b for b in await db.books.find({**q, "_id": {"$in": top}}).to_list(None)}
        books = [full[oid] for oid in top if oid in full]
        for b in books:
            b.pop("_id", None)
    else:
        books = await db.books.find(q, {"_id": 0}).sort("created_at", -1).to_list(200)
    owners = {
        u["user_id"]: u
        for u in await db.users.find({"user_id": {"$in": list({b["owner_id"] for b in books})}}, {"_id": 0}).to_list(None)
    }
    out = []
    for b in books:
        owner = owners.get(b["owner_id"])
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
async def wishlist(
    # Bounded to distances that exist on Earth (≈ half its circumference): no NaN, infinity or negative radius.
    max_distance: float = Query(25.0, ge=0, le=20_040),
    user: dict = Depends(get_current_user),
):
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

    others = await db.users.find(
        {"deleted_at": None, "user_id": {"$nin": [user["user_id"], *await _blocked_ids(user["user_id"])]}}, {"_id": 0}
    ).to_list(500)
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


def _area_of(p: dict) -> Optional[tuple]:
    """(city, label) of the area a reader's place names put them in — neighborhood, else city."""
    city = p.get("city") or ""
    label = p.get("neighborhood") or city
    return (city, label) if label else None


def _cluster_area(p: dict) -> Optional[tuple]:
    """The map area a reader belongs to, or None when they have no real location or no area name
    and so are not on the map."""
    return _area_of(p) if has_location(p) else None


def _cluster_id(city: Optional[str], label: str) -> str:
    # `city or None` keeps ids identical to earlier releases, which hashed a missing city as "None".
    return hashlib.sha1(f"{city or None}|{label}".casefold().encode()).hexdigest()[:12]


async def _cluster_area_filters(cluster_id: str) -> List[dict]:
    """Mongo filters ({city, neighborhood} pairs) selecting the readers of map cluster `cluster_id`.
    Ids are hashes, so they are resolved against the distinct place names readers actually have."""
    pairs = await db.users.aggregate([
        {"$match": {"deleted_at": None, "lat": {"$ne": None}, "lng": {"$ne": None}}},
        {"$group": {"_id": {"city": "$city", "neighborhood": "$neighborhood"}}},
    ]).to_list(None)
    out = []
    for pair in pairs:
        place = {"city": pair["_id"].get("city"), "neighborhood": pair["_id"].get("neighborhood")}
        area = _area_of(place)
        if area and _cluster_id(*area) == cluster_id:
            out.append(place)
    return out


def _cluster_id_of(p: dict) -> Optional[str]:
    """The id of the map cluster this reader is counted in (same rule as GET /map/clusters)."""
    area = _cluster_area(p)
    return _cluster_id(*area) if area else None


@api.get("/map/clusters")
async def map_clusters(user: dict = Depends(get_current_user)):
    """Exchanging readers grouped by the area their device reported (neighborhood, else city).
    Readers without a real location are left off the map. Totals count readers within
    DEMAND_RADIUS_KM of the viewer when the viewer's own location is known, otherwise everyone."""
    blocked = await _blocked_ids(user["user_id"])
    people = await db.users.find({"is_exchanging": True, "deleted_at": None, "user_id": {"$nin": list(blocked)}},
                                 {"_id": 0}).to_list(5000)
    groups: dict = {}
    for p in people:
        area = _cluster_area(p)
        if not area:
            continue
        city, label = area
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
        cluster_id = _cluster_id(g["city"], g["label"])
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
# Event pushes describe the event in the title. A chat message push is the title "BookLoop" plus
# "message" as its body and never carries the message itself: lock screens, Expo and Google/Apple
# push services must not see private chat text (or whether it was a photo).
PUSH_TEXT = {
    "en": {"proposal_received": "New swap proposal from {name}", "proposal_accepted": "{name} accepted your swap",
           "proposal_declined": "{name} declined the swap", "proposal_cancelled": "{name} cancelled the swap",
           "chat_accepted": "{name} accepted your request — you can chat now", "message": "You have a new message from {name}",
           "message_anon": "You have a new message"},
    "uz": {"proposal_received": "{name}dan yangi almashuv taklifi", "proposal_accepted": "{name} almashuvingizni qabul qildi",
           "proposal_declined": "{name} almashuvni rad etdi", "proposal_cancelled": "{name} almashuvni bekor qildi",
           "chat_accepted": "{name} so‘rovingizni qabul qildi — endi yozishishingiz mumkin",
           "message": "{name}dan sizga yangi xabar keldi", "message_anon": "Sizga yangi xabar keldi"},
    "ru": {"proposal_received": "Новое предложение обмена от {name}", "proposal_accepted": "{name} принял(а) ваш обмен",
           "proposal_declined": "{name} отклонил(а) обмен", "proposal_cancelled": "{name} отменил(а) обмен",
           "chat_accepted": "{name} принял(а) ваш запрос — теперь можно переписываться",
           "message": "У вас новое сообщение от {name}", "message_anon": "У вас новое сообщение"},
    "it": {"proposal_received": "Nuova proposta di scambio da {name}", "proposal_accepted": "{name} ha accettato il tuo scambio",
           "proposal_declined": "{name} ha rifiutato lo scambio", "proposal_cancelled": "{name} ha annullato lo scambio",
           "chat_accepted": "{name} ha accettato la tua richiesta — ora potete chattare",
           "message": "Hai un nuovo messaggio da {name}", "message_anon": "Hai un nuovo messaggio"},
    "ar": {"proposal_received": "عرض تبادل جديد من {name}", "proposal_accepted": "قبل {name} التبادل",
           "proposal_declined": "رفض {name} التبادل", "proposal_cancelled": "ألغى {name} التبادل",
           "chat_accepted": "قبل {name} طلبك — يمكنكما الدردشة الآن", "message": "لديك رسالة جديدة من {name}",
           "message_anon": "لديك رسالة جديدة"},
}
EXPO_PUSH_URL = os.environ.get("EXPO_PUSH_URL") or "https://exp.host/--/api/v2/push/send"
PUSH_TOKEN_RE = re.compile(r"^(ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_\-]{8,120}\]$")


PUSH_TITLE_APP = "BookLoop"


def _push_name(name: Optional[str]) -> str:
    """The actor's name as one plain line: line breaks and runs of spaces collapse, so a name can't add
    fake lines to a notification. (The push is JSON, so nothing else needs escaping; the name is a
    format() argument, so braces in it are never interpreted.)"""
    return " ".join((name or "").split())[:NAME_MAX_CHARS]


def _push_text(lang: Optional[str], kind: str, name: str) -> str:
    table = PUSH_TEXT.get(lang or "en") or PUSH_TEXT["en"]
    name = _push_name(name)
    if kind == "message" and not name:
        kind = "message_anon"
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


async def _push(user_id: str, kind: str, actor_name: str, data: dict) -> None:
    """Push to the user's registered devices — only if they turned Notifications on. Texts are in the
    RECIPIENT's preferred language. `data` carries only what the app needs to open the conversation
    (swap id, type, in-app route), never content."""
    u = await db.users.find_one({"user_id": user_id, "deleted_at": None},
                                {"_id": 0, "notifications_enabled": 1, "preferred_language": 1})
    if not u or not u.get("notifications_enabled"):
        return
    tokens = await db.push_tokens.find({"user_id": user_id}, {"_id": 0, "token": 1}).to_list(20)
    if not tokens:
        return
    text = _push_text(u.get("preferred_language"), kind, actor_name)
    title, body = (PUSH_TITLE_APP, text) if kind == "message" else (text, "")
    msgs = [{"to": t["token"], "title": title, "body": body, "sound": "default", "data": data,
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
    if not receiver or await _is_blocked_pair(uid, body.receiver_id):
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
        "can_propose": bool(other) and user_id != uid and other.get("is_exchanging", True)
        and not await _is_blocked_pair(uid, user_id),
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
    await _require_not_blocked(s, user["user_id"])
    state = await _chat_state(s, user["user_id"])
    if state == CHAT_CLOSED:
        _require_status(s, *ACCEPTED_STATUSES, "pending")  # -> 409 "Swap is declined; action not allowed"
    if state in (CHAT_LOCKED_WAITING, CHAT_LOCKED_NEEDS_YOU):
        raise _coded(403, "chat_locked", "Chat opens once the other reader accepts the request")
    if not body.text.strip() and not body.image_url:
        raise HTTPException(status_code=400, detail="Empty message")
    image_url = checked_image_url(body.image_url, user["user_id"]) if body.image_url else None
    if body.client_id:
        prior = await db.messages.find_one(
            {"swap_id": swap_id, "sender_id": user["user_id"], "client_id": body.client_id}, {"_id": 0}
        )
        if prior:  # the same send retried: return the original, never store it twice
            prior["created_at"] = prior["created_at"].isoformat()
            return {"message": prior, "duplicate": True}
    await _rate_limit(f"message:{user['user_id']}", 30, timedelta(minutes=1))
    mtype = "image" if image_url else "text"
    msg = await _add_message(swap_id, user["user_id"], mtype, body.text, image_url=image_url, client_id=body.client_id)
    other_id = _other_id(s, user["user_id"])
    # Private: the push names the sender only — no message text, preview or photo hint (see PUSH_TEXT).
    await _push(other_id, "message", user.get("name", ""), {"swap_id": swap_id, "type": "message", "url": f"/swap/{swap_id}"})
    msg["created_at"] = msg["created_at"].isoformat()
    return {"message": msg}


@api.post("/swaps/{swap_id}/propose")
async def propose(swap_id: str, body: ProposeBody, user: dict = Depends(get_current_user)):
    s = await _participant_swap(swap_id, user)
    await _require_not_blocked(s, user["user_id"])
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
    await _require_not_blocked(s, user["user_id"])
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
    await _require_not_blocked(s, user["user_id"])
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
    blocked = await _blocked_ids(user["user_id"])
    ids = [f["target_id"] for f in favs if f["target_id"] not in blocked]
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
    if await _is_blocked_pair(user["user_id"], target_id) or not await db.users.find_one(
            {"user_id": target_id, "deleted_at": None}, {"_id": 1}):
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
# Blocking. Enforced here, never only in the app: a block hides each reader from the other
# everywhere (Discover, search, Map, profiles, books, favorites) and stops every interaction
# (new swaps, proposals, accepting, chat). Past conversations stay readable as history.
# ----------------------------------------------------------------------------
async def _blocked_ids(uid: str) -> set:
    """Readers `uid` blocked, plus readers who blocked `uid` (both directions hide)."""
    out = set()
    async for b in db.blocks.find({"$or": [{"blocker_id": uid}, {"blocked_id": uid}]}, {"_id": 0}):
        out.add(b["blocked_id"] if b["blocker_id"] == uid else b["blocker_id"])
    return out


async def _is_blocked_pair(a: str, b: str) -> bool:
    return bool(await db.blocks.find_one(
        {"$or": [{"blocker_id": a, "blocked_id": b}, {"blocker_id": b, "blocked_id": a}]}, {"_id": 1}))


async def _require_not_blocked(s: dict, uid: str) -> None:
    if await _is_blocked_pair(uid, _other_id(s, uid)):
        raise _coded(403, "blocked", "You can't interact with this reader")


async def _close_open_swaps(a: str, b: Optional[str], reason_key: str, reason_text: str) -> None:
    """Cancel the open swaps of `a` (with `b` only, when given), releasing books they reserved."""
    pair = _pair_query(a, b) if b else {"$or": [{"requester_id": a}, {"receiver_id": a}]}
    async for s in db.swaps.find({"$and": [pair, {"status": {"$in": ["pending", "accepted", "active"]}}]}, {"_id": 0}):
        res = await db.swaps.update_one({"id": s["id"], "status": s["status"]}, {"$set": {"status": "cancelled"}})
        if res.modified_count and s["status"] in ("accepted", "active"):
            await _set_proposal_books(s.get("active_proposal"), "Reserved", "Available")
        if res.modified_count:
            await _add_message(s["id"], None, "system", reason_text, key=reason_key, params={})


@api.get("/blocks")
async def list_blocks(user: dict = Depends(get_current_user)):
    """The readers I blocked (so I can unblock them). Who blocked me is never revealed."""
    rows = await db.blocks.find({"blocker_id": user["user_id"]}, {"_id": 0}).sort("created_at", -1).to_list(500)
    people = {u["user_id"]: u for u in await db.users.find(
        {"user_id": {"$in": [r["blocked_id"] for r in rows]}}, {"_id": 0}).to_list(None)}
    out = []
    for r in rows:
        u = people.get(r["blocked_id"])
        if u:
            pu = public_user(u)
            out.append({"user_id": pu["user_id"], "name": pu["name"], "avatar_url": pu["avatar_url"],
                        "is_deleted": bool(pu.get("is_deleted")), "blocked_at": r["created_at"].isoformat()})
    return {"blocked": out}


@api.put("/blocks/{target_id}")
async def block_user(target_id: str, user: dict = Depends(get_current_user)):
    uid = user["user_id"]
    if target_id == uid:
        raise _coded(400, "cannot_block_self", "You can't block yourself")
    if not await db.users.find_one({"user_id": target_id}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="User not found")
    await _rate_limit(f"block:{uid}", 100, timedelta(days=1))
    try:
        await db.blocks.update_one({"blocker_id": uid, "blocked_id": target_id},
                                   {"$setOnInsert": {"created_at": now_utc()}}, upsert=True)
    except DuplicateKeyError:
        pass
    await db.favorites.delete_many({"$or": [{"user_id": uid, "target_id": target_id}, {"user_id": target_id, "target_id": uid}]})
    # The message names no one: it must not tell the other reader that they were blocked.
    await _close_open_swaps(uid, target_id, "swapChat.sys.closed", "This swap was closed.")
    return {"ok": True, "blocked": True}


@api.delete("/blocks/{target_id}")
async def unblock_user(target_id: str, user: dict = Depends(get_current_user)):
    await db.blocks.delete_one({"blocker_id": user["user_id"], "blocked_id": target_id})
    return {"ok": True, "blocked": False}


# ----------------------------------------------------------------------------
# Reports: any reader can report a reader, a book listing, a review, a chat message or a swap
# they can see. Reports are stored for moderation with a snapshot of the reported content (so it
# can be reviewed even if it is later edited or deleted). There is no moderation UI yet: see
# backend/scripts/moderation.py.
# ----------------------------------------------------------------------------
REPORT_REASONS = ("spam", "harassment", "inappropriate", "fraud", "safety", "other")


class ReportBody(BaseModel):
    target_type: Literal["user", "book", "review", "message", "swap"]
    target_id: str = Field(min_length=1, max_length=64)
    reason: Literal["spam", "harassment", "inappropriate", "fraud", "safety", "other"]
    details: Optional[str] = Field(default=None, max_length=1000)


async def _report_target(t: str, tid: str, uid: str) -> Optional[tuple]:
    """(responsible user id, snapshot) for a target the reporter can see and did not create."""
    if t == "user":
        u = await db.users.find_one({"user_id": tid}, {"_id": 0})
        if u and tid != uid:
            return tid, {"name": u.get("name", ""), "bio": u.get("bio", ""), "avatar_url": u.get("avatar_url")}
    elif t == "book":
        b = await db.books.find_one({"id": tid}, {"_id": 0})
        if b and b["owner_id"] != uid:
            return b["owner_id"], {k: b.get(k) for k in ("title", "author", "cover_url")}
    elif t == "review":
        r = await db.ratings.find_one({"id": tid}, {"_id": 0})
        if r and r.get("rater_id") != uid:
            return r.get("rater_id"), {"stars": r.get("stars"), "review": r.get("review", ""), "ratee_id": r.get("ratee_id")}
    elif t == "message":
        m = await db.messages.find_one({"id": tid}, {"_id": 0})
        s = m and await db.swaps.find_one({"id": m["swap_id"]}, {"_id": 0})
        if s and uid in (s["requester_id"], s["receiver_id"]) and m.get("sender_id") and m["sender_id"] != uid:
            return m["sender_id"], {"text": m.get("text", ""), "image_url": m.get("image_url"), "swap_id": m["swap_id"]}
    elif t == "swap":
        s = await db.swaps.find_one({"id": tid}, {"_id": 0})
        if s and uid in (s["requester_id"], s["receiver_id"]):
            return _other_id(s, uid), {"status": s.get("status"), "note": s.get("note")}
    return None


@api.post("/reports")
async def create_report(body: ReportBody, user: dict = Depends(get_current_user)):
    uid = user["user_id"]
    target = await _report_target(body.target_type, body.target_id, uid)
    if not target:
        raise HTTPException(status_code=404, detail="Not found")
    existing = await db.reports.find_one(
        {"reporter_id": uid, "target_type": body.target_type, "target_id": body.target_id, "status": "open"}, {"_id": 0, "id": 1})
    if existing:  # reporting the same thing twice just confirms the open report
        return {"ok": True, "report_id": existing["id"]}
    await _rate_limit(f"report:{uid}", 20, timedelta(days=1))
    responsible, snapshot = target
    report = {
        "id": new_id("rep"), "reporter_id": uid, "target_type": body.target_type, "target_id": body.target_id,
        "target_user_id": responsible, "reason": body.reason, "details": clean_note_text(body.details, 1000),
        "snapshot": snapshot, "status": "open", "created_at": now_utc(),
    }
    await db.reports.insert_one(dict(report))
    return {"ok": True, "report_id": report["id"]}


# ----------------------------------------------------------------------------
# Account deletion (Profile -> Account -> Delete account). Everything that identifies the person is
# removed; records that also belong to someone else (their side of a swap conversation, a review they
# received from this person) are kept without identity. Irreversible.
# ----------------------------------------------------------------------------
DELETED_LABEL_EN = "A former BookLoop member"


class DeleteAccountBody(BaseModel):
    # Must be exactly true: a deliberate, confirmed request, never a side effect of another call.
    confirm: bool


async def delete_account(uid: str) -> dict:
    """Erase a reader's account. Idempotent. Returns counts of what was removed (for logs/tests)."""
    u = await db.users.find_one({"user_id": uid}, {"_id": 0})
    if not u:
        return {}
    name, email = u.get("name") or "", (u.get("email") or "").lower()
    stats: dict = {}

    # Open swaps end (books the other reader had reserved for them become available again).
    await _close_open_swaps(uid, None, "swapChat.sys.accountDeleted", "This reader deleted their account.")

    # Their name inside the system lines of conversations ("X cancelled the swap"…), for both sides.
    swap_ids = [s["id"] async for s in db.swaps.find(
        {"$or": [{"requester_id": uid}, {"receiver_id": uid}]}, {"_id": 0, "id": 1})]
    if name:
        async for m in db.messages.find({"swap_id": {"$in": swap_ids}, "params.name": name}, {"_id": 1, "text": 1}):
            await db.messages.update_one({"_id": m["_id"]}, {"$set": {
                "params.name": "", "params.deleted": True, "text": (m.get("text") or "").replace(name, DELETED_LABEL_EN)}})
        async for sw in db.swaps.find({"id": {"$in": swap_ids}, "last_message": {"$regex": re.escape(name)}},
                                      {"_id": 1, "last_message": 1}):
            await db.swaps.update_one({"_id": sw["_id"]}, {"$set": {
                "last_message": sw["last_message"].replace(name, DELETED_LABEL_EN),
                "last_message_params.name": "", "last_message_params.deleted": True}})
    # Their uploaded photos (avatar, covers, chat photos) are deleted from storage; chat photos they sent
    # become "photo removed" in the other reader's history.
    uploads = await db.uploads.find({"owner_id": uid}, {"_id": 0, "path": 1}).to_list(None)
    for up in uploads:
        try:
            await run_in_threadpool(delete_object, up["path"])
        except OSError:
            pass
    stats["uploads"] = len(uploads)
    await db.uploads.delete_many({"owner_id": uid})
    await db.messages.update_many({"sender_id": uid, "image_url": {"$ne": None}},
                                  {"$set": {"image_url": None, "image_removed": True}})
    # Books leave Discover; their titles stay only inside past proposals (they describe the swap).
    stats["books"] = (await db.books.update_many(
        {"owner_id": uid}, {"$set": {"deleted_at": now_utc(), "cover_url": None, "aliases": [], "search_tokens": []}})).modified_count
    # Reviews: the ones about them go; the ones they wrote stay for the reviewed reader, without author.
    stats["reviews_received"] = (await db.ratings.delete_many({"ratee_id": uid})).deleted_count
    await db.ratings.update_many({"rater_id": uid}, {"$set": {"rater_id": None, "rater_deleted": True}})
    stats["favorites"] = (await db.favorites.delete_many({"$or": [{"user_id": uid}, {"target_id": uid}]})).deleted_count
    await db.blocks.delete_many({"$or": [{"blocker_id": uid}, {"blocked_id": uid}]})
    await db.notifications.delete_many({"user_id": uid})
    await db.notifications.update_many({"actor_id": uid}, {"$set": {"actor_id": None}})
    await db.reports.update_many({"reporter_id": uid}, {"$set": {"reporter_id": None}})
    await db.push_tokens.delete_many({"user_id": uid})
    await db.user_sessions.delete_many({"user_id": uid})
    await db.auth_identities.delete_many({"user_id": uid})
    await db.google_logins.delete_many({"user_id": uid})
    await db.google_link_tickets.delete_many({"user_id": uid})
    await db.oauth_states.delete_many({"link_user_id": uid})
    await db.security_events.delete_many({"user_id": uid})
    if email:
        await db.pending_registrations.delete_many({"email": email})
        await db.email_verifications.delete_many({"email": email})
        await db.rate_events.delete_many({"key": {"$regex": "^login-fail:" + re.escape(email) + "$"}})
    # The account record stays only as a tombstone (so old swaps still resolve to "deleted reader").
    await db.users.update_one({"user_id": uid}, {"$set": {
        "deleted_at": u.get("deleted_at") or now_utc(), "email": f"deleted+{uid}@deleted.invalid",
        "name": "", "bio": "", "avatar_url": None, "password_hash": None, "city": None, "neighborhood": None,
        "country": None, "lat": None, "lng": None, "location_updated_at": None, "genres": [], "languages": [],
        "reading_interests": [], "is_exchanging": False, "preferred_language": None, "notifications_enabled": False,
        "search_tokens": [], "rating": 0.0, "rating_count": 0,
    }, "$unset": {"seed": "", "test_account": "", "google_sub": "", "apple_sub": "", "client_version": "",
               "client_platform": "", "client_seen_at": "", "google_signin_via": "", "google_signin_at": ""}})
    return stats


@api.post("/users/me/delete")
async def delete_me(body: DeleteAccountBody, user: dict = Depends(get_current_user)):
    if body.confirm is not True:
        raise HTTPException(status_code=400, detail="Confirm the deletion")
    await delete_account(user["user_id"])
    return {"ok": True, "deleted": True}


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
        raise HTTPException(status_code=400, detail="Unsupported file type; upload a JPEG, PNG or WebP image")
    # Type/extension come from the file's actual bytes, not the client-supplied headers.
    ext, ctype = kind
    await _rate_limit(f"upload:{user['user_id']}", 60, timedelta(hours=1))
    # Photos carry EXIF (GPS position, device, time) that must never reach other readers. HEIC's
    # metadata cannot be removed safely here, so it is refused (the app always sends JPEG).
    try:
        data = strip_metadata(data, ext)
    except UnsupportedImage:
        raise _coded(400, "unsupported_image", "Please upload a JPEG, PNG or WebP image")
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

# Request bodies: FastAPI reads and parses a whole body before any endpoint code runs, so without a cap any
# client could make the server buffer (or spool to disk) as much as it sends. JSON bodies here are a few KB;
# an upload is one image of at most MAX_UPLOAD_BYTES plus its multipart framing.
MAX_BODY_BYTES = 1024 * 1024
MAX_UPLOAD_BODY_BYTES = MAX_UPLOAD_BYTES + 64 * 1024
BODY_TOO_LARGE = "Request body too large"


class _BodyTooLarge(HTTPException):
    def __init__(self):
        super().__init__(status_code=413, detail=BODY_TOO_LARGE)


class BodySizeLimitMiddleware:
    """413 for a body over the limit: up front from Content-Length, or as soon as a streamed (chunked)
    body grows past it — before the rest of it is read."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = MAX_UPLOAD_BODY_BYTES if scope["path"] == "/api/upload" else MAX_BODY_BYTES
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            return await JSONResponse({"detail": BODY_TOO_LARGE}, status_code=413)(scope, receive, send)
        received = 0
        started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge()  # an HTTPException: the endpoint's error handling turns it into a 413
            return message

        async def tracking_send(message):
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if started:
                raise
            await JSONResponse({"detail": BODY_TOO_LARGE}, status_code=413)(scope, receive, send)


app.add_middleware(BodySizeLimitMiddleware)
# Added last = outermost, so error responses (413 included) carry CORS headers too. Sessions are bearer
# tokens the app attaches itself — never cookies — so no request needs browser credentials, and with
# allow_credentials off a wildcard origin is never turned into an echo of the caller's Origin.
app.add_middleware(
    CORSMiddleware,
    allow_credentials=False,
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
    await db.google_logins.create_index("created_at", expireAfterSeconds=int(GOOGLE_LOGIN_TTL.total_seconds()))
    await db.google_link_tickets.create_index("lid", unique=True)
    await db.google_link_tickets.create_index("user_id")
    await db.google_link_tickets.create_index("created_at", expireAfterSeconds=int(GOOGLE_LINK_TTL.total_seconds()))
    # v2.2 provider identities (additive; legacy Google accounts get theirs on their next Google sign-in).
    await db.auth_identities.create_index([("provider", 1), ("subject", 1)], unique=True)
    await db.auth_identities.create_index([("user_id", 1), ("provider", 1)], unique=True)
    await db.apple_nonces.create_index("id", unique=True)
    await db.apple_nonces.create_index("created_at", expireAfterSeconds=600)
    await db.google_nonces.create_index("id", unique=True)
    await db.google_nonces.create_index("created_at", expireAfterSeconds=int(GOOGLE_NONCE_TTL.total_seconds()))
    await db.security_events.create_index("user_id")
    await db.security_events.create_index("created_at", expireAfterSeconds=int(SECURITY_EVENT_TTL.total_seconds()))
    # Registration OTP: pending registrations self-clean after a day of inactivity (a user who never
    # finishes signing up); email verification tokens are single-use and expire fast either way, but
    # the TTL index also clears any left stranded by a crash between verify-otp and complete.
    await db.pending_registrations.create_index("email", unique=True)
    await db.pending_registrations.create_index("created_at", expireAfterSeconds=86400)
    await db.email_verifications.create_index("token", unique=True)
    await db.email_verifications.create_index("created_at", expireAfterSeconds=EMAIL_VERIFICATION_TTL_MINUTES * 60)
    await db.books.create_index("owner_id")
    # Every by-id book read/update (detail, edit, swaps). Not unique: an existing duplicate must not
    # be able to stop the server from starting.
    await db.books.create_index("id")
    await db.users.create_index("search_tokens")
    await db.blocks.create_index([("blocker_id", 1), ("blocked_id", 1)], unique=True)
    await db.blocks.create_index("blocked_id")
    await db.reports.create_index([("status", 1), ("created_at", -1)])
    await db.reports.create_index([("reporter_id", 1), ("target_type", 1), ("target_id", 1)])
    await db.rate_events.create_index([("key", 1), ("at", -1)])
    await db.rate_events.create_index("at", expireAfterSeconds=2 * 24 * 3600)
    await db.uploads.create_index("path")
    await db.uploads.create_index("owner_id")
    await db.metadata_cache.create_index("key", unique=True)
    await db.metadata_cache.create_index("fetched_at", expireAfterSeconds=int(METADATA_CACHE_TTL.total_seconds()))
    await db.users.create_index("search_v")
    # Book search: anchored-prefix lookups on normalized words/keys (multikey), and the alias backlog.
    await db.books.create_index("search_tokens")
    # Also serves the legacy branch of a search ({search_v: {$exists: false}}), which would otherwise
    # turn every search into a collection scan, and the versioned backfill's stale-book lookup.
    await db.books.create_index("search_v")
    await db.books.create_index([("aliases_status", 1), ("created_at", -1)])
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
        # The demo community (10 made-up readers with swaps and reviews) is for development and
        # screenshots only — never mixed into a real community. Off unless SEED_DEMO_COMMUNITY=1.
        if SEED_DEMO_COMMUNITY:
            await sync_demo_community()
        await backfill_onboarding_state()
    except Exception as e:
        logger.warning(f"Seed failed: {e}")
    try:
        await coarsen_stored_locations()
    except Exception as e:
        logger.warning(f"Location precision migration failed: {e}")
    # In the background: startup is not delayed, and books not reached yet are still found by the
    # legacy plain-text branch of the search.
    _spawn(_backfill_search())
    _spawn(_cleanup_uploads_quietly())


async def _cleanup_uploads_quietly() -> None:
    try:
        n = await cleanup_orphan_uploads()
        if n:
            logger.info(f"Removed {n} unused uploads")
    except Exception as e:
        logger.warning(f"Upload cleanup failed: {e}")


async def _backfill_search() -> None:
    try:
        n = await backfill_search_tokens()
        if n:
            logger.info(f"Search tokens built for {n} books/readers")
    except Exception as e:
        logger.warning(f"Search token backfill failed: {e}")
    await backfill_aliases()


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
SEED_DEMO_COMMUNITY = (os.environ.get("SEED_DEMO_COMMUNITY") or "").strip().lower() in ("1", "true", "yes")


async def remove_demo_community() -> int:
    """Delete the demo readers (scripts/remove_demo_community.py): their own demo swaps/reviews go
    entirely; anything real users did with them is handled like any other account deletion."""
    demo = await db.users.find({"seed": True, "email": {"$regex": re.escape(DEMO_EMAIL_DOMAIN) + "$"},
                                "password_hash": None}, {"_id": 0, "user_id": 1}).to_list(None)
    ids = [d["user_id"] for d in demo]
    if not ids:
        return 0
    seed_swaps = [s["id"] async for s in db.swaps.find({"seed": True}, {"_id": 0, "id": 1})]
    await db.messages.delete_many({"swap_id": {"$in": seed_swaps}})
    await db.swaps.delete_many({"seed": True})
    await db.ratings.delete_many({"seed": True})
    await db.books.delete_many({"seed": True})
    for uid in ids:
        await delete_account(uid)
    return len(ids)


async def coarsen_stored_locations() -> None:
    """One-time: round coordinates stored before LOCATION_STORE_DECIMALS was lowered (4 -> 3)."""
    marker = "location_precision_3"
    if await db.app_settings.find_one({"_id": marker}):
        return
    d = LOCATION_STORE_DECIMALS
    await db.users.update_many({"lat": {"$type": "number"}, "lng": {"$type": "number"}},
                               [{"$set": {"lat": {"$round": ["$lat", d]}, "lng": {"$round": ["$lng", d]}}}])
    await db.app_settings.update_one({"_id": marker}, {"$set": {"done_at": now_utc()}}, upsert=True)


ORPHAN_UPLOAD_AGE = timedelta(days=1)


async def cleanup_orphan_uploads() -> int:
    """Delete uploads nobody uses a day after they were made (a photo picked and then abandoned)."""
    cutoff = now_utc() - ORPHAN_UPLOAD_AGE
    candidates = await db.uploads.find({"created_at": {"$lt": cutoff}}, {"_id": 0, "path": 1}).to_list(None)
    if not candidates:
        return 0
    used = set()
    pattern = {"$regex": "^" + re.escape(FILE_PREFIX)}
    for coll, field in ((db.users, "avatar_url"), (db.books, "cover_url"), (db.messages, "image_url")):
        async for d in coll.find({field: pattern}, {"_id": 0, field: 1}):
            used.add(_url_base(d[field])[len(FILE_PREFIX):])
    removed = 0
    for c in candidates:
        if c["path"] in used:
            continue
        try:
            await run_in_threadpool(delete_object, c["path"])
        except OSError:
            continue
        await db.uploads.delete_one({"path": c["path"]})
        removed += 1
    return removed


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
            "reading_interests": d["interests"], "is_exchanging": True, **_name_search_fields(d["name"]),
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
                    "cover_url": cover(isbn), "condition": cond, "language": lang, "genre": genre, "isbn": isbn,
                    "status": "Available", "created_at": now_utc(), "deleted_at": None, "seed": True,
                    **_search_fields({"title": title, "author": author}),
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
