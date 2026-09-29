# AI-opschoning en instellingen

Verplaatst uit `CLAUDE.md` (WP-60, 29 september 2026), tekst ongewijzigd. `CLAUDE.md` bevat de
architectuur en verwijst hiernaartoe.

## AI-opschoning (`mdconv/cleanup/`)

- Via **OpenRouter** (OpenAI-compatibele API), niet de Anthropic API. Plain `requests`.
- Sleutel: `OPENROUTER_API_KEY` in `.env`. Optioneel `LLM_MODEL`, `OPENROUTER_BASE_URL`.
- Standaardmodel: **`~anthropic/claude-haiku-latest`** — de **tilde `~` hoort erbij** (OpenRouter's
  auto-updating "latest"-alias). Niet "corrigeren" naar de versie zonder tilde.
- `config.base_url()` normaliseert (strip een eventuele `/chat/completions`), want de code plakt dat pad zelf.
- **Vier profielen** (`cleanup/prompts.py` → `DEFAULTS`): `generic` (documenten/PDF), `caselaw` (uitspraken/arresten:
  koppen vanaf `##`, rechtsoverwegingen behouden, citaten→`>`, lijsten→markdownlijsten,
  voetnoten→`[^n]`), `obsidian` (complete Obsidian-notitie) en `translate_nl` (zuivere
  vertaling naar het Nederlands, structuur ongewijzigd). De UI kiest `generic`/`caselaw`
  automatisch via het `kind`-veld (`sources.kind_for_source`: Rechtspraak/HUDOC/
  `ECLI:EU:`/`CELEX:6…` = caselaw). `obsidian` is een **handmatige extra keuze**
  (checkbox `#obsidian`, zichtbaar bij `doc.allowObsidian` — automatisch bij herkende
  rechtspraak, en ook op Documentupload/Tekst plakken: daar kan de tool niet zien of het
  om een uitspraak gaat, dus mag de gebruiker dat zelf aangeven) die het automatische
  profiel overschrijft. `translate_nl` is geen keuze in die dropdown maar een **eigen
  knop** (`#translate-nl`, "Vertalen naar het Nederlands") naast "Opschonen", op elk
  tabblad — een losse, onafhankelijke actie die je vóór of ná het opschonen kunt draaien
  (eigen `doc.translated`-vlag, blokkeert `doc.cleaned` niet en andersom).
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
- **`translate_nl`-profiel**: draait wél gechunkt (niet in `NO_CHUNK_PROFILES`) — elk deel
  wordt onafhankelijk vertaald, net als `generic`/`caselaw`. Eigen user-prompt-template
  (`prompts.USER_PROMPTS["translate_nl"]`, "Translate this Markdown fragment into
  Dutch:") in plaats van de generieke `DEFAULT_USER_PROMPT` ("Clean up…"), en een eigen
  `OUTPUT_RATIO` van 1,15 voor de kostenraming (een Nederlandse vertaling is doorgaans
  iets langer dan de brontekst). De front-end (`runClean()` in `app.js`) deelt dezelfde
  streaming-implementatie als "Opschonen" — alleen het profiel, de knop en het
  guard-veld (`doc.translated` i.p.v. `doc.cleaned`) verschillen.
- **Anderstalige uitspraak → tweetalige tabel.** Bij een niet-Nederlandse uitspraak (Duits,
  Frans, Spaans, …) instrueert het obsidian-profiel het model om onder `## Volledige
  uitspraak` elke rechtsoverweging/randnummer als tabelrij te zetten: links het origineel
  (letterlijk), rechts een Nederlandse vertaling. Bij een al-Nederlandse uitspraak (bv.
  rechtspraak.nl) blijft de oude opmaak (lopende genummerde alinea's) gewoon gelden — de
  prompt maakt dit expliciet conditioneel, anders zou "vertaal niet" (voor de verbatim-eis)
  in de weg staan van de vertaaltabel die de gebruiker net daar wél wil. De `Instantie`-
  YAML-lijst is uitgebreid met de Duitse federale gerechten (BGH, BVerfG, BVerwG, BFH, BAG,
  BSG, BPatG); bij toekomstige landen (AT/ES, of overige FR-gerechten) moeten hun gerechten er
  ook bij, anders kan het model geen geldige waarde uit de gesloten lijst kiezen.
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
  `runClean()` in `app.js` bewaakt met `isLive()` of de gebruiker tijdens het streamen
  naar een ander documenttabblad is gewisseld: dan wordt `doc.markdown` wel bijgewerkt, maar
  niet het zichtbare tekstvak — pas bij terugschakelen toont de editor het complete resultaat.
- **Voortgang, tokengebruik/kosten en annuleren.** Naast platte tekst kan de stream twee
  afgesloten control-frames bevatten — anders dan `CLEAN_ERROR` (dat altijd het allerlaatste
  in de stream is, dus zonder sluiting): `\x00CLEAN_PROGRESS\x00{...json...}\x00` en
  `\x00CLEAN_USAGE\x00{...json...}\x00` (gebouwd door `_frame()` in `mdconv/api.py`).
  `openrouter.stream_chunk()` yieldt tussen de tekst-stukjes een `Usage`-marker zodra
  OpenRouter die in de laatste SSE-regel van een deel meestuurt (`prompt_tokens`/
  `completion_tokens`/`total_tokens`/`cost` — automatisch aanwezig, geen extra requestveld
  nodig); `cleanup.clean_stream()` telt dat op over alle delen en yieldt zelf `Progress`-
  markers (geproduceerde tekens ÷ 4 vs. de verwachte totale uitvoer — invoergrootte ×
  `OUTPUT_RATIO`, dezelfde schatting als `estimate()`) telkens na `_PROGRESS_STEP_CHARS`
  (400) nieuwe tekens. De front-end-tegenhanger (`makeStreamParser()` in `app.js`) ontleedt
  dit met een kleine buffer die over de grenzen van losse `reader.read()`-happen heen werkt,
  want een frame kan best halverwege een netwerkhap doorlopen.
  **Annuleren** loopt via een `request_id` (door de front-end gegenereerd,
  `Date.now()-Math.random()`) die meegaat in het `/api/clean/stream`-verzoek.
  `mdconv/cleanup/cancel.py` is een proces-brede, thread-safe set van geannuleerde
  `request_id`'s; `/api/clean/cancel` (POST, alleen `request_id`) zet 'm erin,
  `stream_chunk()` checkt 'm per binnenkomende SSE-regel (en sluit dan meteen de
  OpenRouter-verbinding) en `clean_stream()` checkt 'm ook tussen delen — beide stoppen dan
  stil (geen `ConversionError`, dat zou als foutmelding in de UI belanden). De front-end
  (`cancelActiveClean()`) breekt tegelijk zijn eigen `fetch()` af via een `AbortController`
  — dat is wat de gebruiker meteen ziet; de servercheck is vooral bedoeld om te voorkomen dat
  een groot document op de achtergrond dooronline blijft genereren (en dus geld kost) nadat
  de gebruiker al is gestopt met wachten. Opschonen/vertalen mag **per document** maar één
  keer tegelijk lopen (`activeCleans` in `app.js`, een `Map` van docId → `{requestId,
  controller}`) — nog een keer starten terwijl hetzelfde document al bezig is geeft een
  duidelijke foutmelding, maar **verschillende documenten lopen gewoon gelijktijdig**
  (elk zijn eigen `/api/clean/stream`-verzoek; de Flask-dev-server draait `threaded=True`,
  gunicorn in Docker draait `gthread`). De voortgangsbalk en Annuleren-knop in het
  opschoonpaneel zijn gedeelde DOM-elementen die altijd het document weerspiegelen dat op
  dat moment in de editor staat — `renderEditor()` leest `activeCleans.has(doc.id)` bij elke
  wisseling opnieuw uit, en `cancelActiveClean()` annuleert specifiek het weergegeven
  document, niet "de eerste de beste" lopende actie.
- **Beide reformat-prompts** (`generic`/`caselaw`) maken alléén echte sectietitels koppen;
  genummerde overwegingen/randnummers blijven alinea's (uitdrukkelijke wens gebruiker —
  niet terugdraaien).
