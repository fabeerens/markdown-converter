# Markdown converter — projectcontext

Lokale web-tool (Python/Flask) die jurisprudentie, wetgeving en documenten omzet naar
Markdown, met optionele AI-opschoning. Draait volledig lokaal op de Mac van de gebruiker.
De projectmap heet nog "EUR-lex naar md" (historisch); de tool zelf heet "Markdown converter".

De UI heeft vijf tabbladen: **Jurisprudentie** (HvJ EU / EHRM / NL via ECLI of link),
**Wetgeving** (EU via CELEX/ELI/link, NL via wetten.overheid.nl/BWB), **Open overheid**
(subtabs Stukken, Consultaties en Wetgevingskalender: ophalen via id/dossiernotatie/link/D-nummer
óf zoeken — zie `docs/bronnen/open-overheid.md`),
**Documentupload**
(bestand(en) slepen óf link(s) naar een bestand plakken) en **Tekst plakken** (kale of
verrijkte tekst rechtstreeks in een `contenteditable`-vak plakken/typen). Tabs 1 en 2
posten beide naar `/api/convert/link` (auto-detectie); tab Open overheid naar
`/api/convert/overheid` (geen taalkeuze) en zoekt via `/api/search`; Documentupload naar `/api/convert/file` of
`/api/convert/file-url` (of, met de **wiskunde-modus** aan, naar de streaming-varianten
`/api/convert/file/ocr` resp. `/api/convert/file-url/ocr` — zie `docs/bronnen/bestanden-pdf-en-tekst.md`);
Tekst plakken naar `/api/convert/text`. De eerste vier bronnen-tabs (Jurisprudentie, Wetgeving, Open overheid, Documentupload) ondersteunen **meerdere
documenten tegelijk** (zie "Meerdere documenten" in `docs/frontend-en-designsysteem.md`); Tekst plakken is één plakvak per keer
— een batch van tekstvakken past niet bij hoe je knipt-en-plakt. Bij die vier kun je
bovendien een hele **lijst** in één keer aanleveren (zie "Batch-import" in `docs/frontend-en-designsysteem.md`).

## Starten

Dubbelklik in Finder op **`Markdown converter.command`** (of draai `./run.sh`).
Dat maakt de eerste keer een `.venv` aan, installeert `requirements.txt`, start de server
op http://127.0.0.1:5001 en opent de browser.

- `run.sh` → deps installeren + browser openen → delegeert aan `serve.sh`.
- `serve.sh` → laadt `.env` **veilig** (alleen `KEY=VALUE`-regels) en start `python app.py`.
- Python 3 moet geïnstalleerd zijn; `run.sh` controleert dit.

## Architectuur

