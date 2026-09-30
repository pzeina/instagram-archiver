"""File locations, resolved the same way on macOS and Linux.

XDG conventions are used on both systems rather than
"~/Library/Application Support" on macOS: one path to document, one to back up,
and the same one in the documentation whatever the operating system.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

APP = "igarchive"


def config_dir() -> Path:
    """Configuration directory (honours XDG_CONFIG_HOME when it is set)."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / APP


def config_file() -> Path:
    return config_dir() / "config.json"


def sessions_dir() -> Path:
    """Session tokens: worth a password, so they live outside the archive."""
    return config_dir() / "sessions"


def default_archive_dir() -> Path:
    return Path.home() / "InstagramArchive"


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def firefox_profile_roots() -> list[Path]:
    """Every place Firefox keeps its profiles, by operating system and packaging."""
    home = Path.home()
    if is_macos():
        candidates = [home / "Library/Application Support/Firefox/Profiles"]
    else:
        candidates = [
            home / ".mozilla/firefox",
            home / "snap/firefox/common/.mozilla/firefox",          # Ubuntu snap
            home / ".var/app/org.mozilla.firefox/.mozilla/firefox",  # Flatpak
        ]
    return [p for p in candidates if p.is_dir()]


def open_in_browser(target: str) -> bool:
    """Open a URL or file in the default application. True if it was launched."""
    if is_macos():
        opener = ["open"]
    else:
        exe = shutil.which("xdg-open") or shutil.which("gio")
        if not exe:
            return False
        opener = [exe, "open"] if exe.endswith("gio") else [exe]
    try:
        subprocess.Popen([*opener, target],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def human_bytes(size: float, language: str = "en") -> str:
    """Human-readable size. The unit follows the language: "octet" or "byte"."""
    units = ("o", "Ko", "Mo", "Go", "To") if language.startswith("fr") \
        else ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if abs(size) < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == units[0] else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} {units[-1]}"
