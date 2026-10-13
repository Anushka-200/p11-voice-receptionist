"""List the stored calls newest first, so you can match each test call to its scenario (`make calls`)."""

import sys

import psycopg

from app.settings import get_settings


def main() -> int:
    with psycopg.connect(get_settings().database_url) as conn:
        rows = conn.execute(
            "SELECT vapi_call_id, started_at, assistant_name, duration_s, outcome, flags FROM calls ORDER BY id DESC LIMIT 25"
        ).fetchall()
    print(f"{'vapi_call_id':<38} {'started (UTC)':<17} {'assistant':<24} {'sec':>5}  outcome / flags")
    for vid, started, name, dur, outcome, flags in rows:
        print(
            f"{vid:<38} {started.strftime('%m-%d %H:%M') if started else '?':<17} {name or '-':<24} {float(dur or 0):>5.0f}  {outcome or 'pending'} {flags or ''}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
