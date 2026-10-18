"""The hidden footer on everything an agent posts, so later runs (and `learn`) can find and attribute it."""

from __future__ import annotations

import re
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sw_factory import __version__

FOOTER = re.compile(r"<!-- factory: (?P<fields>[^>]*?) -->")


def make_footer(**fields: str | None) -> str:
    """`<!-- factory: agent=triage run=… factory=… -->`; values must not contain spaces. None values are skipped."""
    return "<!-- factory: " + " ".join(f"{k}={v}" for k, v in fields.items() if v) + " -->"


def parse_footer(body: str) -> dict[str, str] | None:
    """Fields of the last factory footer in a comment or issue body, or None if it has none."""
    matches = list(FOOTER.finditer(body))
    if not matches:
        return None
    return dict(field.split("=", 1) for field in matches[-1].group("fields").split() if "=" in field)


def new_run_id() -> str:
    return f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def factory_version() -> str:
    """The sw-factory git commit when run from a checkout (editable install), else the package version."""
    result = subprocess.run(
        ["git", "describe", "--always", "--dirty", "--exclude", "*"],
        cwd=Path(__file__).parent, capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else __version__
