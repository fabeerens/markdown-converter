"""Persistente JSON-staat in `.deploy-state/`, met een mtime-cache.

Twee gebruikers: de instellingen (`cleanup/config.py`) en de versieteller
(`version.py`). Die map staat buiten git en is in Docker als volume gemount,
zodat beide een rebuild overleven.

**Waarom een cache.** In de oude opzet las elke losse getter het hele
`settings.json` opnieuw van schijf: één opschoonverzoek deed dat een handvol
keer (profiel, model, deelgrootte, en per chunk opnieuw). `read()` doet nu één
`os.stat()` en parseert alleen als het bestand daadwerkelijk is gewijzigd —
`(mtime_ns, size)` als vingerafdruk. Een wijziging via de UI werkt daardoor nog
altijd meteen door, zonder herstart, maar kost geen herhaalde disk-I/O meer.

**Waarom twee sloten.** `threading.Lock` beschermt de cache binnen één proces
(Flask draait lokaal met threads); `fcntl.flock` voorkomt dat twee
gunicorn-workers tegelijk schrijven en elkaars bestand halveren.
"""

from __future__ import annotations

import json
import os
import threading

try:
    import fcntl  # POSIX-bestandsvergrendeling (macOS/Linux); ontbreekt op Windows.
except ImportError:  # pragma: no cover
    fcntl = None

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(_BASE_DIR, ".deploy-state")


def base_dir() -> str:
    """De projectmap (waar VERSION, requirements.txt en mdconv/ staan)."""
    return _BASE_DIR


class StateFile:
    """Eén JSON-bestand met gecachet lezen en vergrendeld schrijven."""

    def __init__(self, filename: str):
        self.path = os.path.join(STATE_DIR, filename)
        self._lock_path = os.path.join(STATE_DIR, f".{filename}.lock")
        self._lock = threading.Lock()
        self._stamp: tuple[int, int] | None = None
        self._data: dict = {}
        self._loaded = False

    def _fingerprint(self) -> tuple[int, int] | None:
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def read(self) -> dict:
        """De huidige inhoud; alleen van schijf gelezen als die is gewijzigd.

        Geeft een kopie terug, zodat de aanroeper de cache niet kan muteren.
        """
        stamp = self._fingerprint()
        with self._lock:
            if not self._loaded or stamp != self._stamp:
                self._data = self._load() if stamp is not None else {}
                self._stamp = stamp
                self._loaded = True
            return dict(self._data)

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def write(self, data: dict) -> None:
        """Schrijf de staat weg (atomair, en vergrendeld tegen andere workers)."""
        os.makedirs(STATE_DIR, exist_ok=True)
        with self._lock:
            with open(self._lock_path, "w") as lock_file:
                if fcntl is not None:
                    fcntl.flock(lock_file, fcntl.LOCK_EX)
                try:
                    # Schrijf-en-vervang: een half weggeschreven bestand kan
                    # nooit als geldige staat worden gelezen.
                    tmp = f"{self.path}.tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    os.replace(tmp, self.path)
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_file, fcntl.LOCK_UN)
            self._data = dict(data)
            self._stamp = self._fingerprint()
            self._loaded = True

    def mutate(self, change) -> dict:
        """Lees, pas `change(dict)` toe en schrijf het resultaat weg."""
        data = self.read()
        change(data)
        self.write(data)
        return data
