# Markdown converter — projectcontext

Lokale web-tool (Python/Flask) die jurisprudentie, wetgeving en documenten omzet naar
Markdown, met optionele AI-opschoning. Draait volledig lokaal op de Mac van de gebruiker.
De projectmap heet nog "EUR-lex naar md" (historisch); de tool zelf heet "Markdown converter".

De UI heeft drie tabbladen: **Jurisprudentie** (HvJ EU / EHRM / NL via ECLI of link),
**Wetgeving** (EU via CELEX/ELI/link, NL via wetten.overheid.nl/BWB) en **Documentupload**
(bestand(en) slepen óf link(s) naar een bestand plakken). Tabs 1 en 2 posten beide naar
`/api/convert/link` (auto-detectie); tab 3 naar `/api/convert/file` of `/api/convert/file-url`.
Elk tabblad ondersteunt **meerdere documenten tegelijk** (zie "Meerdere documenten" hieronder).

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
  state.py                 StateFile: mtime-gecachet lezen, flock + atomair schrijven
  render.py                gedeelde HTML→markdown: tidy, koppen promoveren, marker-tabellen
  version.py               lui berekend versienummer/buildteller voor de footer
  sources/
    __init__.py            Document-dataclass, detect_source-precedentie, from_link/from_file
    eurlex.py              CELEX/ELI/EU-ECLI → Cellar, portal als terugval
    rechtspraak.py         ECLI:NL → data.rechtspraak.nl XML → markdown
    hudoc.py               EHRM-ECLI/item-id → HUDOC zoek-API + HTML-body
    wetten.py              BWB/wetten.overheid.nl portal-HTML → markdown
    formex.py              Formex-XML → markdown (context expliciet, dus thread-safe)
    files.py               PDF via pdf-inspector, rest via MarkItDown (beide lui geladen)
  cleanup/
    __init__.py            publieke ingangen: estimate() en clean()
    config.py              standaarden + instellingen (modellen/deelgrootte/prompts)
    prompts.py             de drie systeemprompts
    chunking.py            de splits-ladder (alinea → regel → woord → harde knip)
    openrouter.py          chat-completions + prijscatalogus met TTL-cache
templates/index.html       één pagina, alleen markup
static/app.css             designsysteem (Radix-tokens) + componenten
static/app.js              front-end: één state + render-functies per gebied
tests/                     karakteriseringstests (pinnen het gedrag vast)
```

**De HTTP-laag is dun.** Alleen `mdconv/api.py` importeert Flask. Domeincode gooit
`ConversionError` met een Nederlandse boodschap; de errorhandler maakt daar één keer
`{"error": …}` van. Converters geven een `Document` terug (markdown + bronvermelding +
soort), zodat de route niets over engines of classificatie hoeft te weten.

## Bronherkenning (`mdconv/sources/__init__.py` → `detect_source` + `from_link`)

| Invoer | Route |
|---|---|
| CELEX (`32016R0679`), EUR-Lex link, **ELI-link** (`/eli/reg/2016/679/oj`), **`ECLI:EU:…`** | EUR-Lex |
| **`ECLI:NL:…`** of rechtspraak.nl-link | Rechtspraak.nl |
| HUDOC-link, item-id (`001-…`), **`ECLI:CE:ECHR:…`** | HUDOC (EHRM) |
| wetten.overheid.nl-link of **BWB-nummer** (`BWBR0040940`) | wetten.overheid.nl |
| **`ECLI:DE:…`** (Duitse federale rechtspraak) | rechtsprechung-im-internet.de |
| **`ECLI:BE:…`** (Belgische rechtspraak) | Juportal |

**Buitenlandse rechtspraak** (`_NATIONAL_SOURCES` in `mdconv/sources/__init__.py`): per
ECLI-landcode een eigen module. Nu `DE` → `sources/de_rechtsprechung.py` en `BE` →
`sources/be_juportal.py`; uitbreidbaar door een module met dezelfde vorm (`ECLI_RE` +
`fetch(query) -> (markdown, bron)`) toe te voegen en te registreren in `_NATIONAL_SOURCES`.
Onderzocht maar (nog) niet haalbaar met plain HTTP: **ES** (CENDOJ zet een verplichte,
interactieve CAPTCHA vóór elke volledige-tekst-download — geen sessie/cookie-truc zoals bij
Duitsland, een échte afbeelding-CAPTCHA; gebruikers kunnen zo'n PDF wel gewoon handmatig
uploaden via het bestaande PDF-pad). Frankrijk is gemengd, zie de opmerking bij `FR` hieronder.

## Belangrijke, niet-voor-de-hand-liggende details

- **EUR-Lex fetch**: de portal-HTML (`/legal-content/…/HTML/`) blokkeert bots (HTTP 202, lege body;
  inmiddels een AWS WAF-JS-challenge, dus ook met retries permanent 202 — de portal is in de praktijk
  dood voor een simpele `requests`-scraper). Gebruik het **Cellar-archief** via content negotiation,
  `Accept: application/xhtml+xml, text/html;q=0.9`:
  - CELEX: `http://publications.europa.eu/resource/celex/{CELEX}`
  - EU-ECLI: `http://publications.europa.eu/resource/ecli/{ECLI}` — ECLI **url-encoded** (`ECLI%3AEU%3AC%3A…`), anders 404.
  `Accept-Language` bepaalt de taal. `notice=object` geeft alléén metadata, niet de tekst.
