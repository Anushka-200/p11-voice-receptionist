"""Load Vapi end-of-call reports saved on disk (logs/vapi/*end-of-call-report.json) into Postgres, then analyse them.

Why: the first calls were made before the server stored reports in the database. Safe to run twice (the call id is
unique, so a repeat changes nothing).

Usage: uv run python -m scripts.backfill_reports [--analyse]    (--analyse uses the LLM: a fraction of a cent per call)
"""

import argparse
import json
import sys
from pathlib import Path

import psycopg

from app.postcall import ingest_report, process_pending
from app.settings import get_settings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=get_settings().spike_payload_dir)
    parser.add_argument(
        "--analyse", action="store_true", help="also run the LLM analysis (needs ANTHROPIC_API_KEY)"
    )
    args = parser.parse_args()
    new = old = 0
    with psycopg.connect(get_settings().database_url) as conn:
        for path in sorted(Path(args.dir).glob("*end-of-call-report.json")):
            _, created = ingest_report(conn, json.loads(path.read_text()))
            new, old = new + created, old + (not created)
        conn.commit()
        print(f"reports: {new} newly stored, {old} already there")
        if args.analyse:
            print(process_pending(conn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