```
app.py                     startpunt: create_app() + app.run() (gunicorn-doel blijft app:app)
mdconv/
  __init__.py              create_app(): Flask-app, uploadgrens, blueprint
  api.py                   ALLE routes, dun: valideren → één domeinfunctie → JSON
  errors.py                ConversionError/ConfigError/UpstreamError (+ .status)
  net.py                   gedeelde gepoolde requests-Sessions (retries alleen op GET)
  ocr.py                   wiskunde-modus: PDF pagina-voor-pagina door een vision-LLM
  search.py                zoeken in Open overheid (SRU + Woo) met één uniforme resultaatvorm
  state.py                 StateFile: mtime-gecachet lezen, flock + atomair schrijven
  render.py                gedeelde HTML→markdown: tidy, koppen promoveren, marker-tabellen
  version.py               lui berekend versienummer/buildteller voor de footer
  sources/
    __init__.py            Document-dataclass, detect_source-precedentie, from_link/from_file
    eurlex.py              CELEX/ELI → Formex, Cellar-HTML/portal als terugval; EU-ECLI en sector-6-CELEX → formex_hof, zonder terugval
    formex_hof.py          Cellar-Formex van een arrest/beschikking (JUDGMENT/ORDER) → raw-vorm voor de kennisbank
    rechtspraak.py         ECLI:NL → data.rechtspraak.nl XML → raw-vorm + herkomst + bronbewijs
    hudoc.py               EHRM-ECLI/item-id → HUDOC zoek-API (metadata) + DOCX, zonder terugval
    hudoc_docx.py          HUDOC-Word-bestand → raw-vorm voor de kennisbank
    wetten.py              BWB-XML van KOOP → markdown; portal-HTML als terugval
    bwb_xml.py             BWB-toestand → raw-vorm voor de kennisbank
    formex.py              losse Formex-XML-upload → algemene Markdown
    formex_xml.py          Cellar-Formex-zip → raw-vorm voor de kennisbank
    xml_gedeeld.py         fail-closed XML-tabellen en nummerankers
    files.py               PDF via pdf-inspector (een PDF zonder tekstlaag is een weigering, geen terugval), rest via MarkItDown (beide lui geladen); `pdf_kwaliteit()` meet de omzetting
    docx.py                Word-bestand → raw-vorm; generieke lezer met een `Kaart` per uitgever (HUDOC = `hudoc_docx.py`), koppen uit `outlineLvl`/`Heading N`, noten native, weigert wat het niet kent
    html_document.py       HTML-pagina → raw-vorm; container `<main>` → `<article>` → `[role=main]` en weigert als dat niet eenduidig is; bewaart alleen de gekozen inhoud als bron
    officiele_bekendmakingen.py  Kamerstuk (`kst-…`) → officiële XML van KOOP + `metadata.xml`; geen XML = weigeren, nooit terugval op de PDF
    kamerstuk.py           Open overheid: invoerherkenning (id, link, dossiernotatie, D-nummer via OData) en de PDF-terugval; de XML gaat door officiele_bekendmakingen.py
    woo.py                 documenten van open.overheid.nl (o.a. Woo): zoek-API, bestand → markdown, relaties
    consultatie.py         internetconsultatie.nl: consultaties, documenten, reacties, zoeken (scraping)
    wgk.py                 wetgevingskalender.overheid.nl: regeling-XML → markdown, zoeken
    sru.py                 SRU-client (repository.overheid.nl): zoeken, record op id, bijlagen van een stuk
    common.py              Fetched-dataclass, bijlage()-items (linklijst), header(), slug()
    pasted_text.py         handmatig geplakte tekst (kaal of verrijkte HTML) → markdown
    pdf_images.py           losse afbeeldingen uit een PDF (pdfimages/pdfinfo, poppler)
  attachments.py            tijdelijke, token-based opslag van geëxtraheerde afbeeldingen
  kb_bundle.py              tijdelijke bronopslag + uitpakbare kb-download (wetgeving, rechtspraak, en documenten met een documentnummer)
  cleanup/
    __init__.py            publieke ingangen: estimate(), clean(), clean_stream()
    config.py              standaarden + instellingen (modellen/deelgrootte/prompts)
    prompts.py             de vier systeemprompts
    chunking.py            de splits-ladder (alinea → regel → woord → harde knip)
    openrouter.py          chat-completions + prijscatalogus met TTL-cache
    cancel.py              in-memory annuleervlaggen voor /api/clean/stream
templates/index.html       één pagina, alleen markup
static/app.css             designsysteem (Radix-tokens) + componenten
static/app.js              front-end: één state + render-functies per gebied
tests/                     karakteriseringstests (pinnen het gedrag vast)
meetlat/                   vaste verzameling EU-documenten + meetscript voor de Formex-dekking
```

**De HTTP-laag is dun.** Alleen `mdconv/api.py` importeert Flask. Domeincode gooit
`ConversionError` met een Nederlandse boodschap; de errorhandler maakt daar één keer
`{"error": …}` van. Converters geven een `Document` terug (markdown + bronvermelding +
soort), zodat de route niets over engines of classificatie hoeft te weten.

## Bronherkenning (`mdconv/sources/__init__.py` → `detect_source` + `from_link`)

