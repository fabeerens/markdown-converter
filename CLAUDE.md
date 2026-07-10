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
- `POST /api/estimate`      `{markdown, profile, model?}` → chunks/tokens/kosten voor opschoning.
- `POST /api/clean`         `{markdown, profile, model?}` → opgeschoonde markdown.
- `POST /api/download`      `{markdown, filename}` → `.md`-bestand.
- `GET  /api/config`        → `{llm_available, models}` (of er een sleutel is + de modelkeuzelijst).

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
- **Drie profielen** (`_PROMPTS`): `generic` (documenten/PDF), `caselaw` (uitspraken/arresten:
  koppen vanaf `##`, rechtsoverwegingen behouden, citaten→`>`, lijsten→markdownlijsten,
  voetnoten→`[^n]`) en `obsidian` (complete Obsidian-notitie). De UI kiest `generic`/`caselaw`
  automatisch via het `kind`-veld (`_kind_for_source` in `app.py`: Rechtspraak/HUDOC/
  `ECLI:EU:`/`CELEX:6…` = caselaw). `obsidian` is een **handmatige extra keuze**
  (`#profile-choice`, alleen zichtbaar bij `currentKind === "caselaw"`, dus alleen op het
  Jurisprudentie-tabblad) die het automatische profiel overschrijft.
- **`obsidian`-profiel**: system-prompt is verbatim gekopieerd uit de skill
  `~/Downloads/SKILL jurisprudentie.md` (zonder de skill-YAML-frontmatter — dat is
  Claude Code-metadata, geen model-instructie). Levert YAML-frontmatter + inhoudsopgave-
  callout + juridische analyse (feiten/rechtsvragen/argumenten/conclusie/impact) + de
  volledige uitspraak verbatim, in één `` ```markdown ``` ``-codeblok (dat blok wordt eraf
  gestript door `_strip_markdown_fence` vóórdat het in de textarea komt).
  **Draait altijd ongesplitst** (`_NO_CHUNK_PROFILES = {"obsidian"}`): frontmatter/analyse
  mag maar één keer voorkomen, dus chunking zou meerdere stukken met elk hun eigen
  frontmatter opleveren. Bij zeer lange arresten kan de output daardoor tegen
  `_MAX_OUTPUT_TOKENS` aanlopen. `_OUTPUT_RATIO["obsidian"] = 1.35` compenseert de
  kostenraming voor de extra analyse-tekst bovenop de verbatim-tekst (output > input,
  anders dan bij `generic`/`caselaw` waar output ≈ input).
- **Beide reformat-prompts** (`generic`/`caselaw`) maken alléén echte sectietitels koppen;
  genummerde overwegingen/randnummers blijven alinea's (uitdrukkelijke wens gebruiker —
  niet terugdraaien).
