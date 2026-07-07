# Markdown converter — projectcontext

Lokale web-tool (Python/Flask) die jurisprudentie, wetgeving en documenten omzet naar
Markdown, met optionele AI-opschoning. Draait volledig lokaal op de Mac van de gebruiker.
De projectmap heet nog "EUR-lex naar md" (historisch); de tool zelf heet "Markdown converter".

De UI heeft drie tabbladen: **Jurisprudentie** (HvJ EU / EHRM / NL via ECLI of link),
**Wetgeving** (EU via CELEX/ELI/link, NL via wetten.overheid.nl/BWB) en **Documentupload**
(bestand slepen óf een link naar een bestand plakken). Tabs 1 en 2 posten beide naar
`/api/convert/link` (auto-detectie); tab 3 naar `/api/convert/file` of `/api/convert/file-url`.

## Starten

Dubbelklik in Finder op **`Markdown converter.command`** (of draai `./run.sh`).
Dat maakt de eerste keer een `.venv` aan, installeert `requirements.txt`, start de server
op http://127.0.0.1:5001 en opent de browser.

- `run.sh` → deps installeren + browser openen → delegeert aan `serve.sh`.
- `serve.sh` → laadt `.env` **veilig** (alleen `KEY=VALUE`-regels) en start `python app.py`.
- Python 3 moet geïnstalleerd zijn; `run.sh` controleert dit.

## Architectuur

```
app.py                 Flask-server + API-endpoints
converters/
  formex.py            Formex-XML → markdown (eigen parser, voor geüploade .xml)
  eurlex.py            EUR-Lex ophalen (CELEX / EU-ECLI / URL) + generieke HTML→markdown
  caselaw.py           link-dispatcher: rechtspraak.nl (ECLI), HUDOC (EHRM), wetten.overheid.nl
  wetten.py            Nederlandse wetgeving (wetten.overheid.nl / BWB) → markdown
  generic.py           overige bestanden → markdown via Microsoft MarkItDown (+ PDF-reflow)
  llm_cleanup.py       optionele AI-opschoning via OpenRouter + kostenraming
templates/index.html   web-interface (één pagina, vanilla JS)
```

### API-endpoints
- `POST /api/convert/link`  `{query, lang}` → `{markdown, source, kind}` — dispatcht op bron.
- `POST /api/convert/file`  multipart bestand → `{markdown, source, kind}`.
- `POST /api/convert/file-url`  `{url}` → downloadt het bestand en zet het om via MarkItDown.
- `POST /api/estimate`      `{markdown, profile}` → chunks/tokens/kosten voor opschoning.
- `POST /api/clean`         `{markdown, profile}` → opgeschoonde markdown.
- `POST /api/download`      `{markdown, filename}` → `.md`-bestand.
- `GET  /api/config`        → `{llm_available}` (of er een OpenRouter-sleutel is).

## Bronherkenning (`converters/caselaw.py` → `detect_source` + `eurlex.fetch_and_convert`)

| Invoer | Route |
|---|---|
| CELEX (`32016R0679`), EUR-Lex link, **ELI-link** (`/eli/reg/2016/679/oj`), **`ECLI:EU:…`** | EUR-Lex |
| **`ECLI:NL:…`** of rechtspraak.nl-link | Rechtspraak.nl |
| HUDOC-link, item-id (`001-…`), **`ECLI:CE:ECHR:…`** | HUDOC (EHRM) |
| wetten.overheid.nl-link of **BWB-nummer** (`BWBR0040940`) | wetten.overheid.nl |

## Belangrijke, niet-voor-de-hand-liggende details

- **EUR-Lex fetch**: de portal-HTML (`/legal-content/…/HTML/`) blokkeert bots (HTTP 202, lege body).
  Gebruik het **Cellar-archief** via content negotiation, `Accept: application/xhtml+xml`:
  - CELEX: `http://publications.europa.eu/resource/celex/{CELEX}`
  - EU-ECLI: `http://publications.europa.eu/resource/ecli/{ECLI}` — ECLI **url-encoded** (`ECLI%3AEU%3AC%3A…`), anders 404.
  `Accept-Language` bepaalt de taal. `notice=object` geeft alléén metadata, niet de tekst.
- **ELI-links** (`/eli/reg/2016/679/oj`): Cellar resolvet ELI **niet** direct (404) en de portal blokkeert.
  Daarom `eli_to_celex()`: leidt deterministisch een CELEX af (type→letter reg=R/dir=L/dec=D/reco=H,
  `3{jaar}{letter}{nummer:04d}`) en gebruikt vervolgens de normale CELEX-Cellar-route.
