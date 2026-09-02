"""HTTP-laag: alle routes, dun gehouden.

Elke route doet drie dingen: invoer valideren, één domeinfunctie aanroepen, en
het resultaat als JSON teruggeven. Fouten worden niet per route afgehandeld maar
centraal: de domeinlaag gooit `ConversionError` met een Nederlandse boodschap en
de errorhandler hieronder maakt daar één keer `{"error": …}` van.
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from urllib.parse import unquote, urlparse

import requests
from flask import Blueprint, Response, current_app, jsonify, render_template, request, send_file

from . import attachments, cleanup, net, sources, version
from .errors import ConversionError
from .sources import pdf_images

bp = Blueprint("api", __name__)

# Grens voor een bestand dat via een link wordt gedownload. De upload-grens
# staat in create_app (MAX_CONTENT_LENGTH).
_MAX_REMOTE_BYTES = 40 * 1024 * 1024
_REMOTE_TIMEOUT = 60

# Content-types → extensie, voor links zonder bestandsnaam in het pad.
_CT_EXT = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/html": ".html",
    "text/plain": ".txt",
    "application/rtf": ".rtf",
    "application/epub+zip": ".epub",
}


@bp.errorhandler(ConversionError)
def _handle_conversion_error(e: ConversionError):
    return jsonify(error=e.message), e.status


def _payload() -> dict:
    return request.get_json(silent=True) or {}


def _profile(value) -> str:
    """Alleen bekende profielen; anders het algemene profiel."""
    return value if value in cleanup.PROFILES else "generic"


# --------------------------------------------------------------------------
# Pagina en configuratie
# --------------------------------------------------------------------------

@bp.get("/")
def index():
    app_version, build, installed_at = version.current()
    return render_template(
        "index.html", version=app_version, build=build, installed_at=installed_at,
    )


@bp.get("/api/config")
def config():
    """Wat de UI moet weten bij het laden: is er een sleutel, en welke modellen."""
    return jsonify(
        llm_available=cleanup.is_available(),
        models=cleanup.get_model_choices(),
        profiles=list(cleanup.PROFILES),
        # Bepaalt of de UI de toggle "Losse afbeeldingen extraheren" bij
        # Documentupload aanbiedt — alleen zinvol als poppler-utils
        # (pdfimages/pdfinfo) daadwerkelijk geïnstalleerd is.
        extract_images_available=pdf_images.available(),
    )


@bp.get("/api/settings")
def get_settings():
    """Huidige instellingen plus de standaardwaarden (voor de reset-knoppen)."""
    return jsonify(cleanup.settings_payload())


@bp.post("/api/settings")
def post_settings():
    """Instellingen bijwerken; een leeg veld zet terug naar de standaardwaarde."""
    return jsonify(cleanup.update_settings(_payload()))


# --------------------------------------------------------------------------
# Converteren
# --------------------------------------------------------------------------

@bp.post("/api/convert/link")
def convert_link():
    """Een ECLI, CELEX, BWB-nummer of link ophalen en omzetten."""
    data = _payload()
    query = (data.get("query") or "").strip()
    lang = (data.get("lang") or "NL").strip()
    if not query:
        raise ConversionError("Voer een CELEX-nummer, ECLI, of link in.")
    return jsonify(sources.from_link(query, lang).as_json())


@bp.post("/api/convert/text")
def convert_text():
    """Handmatig geplakte tekst (kaal of verrijkt) omzetten."""
    data = _payload()
    html = data.get("html") or ""
    text = data.get("text") or ""
    if not html.strip() and not text.strip():
        raise ConversionError("Plak eerst tekst in het vak.")
    return jsonify(sources.from_pasted_text(html, text).as_json())


def _doc_payload(doc) -> dict:
    """`Document.as_json()` aangevuld met een bijlage-token als er losse
    afbeeldingen uit een PDF zijn geëxtraheerd — de binaire data zelf gaat
    nooit in JSON mee, zie `mdconv/attachments.py`."""
    payload = doc.as_json()
    if doc.attachments:
        payload["attachments_token"] = attachments.store(doc.attachments)
        payload["attachment_count"] = len(doc.attachments)
    return payload


@bp.post("/api/convert/file")
def convert_file():
    """Een geüpload bestand omzetten."""
    if "file" not in request.files:
        raise ConversionError("Geen bestand ontvangen.")
    upload = request.files["file"]
    if not upload.filename:
        raise ConversionError("Leeg bestand.")
    data = upload.read()
    if not data.strip():
        raise ConversionError("Het bestand is leeg.")
    extract_images = request.form.get("extract_images") == "1"
    doc = sources.from_file(data, upload.filename, extract_images=extract_images)
    return jsonify(_doc_payload(doc))


@bp.post("/api/convert/file-url")
def convert_file_url():
    """Een bestand achter een link downloaden en omzetten.

    Let op: dit endpoint haalt een willekeurige URL op namens de server. De tool
    heeft geen authenticatie en hoort daarom niet zonder reverse proxy + auth
    open op internet te staan (SSRF).
    """
    data_in = _payload()
    url = (data_in.get("url") or "").strip()
    if not re.match(r"^https?://", url, re.I):
        raise ConversionError("Voer een geldige URL in (beginnend met http:// of https://).")

    try:
        r = net.documents().get(url, timeout=_REMOTE_TIMEOUT)
    except requests.exceptions.RequestException as e:
        raise ConversionError(f"Kon het bestand niet ophalen: {e}") from e
    if r.status_code != 200:
        raise ConversionError(f"Kon het bestand niet ophalen (status {r.status_code}).")
    if not r.content:
        raise ConversionError("Het opgehaalde bestand is leeg.")
    if len(r.content) > _MAX_REMOTE_BYTES:
        raise ConversionError("Bestand is groter dan 40 MB.")

    filename = _filename_from_url(url, r.headers.get("Content-Type", ""))
    extract_images = data_in.get("extract_images") is True
    doc = sources.from_file_bytes(r.content, filename, url, extract_images=extract_images)
    return jsonify(_doc_payload(doc))


def _filename_from_url(url: str, content_type: str) -> str:
    """Een bruikbare bestandsnaam (met extensie) uit een URL + content-type."""
    name = os.path.basename(unquote(urlparse(url).path)) or "document"
    if "." not in name:
        ct = (content_type or "").split(";")[0].strip().lower()
        name += _CT_EXT.get(ct, "")
    return name


# --------------------------------------------------------------------------
# AI-opschoning
# --------------------------------------------------------------------------

@bp.post("/api/estimate")
def estimate():
    """Delen, tokens en kosten voor het opschonen van de meegestuurde markdown."""
    data = _payload()
    return jsonify(cleanup.estimate(
        data.get("markdown", ""),
        _profile(data.get("profile")),
        data.get("model") or None,
    ))


@bp.post("/api/clean")
def clean():
    """De markdown door het gekozen model halen."""
    data = _payload()
    markdown = data.get("markdown", "")
    if not markdown.strip():
        raise ConversionError("Niets om op te schonen.")
    cleaned, usage = cleanup.clean(markdown, _profile(data.get("profile")), data.get("model") or None)
    return jsonify(markdown=cleaned, usage=usage)


# Scheidingsteken voor een fout die halverwege een stream optreedt (bv. een
# netwerkstoring bij het tweede deel van een lang document). De HTTP-status is
# op dat moment al 200 verzonden, dus een fout kan alleen nog ín de body
# gemeld worden. \x00 komt niet in echte Markdown voor; de front-end herkent
# dit teken en toont de rest als foutmelding in plaats van als tekst.
#
# Twee andere frametypes gebruiken hetzelfde \x00-teken, maar wél afgesloten
# (in tegenstelling tot CLEAN_ERROR, dat altijd het allerlaatste in de stream
# is): `\x00CLEAN_PROGRESS\x00{...json...}\x00` en
# `\x00CLEAN_USAGE\x00{...json...}\x00`. Zie `_frame()` en, aan de andere
# kant, de gelijknamige parser in static/app.js.
STREAM_ERROR_SENTINEL = "\x00CLEAN_ERROR\x00"


def _frame(kind: str, payload: dict) -> str:
    return f"\x00CLEAN_{kind}\x00{json.dumps(payload, ensure_ascii=False)}\x00"


@bp.post("/api/clean/stream")
def clean_stream():
    """Als /api/clean, maar streamt de opgeschoonde tekst terwijl die binnenkomt.

    Bekende/verwachte fouten (geen API-sleutel, lege invoer) worden vooraf
    gevalideerd en geven een normale JSON-foutrespons — precies zoals
    /api/clean. Alleen een fout die pas ontstaat ná de eerste bytes (een
    verbindingsstoring bij een later deel) kan niet meer als HTTP-statuscode
    gemeld worden en verschijnt in de body achter STREAM_ERROR_SENTINEL.

    `request_id` (optioneel, van de front-end) is het aangrijpingspunt voor
    `/api/clean/cancel` — zonder `request_id` kan dit verzoek niet vroegtijdig
    gestopt worden.
    """
    data = _payload()
    markdown = data.get("markdown", "")
    if not markdown.strip():
        raise ConversionError("Niets om op te schonen.")
    if not cleanup.is_available():
        raise ConversionError(
            "AI-opschoning niet beschikbaar: geen OpenRouter API-sleutel. "
            "Zet de omgevingsvariabele OPENROUTER_API_KEY en herstart de tool."
        )
    profile = _profile(data.get("profile"))
    model = data.get("model") or None
    request_id = (data.get("request_id") or "").strip() or None

    def generate():
        try:
            for item in cleanup.clean_stream(markdown, profile, model, request_id=request_id):
                if isinstance(item, cleanup.Progress):
                    yield _frame("PROGRESS", dict(item))
                elif isinstance(item, cleanup.Usage):
                    yield _frame("USAGE", dict(item))
                else:
                    yield item
        except ConversionError as e:
            yield STREAM_ERROR_SENTINEL + e.message
        except Exception as e:  # noqa: BLE001 — moet als leesbare melding aankomen, niet als kapotte stream
            yield STREAM_ERROR_SENTINEL + str(e)

    response = Response(generate(), mimetype="text/plain")
    # Zonder deze twee headers bufferen sommige reverse proxies (nginx) de
    # hele respons voordat ze iets doorsturen — dan is "streamen" niets meer.
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.post("/api/clean/cancel")
def clean_cancel():
    """Markeer een lopend /api/clean/stream-verzoek (zelfde `request_id`) als
    geannuleerd. Best-effort en stil: een onbekende of al voltooide
    `request_id` is geen fout — er is dan gewoon niets meer te annuleren."""
    request_id = (_payload().get("request_id") or "").strip()
    if request_id:
        cleanup.cancel_request(request_id)
    return jsonify(ok=True)


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------

@bp.post("/api/download")
def download():
    """De meegestuurde markdown terugsturen — als .md, of als .zip mét de
    `attachments/`-map als er bij de conversie losse afbeeldingen uit een PDF
    zijn geëxtraheerd (zie `mdconv/attachments.py`)."""
    data = _payload()
    name = re.sub(r"[^A-Za-z0-9._-]", "_", data.get("filename") or "document") or "document"

    directory = attachments.get((data.get("attachments_token") or "").strip())
    if directory and directory.is_dir():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{name}.md", data.get("markdown", ""))
            for file in sorted(directory.iterdir()):
                zf.write(file, arcname=f"attachments/{file.name}")
        buf.seek(0)
        return send_file(
            buf, mimetype="application/zip", as_attachment=True, download_name=f"{name}.zip"
        )

    buf = io.BytesIO(data.get("markdown", "").encode("utf-8"))
    return send_file(buf, mimetype="text/markdown", as_attachment=True, download_name=f"{name}.md")


@bp.app_errorhandler(413)
def _too_large(_e):
    limit = current_app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return jsonify(error=f"Bestand is te groot (maximaal {limit} MB)."), 413
