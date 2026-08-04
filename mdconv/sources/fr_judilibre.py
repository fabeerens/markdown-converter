"""Franse Cour de cassation via Judilibre (PISTE-portaal, OAuth2).

In tegenstelling tot Légifrance (Cloudflare-geblokkeerd) heeft de Cour de
cassation een officiële, gedocumenteerde JSON-API: Judilibre, ontsloten via
het overheidsportaal PISTE (piste.gouv.fr). Vereist een geregistreerde
PISTE-applicatie met een goedgekeurde souscriptie op "Judilibre" (productie).

**Auth**: OAuth2 `client_credentials`-grant tegen `oauth.piste.gouv.fr`
(gescheiden van de Judilibre-API zelf) levert een Bearer-token (1 uur
geldig); elke API-aanroep gaat met zowel `Authorization: Bearer <token>` als
`KeyId: <client_id>`. `_get_token()` cachet het token tot kort vóór expiry
(`_TOKEN_SAFETY_MARGIN`), zodat niet elke aanroep een nieuwe token ophaalt.

**ECLI-zoeken werkt wél, ondanks eerdere aanname van het tegendeel**: als de
`query`-parameter van `/search` exact een ECLI-string is, herkent Judilibre
dat zelf en herschrijft het intern naar een exacte `terms`-filter op het
`ecli`-veld (zichtbaar in de `searchQuery`-debugkey van de respons) — geen
losse ECLI-parameter nodig. Het eerste resultaat waarvan `ecli` exact matcht,
levert het interne `id`; `/decision?id=<id>` geeft de volledige platte tekst
(`text`-veld) — geen HTML, dus geen structuurwalker nodig, alleen alinea's op
lege regels.

Sleutels in `.env`: `PISTE_JUDILIBRE_PROD_KEY` / `PISTE_JUDILIBRE_PROD_SECRET`
(productie — sandbox-Judilibre bevat alleen demodata, dus niet bruikbaar
voor echte opzoekingen).
"""

from __future__ import annotations

import os
import re
import time

from .. import net
from ..errors import ConfigError, ConversionError
from ..render import tidy

ECLI_RE = re.compile(r"ECLI:FR:CCASS:\d{4}:[A-Za-z0-9]+", re.I)

_OAUTH_URL = "https://oauth.piste.gouv.fr/api/oauth/token"
_API_BASE = "https://api.piste.gouv.fr/cassation/judilibre/v1.0"
_TIMEOUT = 30
_MIN_USEFUL_LENGTH = 40

# Ruim onder de 3600s-geldigheid, zodat een cachetreffer nooit net vóór
# gebruik alsnog verlopen blijkt te zijn.
_TOKEN_SAFETY_MARGIN = 120

_token_cache: dict[str, object] = {"token": None, "expires_at": 0.0}


def _credentials() -> tuple[str, str]:
    key = os.environ.get("PISTE_JUDILIBRE_PROD_KEY")
    secret = os.environ.get("PISTE_JUDILIBRE_PROD_SECRET")
    if not key or not secret:
        raise ConfigError(
            "PISTE_JUDILIBRE_PROD_KEY/PISTE_JUDILIBRE_PROD_SECRET ontbreken in .env "
            "(nodig voor Cour de cassation-uitspraken via Judilibre)."
        )
    return key, secret


def _get_token() -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    key, secret = _credentials()
    r = net.documents().post(
        _OAUTH_URL,
        data={"grant_type": "client_credentials", "client_id": key, "client_secret": secret,
              "scope": "openid"},
        timeout=_TIMEOUT,
    )
    if r.status_code != 200:
        raise ConversionError(
            f"Kon niet authenticeren bij PISTE/Judilibre (status {r.status_code}) — "
            "controleer de souscriptie op de Judilibre-API in het PISTE-portaal."
        )
    data = r.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 3600) - _TOKEN_SAFETY_MARGIN
    return _token_cache["token"]


def fetch(query: str) -> tuple[str, str]:
    """Haal een uitspraak van de Cour de cassation op; geeft (markdown, bronvermelding)."""
    m = ECLI_RE.search(query)
    if not m:
        raise ConversionError(
            "Geen geldig ECLI-nummer voor de Cour de cassation herkend "
            "(bv. ECLI:FR:CCASS:2019:C100589)."
        )
    ecli = m.group(0).upper()

    key, _ = _credentials()
    token = _get_token()
    headers = {"Authorization": f"Bearer {token}", "KeyId": key}

    r = net.documents().get(
        f"{_API_BASE}/search", params={"query": ecli}, headers=headers, timeout=_TIMEOUT
    )
    if r.status_code != 200:
        raise ConversionError(
            f"Judilibre-zoekopdracht voor {ecli} mislukte (status {r.status_code})."
        )
    results = r.json().get("results") or []
    hit = next((res for res in results if (res.get("ecli") or "").upper() == ecli), None)
    if hit is None:
        raise ConversionError(f"Geen uitspraak gevonden voor {ecli} op Judilibre.")

    r2 = net.documents().get(
        f"{_API_BASE}/decision", params={"id": hit["id"]}, headers=headers, timeout=_TIMEOUT
    )
    if r2.status_code != 200:
        raise ConversionError(f"Kon de uitspraaktekst voor {ecli} niet downloaden.")

    markdown = _decision_to_markdown(r2.json())
    if len(markdown.strip()) < _MIN_USEFUL_LENGTH:
        raise ConversionError(f"Uitspraak {ecli} bevat geen leesbare tekst.")
    return markdown, f"Judilibre • {ecli}"


def _decision_to_markdown(decision: dict) -> str:
    chamber = decision.get("chamber") or ""
    number = decision.get("number") or ""
    date = decision.get("decision_date") or ""
    heading = " ".join(p for p in ("Cour de cassation", chamber, number, date) if p)
    blocks = [f"# {heading}"]

    text = (decision.get("text") or "").strip()
    if text:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        blocks.append("\n\n".join(paragraphs))

    return tidy("\n\n".join(blocks))
