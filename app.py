"""Markdown converter — lokale web-interface.

Start met:  ./run.sh   (of: python app.py)
Open dan:   http://127.0.0.1:5001
"""

from __future__ import annotations

import io
import os
import re
import subprocess
from urllib.parse import urlparse, unquote

import requests
from flask import Flask, jsonify, render_template, request, send_file

from converters.caselaw import convert_link
from converters.formex import convert_formex
from converters.generic import convert_with_markitdown
from converters.llm_cleanup import (
    clean_markdown,
    estimate as llm_estimate,
    is_available as llm_is_available,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 MB upload limit


_BASE_DIR = os.path.dirname(__file__)


def _read_base_version() -> str:
    """Major.minor.patch — bumped by hand in VERSION for meaningful releases."""
    path = os.path.join(_BASE_DIR, "VERSION")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=_BASE_DIR, capture_output=True, text=True,
            timeout=5, check=True,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _read_version() -> str:
    """Base version + build metadata: commit count (auto-increments every
    commit) and short commit hash. Resolved from `git` when the repo is
    present (local dev); falls back to GIT_COMMIT/GIT_COMMIT_COUNT baked in
    at Docker build time (see Dockerfile), since the running container
    doesn't have `git` or the `.git` history.
    """
    base = _read_base_version()
    count = _git("rev-list", "--count", "HEAD") or os.environ.get("GIT_COMMIT_COUNT")
    commit = _git("rev-parse", "--short", "HEAD") or os.environ.get("GIT_COMMIT")
    if count and commit and commit != "unknown":
        return f"{base}+{count}.{commit}"
    return base


APP_VERSION = _read_version()


def _llm_available() -> bool:
    """Is an OpenRouter API key configured?"""
    return llm_is_available()


@app.route("/")
def index():
    return render_template("index.html", version=APP_VERSION)


@app.get("/api/config")
def config():
    """Report which optional features are available to the UI."""
    return jsonify(llm_available=_llm_available())


def _looks_like_formex(data: bytes) -> bool:
    """Heuristic: is this an EUR-Lex Formex XML document?"""
    head = data[:8000].upper()
    return b"<" in head[:200] and any(
        m in head for m in (b"FORMEX", b"<ACT", b"ENACTING.TERMS",
                            b"CONS.DOC", b"<CONSID", b"<ARTICLE")
    )


@app.post("/api/convert/file")
def convert_file():
    """Convert an uploaded file to Markdown.

    Formex XML → dedicated structural parser. Any other format (PDF, Word,
    Excel, PowerPoint, HTML, …) → Microsoft MarkItDown.
    """
    if "file" not in request.files:
        return jsonify(error="Geen bestand ontvangen."), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify(error="Leeg bestand."), 400
    data = f.read()
    if not data.strip():
        return jsonify(error="Het bestand is leeg."), 400

    is_xml = f.filename.lower().endswith(".xml")
    try:
        if is_xml and _looks_like_formex(data):
            markdown = convert_formex(data)
            source = f"Formex XML • {f.filename}"
            # Guard against a mislabelled / non-Formex XML slipping through.
            if len(markdown.strip()) < 40:
                markdown = convert_with_markitdown(data, f.filename)
                source = f"MarkItDown • {f.filename}"
        else:
            markdown = convert_with_markitdown(data, f.filename)
            source = f"MarkItDown • {f.filename}"
    except Exception as e:  # noqa: BLE001
        return jsonify(error=f"Conversie mislukt: {e}"), 400
    return jsonify(markdown=markdown, source=source, kind="document")


_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

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


def _filename_from_url(url: str, content_type: str) -> str:
    """Derive a sensible filename (with extension) from a URL + content-type."""
    name = os.path.basename(unquote(urlparse(url).path)) or "document"
    if "." not in name:
        ct = (content_type or "").split(";")[0].strip().lower()
        name += _CT_EXT.get(ct, "")
    return name


@app.post("/api/convert/file-url")
def convert_file_url():
    """Download a document from a URL (e.g. a PDF link) and convert it."""
    payload = request.get_json(silent=True) or {}
    url = (payload.get("url") or "").strip()
    if not re.match(r"^https?://", url, re.I):
        return jsonify(error="Voer een geldige URL in (beginnend met http:// of https://)."), 400
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=60)
        if r.status_code != 200:
            return jsonify(error=f"Kon het bestand niet ophalen (status {r.status_code})."), 400
        data = r.content
        if not data:
            return jsonify(error="Het opgehaalde bestand is leeg."), 400
        if len(data) > 40 * 1024 * 1024:
            return jsonify(error="Bestand is groter dan 40 MB."), 400
        filename = _filename_from_url(url, r.headers.get("Content-Type", ""))
        markdown = convert_with_markitdown(data, filename)
    except Exception as e:  # noqa: BLE001
        return jsonify(error=f"Conversie mislukt: {e}"), 400
    return jsonify(markdown=markdown, source=f"MarkItDown • {url}", kind="document")


def _kind_for_source(source: str) -> str:
    """Classify a converted document as 'caselaw' or 'document' from its source."""
    s = source or ""
    if s.startswith("Rechtspraak.nl") or s.startswith("HUDOC"):
        return "caselaw"
    if "ECLI:EU:" in s or "CELEX:6" in s:  # sector 6 = EU case law
        return "caselaw"
    return "document"


_VALID_PROFILES = {"generic", "caselaw"}


@app.post("/api/convert/link")
def convert_link_route():
    """Fetch a document by link/identifier: EUR-Lex (CELEX), ECLI, or HUDOC."""
    payload = request.get_json(silent=True) or {}
    query = (payload.get("query") or "").strip()
    lang = (payload.get("lang") or "NL").strip()
    if not query:
        return jsonify(error="Voer een CELEX-nummer, ECLI, of link in."), 400
    try:
        markdown, source = convert_link(query, lang)
    except Exception as e:  # noqa: BLE001
        return jsonify(error=str(e)), 400
    return jsonify(markdown=markdown, source=source, kind=_kind_for_source(source))


@app.post("/api/estimate")
def estimate_cleanup():
    """Estimate chunks/tokens/cost for the AI cleanup of the given markdown."""
    payload = request.get_json(silent=True) or {}
    markdown = payload.get("markdown", "")
    profile = payload.get("profile") if payload.get("profile") in _VALID_PROFILES else "generic"
    return jsonify(llm_estimate(markdown, profile))


@app.post("/api/clean")
def clean():
    """Run the AI cleanup pass on the given markdown."""
    payload = request.get_json(silent=True) or {}
    markdown = payload.get("markdown", "")
    profile = payload.get("profile") if payload.get("profile") in _VALID_PROFILES else "generic"
    if not markdown.strip():
        return jsonify(error="Niets om op te schonen."), 400
    try:
        cleaned = clean_markdown(markdown, profile)
    except Exception as e:  # noqa: BLE001
        return jsonify(error=str(e)), 400
    return jsonify(markdown=cleaned)


@app.post("/api/download")
def download():
    """Return the posted markdown as a downloadable .md file."""
    payload = request.get_json(silent=True) or {}
    markdown = payload.get("markdown", "")
    name = payload.get("filename") or "eurlex"
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "eurlex"
    if not name.endswith(".md"):
        name += ".md"
    buf = io.BytesIO(markdown.encode("utf-8"))
    return send_file(buf, mimetype="text/markdown",
                     as_attachment=True, download_name=name)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="127.0.0.1", port=port, debug=False)
