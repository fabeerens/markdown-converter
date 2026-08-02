"""Markdown converter — startpunt.

Lokaal:  ./run.sh  (of: python app.py)  → http://127.0.0.1:5001
Docker:  gunicorn app:app               (zie Dockerfile)

De app zelf zit in het `mdconv`-pakket; dit bestand bestaat alleen om hem te
starten, zodat zowel `python app.py` als `gunicorn app:app` blijft werken.
"""

from __future__ import annotations

import os

from mdconv import create_app

app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    # threaded=True is Flask's standaard: meerdere documenten tegelijk ophalen
    # loopt dus echt parallel.
    app.run(host="127.0.0.1", port=port, debug=False)
