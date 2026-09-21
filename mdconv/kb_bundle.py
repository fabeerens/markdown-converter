"""Tijdelijke opslag en downloadbouw voor invoer van de kennisbank.

Wetgeving, rechtspraak en documenten met een bekende identiteit krijgen deze
vorm; geplakte tekst en losse bestanden zonder identiteit houden de platte
download. De kennisbank noemt haar bestanden naar de
identiteit, en een ECLI bevat dubbele punten die geen bestandsnaam mogen zijn.
Daarom draagt een bundel er twee: `document_id` is de identiteit zoals de
frontmatter hem kent (`ECLI:NL:RBROT:2025:15669`), en `pad_id` is diezelfde
identiteit als naam (`ECLI-NL-RBROT-2025-15669`), precies zoals `raw/` en
`work/` aan de andere kant al heten.

De bronbytes blijven server-side tot de gebruiker downloadt.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
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
    pad_id: str
    provenance: dict


# Wat er na het vervangen van `:` en `/` nog in de weg kan zitten voordat een
# identiteit een bestandsnaam mag zijn.
_ONVEILIG = re.compile(r'[\x00-\x1f\\*?"<>|]')

# Een documentslug (`identifiers.md`, paragraaf 1): kleine letters, cijfers en
# koppeltekens, zonder koppelteken aan de rand of twee achter elkaar. Strenger dan
# `_ONVEILIG` met opzet: een slug is een naam die de kennisbank toekent, en een
# identiteit die er niet aan voldoet is een vergissing, geen stijlkeuze.
DOCUMENT_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def bestandsid(document_id: str) -> str:
    """De identiteit als naamdeel, gelijk aan `source_checks.bestandsid` in de kb."""
    return str(document_id).replace(":", "-").replace("/", "-")


def identiteit(provenance: dict | None) -> tuple[str, str, str] | None:
    """Leid het kb-profiel, de document-id en de padnaam af, of weiger de kb-vorm.

    Een uitspraak wordt op haar ECLI geïdentificeerd, ook wanneer er een CELEX-nummer
    bij hoort: `md-clean-jurisprudentie` schrijft de ECLI in `id`, en
    `source_evidence.verify()` legt de identiteit in het bewijs daarnaast. Alleen
    wanneer de bron zelf geen ECLI noemt is het CELEX-nummer uit sector 6 de
    identiteit, zoals `SKILL.md` het voor het Hof voorschrijft. Een ECLI wordt nooit
    uit een patroon opgebouwd.
    """
    if not isinstance(provenance, dict):
        return None
    if provenance.get("bwb"):
        profiel, document_id = "wetten-nl", provenance["bwb"]
    elif (isinstance(provenance.get("celex"), str)
          and provenance["celex"][:1] in {"0", "3"}):
        profiel = "eurlex"
        document_id = provenance.get("base_celex") or provenance["celex"]
    elif isinstance(provenance.get("ecli"), str) and provenance["ecli"].upper().startswith("ECLI:"):
        profiel, document_id = "jurisprudentie", provenance["ecli"].upper()
    elif isinstance(provenance.get("celex"), str) and provenance["celex"][:1] == "6":
        profiel, document_id = "jurisprudentie", provenance["celex"]
    elif isinstance(provenance.get("document_id"), str):
        # Een document zonder officieel nummer. De slug is toegekend door de gebruiker
        # of door de bron (KOOP), en wordt hier alleen gecontroleerd: geen slug is
        # geen bundel, en geen bundel is de platte download zoals voorheen.
        if not DOCUMENT_ID.match(provenance["document_id"]):
            return None
        profiel, document_id = "documenten", provenance["document_id"]
    else:
        return None
    if not isinstance(document_id, str) or not document_id.strip():
        return None
    pad_id = bestandsid(document_id.strip())
    if _ONVEILIG.search(pad_id) or pad_id.startswith((".", "-")) or pad_id.endswith("."):
        return None
    return profiel, document_id, pad_id


def store(provenance: dict) -> str | None:
    """Bewaar herkomst en bronbytes buiten de browser; geef een token terug."""
    gevonden = identiteit(provenance)
    if gevonden is None:
        return None
    _sweep()
    profiel, document_id, pad_id = gevonden
    directory = Path(tempfile.mkdtemp(prefix="mdconv-kb-"))
    (directory / "bundle.json").write_text(
        json.dumps({"profiel": profiel, "document_id": document_id, "pad_id": pad_id,
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
    return Bundel(data["profiel"], data["document_id"],
                  data.get("pad_id") or bestandsid(data["document_id"]), data["provenance"])


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
        extensie = {"formex": "fmx4.zip", "formex-hvj": "fmx4.zip", "bwb-xml": "xml",
                    "rechtspraak-xml": "xml", "hudoc-docx": "docx", "docx": "docx",
                    "op-xml": "xml", "pdf": "pdf"}.get(formaat)
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
    root = f"raw/source-evidence/{bundel.pad_id}"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"raw/{bundel.profiel}/{bundel.pad_id}.md", markdown)
        archive.writestr(f"raw/{bundel.profiel}/{bundel.pad_id}.source.json", zij)
        archive.writestr(f"{root}/fetch.json", json.dumps(fetch, ensure_ascii=False, indent=2) + "\n")
        archive.writestr(f"{root}/{digest}.{extensie}", bron)
    stream.seek(0)
    return stream, bundel.pad_id


def _sweep() -> None:
    now = time.monotonic()
    with _lock:
        stale = [token for token, (_, tijd) in _stores.items()
                 if now - tijd > _MAX_AGE_SECONDS]
        directories = [_stores.pop(token)[0] for token in stale]
    for directory in directories:
        shutil.rmtree(directory, ignore_errors=True)
