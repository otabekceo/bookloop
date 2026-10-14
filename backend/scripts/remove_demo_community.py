"""Remove the seeded demo community (10 made-up readers, their books, swaps and reviews) from a
database — e.g. production, where it was created before seeding became opt-in.

    python scripts/remove_demo_community.py           # dry run: only says what would be removed
    python scripts/remove_demo_community.py --yes     # actually remove it

Only accounts the seeder created are touched (seed=True, an "@demo.bookloop" email and no
password). Their demo-only swaps and reviews are deleted outright; anything a real reader did with
a demo reader (a swap, a favorite, a chat) is treated exactly like an account deletion
(server.delete_account): kept on the real reader's side, without identity.

Uses MONGO_URL / DB_NAME from backend/.env (or the environment), like server.py. Make sure
SEED_DEMO_COMMUNITY is not set on the server, or the next start recreates the demo community.
"""
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402


async def main(apply: bool) -> None:
    demo = {"seed": True, "email": {"$regex": re.escape(server.DEMO_EMAIL_DOMAIN) + "$"}, "password_hash": None}
    readers = await server.db.users.count_documents({**demo, "deleted_at": None})
    print(f"demo readers: {readers}")
    print(f"demo books:   {await server.db.books.count_documents({'seed': True})}")
    print(f"demo swaps:   {await server.db.swaps.count_documents({'seed': True})}")
    print(f"demo reviews: {await server.db.ratings.count_documents({'seed': True})}")
    if not apply:
        print("\nDry run — nothing changed. Re-run with --yes to remove them.")
        return
    await server.load_file_secret()
    n = await server.remove_demo_community()
    print(f"\nRemoved {n} demo readers.")


if __name__ == "__main__":
    asyncio.run(main("--yes" in sys.argv[1:]))