- **Cellar 300 (multiple choice) is niet alleen een taalprobleem.** Sommige documenten — met name
  wetgevingsvoorstellen (CELEX-type `PC`/`DC`) met een losse bijlage — bestaan uit **meerdere
  HTML-onderdelen**, elk een eigen manifestatie. Cellar meldt dat met **HTTP 300** en een lijst
  `…/DOC_1`, `…/DOC_2`, … in documentvolgorde. `eurlex._fetch_multipart()` haalt die op en plakt ze
  aan elkaar (`\n\n---\n\n`). **Belangrijk**: elk `DOC_n`-onderdeel moet met `Accept: text/html`
  worden opgehaald, niet `application/xhtml+xml` — de manifestatie-URL zelf heeft `text/html` als
  resource-mimetype en geeft anders 406. Voorbeeld: `CELEX:52025PC0837` (voorstel + bijlage).
  Alleen als er géén `DOC_n`-links in de 300-respons staan, is het wél een taalprobleem.
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
- **Duitse rechtspraak** (`de_rechtsprechung.py`): rechtsprechung-im-internet.de (BMJ)
  publiceert geselecteerde uitspraken van BGH/BVerfG/BVerwG/BFH/BAG/BSG/BPatG sinds 2010, als
  schone XML met een eigen DTD — maar zonder directe "haal-op-met-ECLI"-URL. Het is een
  Java-portlet-app die eerst doorzocht moet worden: (1) GET het zoekfragment
  (`/js_pane/Suchportlet1/media-type/html`) en lees de verborgen formuliervelden
  (`sugportal`/`sughashcode`/…) uit — die zijn **sessiegebonden** en server-gegenereerd; zonder
  exact die velden geeft de site alleen het lege formulier terug. (2) GET hetzelfde fragment,
  nu met die velden + `query=<ECLI>`, **in dezelfde sessie** (cookies) → de HTML bevat
  `doc.id=<ID>` (of "0 Treffer"). Dit gebeurt met een **eigen `requests.Session`**, niet de
  gedeelde `net.documents()` — die wordt gelijktijdig door andere documenten gebruikt (de tool
  haalt meerdere documenten parallel op) en twee gelijktijdige zoekopdrachten op dezelfde
  JSESSIONID zouden elkaars tussenstaat overschrijven. Elke gevonden `doc.id` heeft daarna een
  vaste, **stateloze** `.../docs/bsjrs/{doc.id}.zip` met één XML erin (dus wél via de gedeelde
  sessie). De secties (`leitsatz`/`tenor`/`tatbestand`/`entscheidungsgruende`/`gruende`/
  `abwmeinung`) bestaan uit `<dl class="RspDL"><dt>…</dt><dd>…</dd></dl>`-paren: `<dt>` het
  randnummer (`<a name="rd_N">N</a>`), `<dd>` de alinea of een `<table>` (bv. het
  handtekeningenblok). `_resolve_doc_id()` onderscheidt een bevestigde "0 Treffer"-melding
  (échte lege uitkomst, geen nieuwe poging) van een technische hapering zonder die melding
  (bv. een gewijzigd formulierveld) — dat laatste wordt één keer opnieuw geprobeerd
  (`_SEARCH_ATTEMPTS`) voordat de tool concludeert dat de uitspraak niet gevonden is.
