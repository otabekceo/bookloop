"""BookLoop v2.2: proposal -> chat access state machine, "Currently exchanging" enforcement, proposal
notes, unread state + notification events, favorites, push opt-in, and compatibility with records
written before v2.2.

Same requirements as test_bookloop_api.py: a backend started with OTP_DEBUG_MODE=true on a throwaway
database (with that flag pushes are recorded in `push_debug` instead of being sent).
"""
import os
import uuid
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).parent.parent / ".env")
API = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or "http://127.0.0.1:8001").rstrip("/") + "/api"


def _db():
    return MongoClient(os.environ.get("MONGO_URL") or "mongodb://localhost:27017")[os.environ.get("DB_NAME") or "bookloop_test"]


def _h(u):
    return {"Authorization": f"Bearer {u['token']}"}


def _user(label="U", exchanging=True):
    email = f"TEST_v22_{uuid.uuid4().hex[:10]}@bookloop.com"
    r = requests.post(f"{API}/auth/register/dev-instant", json={"email": email, "password": "test1234", "name": f"V22 {label}"})
    assert r.status_code == 200, r.text
    d = r.json()
    u = {"token": d["session_token"], "id": d["user"]["user_id"], "name": d["user"]["name"]}
    if not exchanging:
        _set_exchanging(u, False)
    return u


def _set_exchanging(u, on):
    r = requests.put(f"{API}/users/me", json={"is_exchanging": on}, headers=_h(u))
    assert r.status_code == 200 and r.json()["user"]["is_exchanging"] is on, r.text


def _book(u, title="Book"):
    r = requests.post(f"{API}/books", json={"title": f"TEST {title}", "author": "T"}, headers=_h(u))
    assert r.status_code == 200, r.text
    return r.json()["book"]["id"]


def _request(a, b, **body):
    return requests.post(f"{API}/swaps", json={"receiver_id": b["id"], **body}, headers=_h(a))


def _post(u, path, body=None):
    return requests.post(f"{API}{path}", json=body, headers=_h(u))


def _say(u, sid, text="hi"):
    return _post(u, f"/swaps/{sid}/messages", {"text": text})


def _detail(u, sid):
    r = requests.get(f"{API}/swaps/{sid}", headers=_h(u))
    assert r.status_code == 200, r.text
    return r.json()


def _code(r):
    d = r.json().get("detail")
    return d.get("code") if isinstance(d, dict) else None


def _proposal(a, b, note=None):
    """A -> B request with books (+ optional note). Returns the swap id."""
    ba, bb = _book(a, "A side"), _book(b, "B side")
    body = {"offered_book_id": ba, "requested_book_id": bb}
    if note is not None:
        body["note"] = note
    r = _request(a, b, **body)
    assert r.status_code == 200, r.text
    return r.json()["swap"]["id"]


def _complete(a, b, sid):
    assert _post(b, f"/swaps/{sid}/accept").status_code == 200
    assert _post(a, f"/swaps/{sid}/complete").status_code == 200
    assert _post(b, f"/swaps/{sid}/complete").status_code == 200
    assert _detail(a, sid)["swap"]["status"] == "completed"


def _counts(u):
    r = requests.get(f"{API}/notifications", headers=_h(u))
    assert r.status_code == 200, r.text
    return r.json()