| Invoer | Route |
|---|---|
| CELEX (`32016R0679`), EUR-Lex link, **ELI-link** (`/eli/reg/2016/679/oj`), **`ECLI:EU:…`** | EUR-Lex |
| **Geconsolideerde versie**: CELEX met datum (`02014R0910-20241018`) of gedateerde ELI (`/eli/reg/2014/910/2024-10-18`) | EUR-Lex (+ overwegingen uit de basishandeling) |
| **`ECLI:NL:…`** of rechtspraak.nl-link | Rechtspraak.nl |
| **Publicatie-id** (`kst-34851-4`) of officielebekendmakingen.nl-link | Officiële Bekendmakingen (alleen Kamerstukken; XML of weigering) |
| HUDOC-link, item-id (`001-…`), **`ECLI:CE:ECHR:…`** | HUDOC (EHRM) |
| wetten.overheid.nl-link of **BWB-nummer** (`BWBR0040940`) | wetten.overheid.nl |
| **`kst-…`/`ah-tk-…`/`h-tk-…`/`blg-…`-id**, officielebekendmakingen.nl- of tweedekamer.nl-link | Open overheid → `kamerstuk.py` (staat bóvenaan `detect_source`) |
| **open.overheid.nl-link**, `ronl-…`/`oep-…`-id | Open overheid → `woo.py` (idem) |
| **internetconsultatie.nl-link**, **`WGK…`-nummer/wetgevingskalender-link** | Open overheid → `consultatie.py` resp. `wgk.py` (idem) |
| **`ECLI:DE:…`** (Duitse rechtspraak) | OpenLegalData (terugval: rechtsprechung-im-internet.de) |
| **`ECLI:BE:…`** (Belgische rechtspraak) | Juportal |
| **`ECLI:FR:CC:…`** (Conseil constitutionnel) of **`ECLI:FR:CCASS:…`** (Cour de cassation); overige FR-gerechten: nette foutmelding | conseil-constitutionnel.fr resp. Judilibre |

**Buitenlandse rechtspraak** (DE, BE, FR via `_NATIONAL_SOURCES`) en waarom ES en AT niet haalbaar zijn:
`docs/bronnen/buitenlandse-rechtspraak.md`.

## Belangrijke, niet-voor-de-hand-liggende details

De uitleg per bron staat sinds WP-60 in `docs/` (tekst ongewijzigd verplaatst; lees het document van de
bron waar je aan werkt vóór je iets wijzigt, want de details zijn er met hun meetgeval opgeschreven):

| Onderwerp | Document |
|---|---|
| EUR-Lex: Formex, Cellar 300, ELI, geconsolideerde versies, koppen | `docs/bronnen/eurlex-formex.md` |
| HUDOC (EHRM) via de DOCX | `docs/bronnen/hudoc.md` |
| Hof van Justitie en Gerecht via Formex | `docs/bronnen/hof-van-justitie.md` |
| Rechtspraak.nl (ECLI:NL, open data) | `docs/bronnen/rechtspraak-nl.md` |
| wetten.overheid.nl (BWB-XML) | `docs/bronnen/wetten-nl.md` |
| Duitse, Belgische en Franse rechtspraak; ES en AT | `docs/bronnen/buitenlandse-rechtspraak.md` |
| PDF, EPUB, losse afbeeldingen (poppler), wiskunde-modus, geplakte tekst | `docs/bronnen/bestanden-pdf-en-tekst.md` |
| Documenten voor de kennisbank (`documenten`) | `docs/documenten-profiel.md` |
| AI-opschoning (OpenRouter), de versie zonder AI (`MDCONV_AI`) en de instellingen | `docs/ai-opschoning-en-instellingen.md` |
| Front-end (`app.js`, `app.css`) en designsysteem | `docs/frontend-en-designsysteem.md` |
| Meetlat voor de Formex-dekking | `docs/meetlat-formex.md` |
| Open overheid: Kamerstukken, Woo, zoeken, consultaties, wetgevingskalender, weergave | `docs/bronnen/open-overheid.md` |

Wat er per versie veranderde, staat in `CHANGELOG.md`; de datums van de meetgevallen staan in de documenten hierboven.
Verwijzingen als "zie Front-end hieronder" in die documenten wijzen naar het document uit de tabel.


## Prestaties — waar de winst zit (en waarom)
- **Lui laden.** `import markitdown` kost honderden ms; die gebeurt nu pas bij de eerste
  bestandsconversie (`files._markitdown_engine()`), niet bij het importeren van de app.
  Idem de versie-vingerafdruk (`version.current()`, pas bij het eerste verzoek dat de
  footer nodig heeft). Gemeten: starttijd van 0,40s naar 0,13s.
- **Instellingen-cache.** `state.StateFile.read()` doet één `os.stat()` en parseert alleen
  als `(mtime, grootte)` is veranderd. Eerder las elke losse getter het bestand opnieuw,
  meerdere keren per opschoonverzoek. Gemeten: 2000× (profiel + model) van 50 ms naar 3,5 ms,
  en een wijziging via de UI werkt nog steeds meteen door zonder herstart.
- **Eén gepoolde `requests.Session`** per doel (`net.documents()` / `net.llm()`) i.p.v. een
  losse `requests.get` per aanroep: keep-alive scheelt bij meerdere documenten de TCP+TLS-
  opzet per stuk. Retries staan **alleen** op GET; een POST naar OpenRouter mag nooit
  automatisch herhaald worden, want dat kost geld en duurt minuten.
