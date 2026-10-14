"""Multilingual / cross-script search, end to end: GET /api/discover/books (ingestion: search
tokens + OpenLibrary/Wikidata aliases with provenance, the metadata cache, ranking, typo fallback),
reader-name search in GET /api/discover/people, the add-book OpenLibrary search, the legacy
fallback + backfill for documents stored before search tokens existed, and that eligibility rules
are unchanged.

Same requirements as test_bookloop_api.py (a backend with OTP_DEBUG_MODE=true on a throwaway
database), plus a fake OpenLibrary + Wikidata that the tests host themselves. Start the backend with
    OPENLIBRARY_URL=http://127.0.0.1:8098 WIKIDATA_URL=http://127.0.0.1:8098/w/api.php
    METADATA_CONTACT=tests@example.invalid METADATA_SPACING_S=0.05 uvicorn server:app --port 8001
and run the tests with FAKE_OPENLIBRARY_PORT=8098. Without it these tests are skipped.
"""
import os
import sys
import json
import time
import uuid
import asyncio
import threading
from pathlib import Path
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).parent.parent / ".env")
API = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or "http://127.0.0.1:8001").rstrip("/") + "/api"
FAKE_OPENLIBRARY_PORT = os.environ.get("FAKE_OPENLIBRARY_PORT")

# Fake OpenLibrary data, keyed by a per-run tag so parallel/repeated runs never collide.
WORKS: dict = {}  # work key -> {"doc": search doc, "editions": [...], "match": title the search must contain}
QUERIES: dict = {}  # add-book search: text the `q` parameter must contain -> docs
WD_ITEMS: dict = {}  # Wikidata id -> entity; a work also has "_match" (title text the search must contain)
HITS: dict = {}  # request counts per (path, sorted query), to see what the metadata cache saved


def _db():
    return MongoClient(os.environ.get("MONGO_URL") or "mongodb://localhost:27017")[os.environ.get("DB_NAME") or "bookloop_test"]


def _h(u):
    return {"Authorization": f"Bearer {u['token']}"}


def _user(label, name=None):
    email = f"TEST_search_{uuid.uuid4().hex[:10]}@bookloop.com"
    r = requests.post(f"{API}/auth/register/dev-instant", json={"email": email, "password": "test1234", "name": name or f"Search {label}"})
    assert r.status_code == 200, r.text
    d = r.json()
    return {"token": d["session_token"], "id": d["user"]["user_id"]}


def _add(u, title, author, **extra):
    body = {"title": title, "author": author, "genre": "Fiction", "language": "English", "condition": "Good", **extra}
    r = requests.post(f"{API}/books", json=body, headers=_h(u))
    assert r.status_code == 200, r.text
    return r.json()["book"]


def _search(u, q, **params):
    r = requests.get(f"{API}/discover/books", params={"search": q, **params}, headers=_h(u))
    assert r.status_code == 200, r.text
    return r.json()["books"]


