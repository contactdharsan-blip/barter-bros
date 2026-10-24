"""Read the Anthropic API key from the macOS Keychain at runtime.

The key is captured into memory only: never printed, logged or written to disk. If it is
missing (or this isn't a Mac), the caller uses the rules parser.
"""

from __future__ import annotations

import os
import subprocess

ACCOUNT = "hercules"
SERVICE = "anthropic-api-key"


def read_key() -> str | None:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", ACCOUNT, "-s", SERVICE, "-w"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def ensure_api_key() -> bool:
    """True when ANTHROPIC_API_KEY is available to this process, loading it from the Keychain if needed.

    The key goes into this process's environment only, where `anthropic.Anthropic()` reads it.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    key = read_key()
    if key is None:
        return False
    os.environ["ANTHROPIC_API_KEY"] = key
    return True