- Lange documenten worden per ~55.000 tokens (`config.get_chunk_tokens(model)`, ≈220k tekens)
  in delen verwerkt; `max_tokens` = 64.000 (Haiku's output-plafond, dus geen afkapping). Meeste
  teksten = één call. **Deelgrootte is per AI-endpoint instelbaar**, geen centrale instelling
  (zie "Instellingen" hieronder) — een model met een kleiner effectief contextvenster kan zo
  een kleinere deelgrootte krijgen zonder dat dat de andere endpoints raakt.
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
  prompt, deelgrootte buiten `MIN_CHUNK_TOKENS`–`MAX_CHUNK_TOKENS`) wist juist dat veld
  terug naar "gebruik de standaardwaarde" in plaats van de ongeldige waarde op te slaan.
- **Deelgrootte is per AI-endpoint, geen centrale instelling.** Elk item in `models` is
  `{id, label, chunk_tokens}`; `chunk_tokens: null` (leeg gelaten in de UI) betekent
  "gebruik `DEFAULT_CHUNK_TOKENS` voor dit endpoint". `config.get_chunk_tokens(model)`
  zoekt het model op in `get_model_choices()` en geeft diens eigen waarde terug, anders de
  standaard — zonder `model` (of een onbekend model) altijd de standaard, er is geen
  centraal veld meer om op terug te vallen. `chunking.chunks_for()`/`split()` krijgen het
  al-opgeloste model (`config.resolve_model(...)`) doorgegeven vanuit `cleanup.estimate()`/
  `clean()`/`clean_stream()`, vóórdat er iets gesplitst wordt.
- Opslag: `mdconv/cleanup/` → `state.StateFile` leest/schrijft `.deploy-state/settings.json` (dezelfde gitignored, in Docker als volume
  gemounte map als `version.json`; ook hier een `fcntl.flock` tegen gelijktijdige writes
  door meerdere gunicorn-workers). Alleen daadwerkelijk gewijzigde sleutels staan erin —
  ontbrekend/leeg = val terug op de `_DEFAULT_*`-constante.
- De ingebouwde standaardwaarden heten `DEFAULT_MODEL_CHOICES`,
  `DEFAULT_CHUNK_TOKENS` en `prompts.DEFAULTS`. `get_model_choices()`,
  `get_chunk_tokens(model)` en `get_prompt(profile)` zijn de dynamische lookups; een
  wijziging via de UI werkt daardoor met terugwerkende kracht, zonder herstart.
- UI (`templates/index.html`): `#open-settings` (header) opent `#settings`, een modal met
  herhaalbare model-rijen (`modelRow()`/`renderModelRows()` — id, label, én een
  `<input type=number class=mchunk>` voor de deelgrootte van dát endpoint) en vier
  prompt-`<textarea>`'s. Elke sectie heeft een eigen "Standaard"-knop die het bijbehorende
  veld terugzet naar `data.defaults.*` (uit de laatste `GET /api/settings`-respons) — puur
  client-side, geen extra round-trip; "Standaardlijst" bij AI-endpoints zet zo ook alle
  per-endpoint deelgroottes terug (de standaardlijst heeft er zelf geen ingesteld).
  Opslaan roept `loadConfig()` opnieuw aan zodat de modellenlijst op het hoofdscherm meteen
  de bijgewerkte lijst toont zonder page-reload.