def _feed(u):
    r = requests.get(f"{API}/notifications/feed", headers=_h(u))
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- A/B/E/F/G/H: chat access
class TestChatAccess:
    def test_a_stranger_cannot_send_messages(self):
        a, b, c = _user("A"), _user("B"), _user("C")
        sid = _proposal(a, b)
        # Neither side of a fresh request can chat, and an outsider can't even see it.
        for u in (a, b):
            r = _say(u, sid)
            assert r.status_code == 403 and _code(r) == "chat_locked", r.text
        assert _say(c, sid).status_code == 404
        assert requests.get(f"{API}/swaps/{sid}", headers=_h(c)).status_code == 404
        # Nothing was stored.
        assert not any(m["type"] == "text" for m in _detail(a, sid)["messages"])

    def test_b_stranger_can_propose_to_an_exchanging_reader(self):
        a, b = _user("A"), _user("B")
        sid = _proposal(a, b, note="Would you like to exchange these?")
        da, db_ = _detail(a, sid), _detail(b, sid)
        assert da["swap"]["status"] == "pending"
        assert da["swap"]["chat"] == {"state": "locked_waiting", "can_send": False}
        assert db_["swap"]["chat"] == {"state": "locked_needs_you", "can_send": False}
        # The recipient sees the books of the proposal and can inspect the requester's shelf.
        ap = db_["swap"]["active_proposal"]
        assert ap["offered"]["title"] == "TEST A side" and ap["requested"]["title"] == "TEST B side"
        prof = requests.get(f"{API}/users/{a['id']}", headers=_h(b))
        assert prof.status_code == 200 and any(bk["title"] == "TEST A side" for bk in prof.json()["books"])
        assert requests.get(f"{API}/swaps/with/{b['id']}", headers=_h(a)).json()["can_propose"] is True

    def test_e_pending_proposal_does_not_unlock_chat(self):
        a, b = _user("A"), _user("B")
        sid = _proposal(a, b)
        # Even proposing again (a structured change) does not open the chat.
        assert _post(a, f"/swaps/{sid}/propose", {"offered_book_id": _book(a, "A2"), "requested_book_id": _book(b, "B2")}).status_code == 200
        assert _say(a, sid).status_code == 403
        assert _detail(a, sid)["swap"]["chat"]["can_send"] is False

    def test_f_accepting_the_proposal_unlocks_chat(self):
        a, b = _user("A"), _user("B")
        sid = _proposal(a, b)
        assert _post(b, f"/swaps/{sid}/accept").status_code == 200
        assert _say(a, sid, "hello").status_code == 200
        assert _say(b, sid, "hi back").status_code == 200
        assert _detail(a, sid)["swap"]["chat"] == {"state": "open", "can_send": True}

    def test_f_accept_to_continue_chatting_unlocks_chat_without_accepting_books(self):
        a, b = _user("A"), _user("B")
        sid = _proposal(a, b)
        r = _post(a, f"/swaps/{sid}/allow-chat")
        assert r.status_code == 403 and _code(r) == "only_recipient"  # the requester can't unlock it
        assert _post(b, f"/swaps/{sid}/allow-chat").status_code == 200
        assert _post(b, f"/swaps/{sid}/allow-chat").json()["already"] is True  # idempotent
        d = _detail(a, sid)
        assert d["swap"]["status"] == "pending" and d["swap"]["chat_accepted"] is True
        assert d["messages"][-1]["key"] == "swapChat.sys.chatAccepted"
        assert _say(a, sid).status_code == 200 and _say(b, sid).status_code == 200
        # Declining afterwards closes that chat again (they never met).
        assert _post(b, f"/swaps/{sid}/decline").status_code == 200
        assert _say(a, sid).status_code == 409
        assert _detail(a, sid)["swap"]["chat"]["state"] == "closed"

    def test_g_note_is_not_a_chat_message_and_grants_nothing(self):
        a, b = _user("A"), _user("B")
        sid = _proposal(a, b, note="  My copy is almost new.​‮\n\n\n\nThanks!  ")
        d = _detail(b, sid)
        assert d["swap"]["note"] == "My copy is almost new.\n\nThanks!"  # trimmed, invisible/bidi chars gone
        assert not any("almost new" in (m.get("text") or "") for m in d["messages"])  # not in the chat
        assert d["swap"]["chat"]["can_send"] is False
        assert _say(a, sid).status_code == 403

    def test_g_note_is_limited(self):
        a, b = _user("A"), _user("B")
        r = _request(a, b, note="x" * 281)
        assert r.status_code == 400 and _code(r) == "note_too_long"
        assert _request(a, b, note="x" * 280).status_code == 200
        # Only one open request per pair: a second "request" returns the same swap, never a new note.
        again = _request(a, b, note="another note to sneak in")
        assert again.json()["existing"] is True and again.json()["swap"]["note"] == "x" * 280

    def test_g_legacy_message_field_becomes_the_note(self):
        a, b = _user("A"), _user("B")
        r = _request(a, b, message="Hi from an old app")
        assert r.status_code == 200 and r.json()["swap"]["note"] == "Hi from an old app"
        sid = r.json()["swap"]["id"]
        assert not any(m["type"] == "text" for m in _detail(b, sid)["messages"])

    def test_locked_requester_can_revise_proposal_only_a_few_times(self):
        a, b = _user("A"), _user("B")
        sid = _proposal(a, b)  # proposal 1
        for i in range(2):  # proposals 2 and 3
            assert _post(a, f"/swaps/{sid}/propose", {"offered_book_id": _book(a, f"r{i}"), "requested_book_id": _book(b, f"s{i}")}).status_code == 200
        r = _post(a, f"/swaps/{sid}/propose", {"offered_book_id": _book(a, "r9"), "requested_book_id": _book(b, "s9")})
        assert r.status_code == 429 and _code(r) == "proposal_limit"
        # Once the recipient opens the chat, negotiating is unrestricted again.
        assert _post(b, f"/swaps/{sid}/allow-chat").status_code == 200
        assert _post(a, f"/swaps/{sid}/propose", {"offered_book_id": _book(a, "r10"), "requested_book_id": _book(b, "s10")}).status_code == 200

    def test_h_completed_swap_keeps_chat_open(self):
        a, b = _user("A"), _user("B")
        sid = _proposal(a, b)
        _complete(a, b, sid)
        assert _say(a, sid, "thanks for the book!").status_code == 200
        assert _say(b, sid, "enjoy!").status_code == 200
        assert _detail(b, sid)["swap"]["chat"]["state"] == "open"

    def test_met_pair_keeps_chat_in_later_cancelled_swap(self):
        a, b = _user("A"), _user("B")
        first = _proposal(a, b)
        _complete(a, b, first)
        second = _proposal(a, b)
        # They met before: the new pending swap is open to chat, and stays open after a cancel.
        assert _say(b, second).status_code == 200
        assert _post(a, f"/swaps/{second}/cancel").status_code == 200
        assert _say(b, second, "no worries").status_code == 200

    def test_accepted_then_cancelled_counts_as_met(self):
        a, b = _user("A"), _user("B")
        sid = _proposal(a, b)
        assert _post(b, f"/swaps/{sid}/accept").status_code == 200
        assert _post(a, f"/swaps/{sid}/cancel").status_code == 200
        assert _say(b, sid, "shame, maybe next time").status_code == 200

    def test_declined_stranger_gets_no_chat(self):
        a, b = _user("A"), _user("B")
        sid = _proposal(a, b)
        assert _post(b, f"/swaps/{sid}/decline").status_code == 200
        assert _say(a, sid).status_code == 409
        assert _say(b, sid).status_code == 409