- Lange documenten worden per ~55.000 tokens (`_CHUNK_TOKENS`, ≈220k tekens) in delen verwerkt;
  `max_tokens` = 64.000 (Haiku's output-plafond, dus geen afkapping). Meeste teksten = één call.
  **Let op**: de UI toont `est.input_tokens` (documentgrootte), NIET `input+output` opgeteld —
  dat laatste oogt ~2x zo groot als het echte document (output ≈ input bij opschonen) en
  deed gebruikers denken dat het chunk-aantal niet klopte terwijl het wél correct was.
- **Modelkeuze** (`MODEL_CHOICES` in `llm_cleanup.py`): 7 opties, allemaal via dezelfde
  OpenRouter-sleutel. `_model(override)` accepteert een expliciete keuze uit de UI (moet in
  `_VALID_MODEL_IDS` zitten), anders terugval op `LLM_MODEL`/default. De `:nitro`-suffix
  (snelste provider) bestaat NIET als los item in OpenRouter's `/models`-catalogus — `get_pricing()`
  matcht daarom ook op het model-id vóór de `:`, anders krijgt elk `:nitro`-model `cost=None`.
  UI: dropdown in `#model-choice`, gevuld vanuit `/api/config`, keuze onthouden in `localStorage`.
  Een `change`-listener op `#model-choice` roept `loadEstimate()` opnieuw aan zodat de
  kostenraming meteen het nieuw gekozen model reflecteert (was eerder een gemiste update).
- **Regelnummers**: altijd aan (`#gutter`), geen toggle. Eén nummer per brontekst-regel
  (niet per visueel omgebogen regel) — `#line-mirror` is een onzichtbare kloon van de
  textarea (zelfde font/breedte/padding) waarin elke regel als eigen `<div>` wordt gemeten
  (`getBoundingClientRect().height`); de gutter geeft elk nummer precies die hoogte, zodat
  een lange gewrapte zin één nummer krijgt met witruimte eronder. Herberekend bij
  input/resize en na elke nieuwe/opgeschoonde tekst.
  **Scroll-sync**: `#gutter` heeft géén eigen `scrollTop` — de nummers staan in
  `#gutter-inner`, dat met een CSS-`transform: translateY(-textarea.scrollTop)` exact
  evenveel verschuift als de textarea scrolt (`syncGutterScroll()`), pixel-precies en
  zonder aparte scroll-container-eigenaardigheden.
  **Gelijke hoogte**: `#editor` (flex-row) heeft een expliciete `height: 460px` +
  `resize: vertical` — gutter en textarea vullen dat samen met `height:100%`, zodat ze
  nooit uit elkaar kunnen lopen. De textarea's eigen `resize` staat uit (`resize:none`);
  de gebruiker resized het hele blok via de rand van `#editor`. Een `ResizeObserver` op
  `#editor` roept `syncGutterScroll()` opnieuw aan na zo'n resize.
- **NL-wetgeving met een fragment** in de link (`…#Hoofdstuk16`) → `wetten.py` haalt alléén dat
  element op (`soup.find(id=anchor)`), niet de hele regeling.

## Deployment (Docker / VPS)
- `Dockerfile` (python:3.13-slim) draait de app met **gunicorn** (`app:app`, `0.0.0.0:5001`,
  `--timeout 600` want AI-opschoning kan minuten duren). `docker-compose.yml` bindt bewust op
  `127.0.0.1` — de tool heeft **geen auth**; publiek ontsluiten alleen achter reverse proxy + auth
  (het `/api/convert/file-url`-endpoint is een SSRF-vector).
- Env-vars via compose: `OPENROUTER_API_KEY`, `LLM_MODEL`, `OPENROUTER_BASE_URL`. Code behandelt
  lege strings als "niet gezet" (`or DEFAULT`), zodat compose's `${VAR:-}` de defaults niet breekt.

## Versienummer (footer) — git-onafhankelijk
- `VERSION`-bestand = handmatige major.minor.patch. Build-nummer + installatiedatum komen
  NIET van git (dat faalde in Docker: geen git-binary, geen `.git`/build-args nodig)
  maar worden door `app.py:_read_version()` zelf bijgehouden.
- Mechanisme: `_source_fingerprint()` hasht `app.py` + `VERSION` + `requirements.txt` +
  `converters/*.py` + `templates/*.html`. Wijkt de hash af van de laatst opgeslagen
  fingerprint in `.deploy-state/version.json`, dan wordt `build` +1 en `installed_at` =
  nu. Ongewijzigd → build blijft gelijk (idempotent bij herstarts).
- `.deploy-state/` staat in `.gitignore`. In Docker is het als **volume** gemount
  (`docker-compose.yml`) zodat de teller een `docker compose build` overleeft — zonder
  die volume-mount zou elke rebuild terugvallen naar build 1.
- Een `fcntl.flock` op `.deploy-state/.lock` voorkomt dat meerdere gunicorn-workers
  gelijktijdig dubbel ophogen bij opstarten.

## Conventies
- Alles lokaal (macOS-launcher) óf via Docker. Geen build-stap. Vanilla JS in één HTML-template.
- Toelichtingen en UI-teksten zijn in het Nederlands.
- Geen `.venv`, `.env` of secrets in versiebeheer (zie `.gitignore`).
