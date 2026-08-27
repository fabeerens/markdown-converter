"""Registratie van geannuleerde AI-verzoeken.

Eén proces-brede set met actieve annuleringen, thread-safe. Een streaming-
opschoon-/vertaalverzoek (`cleanup.clean_stream()`) checkt zijn `request_id`
periodiek; `/api/clean/cancel` zet 'm in de set. `clear()` ruimt op zodra een
verzoek stopt — succesvol, met een fout, of geannuleerd — zodat de set niet
onbeperkt groeit.

Puur een in-memory vlaggetje: dit stopt de generatie bij de eerstvolgende
control-check in `openrouter.stream_chunk()`/`cleanup.clean_stream()`, niet
per se instant (een deel dat al onderweg is, loopt af tot de eerstvolgende
regel van de SSE-stream). Voor deze tool — één gebruiker, lokaal of achter
een reverse proxy — is dat ruim voldoende; geen taak-queue nodig.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_cancelled: set[str] = set()


def request(request_id: str) -> None:
    """Markeer `request_id` als geannuleerd."""
    if not request_id:
        return
    with _lock:
        _cancelled.add(request_id)


def is_cancelled(request_id: str | None) -> bool:
    if not request_id:
        return False
    with _lock:
        return request_id in _cancelled


def clear(request_id: str | None) -> None:
    if not request_id:
        return
    with _lock:
        _cancelled.discard(request_id)