# ---------------------------------------------------------------- C/D: Currently exchanging
class TestCurrentlyExchanging:
    def test_c_off_blocks_new_proposals_but_profile_stays_visible(self):
        a, b = _user("A"), _user("B")
        bb = _book(b, "visible")
        _set_exchanging(b, False)
        for body in ({}, {"note": "hi"}, {"offered_book_id": _book(a, "x"), "requested_book_id": bb}):
            r = _request(a, b, **body)
            assert r.status_code == 403 and _code(r) == "not_exchanging", r.text
        prof = requests.get(f"{API}/users/{b['id']}", headers=_h(a))
        assert prof.status_code == 200 and prof.json()["user"]["is_exchanging"] is False
        assert any(bk["id"] == bb for bk in prof.json()["books"])  # inventory still viewable
        w = requests.get(f"{API}/swaps/with/{b['id']}", headers=_h(a)).json()
        assert w["can_propose"] is False and w["open"] is None
        # No swap was created behind the scenes.
        assert not _db().swaps.find_one({"requester_id": a["id"], "receiver_id": b["id"]})
        _set_exchanging(b, True)
        assert _request(a, b).status_code == 200

    def test_d_completed_partners_keep_chatting_when_off(self):
        a, b = _user("A"), _user("B")
        sid = _proposal(a, b)
        _complete(a, b, sid)
        _set_exchanging(b, False)
        assert _say(a, sid, "still there?").status_code == 200
        assert _say(b, sid, "yes!").status_code == 200
        w = requests.get(f"{API}/swaps/with/{b['id']}", headers=_h(a)).json()
        assert w["can_propose"] is False and w["latest_chat_open"] is True
        # ...but no NEW proposal, even from a previous partner.
        assert _code(_request(a, b)) == "not_exchanging"

    def test_turning_off_leaves_existing_open_request_intact(self):
        a, b = _user("A"), _user("B")
        sid = _proposal(a, b)
        _set_exchanging(b, False)
        r = _request(a, b)
        assert r.status_code == 200 and r.json()["existing"] is True and r.json()["swap"]["id"] == sid
        assert _post(b, f"/swaps/{sid}/accept").status_code == 200


