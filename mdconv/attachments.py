"""Tijdelijke opslag voor bijlagen (afbeeldingen) tussen conversie en download.

Bij een PDF-conversie met geëxtraheerde afbeeldingen kan de front-end die niet
zomaar in de JSON-conversierespons meesturen (binaire data, en de gebruiker
kan de markdown nog bewerken vóórdat hij downloadt) — dus staan ze hier even
klaar onder een token; `/api/download` bouwt er pas een zip van zodra de
gebruiker daadwerkelijk downloadt.

`get()` verwijdert niets — de gebruiker mag hetzelfde document best twee keer
downloaden. Opruimen gebeurt lui: elke `store()`-aanroep gooit sets ouder dan
`_MAX_AGE_SECONDS` weg (server draait vaak dagenlang door, en zonder dit zou
elke niet-gedownloade set voor altijd op de schijf blijven staan). Geen
aparte achtergrondtaak/cron nodig voor deze single-user, lokaal draaiende
tool.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

_MAX_AGE_SECONDS = 2 * 60 * 60  # 2 uur — ruim genoeg om een groot document te bekijken en te downloaden

_lock = threading.Lock()
_stores: dict[str, tuple[Path, float]] = {}


def store(attachments) -> str:
    """Schrijft `attachments` (elk met `.filename`/`.data`) weg; geeft een token terug."""
    _sweep()
    directory = Path(tempfile.mkdtemp(prefix="mdconv-attach-"))
    for att in attachments:
        (directory / att.filename).write_bytes(att.data)
    token = uuid.uuid4().hex
    with _lock:
        _stores[token] = (directory, time.monotonic())
    return token


def get(token: str) -> Path | None:
    if not token:
        return None
    with _lock:
        entry = _stores.get(token)
    return entry[0] if entry else None


def _sweep() -> None:
    now = time.monotonic()
    with _lock:
        stale = [t for t, (_, ts) in _stores.items() if now - ts > _MAX_AGE_SECONDS]
        directories = [_stores.pop(t)[0] for t in stale]
    for directory in directories:
        shutil.rmtree(directory, ignore_errors=True)
