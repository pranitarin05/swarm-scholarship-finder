"""Check which seed URLs actually fetch OK (for pruning the seed list)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.agents.scout import SEED_CANDIDATES
from backend.app.tools.fetch import fetch_page


async def main() -> None:
    for name, url, country, ctype in SEED_CANDIDATES:
        page = await fetch_page(url)
        print(f"{page.status:3} {'OK ' if page.ok else 'BAD'} {len(page.text):5}  {name}")


if __name__ == "__main__":
    asyncio.run(main())
