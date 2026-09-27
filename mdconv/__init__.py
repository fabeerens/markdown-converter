"""Markdown converter — applicatiefabriek.

`create_app()` zet de Flask-app op en registreert de routes. Er gebeurt hier
bewust geen zwaar werk: MarkItDown, pdf-inspector en de versie-vingerafdruk
worden pas bij het eerste gebruik geladen, zodat de server meteen luistert.
"""

from __future__ import annotations

import os

from flask import Flask

__all__ = ["create_app"]

# Uploadgrens. Grotere bestanden geven een nette 413 (zie api._too_large).
_MAX_UPLOAD_BYTES = 32 * 1024 * 1024


def create_app(ai_enabled: bool | None = None) -> Flask:
    """`ai_enabled=None` volgt de omgevingsvariabele `MDCONV_AI` (zie
    `mdconv/features.py`); tests geven de waarde expliciet mee. `False`
    vergrendelt AI op uit; `True` laat het aan de schakelaar in de instellingen."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(
        __name__,
        template_folder=os.path.join(root, "templates"),
        static_folder=os.path.join(root, "static"),
        static_url_path="/static",
    )
    app.config["MAX_CONTENT_LENGTH"] = _MAX_UPLOAD_BYTES
    # Compacte JSON en geen alfabetische sortering: de UI leest velden op naam,
    # en dit scheelt bytes bij grote markdown-antwoorden.
    app.json.sort_keys = False

    from .features import ai_locked_off
    app.config["AI_LOCKED_OFF"] = ai_locked_off() if ai_enabled is None else not ai_enabled

    from .api import ai_bp, bp
    app.register_blueprint(bp)
    # Vergrendeld zonder AI bestaan de AI-/OCR-/instellingenroutes simpelweg
    # niet (404). Anders zijn ze er, en bewaakt ai_bp zelf de schakelaar.
    if not app.config["AI_LOCKED_OFF"]:
        app.register_blueprint(ai_bp)
    return app