- **Belgische rechtspraak** (`be_juportal.py`): in tegenstelling tot Duitsland een **stateloze,
  directe** route — `GET https://juportal.be/content/{ECLI}`, geen sessie/tokens nodig. Een
  geldige ECLI geeft 200 met statische HTML (geen JS-rendering); een onbekende geeft **HTTP
  400**. Is een uitspraak later gerectificeerd, dan toont Juportal gewoon 200 met de
  **vervangende** tekst — geen HTTP-redirect — en staat de oorspronkelijke ECLI in het veld
  "Vervangt nummer:" van de metadatatabel; de canonieke ECLI in de bronvermelding komt daaruit,
  niet uit de aangevraagde URL. De volledige tekst staat in het `<fieldset>` met
  `<legend>Tekst van de beslissing</legend>`, als één doorlopend `<p>` met `<br>`-regeleinden
  (geen aparte structuurelementen) — **let op**: de omringende `<div>` bevat bij sommige
  documenten óók een losse, gelekte serverregel (`ERROR JUPORTARobotRecordLienECLI …`) als
  tekstnode vóór de `<p>`; daarom wordt specifiek de `<p>` geselecteerd, niet de hele `<div>`.
  Romeinse-cijfer sectiekoppen ("I. RECHTSPLEGING VOOR HET HOF") worden gepromoveerd; genummerde
  overwegingen ("1.", "2.") blijven bewust gewone alinea's, net als bij de andere bronnen.
- **PDF-conversie** (`mdconv/sources/files.py`): een geüploade/gelinkte `.pdf` gaat eerst door
  **pdf-inspector** (Rust-library van Firecrawl, `process_pdf_bytes()`) — layout-aware Markdown
  (koppen/lijsten/tabellen) zonder de losse-regeleinde-reflow-hack die MarkItDown nodig heeft.
  `result.pdf_type` classificeert de PDF (`text_based`/`scanned`/`image_based`/`mixed`); bij
  `scanned`/`image_based` (geen tekstlaag) of een lege/foutieve extractie valt de code terug op
  MarkItDown (die óók geen OCR doet, maar wel de bestaande gedrag is voor dat geval). Alle andere
  formaten (Word/Excel/PowerPoint/HTML/CSV/JSON/EPUB/…) blijven altijd via MarkItDown lopen —
  pdf-inspector kent alleen PDF. `files.convert()` geeft `(markdown, engine)`
  terug zodat de UI kan tonen welke engine het document daadwerkelijk verwerkte
  (`"pdf-inspector"` of `"MarkItDown"` in het bronveld).

## AI-opschoning (`mdconv/cleanup/`)

- Via **OpenRouter** (OpenAI-compatibele API), niet de Anthropic API. Plain `requests`.
- Sleutel: `OPENROUTER_API_KEY` in `.env`. Optioneel `LLM_MODEL`, `OPENROUTER_BASE_URL`.
- Standaardmodel: **`~anthropic/claude-haiku-latest`** — de **tilde `~` hoort erbij** (OpenRouter's
  auto-updating "latest"-alias). Niet "corrigeren" naar de versie zonder tilde.
