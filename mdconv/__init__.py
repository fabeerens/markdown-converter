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


def create_app() -> Flask:
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

    from .api import bp
    app.register_blueprint(bp)
    return app
