"""Which app builds active accounts use, and how their Google sign-ins run — the numbers that decide
when GOOGLE_WEB_FLOW_APP_SCHEMES (the browser Google flow returning to bookloop://) can be switched off.

    python scripts/client_versions.py            # accounts seen in the last 30 days
    python scripts/client_versions.py --days 7

Read-only. "(none)" = a build that sends no X-App-Version header, i.e. one from before native Google
sign-in. Uses MONGO_URL / DB_NAME from backend/.env (or the environment), like server.py.
"""
import argparse
import asyncio
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402


async def main(days: int) -> None:
    since = server.now_utc() - timedelta(days=days)
    builds, google = Counter(), Counter()
    async for u in server.db.users.find(
        {"deleted_at": None, "client_seen_at": {"$gte": since}},
        {"_id": 0, "client_platform": 1, "client_version": 1, "google_signin_via": 1, "google_signin_at": 1},
    ):
        builds[(u.get("client_platform") or "(none)", u.get("client_version") or "(none)")] += 1
        at = u.get("google_signin_at")
        if at and server._aware(at) >= since:
            google[u.get("google_signin_via") or "?"] += 1
    print(f"Accounts seen in the last {days} days: {sum(builds.values())}")
    for (platform, version), n in builds.most_common():
        print(f"  {platform:<10} {version:<16} {n}")
    print(f"\nGoogle sign-ins in the last {days} days (latest per account):")
    for via, n in google.most_common():
        print(f"  {via:<14} {n}")
    if not google:
        print("  none")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    asyncio.run(main(ap.parse_args().days))
