import os
import uuid
import math
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import bcrypt
import httpx
import requests
from fastapi import FastAPI, APIRouter, Depends, HTTPException, Header, UploadFile, File, Query
from fastapi.responses import Response
from fastapi.concurrency import run_in_threadpool
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

# ----------------------------------------------------------------------------
# Config / DB
# ----------------------------------------------------------------------------
mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

EMERGENT_KEY = os.environ.get("EMERGENT_LLM_KEY")
EMERGENT_AUTH_URL = "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data"

STORAGE_BASE = (os.environ.get("INTEGRATION_PROXY_URL") or "").strip() or "https://integrations.emergentagent.com"
STORAGE_URL = STORAGE_BASE.rstrip("/") + "/objstore/api/v1/storage"
APP_NAME = "bookloop"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("bookloop")

app = FastAPI()
api = APIRouter(prefix="/api")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


# ----------------------------------------------------------------------------
# Object storage helpers (sync -> run_in_threadpool)
# ----------------------------------------------------------------------------
_storage_key = None


def init_storage():
    global _storage_key
    if _storage_key:
        return _storage_key
    resp = requests.post(f"{STORAGE_URL}/init", json={"emergent_key": EMERGENT_KEY}, timeout=30)
    resp.raise_for_status()
    _storage_key = resp.json()["storage_key"]
    return _storage_key


def put_object(path: str, data: bytes, content_type: str) -> dict:
    global _storage_key
    key = init_storage()
    resp = requests.put(
        f"{STORAGE_URL}/objects/{path}",
        headers={"X-Storage-Key": key, "Content-Type": content_type},
        data=data,
        timeout=120,
    )
    if resp.status_code == 503:
        _storage_key = None
        key = init_storage()
        resp = requests.put(
            f"{STORAGE_URL}/objects/{path}",
            headers={"X-Storage-Key": key, "Content-Type": content_type},
            data=data,
            timeout=120,
        )
    resp.raise_for_status()
    return resp.json()


def get_object(path: str):
    key = init_storage()
    resp = requests.get(f"{STORAGE_URL}/objects/{path}", headers={"X-Storage-Key": key}, timeout=60)
    resp.raise_for_status()
    return resp.content, resp.headers.get("Content-Type", "application/octet-stream")


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


# ----------------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------------
class RegisterBody(BaseModel):
    email: EmailStr
    password: str
    name: str


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class SessionBody(BaseModel):
    session_id: str


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    bio: Optional[str] = None
    city: Optional[str] = None
    neighborhood: Optional[str] = None
    avatar_url: Optional[str] = None
    genres: Optional[List[str]] = None
    languages: Optional[List[str]] = None
    is_exchanging: Optional[bool] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


class BookBody(BaseModel):
    title: str
    author: str = ""
    cover_url: Optional[str] = None
    condition: str = "Good"
    language: str = "English"
    genre: str = "Fiction"
    status: str = "Available"
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


class RateBody(BaseModel):
    stars: int
    review: Optional[str] = None


# ----------------------------------------------------------------------------
# Serialization helpers
# ----------------------------------------------------------------------------
def public_user(u: dict) -> dict:
    if not u:
        return {}
    return {
        "user_id": u["user_id"],
        "name": u.get("name", ""),
        "email": u.get("email", ""),
        "avatar_url": u.get("avatar_url"),
        "bio": u.get("bio", ""),
        "city": u.get("city", "Messina"),
        "neighborhood": u.get("neighborhood", "Centro"),
        "lat": u.get("lat"),
        "lng": u.get("lng"),
        "genres": u.get("genres", []),
        "languages": u.get("languages", []),
        "is_exchanging": u.get("is_exchanging", True),
        "rating": round(u.get("rating", 0.0), 1),
        "rating_count": u.get("rating_count", 0),
        "swaps_count": u.get("swaps_count", 0),
    }


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


@api.post("/auth/register")
async def register(body: RegisterBody):
    existing = await db.users.find_one({"email": body.email.lower()})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    uid = new_id("user")
    user = {
        "user_id": uid,
        "email": body.email.lower(),
        "password_hash": hash_pw(body.password),
        "name": body.name,
        "avatar_url": None,
        "bio": "",
        "city": "Messina",
        "neighborhood": "Centro",
        "lat": 38.1938,
        "lng": 15.5540,
        "genres": [],
        "languages": ["Italian", "English"],
        "is_exchanging": True,
        "rating": 0.0,
        "rating_count": 0,
        "swaps_count": 0,
        "created_at": now_utc(),
        "deleted_at": None,
    }
    await db.users.insert_one(user)
    token = await create_session(uid)
    return {"session_token": token, "user": public_user(user)}