# ---------------------------------------------------------------- anti-spam
class TestRequestLimits:
    def test_recently_declined_pair_is_blocked(self):
        a, b = _user("A"), _user("B")
        sid = _proposal(a, b)
        assert _post(b, f"/swaps/{sid}/decline").status_code == 200
        r = _request(a, b, note="please?")
        assert r.status_code == 429 and _code(r) == "recently_declined"

    def test_per_pair_daily_limit(self):
        a, b = _user("A"), _user("B")
        for _ in range(3):
            r = _request(a, b)
            assert r.status_code == 200, r.text
            assert _post(a, f"/swaps/{r.json()['swap']['id']}/cancel").status_code == 200
        r = _request(a, b)
        assert r.status_code == 429 and _code(r) == "rate_limited"

    def test_unanswered_requests_are_capped(self):
        a = _user("Spammer")
        codes = [_request(a, _user(f"R{i}")).status_code for i in range(11)]
        assert codes[:10] == [200] * 10 and codes[10] == 429, codes


# ---------------------------------------------------------------- I + events: unread state
class TestUnreadAndEvents:
    def test_i_unread_messages_in_completed_conversation(self):
        a, b = _user("A"), _user("B")
        sid = _proposal(a, b)
        _complete(a, b, sid)
        assert _post(b, f"/swaps/{sid}/read").status_code == 200  # b is up to date
        assert _counts(b)["unread_messages"] == 0
        m1 = _say(a, sid, "one").json()["message"]
        _say(a, sid, "two")
        # Loading the app (counters, lists, feed) never marks anything read.
        for _ in range(2):
            n = _counts(b)
            assert n["unread_messages"] == 2 and n["unread_total"] >= 2
            row = next(s for s in requests.get(f"{API}/swaps", headers=_h(b)).json()["completed"] if s["id"] == sid)
            assert row["unread_count"] == 2
            item = next(i for i in _feed(b)["items"] if i["kind"] == "message" and i["swap_id"] == sid)
            assert item["unread_count"] == 2 and item["swap_status"] == "completed" and item["actor"]["user_id"] == a["id"]
        assert _counts(a)["unread_messages"] == 0  # your own messages are never unread for you
        # Reading up to the first message leaves exactly the second one unread.
        assert _post(b, f"/swaps/{sid}/read", {"message_id": m1["id"]}).status_code == 200
        assert _counts(b)["unread_messages"] == 1
        assert _post(b, f"/swaps/{sid}/read").status_code == 200
        assert _counts(b)["unread_messages"] == 0
        assert not any(i["kind"] == "message" for i in _feed(b)["items"])

    def test_read_marker_never_moves_backwards(self):
        a, b = _user("A"), _user("B")
        sid = _proposal(a, b)
        _post(b, f"/swaps/{sid}/allow-chat")
        m1 = _say(a, sid, "one").json()["message"]
        _say(a, sid, "two")
        _post(b, f"/swaps/{sid}/read")
        _post(b, f"/swaps/{sid}/read", {"message_id": m1["id"]})  # a late, older read report
        assert _counts(b)["unread_messages"] == 0

    def test_events_for_each_step_and_no_duplicates(self):
        a, b = _user("A"), _user("B")
        sid = _proposal(a, b, note="hello")
        ev = [i for i in _feed(b)["items"] if i["kind"] == "event"]
        assert [(e["type"], e["swap_id"], e["read"]) for e in ev] == [("proposal_received", sid, False)]
        # A revised proposal refreshes the same unread event instead of adding a second one.
        _post(a, f"/swaps/{sid}/propose", {"offered_book_id": _book(a, "r"), "requested_book_id": _book(b, "s")})
        assert [e["type"] for e in _feed(b)["items"] if e["kind"] == "event"] == ["proposal_received"]
        assert _counts(b)["unread_events"] == 1
        assert _post(b, f"/swaps/{sid}/accept").status_code == 200
        assert [e["type"] for e in _feed(a)["items"] if e["kind"] == "event"] == ["proposal_accepted"]
        # Opening the conversation marks that swap's events read.
        assert _post(b, f"/swaps/{sid}/read").status_code == 200
        assert _counts(b)["unread_events"] == 0

    def test_decline_cancel_and_chat_accept_events(self):
        a, b = _user("A"), _user("B")
        s1 = _proposal(a, b)
        _post(b, f"/swaps/{s1}/allow-chat")
        _post(b, f"/swaps/{s1}/decline")
        types = [e["type"] for e in _feed(a)["items"] if e["kind"] == "event"]
        assert types == ["proposal_declined", "chat_accepted"]
        c = _user("C")
        s2 = _proposal(a, c)
        _post(a, f"/swaps/{s2}/cancel")
        assert [e["type"] for e in _feed(c)["items"] if e["kind"] == "event"] == ["proposal_cancelled", "proposal_received"]

    def test_mark_single_and_all_read(self):
        a, b = _user("A"), _user("B")
        sid = _proposal(a, b)
        ev = next(i for i in _feed(b)["items"] if i["kind"] == "event")
        assert _post(b, f"/notifications/{ev['id']}/read").status_code == 200
        assert _counts(b)["unread_events"] == 0
        _post(b, f"/swaps/{sid}/allow-chat")
        _say(a, sid, "x")
        assert _counts(b)["unread_total"] == 1
        assert _post(b, "/notifications/read-all").status_code == 200
        assert _counts(b)["unread_total"] == 0

    def test_completed_swap_is_not_an_incoming_request(self):
        a, b = _user("A"), _user("B")
        sid = _proposal(a, b)
        _complete(a, b, sid)
        buckets = requests.get(f"{API}/swaps", headers=_h(b)).json()
        assert not any(s["id"] == sid for s in buckets["incoming"])
        assert _counts(b)["incoming_requests"] == 0


