"""Eén gedeelde, gepoolde HTTP-client voor alle uitgaande verzoeken.

Voorheen deed elke converter zijn eigen `requests.get(...)`, waardoor er per
verzoek een nieuwe TCP+TLS-verbinding werd opgezet — bij het ophalen van
meerdere documenten tegelijk is dat de grootste vertraging. Hier staat één
`Session` met connection pooling en keep-alive, plus nette retries voor
idempotente verzoeken.

Twee sessies, bewust gescheiden:

- `documents()` — voor het ophalen van bronteksten. Retries aan: die endpoints
  (Cellar, HUDOC, wetten.overheid.nl) geven onder druk wel eens een 502/503,
  en opnieuw proberen is gratis en veilig.
- `llm()` — voor OpenRouter. Retries UIT op POST: een opschoonverzoek kost geld
  en kan minuten duren; automatisch herhalen zou dubbel factureren.
"""

from __future__ import annotations

import threading

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Ruim genoeg om meerdere documenten tegelijk op te halen zonder dat urllib3
# verbindingen weggooit ("Connection pool is full").
_POOL_SIZE = 16

_lock = threading.Lock()
_documents: requests.Session | None = None
_llm: requests.Session | None = None


def _make_session(retries: Retry | int) -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    adapter = HTTPAdapter(
        pool_connections=_POOL_SIZE, pool_maxsize=_POOL_SIZE, max_retries=retries,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def documents() -> requests.Session:
    """Gedeelde sessie voor het ophalen van brondocumenten."""
    global _documents
    if _documents is None:
        with _lock:
            if _documents is None:
                _documents = _make_session(Retry(
                    total=2,
                    backoff_factor=0.4,
                    status_forcelist=(500, 502, 503, 504),
                    # Alleen idempotente methodes; POST wordt nooit herhaald.
                    allowed_methods=frozenset({"GET", "HEAD"}),
                    raise_on_status=False,
                ))
    return _documents


def llm() -> requests.Session:
    """Gedeelde sessie voor OpenRouter — géén automatische retries."""
    global _llm
    if _llm is None:
        with _lock:
            if _llm is None:
                _llm = _make_session(0)
    return _llm


def decoded_text(response: requests.Response) -> str:
    """Antwoordtekst met de tekenset die de bron feitelijk gebruikt.

    De EU-endpoints laten de charset vaak weg, waardoor requests terugvalt op
    latin-1 en accenten kapot gaan; vandaar de encoding-detectie.
    """
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text
