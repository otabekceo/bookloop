"""Minimal moderation tool for user reports (POST /api/reports) until a proper admin UI exists.

    python scripts/moderation.py list                    # open reports, oldest first
    python scripts/moderation.py list --all              # every report
    python scripts/moderation.py show <report_id>        # one report with its content snapshot
    python scripts/moderation.py resolve <report_id> <note>    # mark handled (keeps the record)
    python scripts/moderation.py hide-book <book_id>           # take a listing out of Discover
    python scripts/moderation.py delete-account <user_id> --yes  # remove an abusive account

Every action is recorded on the report or printed; nothing is deleted without --yes. Uses
MONGO_URL / DB_NAME from backend/.env (or the environment), like server.py.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402


async def main(args: list) -> int:
    db = server.db
    cmd = args[0] if args else "list"
    if cmd == "list":
        q = {} if "--all" in args else {"status": "open"}
        async for r in db.reports.find(q, {"_id": 0}).sort("created_at", 1):
            print(f"{r['id']}  {r['created_at']:%Y-%m-%d %H:%M}  {r['status']:<8} {r['target_type']:<7} "
                  f"{r['reason']:<13} target={r['target_id']} user={r.get('target_user_id')}")
        return 0
    if cmd == "show" and len(args) > 1:
        r = await db.reports.find_one({"id": args[1]}, {"_id": 0})
        print(json.dumps(r, default=str, indent=2, ensure_ascii=False) if r else "not found")
        return 0 if r else 1
    if cmd == "resolve" and len(args) > 2:
        res = await db.reports.update_one({"id": args[1]}, {"$set": {
            "status": "resolved", "resolution": " ".join(args[2:]), "resolved_at": server.now_utc()}})
        print("resolved" if res.matched_count else "not found")
        return 0 if res.matched_count else 1
    if cmd == "hide-book" and len(args) > 1:
        res = await db.books.update_one({"id": args[1], "deleted_at": None},
                                        {"$set": {"deleted_at": server.now_utc(), "moderated": True}})
        print("hidden" if res.modified_count else "not found / already hidden")
        return 0
    if cmd == "delete-account" and len(args) > 1:
        if "--yes" not in args:
            print("This permanently deletes the account. Re-run with --yes.")
            return 1
        await server.load_file_secret()
        print(await server.delete_account(args[1]) or "not found")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