@pytest.fixture(scope="module")
def fake_openlibrary():
    if not FAKE_OPENLIBRARY_PORT:
        pytest.skip("FAKE_OPENLIBRARY_PORT not set (backend not pointed at a fake OpenLibrary)")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urlparse(self.path)
            qs = {k: v[0] for k, v in parse_qs(u.query).items()}
            HITS[(u.path, tuple(sorted(qs.items())))] = HITS.get((u.path, tuple(sorted(qs.items()))), 0) + 1
            if u.path == "/w/api.php" and qs.get("action") == "wbsearchentities":
                wanted = (qs.get("search") or "").casefold()
                body = {"search": [{"id": i} for i, e in WD_ITEMS.items() if e.get("_match") and e["_match"].casefold() in wanted]}
            elif u.path == "/w/api.php" and qs.get("action") == "wbgetentities":
                ids = (qs.get("ids") or "").split("|")
                body = {"entities": {i: {k: v for k, v in WD_ITEMS[i].items() if k != "_match"} for i in ids if i in WD_ITEMS}}
            elif u.path == "/search.json" and "q" in qs:
                body = {"docs": [d for m, docs in QUERIES.items() if m in qs["q"].casefold() for d in docs]}
            elif u.path == "/search.json":
                wanted = (qs.get("title") or qs.get("isbn") or "").casefold()
                docs = [w["doc"] for w in WORKS.values() if w["match"].casefold() in wanted or wanted in w.get("isbns", ())]
                body = {"docs": docs}
            elif u.path.endswith("/editions.json"):
                work = WORKS.get(u.path[: -len("/editions.json")])
                body = {"entries": work["editions"] if work else []}
            else:
                self.send_response(404)
                self.end_headers()
                return
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = HTTPServer(("127.0.0.1", int(FAKE_OPENLIBRARY_PORT)), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


def _wd(qid, labels, aliases=None, authors=(), match=None):
    WD_ITEMS[qid] = {
        "id": qid, "_match": match, "labels": {l: {"language": l, "value": v} for l, v in labels.items()},
        "aliases": {l: [{"language": l, "value": v} for v in vs] for l, vs in (aliases or {}).items()},
        "claims": {"P50": [{"mainsnak": {"datavalue": {"value": {"id": a}}}} for a in authors]},
    }


def _wait_enriched(ids, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _db().books.count_documents({"id": {"$in": ids}, "aliases_status": "pending"}) == 0:
            return
        time.sleep(0.3)
    pytest.fail("background alias lookups did not finish")


def _ed(title, lang, n=1):
    return [{"title": title, "languages": [{"key": f"/languages/{lang}"}]}] * n


@pytest.fixture(scope="module")
def lib(fake_openlibrary):
    tag = "t" + uuid.uuid4().hex[:8]
    key = f"/works/OL{tag}W"
    WORKS[key] = {
        "match": f"crime and punishment {tag}",
        "doc": {"key": key, "title": f"Преступление и наказание {tag}", "author_name": ["Fyodor Dostoevsky"],
                "author_alternative_name": ["Fyodor Dostoyevsky", "Фёдор Достоевский", "فيودور دوستويفسكي", "ドストエフスキー"]},
        "editions": _ed(f"Crime and Punishment {tag}", "eng", 3) + _ed(f"Delitto e castigo {tag}", "ita", 2)
        + _ed(f"Jinoyat va jazo {tag}", "uzb") + _ed(f"الجريمة والعقاب {tag}", "ara") + _ed(f"Schuld und Sühne {tag}", "ger", 4),
    }
    # A work that does NOT match what the user stored (different author): must never be attached.
    WORKS[f"/works/OLx{tag}W"] = {
        "match": f"the idiot {tag}",
        "doc": {"key": f"/works/OLx{tag}W", "title": f"The Idiot {tag}", "author_name": ["Someone Else"]},
        "editions": _ed(f"L'idiota {tag}", "ita", 2),
    }
    _wd(f"Q1{tag}", {"en": f"Palace Walk {tag}", "ar": f"بين القصرين {tag}", "ru": f"Между дворцами {tag}",
                     "it": f"Tra i due palazzi {tag}"}, {"it": [f"Kamal Abd al-Jawad {tag}"]}, authors=[f"Q2{tag}"],
        match=f"palace walk {tag}")
    _wd(f"Q2{tag}", {"en": "Naguib Mahfouz", "ar": "نجيب محفوظ", "ru": "Нагиб Махфуз"}, {"en": ["Najib Mahfuz"]})
    _wd(f"Q3{tag}", {"en": f"Palace Walk {tag}", "ru": f"Фильм не книга {tag}"}, match=f"palace walk {tag}")  # a film: no P50
    owner, owner2, viewer = _user(f"{tag} Owner"), _user(f"{tag} Owner2"), _user(f"{tag} Viewer")
    books = {
        "crime": _add(owner, f"Crime and Punishment {tag}", "Fyodor Dostoevsky"),
        "karamazov": _add(owner2, f"Братья Карамазовы {tag}", "Фёдор Достоевский", language="Russian"),
        "scene": _add(owner, f"Crime Scene {tag}", "Patricia Cornwell"),
        "idiot": _add(owner2, f"The Idiot {tag}", "Fyodor Dostoevsky"),
        "kunlar": _add(owner2, f"O'tkan kunlar {tag}", "Abdulla Qodiriy", language="Uzbek"),
        "rosa": _add(owner, f"Il nome della rosa {tag}", "Umberto Eco", language="Italian"),
        "aswad": _add(owner2, f"الأسود يليق بك {tag}", "أحلام مستغانمي", language="Arabic"),
        "reserved": _add(owner, f"Бесы {tag}", "Фёдор Достоевский", status="Reserved"),
        "deleted": _add(owner, f"Игрок {tag}", "Фёдор Достоевский"),
        "own": _add(viewer, f"Белые ночи {tag}", "Фёдор Достоевский"),
        "history": _add(owner, f"Бедные люди {tag}", "Фёдор Достоевский", genre="History"),
        "palace": _add(owner, f"Palace Walk {tag}", "Naguib Mahfouz"),
    }
    assert requests.delete(f"{API}/books/{books['deleted']['id']}", headers=_h(owner)).status_code == 200
    # Wait for every live book's background metadata lookup to finish.
    _wait_enriched([bk["id"] for name, bk in books.items() if name != "deleted"])
    return {"tag": tag, "key": key, "owner": owner, "owner2": owner2, "viewer": viewer, "books": books}


def _ids(books):
    return [b["id"] for b in books]


class TestMultilingualBookSearch:
    def test_cyrillic_author_found_with_latin_transliteration(self, lib):
        b, t = lib["books"], lib["tag"]
        for q in ("Dostoevsky", "Dostoyevsky", "Dostoevskiy", "dostoevskii", "Fedor Dostoevskij"):
            assert b["karamazov"]["id"] in _ids(_search(lib["viewer"], f"{q} {t}")), q

    def test_latin_author_found_with_cyrillic(self, lib):
        b, t = lib["books"], lib["tag"]
        ids = _ids(_search(lib["viewer"], f"Достоевский {t}"))
        assert {b["crime"]["id"], b["idiot"]["id"], b["karamazov"]["id"]} <= set(ids)
        assert b["scene"]["id"] not in ids

    def test_translated_titles_via_metadata_aliases(self, lib):
        b, t = lib["books"], lib["tag"]
        for q in ("Преступление и наказание", "Delitto e castigo", "Jinoyat va jazo", "الجريمة والعقاب"):
            got = _ids(_search(lib["viewer"], f"{q} {t}"))
            assert got and got[0] == b["crime"]["id"], q
        # An Arabic author spelling that only the metadata knows.
        assert b["crime"]["id"] in _ids(_search(lib["viewer"], f"فيودور دوستويفسكي {t}"))

    def test_unverified_work_is_never_attached(self, lib):
        # "The Idiot" exists in the fake OpenLibrary under a different author: no aliases are taken.
        b, t = lib["books"], lib["tag"]
        assert b["idiot"]["id"] not in _ids(_search(lib["viewer"], f"L'idiota {t}"))
        doc = _db().books.find_one({"id": b["idiot"]["id"]})
        assert doc["aliases"] == [] and doc["aliases_status"] == "none"
        # Languages outside BookLoop's five (German here) are not imported either.
        assert not _search(lib["viewer"], f"Schuld und Sühne {t}")

    def test_aliases_are_stored_with_provenance_and_originals_untouched(self, lib):
        b = lib["books"]["crime"]
        doc = _db().books.find_one({"id": b["id"]})
        assert doc["title"] == f"Crime and Punishment {lib['tag']}" and doc["author"] == "Fyodor Dostoevsky"
        assert doc["aliases_status"] == "done" and doc["search_v"] >= 1
        assert doc["aliases"] and all(a["source"] == "openlibrary" and a["ref"] == lib["key"] for a in doc["aliases"])
        assert {a.get("lang") for a in doc["aliases"] if a["kind"] == "title"} >= {"it", "uz", "ar"}
        assert not any("ドストエフスキー" in a["value"] for a in doc["aliases"])
        # The API shape is unchanged: no aliases or search internals leak into responses.
        api_book = _search(lib["viewer"], f"Crime and Punishment {lib['tag']}")[0]
        assert "aliases" not in api_book and "search_tokens" not in api_book

    def test_english_title_still_works(self, lib):
        b, t = lib["books"], lib["tag"]
        got = _ids(_search(lib["viewer"], f"crime and punishment {t}"))
        assert got[0] == b["crime"]["id"] and b["scene"]["id"] not in got
        assert set(_ids(_search(lib["viewer"], f"crime {t}"))) == {b["crime"]["id"], b["scene"]["id"]}

    def test_uzbek_search(self, lib):
        b, t = lib["books"], lib["tag"]
        for q in ("Ўткан кунлар", "otkan kunlar", "O‘tkan kunlar", "Қодирий", "Qodiriy"):
            assert _ids(_search(lib["viewer"], f"{q} {t}")) == [b["kunlar"]["id"]], q

    def test_russian_search(self, lib):
        b, t = lib["books"], lib["tag"]
        for q in ("Братья Карамазовы", "братья карамазовы", "Bratya Karamazovy", "Карамазовы"):
            assert _ids(_search(lib["viewer"], f"{q} {t}")) == [b["karamazov"]["id"]], q

    def test_italian_search(self, lib):
        b, t = lib["books"], lib["tag"]
        for q in ("Il nome della rosa", "nome della rosa", "IL NOME DELLA ROSA!", "Umberto Eco"):
            assert _ids(_search(lib["viewer"], f"{q} {t}")) == [b["rosa"]["id"]], q

    def test_arabic_search(self, lib):
        b, t = lib["books"], lib["tag"]
        for q in ("الأسود يليق بك", "الاسود يليق بك", "احلام مستغانمي", "اسود"):
            assert _ids(_search(lib["viewer"], f"{q} {t}")) == [b["aswad"]["id"]], q

    def test_typos_tolerated_but_exact_ranks_first(self, lib):
        b, t = lib["books"], lib["tag"]
        assert b["crime"]["id"] in _ids(_search(lib["viewer"], f"Dostoevksy {t}"))
        assert b["crime"]["id"] in _ids(_search(lib["viewer"], f"punishmnet {t}"))
        assert _ids(_search(lib["viewer"], f"Crime Scene {t}"))[0] == b["scene"]["id"]

    def test_no_false_positive_explosion(self, lib):
        t = lib["tag"]
        assert _search(lib["viewer"], f"zzzzqq {t}") == []
        assert _search(lib["viewer"], f"Tolstoy {t}") == []
        for s in ("*", ".*", "!!!"):
            assert _search(lib["viewer"], s) == [], s

    def test_eligibility_rules_still_apply(self, lib):
        b, t, v = lib["books"], lib["tag"], lib["viewer"]
        ids = set(_ids(_search(v, f"Достоевский {t}")))
        assert b["reserved"]["id"] not in ids      # not Available
        assert b["deleted"]["id"] not in ids       # deleted
        assert b["own"]["id"] not in ids           # the viewer's own book
        assert b["history"]["id"] in ids
        assert b["history"]["id"] not in set(_ids(_search(v, f"Достоевский {t}", genre="Fiction")))
        assert set(_ids(_search(v, f"Достоевский {t}", language="Russian"))) == {b["karamazov"]["id"]}
        # The owner never sees their own books in Discover, whatever the query.
        assert not set(_ids(_search(lib["owner"], f"Crime {t}"))) & {b["crime"]["id"], b["scene"]["id"]}

    def test_edit_reindexes_and_drops_old_aliases(self, lib):
        t, owner, v = lib["tag"], lib["owner"], lib["viewer"]
        bk = _add(owner, f"Draft title {t}", "Unknown")
        r = requests.put(f"{API}/books/{bk['id']}", json={"title": f"Мастер и Маргарита {t}", "author": "Михаил Булгаков"}, headers=_h(owner))
        assert r.status_code == 200
        assert bk["id"] in _ids(_search(v, f"Master i Margarita {t}"))
        assert bk["id"] in _ids(_search(v, f"Bulgakov {t}"))
        assert bk["id"] not in _ids(_search(v, f"Draft title {t}"))


class TestWikidataAndCache:
    def test_translated_titles_from_wikidata(self, lib):
        b, t = lib["books"], lib["tag"]
        for q in ("بين القصرين", "Между дворцами", "Tra i due palazzi"):
            assert _ids(_search(lib["viewer"], f"{q} {t}")) == [b["palace"]["id"]], q

    def test_arabic_and_latin_author_spellings_from_wikidata(self, lib):
        b, t = lib["books"], lib["tag"]
        for q in ("نجيب محفوظ", "Нагиб Махфуз", "Najib Mahfuz"):
            assert b["palace"]["id"] in _ids(_search(lib["viewer"], f"{q} {t}")), q

    def test_wikidata_provenance_and_noise_filtering(self, lib):
        doc = _db().books.find_one({"id": lib["books"]["palace"]["id"]})
        t = lib["tag"]
        wd = [a for a in doc["aliases"] if a["source"] == "wikidata"]
        assert {(a["kind"], a["ref"]) for a in wd} == {("title", f"Q1{t}"), ("author", f"Q2{t}")}
        values = {a["value"] for a in doc["aliases"]}
        assert f"Фильм не книга {t}" not in values      # the same-titled film (no author) is ignored
        assert f"Kamal Abd al-Jawad {t}" not in values  # work aliases (character names…) are not titles
        assert doc["title"] == f"Palace Walk {t}" and doc["author"] == "Naguib Mahfouz"

    def test_metadata_cache_avoids_repeat_lookups(self, lib):
        t = lib["tag"]
        searches = lambda: sum(n for (path, q), n in HITS.items() if path == "/w/api.php" and ("search", f"Palace Walk {t}") in q)  # noqa: E731
        before = searches()
        assert before >= 1
        copy = _add(lib["owner2"], f"Palace Walk {t}", "Naguib Mahfouz")
        _wait_enriched([copy["id"]])
        assert searches() == before  # served from metadata_cache
        first = _db().books.find_one({"id": lib["books"]["palace"]["id"]})
        second = _db().books.find_one({"id": copy["id"]})
        assert [a["value"] for a in second["aliases"]] == [a["value"] for a in first["aliases"]]
        assert _db().metadata_cache.count_documents({}) > 0

    def test_typo_in_first_letters(self, lib):
        b, t = lib["books"], lib["tag"]
        for q in ("Fostoevsky", "Bostoyevsky", "Xaguib"):
            ids = _ids(_search(lib["viewer"], f"{q} {t}"))
            assert ids, q
        assert b["crime"]["id"] in _ids(_search(lib["viewer"], f"Fostoevsky {t}"))
        assert b["palace"]["id"] in _ids(_search(lib["viewer"], f"Xaguib {t}"))


class TestPeopleNameSearch:
    @pytest.fixture(scope="class")
    def people(self):
        tag = "t" + uuid.uuid4().hex[:8]
        cyr = _user("x", name=f"Алишер Навоий {tag}")
        lat = _user("x", name=f"Abdulla Qodiriy {tag}")
        ar = _user("x", name=f"أحمد الحسن {tag}")
        off = _user("x", name=f"Alisher Offline {tag}")
        r = requests.put(f"{API}/users/me", json={"is_exchanging": False}, headers=_h(off))
        assert r.status_code == 200
        viewer = _user("x", name=f"Viewer Navoiy {tag}")
        return {"tag": tag, "cyr": cyr, "lat": lat, "ar": ar, "off": off, "viewer": viewer}

    def _find(self, ppl, q, **params):
        r = requests.get(f"{API}/discover/people", params={"search": f"{q} {ppl['tag']}", **params}, headers=_h(ppl["viewer"]))
        assert r.status_code == 200, r.text
        return [p["user_id"] for p in r.json()["people"]]

    def test_cyrillic_name_found_in_latin_and_back(self, people):
        assert self._find(people, "Alisher Navoiy") == [people["cyr"]["id"]]
        assert self._find(people, "Navoi") == [people["cyr"]["id"]]
        assert self._find(people, "Абдулла Қодирий") == [people["lat"]["id"]]
        assert self._find(people, "qodiriy") == [people["lat"]["id"]]

    def test_arabic_name_and_typos(self, people):
        assert self._find(people, "احمد") == [people["ar"]["id"]]
        assert self._find(people, "Qodiryi") == [people["lat"]["id"]]   # swapped letters
        assert self._find(people, "Abdula") == [people["lat"]["id"]]

    def test_name_search_keeps_discovery_rules(self, people):
        # The viewer's own name matches "Navoiy" but they never see themselves.
        assert people["viewer"]["id"] not in self._find(people, "Navoiy")
        assert people["off"]["id"] not in self._find(people, "Alisher", exchanging="true")
        assert people["off"]["id"] in self._find(people, "Alisher", exchanging="false")
        assert self._find(people, "zzzzqq") == []

    def test_rename_reindexes(self, people):
        u = _user("x", name=f"Draft Name {people['tag']}")
        r = requests.put(f"{API}/users/me", json={"name": f"Чингиз Айтматов {people['tag']}"}, headers=_h(u))
        assert r.status_code == 200
        assert self._find(people, "Chingiz Aytmatov") == [u["id"]]
        assert self._find(people, "Draft Name") == []


class TestAddBookSearch:
    def test_cyrillic_query_also_searched_in_latin_and_ranked(self, fake_openlibrary):
        tag = "t" + uuid.uuid4().hex[:8]
        u = _user(f"{tag} Adder")
        # OpenLibrary (fake) only knows the Latin spelling, like the real one mostly does for names.
        QUERIES[f"dostoevskiy {tag}"] = [
            {"title": f"Notes from Underground {tag}", "author_name": ["Fyodor Dostoevsky"], "isbn": ["1"]},
            {"title": f"Crime and Punishment {tag}", "author_name": ["Fiódor Dostoievski"],
             "author_alternative_name": ["Фёдор Достоевский"], "isbn": ["2"]},
        ]
        r = requests.get(f"{API}/books/search", params={"q": f"Достоевский {tag}"}, headers=_h(u))
        assert r.status_code == 200
        titles = [x["title"] for x in r.json()["results"]]
        assert set(titles) == {f"Notes from Underground {tag}", f"Crime and Punishment {tag}"}
        # The exact same-script author alias ranks above a transliteration match.
        assert titles[0] == f"Crime and Punishment {tag}"
        # A Latin query is sent as is (no second request).
        r = requests.get(f"{API}/books/search", params={"q": f"Dostoevskiy {tag}"}, headers=_h(u))
        assert len(r.json()["results"]) == 2


def _run_backfill():
    """Run the server's startup backfill twice in this process; returns both counts. Each call
    gets its own Motor client, since a client stays bound to the event loop it first ran on."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        import server
        from motor.motor_asyncio import AsyncIOMotorClient
    except Exception as e:  # e.g. no MONGO_URL configured in this environment
        pytest.skip(f"cannot import server: {e!r}")

    async def go():
        client = AsyncIOMotorClient(os.environ.get("MONGO_URL") or "mongodb://localhost:27017")
        saved, server.db = server.db, client[os.environ.get("DB_NAME") or "bookloop_test"]
        try:
            return await server.backfill_search_tokens(), await server.backfill_search_tokens()
        finally:
            server.db = saved
            client.close()

    return asyncio.run(go())


class TestExistingBooksBackfill:
    def test_legacy_book_is_searchable_before_and_after_backfill(self):
        tag = "t" + uuid.uuid4().hex[:8]
        owner, viewer = _user(f"{tag} LegacyOwner"), _user(f"{tag} LegacyViewer")
        book_id = f"book_legacy_{tag}"
        # A book exactly as stored before search tokens existed (no search_* / aliases fields).
        _db().books.insert_one({
            "id": book_id, "owner_id": owner["id"], "title": f"Тихий Дон {tag}", "author": "Михаил Шолохов",
            "cover_url": None, "condition": "Good", "language": "Russian", "genre": "Fiction", "isbn": None,
            "status": "Available", "created_at": datetime.now(timezone.utc), "deleted_at": None,
        })
        # Before the backfill reaches it: still found the old way (plain text).
        assert book_id in _ids(_search(viewer, f"Тихий Дон {tag}"))
        assert book_id not in _ids(_search(viewer, f"Tikhiy Don {tag}"))
        first, second = _run_backfill()
        assert first >= 1 and second == 0  # idempotent
        doc = _db().books.find_one({"id": book_id})
        assert doc["title"] == f"Тихий Дон {tag}" and doc["search_tokens"]
        assert book_id in _ids(_search(viewer, f"Tikhiy Don {tag}"))
        assert book_id in _ids(_search(viewer, f"Sholokhov {tag}"))

    def test_legacy_reader_name_is_searchable_before_and_after_backfill(self):
        tag = "t" + uuid.uuid4().hex[:8]
        legacy, viewer = _user("x", name=f"Эркин Воҳидов {tag}"), _user(f"{tag} V")
        # As stored before name search tokens existed.
        _db().users.update_one({"user_id": legacy["id"]}, {"$unset": {"search_tokens": "", "search_v": ""}})

        def find(q):
            r = requests.get(f"{API}/discover/people", params={"search": f"{q} {tag}"}, headers=_h(viewer))
            return [p["user_id"] for p in r.json()["people"]]

        # Meanwhile it is found exactly as the old search found it (the query as a plain substring).
        assert find("Эркин Воҳидов") == [legacy["id"]]
        assert find("Erkin Vohidov") == []
        _run_backfill()
        assert find("Erkin Vohidov") == [legacy["id"]]