# ---------------------------------------------------------------- push opt-in (recorded, not sent)
TOKEN = "ExponentPushToken[" + "t" * 22 + "]"


class TestPushOptIn:
    def _pushes(self, u):
        return list(_db().push_debug.find({"user_id": u["id"]}))

    def test_push_only_when_enabled_and_after_completion(self):
        a, b = _user("A"), _user("B")
        token = "ExponentPushToken[" + uuid.uuid4().hex[:22] + "]"
        assert _post(b, "/push/register", {"token": token, "platform": "android"}).status_code == 200
        sid = _proposal(a, b)
        assert self._pushes(b) == []  # notifications are OFF by default
        r = requests.put(f"{API}/users/me", json={"notifications_enabled": True}, headers=_h(b))
        assert r.status_code == 200 and r.json()["user"]["notifications_enabled"] is True
        _complete(a, b, sid)
        _say(a, sid, "after completion")
        kinds = [p["kind"] for p in self._pushes(b)]
        assert "message" in kinds
        msg = next(p for p in self._pushes(b) if p["kind"] == "message")
        # Private: the push names the sender but never carries the message (see TestChatPushPrivacy).
        assert msg["to"] == token and "after completion" not in str(msg) and msg["data"]["url"] == f"/swap/{sid}"
        # Turning it off stops pushes (unread state is unaffected).
        requests.put(f"{API}/users/me", json={"notifications_enabled": False}, headers=_h(b))
        before = len(self._pushes(b))
        _say(a, sid, "while off")
        assert len(self._pushes(b)) == before
        assert _counts(b)["unread_messages"] >= 1

    def test_token_validation_and_ownership(self):
        a, b = _user("A"), _user("B")
        assert _post(a, "/push/register", {"token": "not-a-token"}).status_code == 400
        token = "ExponentPushToken[" + uuid.uuid4().hex[:22] + "]"
        assert _post(a, "/push/register", {"token": token}).status_code == 200
        # b can't remove a's token.
        _post(b, "/push/unregister", {"token": token})
        assert _db().push_tokens.find_one({"token": token})["user_id"] == a["id"]
        # The same device signing in as b moves the token to b.
        _post(b, "/push/register", {"token": token})
        assert _db().push_tokens.count_documents({"token": token}) == 1
        assert _db().push_tokens.find_one({"token": token})["user_id"] == b["id"]
        assert _post(b, "/push/unregister", {"token": token}).status_code == 200
        assert _db().push_tokens.find_one({"token": token}) is None