- **Chunks parallel.** `cleanup.clean()` verwerkt de delen van een lang document met een
  kleine thread-pool (max 3) en plakt ze in volgorde weer aan elkaar.
- **Prijscatalogus** wordt een uur gecachet (inclusief een mislukte poging), zodat de
  kostenraming niet bij elke wisseling het netwerk op gaat.
- **Front-end**: regelnummers worden per animatieframe herbouwd en overgeslagen als er
  niets is veranderd (50 toetsaanslagen → 1 herbouw).

## Deployment (Docker / VPS)
- `Dockerfile` (python:3.13-slim) draait de app met **gunicorn** en de **gthread**-worker
  (`--workers 2 --threads 8 --timeout 600`). Threads zijn nodig omdat een opschoonverzoek
  minuten op OpenRouter wacht; met alleen processen bezet zo'n verzoek een hele worker en
  staat de tool stil. `docker-compose.yml` bindt bewust op
  `127.0.0.1` — de tool heeft **geen auth**; publiek ontsluiten alleen achter reverse proxy + auth
  (`/api/convert/file-url` én `/api/convert/file-url/ocr` zijn SSRF-vectoren).
- **poppler-utils** (apt) wordt in de Dockerfile meegeïnstalleerd voor het extraheren van
  losse afbeeldingen én het rasteren van pagina's voor de wiskunde-modus
  (`mdconv/sources/pdf_images.py` — `pdfimages`/`pdfinfo`/`pdftoppm`). Lokaal (macOS via
  `run.sh`): `brew install poppler`.
- Env-vars via compose: `OPENROUTER_API_KEY`, `LLM_MODEL`, `OPENROUTER_BASE_URL`, `OCR_MODEL`,
  `OCR_DPI`, `MDCONV_AI` (zie "Versie zonder AI"). Code behandelt lege strings als "niet gezet" (`or DEFAULT`), zodat compose's
  `${VAR:-}` de defaults niet breekt.

## Versienummer (footer) — git-onafhankelijk
- `VERSION`-bestand = handmatige major.minor.patch. Build-nummer + installatiedatum komen
  NIET van git (dat faalde in Docker: geen git-binary, geen `.git`/build-args nodig)
  maar worden door `mdconv/version.py` zelf bijgehouden — **lui**, bij het eerste verzoek.
- Mechanisme: `_fingerprint()` hasht `app.py` + `VERSION` + `requirements.txt` +
  `mdconv/**/*.py` + `templates/*.html` + `static/*`. Wijkt de hash af van de laatst opgeslagen
  fingerprint in `.deploy-state/version.json`, dan wordt `build` +1 en `installed_at` =
  nu. Ongewijzigd → build blijft gelijk (idempotent bij herstarts).
- `.deploy-state/` staat in `.gitignore`. In Docker is het als **volume** gemount
  (`docker-compose.yml`) zodat de teller een `docker compose build` overleeft — zonder
  die volume-mount zou elke rebuild terugvallen naar build 1.
- `state.StateFile.write()` schrijft atomair (tmp + `os.replace`) en vergrendelt met
  `fcntl.flock`, zodat meerdere gunicorn-workers de teller niet dubbel ophogen en een half
  weggeschreven bestand nooit als geldige staat gelezen kan worden.
- **`installed_at` staat vast op Europe/Amsterdam** (`datetime.now(_TZ)`, `_TZ =
  ZoneInfo("Europe/Amsterdam")`), niet op de tijdzone van de host. Een kale
  `datetime.now()` gaf op een server die zonder eigen `TZ`-instelling draait (de standaard
  in Docker: UTC) twee uur het verkeerde tijdstip. `tzdata` (requirements.txt) levert de
  tijdzonedatabase zelf mee, want een minimale Docker-image (`python:3.13-slim`) heeft
  `/usr/share/zoneinfo` niet per se aan boord — zonder die dependency zou `ZoneInfo(...)`
  daar een `ZoneInfoNotFoundError` geven in plaats van gewoon te werken.

