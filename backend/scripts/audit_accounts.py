"""Read-only report of who is in the users collection. It never modifies or deletes anything.

    python scripts/audit_accounts.py            # summary + the accounts that look genuine
    python scripts/audit_accounts.py --all      # also list every probable test account

Uses MONGO_URL / DB_NAME from backend/.env (or the environment), like server.py.

Categories:
  demo        seed=True AND an "@demo.bookloop" email AND no password — the seeded demo community,
              managed by sync_demo_community() in server.py (kept at exactly 10).
  test        test_account=True — created by the test-only /auth/register/dev-instant route.
  probable    NO flag in the schema; only the email pattern suggests the test suite made it (e.g.
              test_1a2b3c4d@bookloop.com, g_1a2b3c4d@example.com, delivered+otp_…@resend.dev).
              Email patterns are not proof, so these are reported for a human to review, never removed.
  genuine     everything else.
"""
import os
import re
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

PROBABLE_TEST_EMAILS = [
    re.compile(r"^test@bookloop\.com$"),
    re.compile(r"^test_(rev_)?[0-9a-f]{8,10}@bookloop\.com$"),
    re.compile(r"^probe_[a-z0-9]+@bookloop\.com$"),
    re.compile(r"^g_[0-9a-f]{8}@example\.com$"),
    re.compile(r"^[a-z0-9_]+@example\.com$"),
    re.compile(r"^delivered(\+otp_[0-9a-f]+)?@resend\.dev$"),
]


def classify(u: dict) -> str:
    email = (u.get("email") or "").lower()
    if u.get("seed") and email.endswith("@demo.bookloop") and not u.get("password_hash"):
        return "demo"
    if u.get("test_account"):
        return "test"
    if any(p.match(email) for p in PROBABLE_TEST_EMAILS):
        return "probable"
    return "genuine"


def main() -> None:
    show_all = "--all" in sys.argv
    db = MongoClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=5000)[os.environ["DB_NAME"]]
    counts: Counter = Counter()
    rows: dict = {"genuine": [], "probable": [], "demo": []}
    for u in db.users.find({}, {"_id": 0, "email": 1, "name": 1, "seed": 1, "test_account": 1, "password_hash": 1,
                                  "created_at": 1, "deleted_at": 1}):
        kind = classify(u)
        counts[kind] += 1
        if kind in rows:
            rows[kind].append(u)
    print(f"database: {os.environ['DB_NAME']}")
    for kind in ("demo", "test", "probable", "genuine"):
        print(f"  {kind:9} {counts[kind]}")
    print(f"  {'total':9} {sum(counts.values())}")
    print("\ndemo readers:")
    for u in rows["demo"]:
        print(f"  {u.get('name')} <{u.get('email')}>")
    print("\naccounts treated as GENUINE (never touched):")
    for u in rows["genuine"]:
        print(f"  {u.get('email')} | {u.get('name')} | created {u.get('created_at')}")
    if show_all:
        print("\nPROBABLE test accounts (email pattern only — review manually):")
        for u in rows["probable"]:
            print(f"  {u.get('email')} | {u.get('name')} | created {u.get('created_at')}")


if __name__ == "__main__":
    main()