class TestChatPushPrivacy:
    """Chat pushes reach lock screens and third-party push services (Expo, FCM/APNs): they say who wrote,
    in the RECIPIENT's language, and nothing about what was written."""
    SECRET = "my flat is 12B, door code 4471"
    EXPECTED = {
        "en": "You have a new message from {name}",
        "uz": "{name}dan sizga yangi xabar keldi",
        "ru": "У вас новое сообщение от {name}",
        "it": "Hai un nuovo messaggio da {name}",
        "ar": "لديك رسالة جديدة من {name}",
    }

    def _opt_in(self, u, lang=None):
        token = "ExponentPushToken[" + uuid.uuid4().hex[:22] + "]"
        assert _post(u, "/push/register", {"token": token, "platform": "ios"}).status_code == 200
        body = {"notifications_enabled": True, **({"preferred_language": lang} if lang else {})}
        assert requests.put(f"{API}/users/me", json=body, headers=_h(u)).status_code == 200
        return token

    def _pushes(self, u, kind=None):
        return [p for p in _db().push_debug.find({"user_id": u["id"]}, {"_id": 0, "created_at": 0})
                if kind is None or p["kind"] == kind]

    def _open_chat(self):
        a, b = _user("Sender"), _user("Receiver")
        sid = _proposal(a, b)
        assert _post(b, f"/swaps/{sid}/allow-chat").status_code == 200
        return a, b, sid

    def test_message_push_has_no_content_anywhere_and_routes_to_the_chat(self):
        a, b, sid = self._open_chat()
        token = self._opt_in(b)
        assert _say(a, sid, self.SECRET).status_code == 200
        [p] = self._pushes(b, "message")
        assert p["title"] == "BookLoop"
        assert p["body"] == f"You have a new message from {a['name']}"  # the sender's name may appear
        # Not in title, body, data, nor any other field of the payload.
        assert self.SECRET not in str(p) and "4471" not in str(p)
        # Only what the app needs to open (and not double-notify inside) the conversation.
        assert p["data"] == {"swap_id": sid, "type": "message", "url": f"/swap/{sid}"}
        assert p["to"] == token
        assert set(p) == {"to", "title", "body", "sound", "data", "channelId", "user_id", "kind"}
        # The recipient still reads the message in the authenticated chat.
        msgs = _detail(b, sid)["messages"]
        assert any(m.get("text") == self.SECRET for m in msgs)

    def test_photo_message_push_does_not_reveal_it_is_a_photo(self):
        a, b, sid = self._open_chat()
        self._opt_in(b)
        png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15"
               b"\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01\x5e\xf3\x2a\xea\x00\x00"
               b"\x00\x00IEND\xaeB`\x82")
        up = requests.post(f"{API}/upload", files={"file": ("p.png", png, "image/png")}, headers=_h(a))
        assert up.status_code == 200, up.text
        assert _post(a, f"/swaps/{sid}/messages", {"image_url": up.json()["url"]}).status_code == 200
        [p] = self._pushes(b, "message")
        assert p["body"] == f"You have a new message from {a['name']}"
        assert "/api/files" not in str(p) and "📷" not in str(p)

    def test_each_language_uses_the_recipients_language_not_the_senders(self):
        for lang, text in self.EXPECTED.items():
            a, b, sid = self._open_chat()
            requests.put(f"{API}/users/me", json={"preferred_language": "en" if lang != "en" else "ru"}, headers=_h(a))
            self._opt_in(b, lang)
            _say(a, sid, self.SECRET)
            [p] = self._pushes(b, "message")
            assert p["title"] == "BookLoop", lang
            assert p["body"] == text.format(name=a["name"]), lang
            assert self.SECRET not in str(p), lang

    def test_changing_the_recipients_language_changes_the_push(self):
        a, b, sid = self._open_chat()
        self._opt_in(b, "en")
        _say(a, sid, "one")
        requests.put(f"{API}/users/me", json={"preferred_language": "ar"}, headers=_h(b))
        _say(a, sid, "two")
        first, second = self._pushes(b, "message")
        assert first["body"] == self.EXPECTED["en"].format(name=a["name"])
        assert second["body"] == self.EXPECTED["ar"].format(name=a["name"])

    def test_sender_name_is_one_plain_line(self):
        a, b, sid = self._open_chat()
        r = requests.put(f"{API}/users/me", json={"name": "Eve\nYour account is suspended  {name}"}, headers=_h(a))
        assert r.status_code == 200
        self._opt_in(b)
        _say(a, sid, self.SECRET)
        [p] = self._pushes(b, "message")
        assert "\n" not in p["body"]
        assert p["body"] == "You have a new message from Eve Your account is suspended {name}"  # braces literal

    def test_event_pushes_still_describe_the_event(self):
        a, b = _user("A"), _user("B")
        self._opt_in(b, "it")
        self._opt_in(a, "ru")
        sid = _proposal(a, b)
        [received] = self._pushes(b, "proposal_received")
        assert received["title"] == f"Nuova proposta di scambio da {a['name']}" and received["body"] == ""
        assert received["data"] == {"swap_id": sid, "type": "proposal_received", "url": f"/swap/{sid}"}
        assert _post(b, f"/swaps/{sid}/allow-chat").status_code == 200
        [accepted] = self._pushes(a, "chat_accepted")
        assert accepted["title"].startswith(b["name"]) and "переписываться" in accepted["title"]