## Tests
`.venv/bin/python -m pytest tests/ -q` — 803 tests (`tests/test_kamerstuk.py`, `test_open_overheid.py` en
`test_consultatie_wgk.py` zijn de Open-overheid-bronnen, het zoeken en de weergave; `test_kb_route_streng.py` de drie
besluiten van WP-77; de rest karakteriseringstests) die het gedrag
vastleggen in plaats van het te beschrijven: `detect_source`-precedentie, ELI→CELEX,
de CLG-markupnormalisatie (lidnummers, lettermarkers, voetnootankers), de
voetnootdefinities die met de preambule meereizen, de notitievorm die een intakepoort
passeert, de geconsolideerde-CELEX-afhandeling (datum behouden, preambule invoegen, en de vier
terugvalpaden als dat niet lukt), de versie-terugvalladder (nieuwste versie op of vóór de
gevraagde datum, nooit een latere, en een notitie die niet beweert dat een bestaande versie
niet bestaat), de chunking-ladder (ook zonder witregels en met één te
lang woord), de PDF-reflow, de losse Formex-parser en de Cellar-Formex-route (inclusief
bronbytes, manifestidentiteit en fail-closed tabellen), de EPUB-parser (koppen (échte tags én
koppromotie op CSS-typografie voor EPUB's zonder kop-tags), interne links → wikilinks met
de juiste terugvallen, de nav/inhoudsopgave overslaan, afbeeldingen weglaten, en de
terugval naar MarkItDown bij een ongeldige structuur), de settings-semantiek (leeg wist terug naar
standaard), de batch-zip (eigen naam en eigen `attachments/`-map per document), de wiskunde-modus
(paginasortering + paginagrens bij het rasteren, de stream-orchestratie: `\n\n`-join, voortgang per
pagina, opgeteld tokengebruik, stil annuleren, en de streaming-endpoint) en de
Nederlandse foutmeldingen. Enkele tests pinnen de front-end vast waar Python niet bij de
JS kan: de id's die `app.js` per conventie opbouwt (`#bulk-<kind>-text`, `#ocr-mode` enz.) moeten in
`index.html` bestaan, en het CELEX-patroon mag maar één keer in `app.js` voorkomen. Ze raken geen netwerk. Verander je de structuur, dan hoeven alleen de
imports mee te verhuizen; blijft de suite groen, dan is het gedrag identiek.

Eén test dwingt gelijktijdigheid af: `test_formex_footnotes_survive_concurrent_conversions`
draait vier Formex-conversies naast elkaar. Dat faalde vóór de herbouw, doordat
`formex.py` een module-level context-stack gebruikte en documenten dus elkaars voetnoten
oppikten — met threaded Flask en parallelle uploads was dat echt bereikbaar.

## Conventies
- Alles lokaal (macOS-launcher) óf via Docker. **Geen build-stap**, geen Node.js: de UI is
  platte HTML/CSS/JS. De opmaak lijkt op Radix Themes, maar er is geen Radix-dependency.
- Toelichtingen en UI-teksten zijn in het Nederlands.
- **Bronspecifieke voorbewerking hoort in de bronmodule, niet in `render.py`.** Die module is
  bewust **bronloos**: geen klassenamen, geen profielparameter. `html_to_markdown` wordt gedeeld
  met `hudoc.py`, `container_to_markdown` met `wetten.py`, `pasted_text.py`,
  `fr_conseil_constitutionnel.py` en `de_openlegaldata.py` — en twee daarvan lossen een bijna
  identiek markerpatroon al zélf op (`span.numero-considerant`, `span.absatzRechts` via
  `_merge_absatz_pairs()`). Het spoor dat je volgt: `wetten.py` strip't portal-ruis op de soup,
  `fr_conseil_constitutionnel.py` kiest zijn eigen container, `eurlex.py` normaliseert CLG-markup
  — allemaal vóór de gedeelde render. Er is een test die afdwingt dat CLG-klassenamen niet in
  `render.py` terechtkomen.
- **De herkomst is niet voor de UI.** Een bron levert naast de markdown een `Herkomst`
  (`mdconv/herkomst.py`) met de bronbytes en de geldigheid. `_doc_payload()` haalt haar
  bewust uit het JSON-antwoord. Alleen een document met een kb-identiteit — wetgeving op
  BWB of CELEX, een uitspraak op ECLI — krijgt een opaak `bundle_token`; de server maakt daar bij download de kb-zip en het `.source.json` van.
  Andere documenten blijven een los `.md`-bestand. Tests leggen beide paden vast.
- Domeincode kent geen Flask: alleen `mdconv/api.py` importeert het. Fouten gaan als
  `ConversionError` met een Nederlandse boodschap naar boven.
- Geen `.venv`, `.env` of secrets in versiebeheer (zie `.gitignore`).