- `config.base_url()` normaliseert (strip een eventuele `/chat/completions`), want de code plakt dat pad zelf.
- **Drie profielen** (`cleanup/prompts.py` → `DEFAULTS`): `generic` (documenten/PDF), `caselaw` (uitspraken/arresten:
  koppen vanaf `##`, rechtsoverwegingen behouden, citaten→`>`, lijsten→markdownlijsten,
  voetnoten→`[^n]`) en `obsidian` (complete Obsidian-notitie). De UI kiest `generic`/`caselaw`
  automatisch via het `kind`-veld (`sources.kind_for_source`: Rechtspraak/HUDOC/
  `ECLI:EU:`/`CELEX:6…` = caselaw). `obsidian` is een **handmatige extra keuze**
  (`#profile-choice`, alleen zichtbaar bij `currentKind === "caselaw"`, dus alleen op het
  Jurisprudentie-tabblad) die het automatische profiel overschrijft.
- **`obsidian`-profiel**: system-prompt is verbatim gekopieerd uit de skill
  `~/Downloads/SKILL jurisprudentie.md` (zonder de skill-YAML-frontmatter — dat is
  Claude Code-metadata, geen model-instructie). Levert YAML-frontmatter + inhoudsopgave-
  callout + juridische analyse (feiten/rechtsvragen/argumenten/conclusie/impact) + de
  volledige uitspraak verbatim, in één `` ```markdown ``` ``-codeblok (dat blok wordt eraf
  gestript door `openrouter.strip_markdown_fence` vóórdat het in de textarea komt).
  **Draait altijd ongesplitst** (`config.NO_CHUNK_PROFILES`): frontmatter/analyse
  mag maar één keer voorkomen, dus chunking zou meerdere stukken met elk hun eigen
  frontmatter opleveren. Bij zeer lange arresten kan de output daardoor tegen
  `config.MAX_OUTPUT_TOKENS` aanlopen. `config.OUTPUT_RATIO["obsidian"] = 1.35` compenseert de
  kostenraming voor de extra analyse-tekst bovenop de verbatim-tekst (output > input,
  anders dan bij `generic`/`caselaw` waar output ≈ input).
