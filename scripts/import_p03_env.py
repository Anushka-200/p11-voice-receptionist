"""Copy P03's Google Calendar and HubSpot settings into P11's .env WITHOUT printing any secret.

The credentials stay where they are: GOOGLE_CREDENTIALS_FILE is a PATH to P03's service-account file (outside any repo),
so nothing sensitive is duplicated. It never touches CALENDAR_BACKEND, CRM_BACKEND or DRY_RUN: you flip those on purpose.

Usage: uv run python -m scripts.import_p03_env
"""

import argparse
import re
from pathlib import Path

KEYS = ("GOOGLE_CALENDAR_ID", "GOOGLE_CREDENTIALS_FILE", "HUBSPOT_TOKEN")
DEFAULT_SRC = Path.home() / "portfolio" / "p03-ai-operations-agent" / ".env"


def read_env(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            out[key.strip()] = re.sub(r"\s+#.*$", "", value).strip()
    return out


def merge(dest_text: str, values: dict[str, str]) -> str:
    lines = dest_text.splitlines()
    for key, value in values.items():
        new = f"{key}={value}"
        for i, line in enumerate(lines):
            if line.partition("=")[0].strip() == key and not line.lstrip().startswith("#"):
                lines[i] = new
                break
        else:
            lines.append(new)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="src", type=Path, default=DEFAULT_SRC)
    parser.add_argument("--to", dest="dest", type=Path, default=Path(".env"))
    args = parser.parse_args(argv)
    source = read_env(args.src)
    found = {k: source[k] for k in KEYS if source.get(k)}
    for key in KEYS:
        if key not in found:
            print(f"{key}: not set in {args.src.name}: nothing copied")
        else:
            extra = ""
            if key == "GOOGLE_CREDENTIALS_FILE":
                extra = f", file exists: {'yes' if Path(found[key]).expanduser().is_file() else 'NO'}"
            print(f"{key}: copied ({len(found[key])} characters{extra})")
    if found:
        args.dest.write_text(merge(args.dest.read_text(), found))
        args.dest.chmod(0o600)
        print(
            f"\nwrote {len(found)} values to {args.dest}. Nothing was displayed. CALENDAR_BACKEND, CRM_BACKEND and DRY_RUN are unchanged."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