@api.post("/auth/login")
async def login(body: LoginBody):
    user = await db.users.find_one({"email": body.email.lower(), "deleted_at": None})
    if not user or not user.get("password_hash") or not verify_pw(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = await create_session(user["user_id"])
    return {"session_token": token, "user": public_user(user)}


@api.post("/auth/session")
async def google_session(body: SessionBody):
    async with httpx.AsyncClient(timeout=30) as hc:
        resp = await hc.get(EMERGENT_AUTH_URL, headers={"X-Session-ID": body.session_id})
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid session")
    data = resp.json()
    email = (data.get("email") or "").lower()
    name = data.get("name") or email.split("@")[0]
    picture = data.get("picture")
    user = await db.users.find_one({"email": email})
    if user:
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
            "city": "Messina",
            "neighborhood": "Centro",
            "lat": 38.1938,
            "lng": 15.5540,
            "genres": [],
            "languages": ["Italian", "English"],
            "is_exchanging": True,
            "rating": 0.0,
            "rating_count": 0,
            "swaps_count": 0,
            "created_at": now_utc(),
            "deleted_at": None,
        }
        await db.users.insert_one(user)
    token = await create_session(uid)
    return {"session_token": token, "user": public_user(user)}


@api.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return {"user": public_user(user)}


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
    if updates:
        await db.users.update_one({"user_id": user["user_id"]}, {"$set": updates})
    fresh = await db.users.find_one({"user_id": user["user_id"]}, {"_id": 0})
    return {"user": public_user(fresh)}


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
    pu = public_user(u)
    pu["distance_km"] = haversine_km(user.get("lat"), user.get("lng"), u.get("lat"), u.get("lng"))
    return {"user": pu, "books": await _books_for(user_id), "reviews": ratings}


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


@api.put("/books/{book_id}")
async def update_book(book_id: str, body: BookBody, user: dict = Depends(get_current_user)):
    book = await db.books.find_one({"id": book_id, "owner_id": user["user_id"], "deleted_at": None})
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    updates = body.model_dump()
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
        ownerp["distance_km"] = haversine_km(user.get("lat"), user.get("lng"), owner.get("lat"), owner.get("lng"))
    return {"book": clean_book(b), "owner": ownerp, "is_owner": b["owner_id"] == user["user_id"]}