- **Anderstalige uitspraak → tweetalige tabel.** Bij een niet-Nederlandse uitspraak (Duits,
  Frans, Spaans, …) instrueert het obsidian-profiel het model om onder `## Volledige
  uitspraak` elke rechtsoverweging/randnummer als tabelrij te zetten: links het origineel
  (letterlijk), rechts een Nederlandse vertaling. Bij een al-Nederlandse uitspraak (bv.
  rechtspraak.nl) blijft de oude opmaak (lopende genummerde alinea's) gewoon gelden — de
  prompt maakt dit expliciet conditioneel, anders zou "vertaal niet" (voor de verbatim-eis)
  in de weg staan van de vertaaltabel die de gebruiker net daar wél wil. De `Instantie`-
  YAML-lijst is uitgebreid met de Duitse federale gerechten (BGH, BVerfG, BVerwG, BFH, BAG,
  BSG, BPatG); bij toekomstige landen (BE/FR/AT/ES) moeten hun gerechten er ook bij, anders
  kan het model geen geldige waarde uit de gesloten lijst kiezen.
- **Afkapping wordt niet stilletjes geaccepteerd.** Zowel `clean_chunk()` als
  `stream_chunk()` controleren `choice["finish_reason"]`; is die `"length"`, dan gooien ze
  een `ConversionError` in plaats van de afgekapte tekst terug te geven. Dit was een echte,
  bevestigde bug: een groot document (bv. een EU-voorstel met bijlage, ~108k tokens) liep bij
  het obsidian-profiel tegen `MAX_OUTPUT_TOKENS` (64.000) aan — de bijlage (het tweede "deel")
  verdween daardoor **zonder enige foutmelding**. `_truncation_message(profile)` geeft een
  profielspecifieke boodschap: bij `obsidian` (dat nooit chunkt) wordt aangeraden een ander
  profiel te gebruiken; bij `generic`/`caselaw` wordt aangeraden de deelgrootte te verlagen.
- **Streaming** (`clean_stream()` in `cleanup/__init__.py`, endpoint `/api/clean/stream`):
  levert de opgeschoonde tekst als een reeks stukjes op i.p.v. één keer het hele resultaat.
  Bij meerdere delen worden die **na elkaar** gestreamd (niet parallel zoals `clean()`) —
  de tekst moet in de editor van boven naar onder groeien, in documentvolgorde.
  `openrouter.stream_chunk()` leest OpenRouters SSE-respons (`stream: true`,
  `data: {...}`-regels, afgesloten met `data: [DONE]`) en levert `delta.content`-stukjes op.
  Voor het obsidian-profiel haalt `openrouter.strip_fence_stream()` het
  ```markdown-codeblok er *tijdens* het streamen af (een sluitende ``` mag niet even
  zichtbaar zijn in de live-weergave) — met een kleine "holdback"-buffer die de laatste
  paar tekens vasthoudt totdat zeker is of ze bij de sluitende fence horen.
  **Foutafhandeling na de eerste bytes**: de HTTP-status (200) is dan al verzonden, dus een
  fout die halverwege ontstaat (bv. een afkapping bij het tweede deel) kan niet meer als
  statuscode gemeld worden. Die komt in de body terecht achter `STREAM_ERROR_SENTINEL`
  (`\x00CLEAN_ERROR\x00`, identiek gedefinieerd in `mdconv/api.py` en `static/app.js`) — de
  front-end herkent dat teken, toont de rest als foutmelding, en zet het tekstvak terug naar
  de laatst bewaarde tekst i.p.v. de afgebroken streaming-tekst te laten staan.
  `cleanActiveDoc()` in `app.js` bewaakt met `isLive()` of de gebruiker tijdens het streamen
  naar een ander documenttabblad is gewisseld: dan wordt `doc.markdown` wel bijgewerkt, maar
  niet het zichtbare tekstvak — pas bij terugschakelen toont de editor het complete resultaat.
- **Beide reformat-prompts** (`generic`/`caselaw`) maken alléén echte sectietitels koppen;
  genummerde overwegingen/randnummers blijven alinea's (uitdrukkelijke wens gebruiker —
  niet terugdraaien).
