"""READ-ONLY: classify every account as demo / test / review / genuine, for a human to review before any
cleanup. It never writes, updates or deletes anything, and it prints no email address, name, password hash,
session token or message text — only user ids, yes/no indicators and record counts.

    python scripts/identify_test_accounts.py --out candidates.csv

Uses MONGO_URL / DB_NAME from the environment (or backend/.env), like server.py. Against production, connect
with a database user that has ONLY read access, so the script physically cannot change anything; it
refuses to run when the connected user could write.

Classes (the "reasons" column gives the evidence of each row):
  demo       seed=True + "@demo.bookloop" email + no password: the seeded demo readers.
  test       server-set proof: test_account=True (only /auth/register/dev-instant sets it, a route the app
             never calls and that exists only on OTP_DEBUG_MODE servers), or the password is "test1234" —
             the test suite's password, which every email registration since 2026-09-26 (OTP + server-side
             password policy, before the first APK build) rejects, so no APK registration can have it.
  review     anything weaker or mixed: an email that only LOOKS like a test address, a policy-compliant
             password the tests also use, a "test" account that is also linked to Google/Apple or has a
             real-looking push token, or one that has swaps/messages with non-test readers.
  genuine    no test indicator at all.
  tombstone  already deleted (deleted_at set).
Email patterns and creation dates alone NEVER make an account "test".
"""
import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import bcrypt
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DEMO_DOMAIN = "@demo.bookloop"
# The password the test suite has always used; it fails validate_password (no upper case, no symbol).
POLICY_IMPOSSIBLE_TEST_PASSWORDS = ("test1234",)
# Test-suite passwords that DO satisfy the policy: a real reader could have chosen one, so only a hint.
POLICY_COMPLIANT_TEST_PASSWORDS = ("Test1234!", "Str0ng!Pass")
PUBLIC_TEST_EMAILS = ("test@bookloop.com",)
TEST_EMAIL_PATTERNS = [re.compile(p) for p in (
    r"^test_[a-z0-9_]+@bookloop\.com$", r"^probe_[a-z0-9]+@bookloop\.com$", r"^[a-z0-9_]+@example\.com$",
    r"^delivered(\+otp_[0-9a-f]+)?@resend\.dev$",
)]
# The old open /auth/register (before 2026-09-26) gave every account this made-up location.
LEGACY_DEFAULT_LOCATION = (38.1938, 15.5540)
# Everything MongoDB's built-in read roles allow; any other privilege (insert, update, remove, drop…) makes the
# script refuse to run.
READ_ONLY_ACTIONS = {"find", "listCollections", "listIndexes", "listSearchIndexes", "collStats", "dbStats",
                     "dbHash", "killCursors", "changeStream", "indexStats", "listDatabases", "planCacheRead",
                     "performRawDataOperations"}  # the last two: part of MongoDB 8's built-in read role


def email_class(email: str) -> str:
    e = (email or "").lower()
    if e in PUBLIC_TEST_EMAILS:
        return "public_test_account"
    if e.endswith(DEMO_DOMAIN):
        return "demo_domain"
    if any(p.match(e) for p in TEST_EMAIL_PATTERNS):
        return "test_like_pattern"
    return "other"


def password_matches(password_hash, candidates) -> bool:
    if not password_hash:
        return False
    for pw in candidates:
        try:
            if bcrypt.checkpw(pw.encode(), password_hash.encode()):
                return True
        except ValueError:
            return False
    return False


def classify(row: dict) -> tuple:
    """(class, reasons) for one row of indicators. Pure; unit-tested in tests/test_identify_test_accounts.py."""
    if row["deleted"]:
        return "tombstone", ["deleted_at set"]
    if row["seed"] and row["email_class"] == "demo_domain" and not row["has_password"]:
        return "demo", ["seed + demo domain + no password"]
    proof = []
    if row["test_account_flag"]:
        proof.append("test_account flag (server-set, dev-instant only)")
    if row["pw_policy_impossible_test"]:
        proof.append("password is the test suite's 'test1234' (impossible for APK registrations)")
    hints = []
    if row["email_class"] in ("public_test_account", "test_like_pattern"):
        hints.append(f"email {row['email_class']} (pattern only)")
    if row["pw_policy_compliant_test"]:
        hints.append("password equals a policy-compliant test password")
    if row["legacy_default_location"]:
        hints.append("legacy default location (old open registration)")
    genuine = []
    if row["google_linked"] or row["apple_linked"]:
        genuine.append("linked to a Google/Apple account")
    if row["push_tokens_real_looking"]:
        genuine.append("real-looking push token")
    if row["swaps_with_non_test"] or row["messages_with_non_test"]:
        genuine.append("swaps/messages with readers not classified test")
    if proof and not genuine:
        return "test", proof + hints
    if proof or hints:
        return "review", proof + hints + [f"BUT {g}" for g in genuine]
    return "genuine", genuine or ["no test indicator"]


def _counts(coll, field, match=None):
    pipeline = ([{"$match": match}] if match else []) + [{"$group": {"_id": f"${field}", "n": {"$sum": 1}}}]
    return {d["_id"]: d["n"] for d in coll.aggregate(pipeline)}


def _real_looking_push(token: str) -> bool:
    # Test tokens are "ExponentPushToken[" + "t"*22 + "]" or 22 hex characters; Expo's real ones are mixed case.
    m = re.fullmatch(r"Expo(?:nent)?PushToken\[([^\]]+)\]", token or "")
    return bool(m) and not re.fullmatch(r"t+|[0-9a-f]+", m.group(1))