LANG_CODE_MAP = {
    "eng": "English", "ita": "Italian", "spa": "Spanish", "fre": "French",
    "fra": "French", "ger": "German", "deu": "German", "ara": "Arabic", "por": "Portuguese",
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
    res = await db.books.update_one(
        {"id": book_id, "owner_id": user["user_id"]}, {"$set": {"deleted_at": now_utc()}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Book not found")
    return {"ok": True}


# ----------------------------------------------------------------------------
# Discover
# ----------------------------------------------------------------------------
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
        q["name"] = {"$regex": search, "$options": "i"}
    people = await db.users.find(q, {"_id": 0}).to_list(200)
    result = []
    for p in people:
        pu = public_user(p)
        dist = haversine_km(user.get("lat"), user.get("lng"), p.get("lat"), p.get("lng"))
        if max_distance is not None and dist > max_distance and dist != 999.0:
            continue
        pu["distance_km"] = dist
        pu["books"] = (await _books_for(p["user_id"], only_available=True))[:6]
        pu["available_count"] = len(pu["books"])
        result.append(pu)
    result.sort(key=lambda x: x["distance_km"])
    return {"people": result}


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
        q["$or"] = [
            {"title": {"$regex": search, "$options": "i"}},
            {"author": {"$regex": search, "$options": "i"}},
        ]
    books = await db.books.find(q, {"_id": 0}).sort("created_at", -1).to_list(200)
    out = []
    for b in books:
        owner = await db.users.find_one({"user_id": b["owner_id"]}, {"_id": 0})
        cb = clean_book(b)
        cb["owner_name"] = owner.get("name") if owner else ""
        cb["owner_avatar"] = owner.get("avatar_url") if owner else None
        cb["distance_km"] = haversine_km(
            user.get("lat"), user.get("lng"), owner.get("lat") if owner else None, owner.get("lng") if owner else None
        )
        out.append(cb)
    return {"books": out}


GENRE_LIST = ["Fiction", "Psychology", "Business", "History", "Biography", "Self-development", "Romance", "Fantasy", "Philosophy", "Science"]


# ----------------------------------------------------------------------------
# Map
# ----------------------------------------------------------------------------
NEIGHBORHOODS = {
    "Centro": (38.1938, 15.5540),
    "University Area": (38.2490, 15.5560),
    "Annunziata": (38.2470, 15.5470),
    "Giostra": (38.2100, 15.5470),
    "Tremestieri": (38.1200, 15.5200),
    "Provinciale": (38.1850, 15.5450),
}


@api.get("/map/clusters")
async def map_clusters(user: dict = Depends(get_current_user)):
    clusters = []
    total_people = 0
    total_books = 0
    for name, (lat, lng) in NEIGHBORHOODS.items():
        people = await db.users.find(
            {"neighborhood": name, "is_exchanging": True, "deleted_at": None}, {"_id": 0}
        ).to_list(500)
        pcount = len(people)
        ids = [p["user_id"] for p in people]
        bcount = await db.books.count_documents(
            {"owner_id": {"$in": ids}, "status": "Available", "deleted_at": None}
        ) if ids else 0
        genre_counts = {}
        for p in people:
            for g in p.get("genres", []):
                genre_counts[g] = genre_counts.get(g, 0) + 1
        top = sorted(genre_counts.items(), key=lambda x: -x[1])[:4]
        clusters.append(
            {
                "neighborhood": name,
                "lat": lat,
                "lng": lng,
                "people_count": pcount,
                "books_count": bcount,
                "top_genres": [g for g, _ in top],
            }
        )
        total_people += pcount
        total_books += bcount
    clusters.sort(key=lambda c: -c["people_count"])
    return {"clusters": clusters, "total_active": total_people, "total_books": total_books}


# ----------------------------------------------------------------------------
# Swaps
# ----------------------------------------------------------------------------
async def _add_message(swap_id: str, sender_id: Optional[str], mtype: str, text: str = "", proposal: dict = None, image_url: str = None):
    msg = {
        "id": new_id("msg"),
        "swap_id": swap_id,
        "sender_id": sender_id,
        "type": mtype,
        "text": text,
        "proposal": proposal,
        "image_url": image_url,
        "created_at": now_utc(),
    }
    await db.messages.insert_one(dict(msg))
    label = "📷 Photo" if mtype == "image" else (text or mtype)
    await db.swaps.update_one({"id": swap_id}, {"$set": {"updated_at": now_utc(), "last_message": label}})
    return msg


def clean_swap(s: dict) -> dict:
    return {
        "id": s["id"],
        "requester_id": s["requester_id"],
        "receiver_id": s["receiver_id"],
        "status": s["status"],
        "last_message": s.get("last_message", ""),
        "requester_completed": s.get("requester_completed", False),
        "receiver_completed": s.get("receiver_completed", False),
        "requester_rated": s.get("requester_rated", False),
        "receiver_rated": s.get("receiver_rated", False),
        "active_proposal": s.get("active_proposal"),
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
    await _add_message(swap["id"], None, "system", f"{user['name']} is interested in exchanging books.")
    if body.message:
        await _add_message(swap["id"], user["user_id"], "text", body.message)
    return {"swap": await _swap_with_meta(swap, user["user_id"]), "existing": False}


@api.get("/swaps")
async def list_swaps(user: dict = Depends(get_current_user)):
    uid = user["user_id"]
    swaps = await db.swaps.find(
        {"$or": [{"requester_id": uid}, {"receiver_id": uid}]}, {"_id": 0}
    ).sort("updated_at", -1).to_list(300)
    buckets = {"incoming": [], "outgoing": [], "active": [], "completed": []}
    for s in swaps:
        meta = await _swap_with_meta(s, uid)
        if s["status"] == "pending":
            if s["receiver_id"] == uid:
                buckets["incoming"].append(meta)
            else:
                buckets["outgoing"].append(meta)
        elif s["status"] in ("accepted", "active"):
            buckets["active"].append(meta)
        elif s["status"] == "completed":
            buckets["completed"].append(meta)
    return buckets


@api.get("/swaps/{swap_id}")
async def get_swap(swap_id: str, user: dict = Depends(get_current_user)):
    s = await db.swaps.find_one({"id": swap_id}, {"_id": 0})
    if not s or user["user_id"] not in (s["requester_id"], s["receiver_id"]):
        raise HTTPException(status_code=404, detail="Swap not found")
    meta = await _swap_with_meta(s, user["user_id"])
    msgs = await db.messages.find({"swap_id": swap_id}, {"_id": 0}).sort("created_at", 1).to_list(500)
    for m in msgs:
        m["created_at"] = m["created_at"].isoformat() if m.get("created_at") else None
        if m.get("proposal"):
            for k in ("offered_book_id", "requested_book_id"):
                bid = m["proposal"].get(k)
                if bid:
                    bk = await db.books.find_one({"id": bid}, {"_id": 0})
                    m["proposal"][k.replace("_id", "")] = clean_book(bk) if bk else None
    other_id = s["receiver_id"] if s["requester_id"] == user["user_id"] else s["requester_id"]
    my_books = await _books_for(user["user_id"], only_available=True)
    their_books = await _books_for(other_id, only_available=True)
    return {"swap": meta, "messages": msgs, "my_books": my_books, "their_books": their_books}


@api.post("/swaps/{swap_id}/messages")
async def send_message(swap_id: str, body: MessageBody, user: dict = Depends(get_current_user)):
    s = await db.swaps.find_one({"id": swap_id})
    if not s or user["user_id"] not in (s["requester_id"], s["receiver_id"]):
        raise HTTPException(status_code=404, detail="Swap not found")
    if not body.text.strip() and not body.image_url:
        raise HTTPException(status_code=400, detail="Empty message")
    mtype = "image" if body.image_url else "text"
    msg = await _add_message(swap_id, user["user_id"], mtype, body.text, image_url=body.image_url)
    msg["created_at"] = msg["created_at"].isoformat()
    return {"message": msg}


@api.post("/swaps/{swap_id}/propose")
async def propose(swap_id: str, body: ProposeBody, user: dict = Depends(get_current_user)):
    s = await db.swaps.find_one({"id": swap_id})
    if not s or user["user_id"] not in (s["requester_id"], s["receiver_id"]):
        raise HTTPException(status_code=404, detail="Swap not found")
    proposal = {
        "proposer_id": user["user_id"],
        "offered_book_id": body.offered_book_id,
        "requested_book_id": body.requested_book_id,
    }
    await db.swaps.update_one({"id": swap_id}, {"$set": {"active_proposal": proposal, "status": "accepted" if s["status"] == "accepted" else "pending"}})
    offered = await db.books.find_one({"id": body.offered_book_id}, {"_id": 0})
    requested = await db.books.find_one({"id": body.requested_book_id}, {"_id": 0})
    text = f"Proposed a swap: '{(offered or {}).get('title','?')}' ⇄ '{(requested or {}).get('title','?')}'"
    await _add_message(swap_id, user["user_id"], "proposal", text, proposal)
    return {"ok": True}


@api.post("/swaps/{swap_id}/accept")
async def accept_swap(swap_id: str, user: dict = Depends(get_current_user)):
    s = await db.swaps.find_one({"id": swap_id})
    if not s or user["user_id"] not in (s["requester_id"], s["receiver_id"]):
        raise HTTPException(status_code=404, detail="Swap not found")
    proposal = s.get("active_proposal")
    await db.swaps.update_one({"id": swap_id}, {"$set": {"status": "active"}})
    if proposal:
        for bid in (proposal.get("offered_book_id"), proposal.get("requested_book_id")):
            if bid:
                await db.books.update_one({"id": bid, "status": "Available"}, {"$set": {"status": "Reserved"}})
    await _add_message(swap_id, user["user_id"], "system", "Swap confirmed! Arrange to meet locally.")
    return {"ok": True}


@api.post("/swaps/{swap_id}/decline")
async def decline_swap(swap_id: str, user: dict = Depends(get_current_user)):
    s = await db.swaps.find_one({"id": swap_id})
    if not s or user["user_id"] not in (s["requester_id"], s["receiver_id"]):
        raise HTTPException(status_code=404, detail="Swap not found")
    await db.swaps.update_one({"id": swap_id}, {"$set": {"status": "declined"}})
    await _add_message(swap_id, user["user_id"], "system", f"{user['name']} declined the swap.")
    return {"ok": True}


@api.post("/swaps/{swap_id}/cancel")
async def cancel_swap(swap_id: str, user: dict = Depends(get_current_user)):
    s = await db.swaps.find_one({"id": swap_id})
    if not s or user["user_id"] not in (s["requester_id"], s["receiver_id"]):
        raise HTTPException(status_code=404, detail="Swap not found")
    proposal = s.get("active_proposal")
    if proposal:
        for bid in (proposal.get("offered_book_id"), proposal.get("requested_book_id")):
            if bid:
                await db.books.update_one({"id": bid, "status": "Reserved"}, {"$set": {"status": "Available"}})
    await db.swaps.update_one({"id": swap_id}, {"$set": {"status": "cancelled"}})
    await _add_message(swap_id, user["user_id"], "system", f"{user['name']} cancelled the swap.")
    return {"ok": True}


async def _recompute_rating(user_id: str):
    ratings = await db.ratings.find({"ratee_id": user_id}).to_list(1000)
    if not ratings:
        return
    avg = sum(r["stars"] for r in ratings) / len(ratings)
    await db.users.update_one({"user_id": user_id}, {"$set": {"rating": round(avg, 1), "rating_count": len(ratings)}})


@api.post("/swaps/{swap_id}/complete")
async def complete_swap(swap_id: str, user: dict = Depends(get_current_user)):
    s = await db.swaps.find_one({"id": swap_id})
    if not s or user["user_id"] not in (s["requester_id"], s["receiver_id"]):
        raise HTTPException(status_code=404, detail="Swap not found")
    if s["status"] not in ("active", "completed"):
        raise HTTPException(status_code=400, detail="Swap is not active")
    field = "requester_completed" if s["requester_id"] == user["user_id"] else "receiver_completed"
    await db.swaps.update_one({"id": swap_id}, {"$set": {field: True}})
    s = await db.swaps.find_one({"id": swap_id})
    if s.get("requester_completed") and s.get("receiver_completed") and s["status"] != "completed":
        await db.swaps.update_one({"id": swap_id}, {"$set": {"status": "completed"}})
        for uid in (s["requester_id"], s["receiver_id"]):
            await db.users.update_one({"user_id": uid}, {"$inc": {"swaps_count": 1}})
        proposal = s.get("active_proposal")
        if proposal:
            for bid in (proposal.get("offered_book_id"), proposal.get("requested_book_id")):
                if bid:
                    await db.books.update_one({"id": bid}, {"$set": {"status": "Swapped"}})
        await _add_message(swap_id, None, "system", "Swap completed! Leave a rating.")
    else:
        await _add_message(swap_id, user["user_id"], "system", f"{user['name']} marked the swap complete.")
    return {"ok": True}


@api.post("/swaps/{swap_id}/rate")
async def rate_swap(swap_id: str, body: RateBody, user: dict = Depends(get_current_user)):
    s = await db.swaps.find_one({"id": swap_id})
    if not s or user["user_id"] not in (s["requester_id"], s["receiver_id"]):
        raise HTTPException(status_code=404, detail="Swap not found")
    if s["status"] != "completed":
        raise HTTPException(status_code=400, detail="Swap not completed yet")
    ratee_id = s["receiver_id"] if s["requester_id"] == user["user_id"] else s["requester_id"]
    field = "requester_rated" if s["requester_id"] == user["user_id"] else "receiver_rated"
    if s.get(field):
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
    await db.swaps.update_one({"id": swap_id}, {"$set": {field: True}})
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
@api.post("/upload")
async def upload(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    data = await file.read()
    ext = (file.filename or "img.jpg").split(".")[-1].lower()
    if ext not in ("jpg", "jpeg", "png", "webp", "heic"):
        ext = "jpg"
    path = f"{APP_NAME}/uploads/{user['user_id']}/{uuid.uuid4().hex}.{ext}"
    ctype = file.content_type or "image/jpeg"
    await run_in_threadpool(put_object, path, data, ctype)
    await db.uploads.insert_one(
        {"path": path, "owner_id": user["user_id"], "content_type": ctype, "created_at": now_utc()}
    )
    return {"path": path, "url": f"/api/files/{path}"}


@api.get("/files/{path:path}")
async def files(path: str):
    rec = await db.uploads.find_one({"path": path})
    if not rec:
        raise HTTPException(status_code=404, detail="Not found")
    content, ctype = await run_in_threadpool(get_object, path)
    return Response(content=content, media_type=ctype)


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
    await db.users.create_index("email", unique=True)
    await db.users.create_index("user_id", unique=True)
    await db.user_sessions.create_index("session_token", unique=True)
    await db.user_sessions.create_index("expires_at", expireAfterSeconds=0)
    await db.books.create_index("owner_id")
    await db.swaps.create_index("requester_id")
    await db.swaps.create_index("receiver_id")
    try:
        await run_in_threadpool(init_storage)
        logger.info("Storage initialized")
    except Exception as e:
        logger.warning(f"Storage init failed: {e}")
    try:
        await seed_demo()
    except Exception as e:
        logger.warning(f"Seed failed: {e}")


@app.on_event("shutdown")
async def shutdown():
    client.close()


# ----------------------------------------------------------------------------
# Seed demo Messina community
# ----------------------------------------------------------------------------
def cover(isbn: str) -> str:
    return f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg"


async def seed_demo():
    if await db.users.count_documents({"seed": True}) > 0:
        return
    fem = "https://images.unsplash.com/photo-1514355315815-2b64b0216b14?crop=entropy&cs=srgb&fm=jpg&w=400&q=80"
    male = "https://images.unsplash.com/photo-1525457136159-8878648a7ad0?crop=entropy&cs=srgb&fm=jpg&w=400&q=80"
    fem2 = "https://images.unsplash.com/photo-1544005313-94ddf0286df2?crop=entropy&cs=srgb&fm=jpg&w=400&q=80"
    male2 = "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?crop=entropy&cs=srgb&fm=jpg&w=400&q=80"
    fem3 = "https://images.unsplash.com/photo-1534528741775-53994a69daeb?crop=entropy&cs=srgb&fm=jpg&w=400&q=80"

    demo = [
        {
            "name": "Maria Rossi", "avatar": fem, "nb": "Centro", "rating": 4.9, "rc": 22, "swaps": 18,
            "genres": ["Fiction", "Psychology", "History"], "langs": ["Italian", "English"],
            "bio": "Literature student. Love a good psychological thriller and Sunday reading in the park.",
            "books": [
                ("Atomic Habits", "James Clear", "9780735211292", "Self-development", "English", "Good"),
                ("Sapiens", "Yuval Noah Harari", "9780062316097", "History", "English", "Like New"),
                ("Norwegian Wood", "Haruki Murakami", "9780375704024", "Fiction", "English", "Good"),
                ("The Silent Patient", "Alex Michaelides", "9781250301697", "Fiction", "English", "Good"),
            ],
        },
        {
            "name": "Luca Bianchi", "avatar": male, "nb": "University Area", "rating": 4.8, "rc": 14, "swaps": 12,
            "genres": ["Business", "Science", "Self-development"], "langs": ["Italian", "English"],
            "bio": "Engineering @ UniMe. Trading business & science reads for good fiction.",
            "books": [
                ("Thinking, Fast and Slow", "Daniel Kahneman", "9780374533557", "Psychology", "English", "Good"),
                ("Dune", "Frank Herbert", "9780441013593", "Fantasy", "English", "Acceptable"),
                ("Zero to One", "Peter Thiel", "9780804139298", "Business", "English", "Like New"),
            ],
        },
        {
            "name": "Laura Conti", "avatar": fem2, "nb": "Annunziata", "rating": 5.0, "rc": 30, "swaps": 24,
            "genres": ["Fantasy", "Romance", "Fiction"], "langs": ["Italian", "English", "Spanish"],
            "bio": "Fantasy addict & Erasmus mentor. Always up for a book chat over coffee.",
            "books": [
                ("Harry Potter and the Sorcerer's Stone", "J.K. Rowling", "9780590353427", "Fantasy", "English", "Good"),
                ("The Alchemist", "Paulo Coelho", "9780061122415", "Fiction", "English", "Good"),
                ("1984", "George Orwell", "9780451524935", "Fiction", "English", "Like New"),
                ("The Midnight Library", "Matt Haig", "9780525559474", "Fiction", "English", "Good"),
            ],
        },
        {
            "name": "Ahmed Hassan", "avatar": male2, "nb": "Giostra", "rating": 4.6, "rc": 11, "swaps": 9,
            "genres": ["History", "Philosophy", "Biography"], "langs": ["English", "Arabic"],
            "bio": "History buff & PhD candidate. Big on biographies and philosophy.",
            "books": [
                ("Man's Search for Meaning", "Viktor Frankl", "9780807014271", "Philosophy", "English", "Good"),
                ("Educated", "Tara Westover", "9780399590504", "Biography", "English", "Like New"),
                ("Meditations", "Marcus Aurelius", "9780140449334", "Philosophy", "English", "Acceptable"),
            ],
        },
        {
            "name": "Giulia Marino", "avatar": fem3, "nb": "Centro", "rating": 4.7, "rc": 9, "swaps": 7,
            "genres": ["Romance", "Fiction", "Psychology"], "langs": ["Italian"],
            "bio": "Romance & contemporary fiction lover. Messina born and raised.",
            "books": [
                ("It Ends with Us", "Colleen Hoover", "9781501110368", "Romance", "English", "Good"),
                ("Where the Crawdads Sing", "Delia Owens", "9780735219090", "Fiction", "English", "Good"),
            ],
        },
        {
            "name": "Marco De Luca", "avatar": male, "nb": "University Area", "rating": 4.9, "rc": 16, "swaps": 15,
            "genres": ["Science", "Business", "History"], "langs": ["Italian", "English"],
            "bio": "Physics student. Trading science and business books near campus.",
            "books": [
                ("A Brief History of Time", "Stephen Hawking", "9780553380163", "Science", "English", "Good"),
                ("The Lean Startup", "Eric Ries", "9780307887894", "Business", "English", "Like New"),
            ],
        },
        {
            "name": "Sofia Greco", "avatar": fem, "nb": "Annunziata", "rating": 4.8, "rc": 13, "swaps": 10,
            "genres": ["Fiction", "Fantasy", "Self-development"], "langs": ["Italian", "English", "French"],
            "bio": "Erasmus from France. Building my Italian one novel at a time.",
            "books": [
                ("The Name of the Wind", "Patrick Rothfuss", "9780756404741", "Fantasy", "English", "Good"),
                ("Deep Work", "Cal Newport", "9781455586691", "Self-development", "English", "Good"),
            ],
        },
        {
            "name": "Antonio Ferrara", "avatar": male2, "nb": "Provinciale", "rating": 4.5, "rc": 6, "swaps": 5,
            "genres": ["History", "Biography"], "langs": ["Italian"],
            "bio": "Retired teacher with a big home library to share.",
            "books": [
                ("Steve Jobs", "Walter Isaacson", "9781451648539", "Biography", "English", "Good"),
                ("Guns, Germs, and Steel", "Jared Diamond", "9780393317558", "History", "English", "Acceptable"),
            ],
        },
    ]

    for d in demo:
        uid = new_id("user")
        lat, lng = NEIGHBORHOODS[d["nb"]]
        lat += (hash(d["name"]) % 20 - 10) / 2000.0
        lng += (hash(d["name"][::-1]) % 20 - 10) / 2000.0
        await db.users.insert_one(
            {
                "user_id": uid,
                "email": f"{d['name'].split()[0].lower()}.{d['name'].split()[1].lower()}@demo.bookloop",
                "password_hash": None,
                "name": d["name"],
                "avatar_url": d["avatar"],
                "bio": d["bio"],
                "city": "Messina",
                "neighborhood": d["nb"],
                "lat": lat,
                "lng": lng,
                "genres": d["genres"],
                "languages": d["langs"],
                "is_exchanging": True,
                "rating": d["rating"],
                "rating_count": d["rc"],
                "swaps_count": d["swaps"],
                "created_at": now_utc(),
                "deleted_at": None,
                "seed": True,
            }
        )
        for (title, author, isbn, genre, lang, cond) in d["books"]:
            await db.books.insert_one(
                {
                    "id": new_id("book"),
                    "owner_id": uid,
                    "title": title,
                    "author": author,
                    "cover_url": cover(isbn),
                    "condition": cond,
                    "language": lang,
                    "genre": genre,
                    "status": "Available",
                    "created_at": now_utc(),
                    "deleted_at": None,
                    "seed": True,
                }
            )
    logger.info("Seeded demo community")