- Lange documenten worden per ~55.000 tokens (`config.get_chunk_tokens()`, ≈220k tekens) in delen verwerkt;
  `max_tokens` = 64.000 (Haiku's output-plafond, dus geen afkapping). Meeste teksten = één call.
  **Let op**: de UI toont `est.input_tokens` (documentgrootte), NIET `input+output` opgeteld —
  dat laatste oogt ~2x zo groot als het echte document (output ≈ input bij opschonen) en
  deed gebruikers denken dat het chunk-aantal niet klopte terwijl het wél correct was.
- **Modelkeuze** (`config.DEFAULT_MODEL_CHOICES`): 7 opties, allemaal via dezelfde
  OpenRouter-sleutel. `config.resolve_model(override)` accepteert een expliciete keuze uit de UI (moet in
  `config.valid_model_ids()` zitten), anders terugval op `LLM_MODEL`/default. De `:nitro`-suffix
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

## Instellingen (⚙-knop rechtsboven)

- `GET /api/settings` → huidige waarden + `defaults` (voor de reset-knoppen per veld,
  géén apart reset-endpoint nodig). `POST /api/settings` → merget het payload over de
  opgeslagen settings en persisteert; een leeg/ongeldig veld (lege modellenlijst, lege
  prompt, deelgrootte buiten `_MIN_CHUNK_TOKENS`–`_MAX_CHUNK_TOKENS`) wist juist dat veld
  terug naar "gebruik de standaardwaarde" in plaats van de ongeldige waarde op te slaan.
- Opslag: `mdconv/cleanup/` → `state.StateFile` leest/schrijft `.deploy-state/settings.json` (dezelfde gitignored, in Docker als volume
  gemounte map als `version.json`; ook hier een `fcntl.flock` tegen gelijktijdige writes
  door meerdere gunicorn-workers). Alleen daadwerkelijk gewijzigde sleutels staan erin —
  ontbrekend/leeg = val terug op de `_DEFAULT_*`-constante.
- De ingebouwde standaardwaarden heten `DEFAULT_MODEL_CHOICES`,
  `DEFAULT_CHUNK_TOKENS` en `prompts.DEFAULTS`. `get_model_choices()`,
  `get_chunk_tokens()` en `get_prompt(profile)` zijn de dynamische lookups; een
  wijziging via de UI werkt daardoor met terugwerkende kracht, zonder herstart.
- UI (`templates/index.html`): `#btn-settings` (header, `position: absolute` in de
  al `position: relative` header) opent `#settings-backdrop`, een modal met
  herhaalbare model-rijen (`modelRow()`/`renderModelRows()`), een `<input type=number>`
  voor de deelgrootte, en drie prompt-`<textarea>`'s. Elke sectie heeft een eigen
  "Standaard"-knop die het bijbehorende veld terugzet naar `data.defaults.*` (uit de
  laatste `GET /api/settings`-respons) — puur client-side, geen extra round-trip.
  Opslaan roept `loadModelChoices()` opnieuw aan zodat de `#model-choice`-dropdown op
  het hoofdscherm meteen de bijgewerkte lijst toont zonder page-reload.

## Front-end (`static/app.js` + `static/app.css` + `templates/index.html`)

Geen framework, geen build-stap. Eén expliciete `state` en per gebied een render-functie
die die state naar de DOM schrijft; wat de gebruiker verandert gaat **eerst** in `state`
en dan door een render. Nooit rechtstreeks de DOM patchen — daar liep de vorige versie
op stuk, doordat dezelfde gegevens in een variabele, in een DOM-waarde én in het
document zelf stonden en uit elkaar liepen bij het wisselen van tabblad.

- **Meerdere documenten**: elk tabblad heeft herhaalbare invoerrijen (`makeRow()`/
  `initRows()`), met "+ toevoegen" en per rij een ×-knop (minstens 1 rij blijft staan).
  `runBatch()` haalt alle ingevulde rijen **parallel** op via `Promise.allSettled`,
  toont voortgang (`3/5 opgehaald…`) en meldt per mislukte rij precies wat faalde —
  één fout blokkeert de rest niet. `#file` heeft `multiple`; slepen en de bestandskiezer
  lopen over alle bestanden.
- **State per document**: `{id, title, filenameBase, source, kind, obsidian, model,
  markdown, cleaned}` in `state.docs`. `obsidian`/`model` zijn **per document**, dus je
  kunt het ene document als Obsidian-notitie opschonen en het andere met het standaard-
  profiel. `setActive()` bewaart eerst de live-bewerkte tekst (`saveEdits()`) voordat het
  volgende document wordt geladen.
- **"Opmaken voor Obsidian" is een checkbox** (`#obsidian`), geen dropdown, en alleen
  zichtbaar bij `kind === "caselaw"`. Aanvinken zet `doc.obsidian` en zet `cleaned` terug
  op false (ander profiel = opnieuw opschonen mag), en vernieuwt de kostenraming.
- **`[hidden]` moet in CSS geforceerd worden.** De UI regelt zichtbaarheid via het
  hidden-attribuut, maar de UA-stijl (`[hidden] { display: none }`) heeft de laagste
  specificiteit: `.checkbox { display: inline-flex }` verslaat hem. Zonder de regel
  `[hidden] { display: none !important }` in `app.css` staat het Obsidian-vinkje
  zichtbaar bij gewone documenten. Er is een test die die regel afdwingt.
- **Regelnummers**: één nummer per échte regel (per enter), niet per visueel omgebogen
  regel. `#line-mirror` is een onzichtbare kloon van de textarea die per regel meet hoe
  hoog die na word-wrap wordt; elk nummer krijgt exact die hoogte. Font en padding komen
  uit `--editor-font`/`--editor-padding`, die beide elementen gebruiken — wijken die
  uiteen, dan lopen de nummers scheef (ook daarvoor is een test).
  Scroll-sync via een CSS-`transform` op `.gutter-inner`, niet via een eigen `scrollTop`.
  De meting is **samengevoegd in één animatieframe** en wordt overgeslagen als tekst en
  breedte niet zijn veranderd: 50 toetsaanslagen leveren 1 herbouw op in plaats van 50.
- **Dialoog**: focus gaat naar binnen en blijft binnen (`trapFocus`), Escape en een klik
  op de achtergrond sluiten, en `body` wordt scroll-vergrendeld zolang hij open staat.
- **Kostenraming** heeft een verzoek-token (`estimateToken`): alleen het laatste antwoord
  mag de UI bijwerken, zodat snel wisselen geen oude raming laat staan.

## Designsysteem (`static/app.css`)

De opmaak volgt **Radix Themes**, met de hand in platte CSS (geen React/npm). De
12-stapsschalen van `@radix-ui/colors` staan letterlijk in `:root`, met de vaste
betekenis per stap: 1 paginablad · 2 subtiel blad · 3 vulling · 4 hover ·
5 actief · 6 zachte rand · 7 rand/ring · 8 hover-rand **en de focusring** ·
9 volvlak · 10 volvlak-hover · 11 secundaire tekst · 12 primaire tekst.

Dark/light volgt `prefers-color-scheme`; er is bewust **geen** knop. Drie dingen
kantelen van betekenis tussen de modi — zonder die omkering leest het niet als Radix:

1. Een paneel is in donker **lichter** dan de pagina (`--gray-2` op `--gray-1`), in licht
   wit-op-wit met alleen een haarlijn.
2. Een invoerveld is in licht een translucent **wit** (opgetild vlak) en in donker een
   translucent **zwart** (verzonken vlak).
3. Stap 9 is identiek in beide modi, maar stap 10 beweegt tegengesteld (donkerder in
   licht, lichter in donker) — daardoor werkt "hover = stap 10" zonder conditionele CSS.

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
  (het `/api/convert/file-url`-endpoint is een SSRF-vector).
- Env-vars via compose: `OPENROUTER_API_KEY`, `LLM_MODEL`, `OPENROUTER_BASE_URL`. Code behandelt
  lege strings als "niet gezet" (`or DEFAULT`), zodat compose's `${VAR:-}` de defaults niet breekt.

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

## Tests
`.venv/bin/python -m pytest tests/ -q` — 83 karakteriseringstests die het gedrag
vastleggen in plaats van het te beschrijven: `detect_source`-precedentie, ELI→CELEX,
de chunking-ladder (ook zonder witregels en met één te lang woord), de PDF-reflow, de
Formex-parser, de settings-semantiek (leeg wist terug naar standaard) en de Nederlandse
foutmeldingen. Ze raken geen netwerk. Verander je de structuur, dan hoeven alleen de
imports mee te verhuizen; blijft de suite groen, dan is het gedrag identiek.

Eén test dwingt gelijktijdigheid af: `test_formex_footnotes_survive_concurrent_conversions`
draait vier Formex-conversies naast elkaar. Dat faalde vóór de herbouw, doordat
`formex.py` een module-level context-stack gebruikte en documenten dus elkaars voetnoten
oppikten — met threaded Flask en parallelle uploads was dat echt bereikbaar.

## Conventies
- Alles lokaal (macOS-launcher) óf via Docker. **Geen build-stap**, geen Node.js: de UI is
  platte HTML/CSS/JS. De opmaak lijkt op Radix Themes, maar er is geen Radix-dependency.
- Toelichtingen en UI-teksten zijn in het Nederlands.
- Domeincode kent geen Flask: alleen `mdconv/api.py` importeert het. Fouten gaan als
  `ConversionError` met een Nederlandse boodschap naar boven.
- Geen `.venv`, `.env` of secrets in versiebeheer (zie `.gitignore`).