def write_privileges(status: dict) -> list:
    """Privileges beyond reading in a connectionStatus(showPrivileges=True) reply; [] = read-only."""
    privs = (status.get("authInfo") or {}).get("authenticatedUserPrivileges") or []
    return sorted({a for p in privs for a in p.get("actions", [])} - READ_ONLY_ACTIONS)


def _require_read_only(client, url: str) -> None:
    """Refuse to run with a database user that could change anything. An unauthenticated connection is
    accepted only for a local test database (localhost)."""
    status = client.admin.command("connectionStatus", showPrivileges=True)
    if not (status.get("authInfo") or {}).get("authenticatedUsers"):
        host = re.sub(r"^mongodb(\+srv)?://", "", url).split("/", 1)[0].split("@")[-1]
        if not re.fullmatch(r"(localhost|127\.0\.0\.1)(:\d+)?", host):
            sys.exit("Refusing to run: not authenticated. Use a read-only database user.")
        return
    extra = write_privileges(status)
    if extra:
        sys.exit(f"Refusing to run: this database user can also {', '.join(extra[:6])}"
                 f"{'...' if len(extra) > 6 else ''}. Use a user with only read access.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="CSV file to write (user ids and indicators only)")
    args = ap.parse_args()

    client = MongoClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=8000)
    _require_read_only(client, os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    books = _counts(db.books, "owner_id", {"deleted_at": None})
    uploads = _counts(db.uploads, "owner_id")
    sent = _counts(db.messages, "sender_id")
    sessions_last = {d["_id"]: d["last"] for d in db.user_sessions.aggregate(
        [{"$group": {"_id": "$user_id", "last": {"$max": "$created_at"}}}])}
    identities = defaultdict(set)
    for d in db.auth_identities.find({}, {"_id": 0, "user_id": 1, "provider": 1}):
        identities[d["user_id"]].add(d["provider"])
    push_real = Counter(d["user_id"] for d in db.push_tokens.find({}, {"_id": 0, "user_id": 1, "token": 1})
                        if _real_looking_push(d.get("token")))
    swaps = list(db.swaps.find({}, {"_id": 0, "id": 1, "requester_id": 1, "receiver_id": 1}))
    partners = defaultdict(set)
    for s in swaps:
        partners[s.get("requester_id")].add(s.get("receiver_id"))
        partners[s.get("receiver_id")].add(s.get("requester_id"))

    rows = []
    for u in db.users.find({}, {"_id": 0, "user_id": 1, "email": 1, "password_hash": 1, "seed": 1,
                                "test_account": 1, "deleted_at": 1, "created_at": 1, "lat": 1, "lng": 1,
                                "location_updated_at": 1, "google_sub": 1, "apple_sub": 1}):
        uid = u["user_id"]
        ph = u.get("password_hash")
        rows.append({
            "user_id": uid,
            "created_date": u["created_at"].date().isoformat() if u.get("created_at") else "",
            "deleted": bool(u.get("deleted_at")),
            "seed": bool(u.get("seed")),
            "test_account_flag": bool(u.get("test_account")),
            "email_class": email_class(u.get("email")),
            "has_password": bool(ph),
            "pw_policy_impossible_test": password_matches(ph, POLICY_IMPOSSIBLE_TEST_PASSWORDS),
            "pw_policy_compliant_test": password_matches(ph, POLICY_COMPLIANT_TEST_PASSWORDS),
            "legacy_default_location": (u.get("lat"), u.get("lng")) == LEGACY_DEFAULT_LOCATION
                                       and not u.get("location_updated_at"),
            "google_linked": "google" in identities[uid] or bool(u.get("google_sub")),
            "apple_linked": "apple" in identities[uid] or bool(u.get("apple_sub")),
            "push_tokens_real_looking": push_real[uid],
            "books": books.get(uid, 0),
            "uploads": uploads.get(uid, 0),
            "messages_sent": sent.get(uid, 0),
            "swap_partners": len(partners[uid] - {None}),
            "last_session_date": sessions_last[uid].date().isoformat() if sessions_last.get(uid) else "",
        })

    # Second pass: which accounts have swaps with readers that are not themselves provably test/demo?
    provable = set()
    for r in rows:
        probe = dict(r, swaps_with_non_test=0, messages_with_non_test=0)
        if classify(probe)[0] in ("test", "demo"):
            provable.add(r["user_id"])
    for r in rows:
        others = partners[r["user_id"]] - provable - {None, r["user_id"]}
        r["swaps_with_non_test"] = len(others)
        r["messages_with_non_test"] = 0
        if others and r["user_id"] in provable:
            sids = [s["id"] for s in swaps if r["user_id"] in (s.get("requester_id"), s.get("receiver_id"))
                    and ({s.get("requester_id"), s.get("receiver_id")} & others)]
            r["messages_with_non_test"] = db.messages.count_documents({"swap_id": {"$in": sids}})
        r["class"], reasons = classify(r)
        r["reasons"] = "; ".join(reasons)

    fields = ["user_id", "class", "reasons", "created_date", "last_session_date", "deleted", "seed",
              "test_account_flag", "email_class", "has_password", "pw_policy_impossible_test",
              "pw_policy_compliant_test", "legacy_default_location", "google_linked", "apple_linked",
              "push_tokens_real_looking", "books", "uploads", "messages_sent", "swap_partners",
              "swaps_with_non_test", "messages_with_non_test"]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["class"], r["created_date"])):
            w.writerow({k: r[k] for k in fields})
    summary = Counter(r["class"] for r in rows)
    print(f"database: {os.environ['DB_NAME']}  accounts: {len(rows)}")
    for k in ("genuine", "review", "test", "demo", "tombstone"):
        print(f"  {k:10} {summary.get(k, 0)}")
    print(f"written: {args.out} (user ids and indicators only; nothing was modified)")


if __name__ == "__main__":
    main()
