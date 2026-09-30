"""Running a backup in the background, so the interface stays responsive.

One task at a time: two simultaneous passes over the same library would double
the request rate, which is exactly what gets an account rate limited.
"""

from __future__ import annotations

import threading
import traceback
from collections import deque
from dataclasses import asdict
from datetime import datetime
from typing import Any, Callable

from igarchive.fetch import Progress

MAX_LOG_LINES = 400


class JobRunner:
    """Holds the state of a background task and exposes it to the interface."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._progress = Progress()
        self._log: deque[str] = deque(maxlen=MAX_LOG_LINES)
        self._started_at: datetime | None = None

    # -- state -----------------------------------------------------------

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def state(self) -> dict[str, Any]:
        with self._lock:
            elapsed = None
            if self._started_at:
                elapsed = (datetime.now() - self._started_at).total_seconds()
            return {
                "running": self._thread is not None and self._thread.is_alive(),
                "progress": asdict(self._progress),
                "log": list(self._log),
                "elapsed_s": elapsed,
            }

    def log(self, line: str) -> None:
        with self._lock:
            self._log.append(f"{datetime.now():%H:%M:%S}  {line}")

    # -- control ---------------------------------------------------------

    def start(self, work: Callable[[threading.Event, Callable[[Progress], None]], Any],
              *, label: str = "Archivage") -> bool:
        """Start `work` in the background. False if a task is already running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._cancel = threading.Event()
            self._progress = Progress(phase="demarrage")
            self._log.clear()
            self._started_at = datetime.now()

        self.log(f"{label} demarre.")

        def report(progress: Progress) -> None:
            with self._lock:
                self._progress = progress

        def target() -> None:
            try:
                work(self._cancel, report)
            except Exception as exc:  # noqa: BLE001 -- l'interface doit voir tout echec
                with self._lock:
                    self._progress.phase = "erreur"
                    self._progress.error = f"{type(exc).__name__}: {exc}"
                    self._progress.finished = True
                self.log(f"Echec inattendu : {type(exc).__name__}: {exc}")
                self.log(traceback.format_exc().strip().splitlines()[-1])
            finally:
                with self._lock:
                    self._progress.finished = True
                self.log("Tache terminee.")

        thread = threading.Thread(target=target, name="igarchive-job", daemon=True)
        with self._lock:
            self._thread = thread
        thread.start()
        return True

    def cancel(self) -> bool:
        """Ask the task to stop. Work in progress finishes cleanly."""
        with self._lock:
            alive = self._thread is not None and self._thread.is_alive()
        if alive:
            self._cancel.set()
            self.log("Arret demande : la tache s'arrete apres le contenu en cours.")
        return alive
