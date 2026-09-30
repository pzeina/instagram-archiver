"""Persisted settings, kept as JSON so no library is needed to read them."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from igarchive import paths

# Safety bounds: below these, Instagram rate limits the account very quickly.
MIN_SLEEP = 1.0
RECOMMENDED_SLEEP_MIN = 3.0
RECOMMENDED_SLEEP_MAX = 8.0


@dataclass
class Config:
    """User settings. All of them reachable from the web interface."""

    username: str = ""
    archive_dir: str = field(default_factory=lambda: str(paths.default_archive_dir()))
    browser: str = "firefox"
    sleep_min: float = RECOMMENDED_SLEEP_MIN
    sleep_max: float = RECOMMENDED_SLEEP_MAX
    download_videos: bool = True
    download_comments: bool = False
    download_thumbnails: bool = True
    stop_after_known: int = 0        # 0 = parcourir toute la bibliotheque
    limit_per_run: int = 0           # 0 = pas de plafond
    webui_port: int = 8765
    language: str = "en"

    # -- derived paths ---------------------------------------------------

    @property
    def archive(self) -> Path:
        return Path(self.archive_dir).expanduser()

    @property
    def media_dir(self) -> Path:
        return self.archive / "media"

    @property
    def metadata_dir(self) -> Path:
        return self.archive / "metadata"

    @property
    def state_dir(self) -> Path:
        return self.archive / "state"

    @property
    def ledger_file(self) -> Path:
        return self.state_dir / "ledger.json"

    @property
    def saved_dates_file(self) -> Path:
        return self.state_dir / "saved_dates.json"

    def session_file(self, username: str | None = None) -> Path:
        return paths.sessions_dir() / f"{username or self.username}.session"

    def ensure_dirs(self) -> None:
        for directory in (self.media_dir, self.metadata_dir, self.state_dir):
            directory.mkdir(parents=True, exist_ok=True)

    # -- validation ------------------------------------------------------

    def problems_detail(self) -> list[dict]:
        """What stops a backup, along with the step it belongs to.

        The step is for the interface: flagging the settings as "needs fixing"
        because no account is set would point at the wrong place.
        """
        issues: list[dict] = []
        if not self.username.strip():
            issues.append({"step": 1, "text": "Aucun compte Instagram renseigne."})
        elif not self.session_file().exists():
            issues.append({"step": 1,
                           "text": f"Aucune session ouverte pour « {self.username} »."})
        if self.sleep_min < MIN_SLEEP:
            issues.append({"step": 2,
                           "text": f"La pause minimale ne peut pas descendre sous {MIN_SLEEP} s."})
        if self.sleep_max < self.sleep_min:
            issues.append({"step": 2,
                           "text": "La pause maximale est inferieure a la pause minimale."})
        parent = self.archive.parent
        if not parent.exists():
            issues.append({"step": 2,
                           "text": f"Le dossier parent de destination n'existe pas : {parent}"})
        return issues

    def problems(self) -> list[str]:
        """Readable list of what stops a backup from starting."""
        return [issue["text"] for issue in self.problems_detail()]

    # -- persistence -----------------------------------------------------

    def save(self, path: Path | None = None) -> Path:
        target = path or paths.config_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(target)
        return target


def load(path: Path | None = None) -> Config:
    """Read the settings, falling back to the defaults if the file is missing.

    Unknown keys are ignored, so a file written by a later version does not
    stop an earlier one from starting.
    """
    target = path or paths.config_file()
    if not target.exists():
        return Config()
    try:
        raw: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return Config()
    known = {f.name for f in fields(Config)}
    return Config(**{k: v for k, v in raw.items() if k in known})