# ---------------------------------------------------------------- J/K/L: favorites + ownership
class TestFavorites:
    def _favs(self, u):
        r = requests.get(f"{API}/favorites", headers=_h(u))
        assert r.status_code == 200, r.text
        return [p["user_id"] for p in r.json()["favorites"]]

    def test_j_add_remove_persist_and_not_self(self):
        a, b = _user("A"), _user("B")
        r = requests.put(f"{API}/favorites/{b['id']}", headers=_h(a))
        assert r.status_code == 200 and r.json()["is_favorite"] is True
        assert self._favs(a) == [b["id"]]
        assert requests.get(f"{API}/users/{b['id']}", headers=_h(a)).json()["user"]["is_favorite"] is True
        # Survives a new login (it lives on the server).
        again = requests.post(f"{API}/auth/login", json={"email": _db().users.find_one({"user_id": a["id"]})["email"], "password": "test1234"})
        assert self._favs({"token": again.json()["session_token"]}) == [b["id"]]
        assert requests.delete(f"{API}/favorites/{b['id']}", headers=_h(a)).status_code == 200
        assert self._favs(a) == []
        assert requests.get(f"{API}/users/{b['id']}", headers=_h(a)).json()["user"]["is_favorite"] is False
        r = requests.put(f"{API}/favorites/{a['id']}", headers=_h(a))
        assert r.status_code == 400 and _code(r) == "cannot_favorite_self"
        assert requests.put(f"{API}/favorites/user_doesnotexist", headers=_h(a)).status_code == 404

    def test_j_favoriting_notifies_nobody_and_grants_nothing(self):
        a, b = _user("A"), _user("B")
        requests.put(f"{API}/favorites/{b['id']}", headers=_h(a))
        assert _feed(b)["items"] == [] and _counts(b)["unread_total"] == 0
        w = requests.get(f"{API}/swaps/with/{b['id']}", headers=_h(a)).json()
        assert w["open"] is None and w["latest"] is None
        assert not _db().swaps.find_one({"$or": [{"requester_id": a["id"]}, {"receiver_id": a["id"]}]})

    def test_j_discover_reports_favorite_state(self):
        a, b = _user("A"), _user("Findme" + uuid.uuid4().hex[:6])
        requests.put(f"{API}/favorites/{b['id']}", headers=_h(a))
        name = _db().users.find_one({"user_id": b["id"]})["name"]
        people = requests.get(f"{API}/discover/people", params={"search": name}, headers=_h(a)).json()["people"]
        assert [p["is_favorite"] for p in people if p["user_id"] == b["id"]] == [True]

    def test_k_duplicates_prevented(self):
        a, b = _user("A"), _user("B")
        for _ in range(3):
            assert requests.put(f"{API}/favorites/{b['id']}", headers=_h(a)).status_code == 200
        threads = [threading.Thread(target=requests.put, args=(f"{API}/favorites/{b['id']}",), kwargs={"headers": _h(a)}) for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert _db().favorites.count_documents({"user_id": a["id"], "target_id": b["id"]}) == 1
        assert self._favs(a) == [b["id"]]

    def test_l_cannot_touch_other_users_favorites_or_read_state(self):
        a, b, c = _user("A"), _user("B"), _user("C")
        requests.put(f"{API}/favorites/{b['id']}", headers=_h(a))
        # c "removing" b only ever acts on c's own (empty) list.
        assert requests.delete(f"{API}/favorites/{b['id']}", headers=_h(c)).status_code == 200
        assert self._favs(a) == [b["id"]]
        # Read state: c can't mark a/b's conversation or b's notifications read.
        sid = _proposal(a, b)
        ev = next(i for i in _feed(b)["items"] if i["kind"] == "event")
        assert _post(c, f"/swaps/{sid}/read").status_code == 404
        assert _post(c, f"/notifications/{ev['id']}/read").status_code == 404
        _post(c, "/notifications/read-all")
        assert _counts(b)["unread_events"] == 1
        # And nothing works without a session.
        assert requests.get(f"{API}/favorites").status_code == 401
        assert requests.put(f"{API}/favorites/{b['id']}").status_code == 401
        assert requests.post(f"{API}/notifications/{ev['id']}/read").status_code == 401
        assert requests.post(f"{API}/swaps/{sid}/read").status_code == 401


# ---------------------------------------------------------------- M: records written before v2.2
class TestLegacyRecords:
    """Swaps/messages exactly as the pre-v2.2 backend wrote them (no note, no chat_accepted_at, no read
    markers, no last_chat_at) keep working."""

    def _legacy_swap(self, a, b, status, messages):
        long_ago = datetime.now(timezone.utc) - timedelta(days=30)
        sid = "swap_legacy_" + uuid.uuid4().hex[:10]
        _db().swaps.insert_one({
            "id": sid, "requester_id": a["id"], "receiver_id": b["id"], "status": status,
            "requester_completed": status == "completed", "receiver_completed": status == "completed",
            "requester_rated": False, "receiver_rated": False, "active_proposal": None,
            "last_message": "old", "created_at": long_ago, "updated_at": long_ago,
        })
        for i, (sender, mtype, text, key) in enumerate(messages):
            _db().messages.insert_one({
                "id": f"msg_legacy_{uuid.uuid4().hex[:10]}", "swap_id": sid, "sender_id": sender, "type": mtype,
                "text": text, "key": key, "params": {}, "proposal": None, "image_url": None,
                "created_at": long_ago + timedelta(minutes=i),
            })
        return sid

    def test_m_legacy_completed_swap(self):
        a, b = _user("A"), _user("B")
        sid = self._legacy_swap(a, b, "completed", [(a["id"], "text", "old hello", None), (b["id"], "text", "old reply", None)])
        d = _detail(a, sid)
        assert d["swap"]["note"] is None and d["swap"]["chat"]["state"] == "open"
        assert [m["text"] for m in d["messages"]] == ["old hello", "old reply"]
        # History from before unread tracking is not suddenly "unread".
        assert _counts(a)["unread_messages"] == 0 and _counts(b)["unread_messages"] == 0
        assert _say(b, sid, "new after upgrade").status_code == 200
        assert _counts(a)["unread_messages"] == 1

    def test_m_legacy_pending_swap_where_recipient_replied_stays_open(self):
        a, b = _user("A"), _user("B")
        sid = self._legacy_swap(a, b, "pending", [(a["id"], "text", "hi", None), (b["id"], "text", "hello!", None)])
        assert _say(a, sid).status_code == 200 and _say(b, sid).status_code == 200

    def test_m_legacy_pending_swap_without_reply_is_locked(self):
        a, b = _user("A"), _user("B")
        sid = self._legacy_swap(a, b, "pending", [(a["id"], "text", "hi?", None), (a["id"], "text", "hello??", None)])
        assert _say(a, sid).status_code == 403
        assert _post(b, f"/swaps/{sid}/allow-chat").status_code == 200
        assert _say(a, sid).status_code == 200

    def test_m_legacy_accepted_then_cancelled_swap_counts_as_met(self):
        a, b = _user("A"), _user("B")
        sid = self._legacy_swap(a, b, "cancelled", [(None, "system", "Swap confirmed!", "swapChat.sys.confirmed")])
        assert _say(a, sid).status_code == 200
        # A later request between them is open to chat too.
        new = _request(a, b).json()["swap"]["id"]
        assert _detail(b, new)["swap"]["chat"]["state"] == "open"

    def test_m_legacy_users_without_new_fields(self):
        u = _user("Legacy")
        _db().users.update_one({"user_id": u["id"]}, {"$unset": {"notifications_enabled": ""}})
        me = requests.get(f"{API}/auth/me", headers=_h(u)).json()["user"]
        assert me["notifications_enabled"] is False
        assert requests.get(f"{API}/favorites", headers=_h(u)).json() == {"favorites": []}
