"""Versienummer voor de footer: handmatige versie + automatische buildteller.

`VERSION` (major.minor.patch) zet je zelf bij een echte release. Het
build-nummer en de installatiedatum houdt de app zelf bij: verandert de
broncode, dan gaat de teller één omhoog en wordt "geïnstalleerd op" nu. Geen git
nodig — dat werkte niet in Docker (geen git-binary, geen `.git`).

De vingerafdruk wordt **lui** berekend, bij het eerste verzoek dat de footer
nodig heeft, en daarna gecachet. Eerder gebeurde dat bij het importeren van de
app: elke serverstart hashte eerst alle bronbestanden voordat hij ging luisteren.
"""

from __future__ import annotations

import glob
import hashlib
import os
import threading
from datetime import datetime

from .state import StateFile, base_dir

# Bestanden die het gedrag van de app bepalen. Wijzigt hier iets, dan is dat
# "een nieuwe versie".
_FINGERPRINT_GLOBS = (
    "app.py",
    "VERSION",
    "requirements.txt",
    "mdconv/*.py",
    "mdconv/*/*.py",
    "templates/*.html",
    "static/*.css",
    "static/*.js",
)

_store = StateFile("version.json")
_lock = threading.Lock()
_cached: tuple[str, int, str] | None = None


def _base_version() -> str:
    try:
        with open(os.path.join(base_dir(), "VERSION"), encoding="utf-8") as f:
            return f.read().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def _fingerprint() -> str:
    paths = sorted(
        p for pattern in _FINGERPRINT_GLOBS
        for p in glob.glob(os.path.join(base_dir(), pattern))
    )
    digest = hashlib.sha256()
    for path in paths:
        try:
            with open(path, "rb") as f:
                digest.update(f.read())
        except OSError:
            continue
    return digest.hexdigest()


def current() -> tuple[str, int, str]:
    """(versie, build, installatiedatum) — één keer berekend per proces."""
    global _cached
    if _cached is not None:
        return _cached
    with _lock:
        if _cached is None:
            _cached = _resolve()
        return _cached


def _resolve() -> tuple[str, int, str]:
    base = _base_version()
    fingerprint = _fingerprint()
    state = _store.read()

    if state.get("fingerprint") != fingerprint:
        state = {
            "fingerprint": fingerprint,
            "build": int(state.get("build", 0)) + 1,
            "installed_at": datetime.now().strftime("%d-%m-%Y %H:%M"),
        }
        # StateFile vergrendelt met flock, zodat twee gunicorn-workers die
        # tegelijk opstarten de teller niet dubbel ophogen.
        _store.write(state)

    return base, int(state["build"]), str(state["installed_at"])
