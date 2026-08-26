"""HTTP-laag: alle routes, dun gehouden.

Elke route doet drie dingen: invoer valideren, één domeinfunctie aanroepen, en
het resultaat als JSON teruggeven. Fouten worden niet per route afgehandeld maar
centraal: de domeinlaag gooit `ConversionError` met een Nederlandse boodschap en
de errorhandler hieronder maakt daar één keer `{"error": …}` van.
"""

from __future__ import annotations

import io
import os
import re
from urllib.parse import unquote, urlparse

import requests
from flask import Blueprint, Response, current_app, jsonify, render_template, request, send_file

from . import cleanup, net, sources, version
from .errors import ConversionError

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
    return jsonify(sources.from_file(data, upload.filename).as_json())


@bp.post("/api/convert/file-url")
def convert_file_url():
    """Een bestand achter een link downloaden en omzetten.

    Let op: dit endpoint haalt een willekeurige URL op namens de server. De tool
    heeft geen authenticatie en hoort daarom niet zonder reverse proxy + auth
    open op internet te staan (SSRF).
    """
    url = (_payload().get("url") or "").strip()
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
    return jsonify(sources.from_file_bytes(r.content, filename, url).as_json())


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
    cleaned = cleanup.clean(markdown, _profile(data.get("profile")), data.get("model") or None)
    return jsonify(markdown=cleaned)


# Scheidingsteken voor een fout die halverwege een stream optreedt (bv. een
# netwerkstoring bij het tweede deel van een lang document). De HTTP-status is
# op dat moment al 200 verzonden, dus een fout kan alleen nog ín de body
# gemeld worden. \x00 komt niet in echte Markdown voor; de front-end herkent
# dit teken en toont de rest als foutmelding in plaats van als tekst.
STREAM_ERROR_SENTINEL = "\x00CLEAN_ERROR\x00"


@bp.post("/api/clean/stream")
def clean_stream():
    """Als /api/clean, maar streamt de opgeschoonde tekst terwijl die binnenkomt.

    Bekende/verwachte fouten (geen API-sleutel, lege invoer) worden vooraf
    gevalideerd en geven een normale JSON-foutrespons — precies zoals
    /api/clean. Alleen een fout die pas ontstaat ná de eerste bytes (een
    verbindingsstoring bij een later deel) kan niet meer als HTTP-statuscode
    gemeld worden en verschijnt in de body achter STREAM_ERROR_SENTINEL.
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

    def generate():
        try:
            yield from cleanup.clean_stream(markdown, profile, model)
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


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------

@bp.post("/api/download")
def download():
    """De meegestuurde markdown als .md-bestand terugsturen."""
    data = _payload()
    name = re.sub(r"[^A-Za-z0-9._-]", "_", data.get("filename") or "document") or "document"
    if not name.endswith(".md"):
        name += ".md"
    buf = io.BytesIO(data.get("markdown", "").encode("utf-8"))
    return send_file(buf, mimetype="text/markdown", as_attachment=True, download_name=name)


@bp.app_errorhandler(413)
def _too_large(_e):
    limit = current_app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return jsonify(error=f"Bestand is te groot (maximaal {limit} MB)."), 413
