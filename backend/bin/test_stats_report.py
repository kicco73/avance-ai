#!/usr/bin/env python3
import json
import sys
from pathlib import Path

STATS_PATH = Path(__file__).resolve().parent.parent / "test_stats.json"


def main() -> None:
    if not STATS_PATH.exists():
        print(f"No stats yet at {STATS_PATH} — run pytest first.")
        return

    stats = json.loads(STATS_PATH.read_text())
    rows = [
        (nodeid, e["runs"], e["failures"], e["skips"], e.get("last_failed"))
        for nodeid, e in stats.items()
    ]

    if "--never-failed" in sys.argv:
        rows = [r for r in rows if r[1] > 0 and r[2] == 0]
        rows.sort(key=lambda r: -r[1])
    else:
        rows.sort(key=lambda r: (-r[2], -(r[2] / r[1]) if r[1] else 0))

    print(f"{'runs':>5} {'fails':>5} {'skips':>5} {'rate':>6}  {'last_failed':<20}  test")
    for nodeid, runs, fails, skips, last_failed in rows:
        rate = f"{fails / runs:.0%}" if runs else "-"
        print(f"{runs:>5} {fails:>5} {skips:>5} {rate:>6}  {last_failed or '-':<20}  {nodeid}")


if __name__ == "__main__":
    main()
