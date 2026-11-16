"""Quick benchmark: parallel timing for all 14 datasets.

Usage (from backend/):
    python -m scripts.bench_parallel

Requires a running PostgreSQL database and network access to external APIs.
"""

import asyncio
import time

from app.database import async_session
from app.resolve.multi import DATASETS_DISPONIBLES, query_multiple_datasets


async def main() -> None:
    lat, lon = 48.8566, 2.3522  # Paris center
    all_ds = sorted(DATASETS_DISPONIBLES)
    print(f"Querying {len(all_ds)} datasets at ({lat}, {lon})...")

    start = time.perf_counter()
    result = await query_multiple_datasets(async_session, all_ds, lat, lon)
    elapsed = time.perf_counter() - start

    ok = len(result["donnees"])
    err = len(result["erreurs"]) if result["erreurs"] else 0
    print(f"Parallel: {elapsed:.3f}s -- {ok} OK, {err} errors")

    for name, data in sorted(result["donnees"].items()):
        typ = "list" if isinstance(data, list) else type(data).__name__
        print(f"  {name}: {typ}")

    if result["erreurs"]:
        for e in result["erreurs"]:
            print(f"  ERROR {e.dataset}: {e.message}")


if __name__ == "__main__":
    asyncio.run(main())
