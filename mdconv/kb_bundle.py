"""Tijdelijke opslag en downloadbouw voor invoer van de kennisbank.

Alleen wetgeving krijgt deze vorm. De publicatiepoort van de kennisbank eist
een alfanumerieke documentidentiteit; ECLI's bevatten dubbele punten en horen
daarom, net als documenten en geplakte tekst, bij de bestaande platte download.
De bronbytes blijven server-side tot de gebruiker downloadt.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .herkomst import als_zijbestand

_MAX_AGE_SECONDS = 2 * 60 * 60
_lock = threading.Lock()
_stores: dict[str, tuple[Path, float]] = {}


@dataclass(frozen=True, slots=True)
class Bundel:
    profiel: str
    document_id: str
    provenance: dict


def identiteit(provenance: dict | None) -> tuple[str, str] | None:
    """Leid het kb-profiel en de document-id af, of weiger de kb-vorm."""
    if not isinstance(provenance, dict):
        return None
    if provenance.get("bwb"):
        profiel, document_id = "wetten-nl", provenance["bwb"]
    elif (isinstance(provenance.get("celex"), str)
          and provenance["celex"][:1] in {"0", "3"}):
        profiel = "eurlex"
        document_id = provenance.get("base_celex") or provenance["celex"]
    else:
        return None
    if not isinstance(document_id, str) or not document_id.isalnum():
        return None
    return profiel, document_id


def store(provenance: dict) -> str | None:
    """Bewaar herkomst en bronbytes buiten de browser; geef een token terug."""
    gevonden = identiteit(provenance)
    if gevonden is None:
        return None
    _sweep()
    profiel, document_id = gevonden
    directory = Path(tempfile.mkdtemp(prefix="mdconv-kb-"))
    (directory / "bundle.json").write_text(
        json.dumps({"profiel": profiel, "document_id": document_id,
                    "provenance": provenance}, ensure_ascii=False),
        encoding="utf-8",
    )
    token = uuid.uuid4().hex
    with _lock:
        _stores[token] = (directory, time.monotonic())
    return token


def _get(token: str) -> Bundel | None:
    if not token:
        return None
    with _lock:
        entry = _stores.get(token)
    if entry is None:
        return None
    data = json.loads((entry[0] / "bundle.json").read_text(encoding="utf-8"))
    return Bundel(data["profiel"], data["document_id"], data["provenance"])


def _bron(provenance: dict) -> tuple[bytes, str, dict]:
    extra = provenance.get("extra")
    bewijs = extra.get("source_structure") if isinstance(extra, dict) else None
    bronnen = bewijs.get("sources") if isinstance(bewijs, dict) else None
    if not isinstance(bronnen, list) or not bronnen:
        raise ValueError("De herkomst bevat geen bewaarde bronbytes.")
    hoofdbron = next((b for b in reversed(bronnen) if b.get("role") != "preamble"), bronnen[0])
    formaat = hoofdbron.get("source_format") or "html"
    if formaat == "html":
        inhoud = hoofdbron.get("original_html")
        if not isinstance(inhoud, str):
            raise ValueError("De HTML-bron ontbreekt in de herkomst.")
        data, extensie = inhoud.encode("utf-8"), "html"
    else:
        inhoud = hoofdbron.get("original_base64")
        if not isinstance(inhoud, str):
            raise ValueError("De binaire bron ontbreekt in de herkomst.")
        data = base64.b64decode(inhoud, validate=True)
        extensie = {"formex": "fmx4.zip", "bwb-xml": "xml"}.get(formaat)
        if extensie is None:
            raise ValueError(f"Onbekend bronformaat voor kennisbankbundel: {formaat}.")
    digest = hashlib.sha256(data).hexdigest()
    if hoofdbron.get("source_sha256") != digest:
        raise ValueError("De bewaarde bronbytes wijken af van hun SHA-256.")
    return data, extensie, hoofdbron


def build(token: str, markdown: str, *, bewerkt_met_ai: bool) -> tuple[io.BytesIO, str] | None:
    """Bouw de uitpakbare kb-zip met de teruggestuurde markdown."""
    bundel = _get((token or "").strip())
    if bundel is None:
        return None
    bron, extensie, bronmeta = _bron(bundel.provenance)
    digest = hashlib.sha256(bron).hexdigest()
    zij = als_zijbestand(
        bundel.provenance, bewerkt_met_ai=bewerkt_met_ai, markdown=markdown,
    )
    fetch = {
        "requested_url": bundel.provenance.get("requested_url"),
        "resolved_url": bronmeta.get("source_url") or bundel.provenance.get("source_url"),
        "sha256": digest,
        "fetched_at": bundel.provenance.get("fetched_at"),
        "language": bundel.provenance.get("language"),
        "source_format": bronmeta.get("source_format") or "html",
        "media_type": bronmeta.get("media_type") or "text/html",
    }
    root = f"raw/source-evidence/{bundel.document_id}"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"raw/{bundel.profiel}/{bundel.document_id}.md", markdown)
        archive.writestr(f"raw/{bundel.profiel}/{bundel.document_id}.source.json", zij)
        archive.writestr(f"{root}/fetch.json", json.dumps(fetch, ensure_ascii=False, indent=2) + "\n")
        archive.writestr(f"{root}/{digest}.{extensie}", bron)
    stream.seek(0)
    return stream, bundel.document_id


def _sweep() -> None:
    now = time.monotonic()
    with _lock:
        stale = [token for token, (_, tijd) in _stores.items()
                 if now - tijd > _MAX_AGE_SECONDS]
        directories = [_stores.pop(token)[0] for token in stale]
    for directory in directories:
        shutil.rmtree(directory, ignore_errors=True)