- **EUR-Lex koppen**: koppen komen als `<p>` binnen; `_promote_headings` promoot volledig-geankerde
  regels ("Artikel N", "HOOFDSTUK I") naar `##`/`###`. Genummerde alinea's (overwegingen, arrest-
  punten) staan in de xhtml als **tweekoloms-tabellen**; `_unwrap_marker_tables` zet die om naar
  alinea's/lijst-items (nummer ín het bestaande blok, niet nesten).
- **HUDOC**: body via `hudoc.echr.coe.int/app/conversion/docx/html/body?library=ECHR&id={itemid}`.
  Een EHRM-**ECLI** → itemid via de zoek-API: `…/app/query/results?query=ecli:"<ECLI>"&select=itemid,ecli,languageisocode&rankingmodelid=11111_Ranking&sort=&facetquery=&start=0&length=30`
  (die extra params zijn **verplicht**, anders 404; `select` is komma-gescheiden, kleine letters).
  Eén ECLI → meerdere docs (EN=HEJUD, FR=HFJUD, vertalingen=HJUD<TAAL>). Kies op taal, val terug op
  ENG→FRE. Veel vertalingen hebben **geen HTML-body (204)** → probeer kandidaten op volgorde.
- **Rechtspraak.nl**: `https://data.rechtspraak.nl/uitspraken/content?id={ECLI}` geeft schone XML
  (`<uitspraak>` met `section`/`title`/`parablock`/`para`).
- **wetten.overheid.nl**: geen bruikbare XML-export gevonden; de **portal-HTML** is server-rendered
  en bevat de volledige tekst in `#regeling` (h1 titel, h3 hoofdstuk, h4 artikel). `wetten.py` pakt
  die container, strip't werkbalk-ruis (`[class*=action--]`, `.visually-hidden`) en markdownify't.
  URL wordt herbouwd uit BWB-id + optionele versiedatum (`/{jjjj-mm-dd}`).

## AI-opschoning (`converters/llm_cleanup.py`)

- Via **OpenRouter** (OpenAI-compatibele API), niet de Anthropic API. Plain `requests`.
- Sleutel: `OPENROUTER_API_KEY` in `.env`. Optioneel `LLM_MODEL`, `OPENROUTER_BASE_URL`.
- Standaardmodel: **`~anthropic/claude-haiku-latest`** — de **tilde `~` hoort erbij** (OpenRouter's
  auto-updating "latest"-alias). Niet "corrigeren" naar de versie zonder tilde.
- `_base_url()` normaliseert (strip een eventuele `/chat/completions`), want de code plakt dat pad zelf.
- **Twee profielen** (`_PROMPTS`): `generic` (documenten/PDF) en `caselaw` (uitspraken/arresten:
  koppen vanaf `##`, rechtsoverwegingen behouden, citaten→`>`, lijsten→markdownlijsten,
  voetnoten→`[^n]`). De UI kiest automatisch via het `kind`-veld (`_kind_for_source` in `app.py`:
  Rechtspraak/HUDOC/`ECLI:EU:`/`CELEX:6…` = caselaw).
- **Beide prompts** maken alléén echte sectietitels koppen; genummerde overwegingen/randnummers
  blijven alinea's (uitdrukkelijke wens gebruiker — niet terugdraaien).
- Lange documenten worden per ~60.000 tokens (`_CHUNK_TOKENS`, ≈240k tekens) in delen verwerkt;
  `max_tokens` = 64.000 (Haiku's output-plafond, dus geen afkapping). Meeste teksten = één call.
- **NL-wetgeving met een fragment** in de link (`…#Hoofdstuk16`) → `wetten.py` haalt alléén dat
  element op (`soup.find(id=anchor)`), niet de hele regeling.

## Deployment (Docker / VPS)
- `Dockerfile` (python:3.13-slim) draait de app met **gunicorn** (`app:app`, `0.0.0.0:5001`,
  `--timeout 600` want AI-opschoning kan minuten duren). `docker-compose.yml` bindt bewust op
  `127.0.0.1` — de tool heeft **geen auth**; publiek ontsluiten alleen achter reverse proxy + auth
  (het `/api/convert/file-url`-endpoint is een SSRF-vector).
- Env-vars via compose: `OPENROUTER_API_KEY`, `LLM_MODEL`, `OPENROUTER_BASE_URL`. Code behandelt
  lege strings als "niet gezet" (`or DEFAULT`), zodat compose's `${VAR:-}` de defaults niet breekt.

## Conventies
- Alles lokaal (macOS-launcher) óf via Docker. Geen build-stap. Vanilla JS in één HTML-template.
- Toelichtingen en UI-teksten zijn in het Nederlands.
- Geen `.venv`, `.env` of secrets in versiebeheer (zie `.gitignore`).
