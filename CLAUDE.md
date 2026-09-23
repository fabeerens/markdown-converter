# Markdown converter — projectcontext

Lokale web-tool (Python/Flask) die jurisprudentie, wetgeving en documenten omzet naar
Markdown, met optionele AI-opschoning. Draait volledig lokaal op de Mac van de gebruiker.
De projectmap heet nog "EUR-lex naar md" (historisch); de tool zelf heet "Markdown converter".

De UI heeft vier tabbladen: **Jurisprudentie** (HvJ EU / EHRM / NL via ECLI of link),
**Wetgeving** (EU via CELEX/ELI/link, NL via wetten.overheid.nl/BWB), **Documentupload**
(bestand(en) slepen óf link(s) naar een bestand plakken) en **Tekst plakken** (kale of
verrijkte tekst rechtstreeks in een `contenteditable`-vak plakken/typen). Tabs 1 en 2
posten beide naar `/api/convert/link` (auto-detectie); tab 3 naar `/api/convert/file` of
`/api/convert/file-url`; tab 4 naar `/api/convert/text`. Tabs 1–3 ondersteunen **meerdere
documenten tegelijk** (zie "Meerdere documenten" hieronder); tab 4 is één plakvak per keer
— een batch van tekstvakken past niet bij hoe je knipt-en-plakt. Bij **Wetgeving** kun je
bovendien een hele **lijst** in één keer aanleveren (zie "Batch-import" onder Front-end).

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
| **`ECLI:DE:…`** (Duitse rechtspraak) | OpenLegalData (terugval: rechtsprechung-im-internet.de) |
| **`ECLI:BE:…`** (Belgische rechtspraak) | Juportal |
| **`ECLI:FR:CC:…`** (Conseil constitutionnel) of **`ECLI:FR:CCASS:…`** (Cour de cassation); overige FR-gerechten: nette foutmelding | conseil-constitutionnel.fr resp. Judilibre |

**Buitenlandse rechtspraak** (`_NATIONAL_SOURCES` in `mdconv/sources/__init__.py`): per
ECLI-landcode een eigen module. Nu `DE` → `sources/de_openlegaldata.py` (valt intern terug
op `sources/de_rechtsprechung.py`), `BE` → `sources/be_juportal.py`, `FR` →
`sources/fr_conseil_constitutionnel.py` (dispatcht intern naar `sources/fr_judilibre.py`
voor de Cour de cassation); uitbreidbaar door een module met dezelfde vorm
(`ECLI_RE` + `fetch(query) -> (markdown, bron)`) toe te voegen en te registreren in
`_NATIONAL_SOURCES`.

**Onderzocht maar niet haalbaar met plain HTTP** (geen browserautomatisering, geen verplichte
accountregistratie namens de gebruiker):
- **ES** (CENDOJ) — een verplichte, interactieve afbeelding-CAPTCHA vóór elke
  volledige-tekst-download (geen sessie/cookie-truc zoals bij Duitsland, een échte CAPTCHA).
  Ook een browserautomatiserings-tool (`computingvictor/mcp-cendoj`, onderzocht) bootst alleen
  de interactieve zoek-UI met Playwright na en garandeert niet dat het downloadpad zonder
  CAPTCHA blijft — niet ingezet, want dat is precies het soort anti-bot-omzeiling die dit
  project bewust vermijdt. Gebruikers kunnen zo'n PDF wel gewoon handmatig uploaden via het
  bestaande PDF-pad.
- **AT** (RIS) heeft wél een gratis, sleutelloze JSON-API (`data.bka.gv.at/ris/api/v2.6`),
  maar géén gedocumenteerde ECLI-zoekparameter (bevestigd: nul treffers voor "ECLI" in de
  60 pagina's officiële API-documentatie; een `Ecli=`-parameter wordt genegeerd, niet als
  filter toegepast). Vrije-tekstzoeken op de ECLI-string (`Suchworte`) werkte één keer bij
  toeval en gaf bij hertesten (ook later, met dezelfde ECLI) telkens 0 treffers — niet
  betrouwbaar genoeg om te bouwen. De wél betrouwbare route (zoeken op `Geschaeftszahl`)
  vereist het terugrekenen van die Geschäftszahl uit de ECLI, en dat encoderingsschema is
  nergens gedocumenteerd; één reverse-engineerpoging op een OGH-voorbeeld klopte niet
  (verwachte senaatsnummer "3", afgeleid "30"). Niet gebouwd op basis van onzekere gok-logica.

## Belangrijke, niet-voor-de-hand-liggende details

- **EUR-Lex fetch**: wetgeving probeert eerst de officiële Formex-manifestatie uit Cellar
  (`Accept: application/zip;mtype=fmx4`, taal als ISO 639-2). Alleen `PK`-bytes activeren
  die route. Een ontvangen zip wordt fail-closed gecontroleerd op documentvolgorde,
  tekstbehoud, bladalinea's, tabellen en structurele eenheden; een niet-zipantwoord valt
  terug op de bestaande HTML-ladder en legt die keuze in de herkomst vast. De bestaande
  `sources/formex.py` blijft de tolerante parser voor een handmatig geüploade losse XML.
  Een geconsolideerde tekst (`CONS.ACT`) heeft een lege `PREAMBLE`; de omzetter haalt dan de
  Formex van de basishandeling (`INFO.CONSLEG/@CONSLEG.REF` → `3…`) en zet haar considerans
  vóór de bepalingen, met de noten van die considerans als eigen reeks vóór de
  vaststellingsformule (`Herkomst.recitals_from`, en de basiszip als bron met `role: "preamble"`).
  Een basishandeling die een andere handeling blijkt te zijn (`BIB.INSTANCE/NO.DOC`) weigert de
  download; een die niet op te halen is of geen overwegingen heeft laat de tekst zonder
  considerans, met `Herkomst.recitals_reason` en een waarschuwing. Vet of cursief dat aan een
  woord vastzit (`cyberbeveiliging<HT TYPE="BOLD">s</HT>certificering`) wordt niet geschreven,
  en opmaak in een kop (`HOOFDSTUK II` cursief) ook niet: beide braken de herkenning in de kb.
  Bij een geconsolideerde tekst leest de omzetter de vindplaats en de wijzigingen uit de bron,
  niet uit de tekst: `CONS.DOC/FAM.COMP/BIB.DATA/BIB.INSTANCE.CONS` geeft `oj_reference` van de
  basishandeling in dezelfde vorm als bij een Publicatieblad-handeling (`PB L 151 van 7.6.2019,
  blz. 15`; een deel van een vindplaats wordt nooit geschreven, en een blok dat een andere
  handeling noemt dan de consolidatie weigert), en `FAM.COMP/GR.MOD.ACT` geeft
  `Herkomst.amendments`, de lijst die `extract_meta.py` als `side["amendments"]` leest. Elk element
  heeft `celex`, uit `NO.CELEX` en gecontroleerd tegen `3` + `NO.DOC/YEAR` + de letter van
  `LEG.VAL` (`REG`→R, `DIR`→L, `DEC`→D) + `NO.CURRENT` op vier cijfers; spreken ze elkaar
  tegen, dan weigert de omzetting, is een van beide niet te bepalen, dan staat de handeling
  niet in de lijst en zegt een waarschuwing waarom. `shown: true` staat er als een `CLG.MDFO`-
  verwerkingsinstructie in de tekst (of een bijlage) `ACTIVE.DOC="<celex>"` noemt: de Formex-vorm
  van het ▼M-teken, dat de tekst zelf niet draagt. Een handeling zonder zo'n instructie krijgt
  geen `shown` (niet `false`: dat de wijziging is overschreven is voor Formex niet gemeten).
  `corrections` schrijft de omzetter niet; een `MOD.ACT` met een ander `TYPE` dan `MOD` komt met
  een waarschuwing niet in `amendments`.
  Een **definitielijst** (`DLIST`) is genummerd: elk `DLIST.ITEM` wordt `16) “term” …` als
  eigen alinea met een eigen structuureenheid (`art-4-16`). Draagt de `DEFINITION` zelf een
  `LIST`, `DLIST` of `TBL`, dan blijft dat een opsomming: de kopregel loopt tot het eerste
  structurele kind — de aanhef van AVG punt 22 (`… omdat:`) hoort dus nog op die regel — en de
  onderdelen krijgen het punt als ankerouder (`art-4-16-a`). Een `LIST` binnen `QUOT.S` splitst
  niet: die citeert een andere handeling. Tot 22 september 2026 ging `DEFINITION` altijd door
  `inline()` en kreeg een `DLIST` nooit een basis mee, waardoor artikel 4 AVG en artikel 3 LED
  één samengevoegde alinea per punt gaven, geen van de 26 definitiepunten een eenheid had, en
  het bronbewijs van de kennisbank de regel niet terugvond.
  Een **wijzigingshandeling** citeert soms hele artikelen binnen `QUOT.S` (zeven in `32026R1744`,
  de Digitale omnibus AI): `ARTICLE`/`TI.ART`/`STI.ART` lopen daar inline door zoals geciteerde
  leden al deden, en de structuurcontrole telt alleen artikelen búiten een citaat. Diezelfde
  handeling draagt een geciteerde bijlage als **inclusie**: een zipbestand dat niet het manifest
  (`REF.PHYS`) maar de handeling zelf aanwijst (`BIB.INSTANCE/INCLUSIONS/INCL.ELEMENT
  TYPE="FORMEX.DOC"`) en dat de tekst aanroept als `<P><QUOT.S><INCL.ELEMENT FILEREF=…/></QUOT.S></P>`.
  `_onderdelen` geeft inclusies apart terug; `geciteerde_inclusie()` schrijft ze als blok op die plek
  (titel als alinea, inhoud via `bijlage_inhoud(…, geciteerd=True)`): zonder `##`-kop en zonder
  eenheden, want het is tekst van een andere handeling. Wel brontekst: de woord- en bladalineacontrole
  tellen haar mee, de structuurtellingen niet. Een inclusie die de tekst nergens aanroept, een
  aanroep zonder bestand, of een `INCL.ELEMENT` midden in een zin is een weigering. `LINK` is de
  ELI-verwijzing die het Publicatieblad sinds 2026 achter elke `REF.DOC.OJ` in een noot zet; de
  zichtbare tekst is de URI, het attribuut wordt niet geschreven.
  De portal-HTML (`/legal-content/…/HTML/`) blokkeert bots (HTTP 202, lege body;
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
  Een vierde padsegment in datumvorm (`/eli/reg/2014/910/2024-10-18`) is de consolidatiedatum en
  levert de geconsolideerde CELEX (sector 0 + datum); `/oj` en andere segmenten niet. Die datum
  eerder negeren gaf stilzwijgend de oorspronkelijke handeling terug — geen fout, wel het
  verkeerde document.
  Daarom `eli_to_celex()`: leidt deterministisch een CELEX af (type→letter reg=R/dir=L/dec=D/reco=H,
  `3{jaar}{letter}{nummer:04d}`) en gebruikt vervolgens de normale CELEX-Cellar-route.
- **Geconsolideerde versies** (`02014R0910-20241018`, sector 0 + de datum waarop die versie geldt):
  Cellar serveert die gewoon op dezelfde `…/resource/celex/{CELEX}`-route, mét `Accept-Language`.
  Wat er níét in zit is de **preambule**: EUR-Lex laat in een geconsolideerde versie de aanhef, de
  "Gezien …"-citaten en **álle overwegingen** weg. Die staan alleen in de oorspronkelijke handeling.
  - **Terugzetten kan structureel, zonder tekstheuristiek.** Beide documenten delen hetzelfde
    xhtml-skelet: `div.eli-main-title#tit_1` → `div.eli-subdivision#pbl_1` (de preambule) →
    `div.eli-subdivision#enc_1` (de artikelen). In een geconsolideerde versie ontbreekt precies
    `#pbl_1`. `_with_base_preamble()` haalt dat blok uit het origineel en zet het terug vóór
    `#enc_1` — op zijn eigen plek, dus vóór Artikel 1, met één herkomstnotitie erboven als
    **blockquote met label** (`> **Overwegingen:** …`), niet als cursieve regel: een volledig
    cursieve regel is in Markdown nadruk, geen herkomstvermelding, en een intakepoort die op
    `^\*[^*\n]{20,}\*\s*$` filtert weigert zo'n bestand terecht.
  - **De voetnootdefinities staan niet ín `#pbl_1` maar ernaast**, als `p.oj-note`-siblings
    binnen `div.eli-container` (ná `#fnp_1` en een `hr.oj-note`). Wie alleen `#pbl_1` kopieert
    neemt de overwegingen mét hun verwijzingen (1)(2)(3) mee maar laat de definities achter —
    dode verwijzingen, en niets dat dat meldt. `_attach_preamble_notes()` volgt daarom de href
    van elk anker bínnen `#pbl_1` naar zijn `p.oj-note`-ouder en verhuist alléén die noten, ná
    de laatste `div.eli-subdivision[id^=rct_]` (dus vóór de vaststellingsformule, waar het
    origineel ze ook heeft). Geen aantal in de code: de noten van de artikelen blijven vanzelf
    achter, want die heeft de geconsolideerde tekst zelf al. De `hr` gaat bewust niet mee — die
    zou een `---` midden in de preambule worden.
  - De CELEX van de basishandeling staat **machineleesbaar in het document zelf**: de eerste
    `►B`-pijl (`p.arrow > a`) linkt ernaartoe en draagt het nummer in zijn `title`-attribuut.
    `_base_celex()` leest dat; ontbreekt de pijl, dan wordt het nummer afgeleid (sector 0 → 3).
  - **Terugvalladder bij het invoegen**: `#enc_1` → anders het eerste element met class
    `title-division-1`/`title-article-norm` (oudere consolidaties zoals `02008R0593-20080724`
    hebben geen eli-markup, wél die CONVEX-klassen) → anders overslaan. Levert het origineel geen
    `#pbl_1` (handelingen van vóór ± 2004, bv. `32002L0058`), dan converteert het document gewoon
    zónder overwegingen — mét een notitie in de tekst, nooit zwijgend.
  - **Alleen de overwegingen van de basishandeling**, bewuste keuze. Die van de wijzigings-
    handelingen (►M1/►M2) zitten er niet bij; hun CELEX-nummers staan wel in dezelfde
    `p.arrow`-links, dus dat is later een kleine uitbreiding.
  - De ▼B/▼M2-wijzigingsmarkeringen (155 stuks in eIDAS) blijven **bewust staan** — ze zeggen welke
    passage door welke wijziging is vervangen of ingevoegd. Niet "opschonen".
  - **Eén CELEX-patroon** (`_CELEX_BODY`), want de vorm stond vier keer los uitgeschreven en liep
    uit elkaar zodra de datum erbij kwam: kaal werd `02014R0910-20241018` afgewezen, uit een URL
    werd de datum stil afgekapt tot een CELEX die niet bestaat. Let ook op de tekenklasse in
    `CELEX[:/]([0-9A-Z()-]+)` — zonder het koppelteken breekt die tak alsnog af.
  - **Sector 0 faalt niet via de portal.** Cellar 404't op een sector-0-CELEX die het niet kan
    serveren; doorvallen naar de geblokkeerde portal maakt daar een netwerkfout van. `_fetch_cellar`
    geeft dan `None` en `fetch_and_convert` gaat over naar `_consolidated_fallback()`.
  - **Een 404 op sector 0 heeft twee heel verschillende oorzaken, met dezelfde statuscode.**
    Ofwel de consolidatiedatum bestaat niet (consolidatiedata liggen vast, één per wijziging),
    ofwel de versie bestaat wél maar is er (nog) niet in de gevraagde taal — **EUR-Lex
    consolideert taal per taal en loopt daarin achter**. Die tweede is niet exotisch
    (geverifieerd: `02024R2979-20241204` alleen in IE+SV, `02026R0798-20260408` alleen in DE+ET,
    `02014R0910-20140917` in 9 van de 24 talen). De tool meldde eerder in álle gevallen dat de
    datum niet bestond — feitelijk onjuist — en gaf niets terug, terwijl de handeling zelf in het
    Nederlands wél op te halen is.
    - **Het onderscheid staat alleen in de metadata**, keyless op te vragen bij het
      **SPARQL-endpoint** van het Publicatiebureau (`publications.europa.eu/webapi/rdf/sparql`,
      dezelfde bron als "Alle versies van dit document" op de portal). `_consolidated_index()`
      vraagt per handeling álle geconsolideerde CELEX-nummers mét hun talen op met één query
      (`cdm:resource_legal_id_celex` + `FILTER(STRSTARTS(…))` op de sector-0-stam, plus
      `cdm:expression_uses_language`). Duurt ~6 s, dus **alleen op het faalpad**. `None` betekent
      "niet te achterhalen", niet "geen" — de ladder behandelt dat anders. Cellars notice-varianten
      (`?notice=object|branch|tree`) zijn hiervoor géén route: 400/404.
    - **Terugvalladder** (`_consolidated_fallback()`): de nieuwste geconsolideerde versie **op of
      vóór** de gevraagde datum die in deze taal bestaat → de oorspronkelijke handeling in deze
      taal → een foutmelding. Die eerste stap is geen concessie maar het juiste antwoord: vraagt
      iemand een willekeurige datum (bv. "vandaag"), dan is de nieuwste versie op of vóór die
      datum precies de versie die op dat moment gold. **Latere versies komen nooit in de plaats** —
      die verwerken wijzigingen die op de gevraagde datum nog niet golden.
    - **Nooit stil.** Elke terugval zet een notitie als blockquote bovenaan de tekst én noemt de
      afwijking in de bronvermelding, want stilzwijgend de oorspronkelijke handeling teruggeven
      is precies de val die deze code eerder maakte (zie de ELI-datum hierboven) — dan lijkt het
      origineel de geconsolideerde versie. `_fallback_reason()` levert die ene zin voor zowel de
      notitie als de foutmelding, zodat die twee nooit iets anders kunnen beweren; de talen/datums
      die ze opsomt zijn er alléén de talen/datums die in de **gevraagde taal** bestaan, want dat
      is wat de gebruiker ermee kan. `_base_act_tail()` houdt de drie gevallen apart die eerder
      door elkaar liepen: geen eerdere versie / eerdere versie niet in deze taal / versielijst
      onbekend. De eerste versie beweerde in dezelfde alinea "geen eerdere versie" én somde de
      bestaande versies op.
  - **CLG-markup is niet OJ-markup.** De geconsolideerde xhtml zet een lidnummer níét in een
    tweekoloms tabel maar in div/span-markup: `<span class="no-parag">1.</span>` (254× in
    02019R0881-20250204) met de tekst in de **volgende sibling** `div.norm.inline-element`, en
    `<div class="grid-container grid-list">` met `grid-list-column-1`/`-2` (223×: 196 letters,
    1 `c bis)`, 26 cijfers). Samen precies de 280 losse lidnummers die de conversie opleverde.
    Beide vormen komen **0× voor in de basishandeling** — daar zijn het 326 echte `<table>`s.
    `_normalise_clg_markup()` loopt daarom over de *markers* (niet over de containers, want
    `div.norm` nest in zichzelf) en gebruikt `render.marker_prefix`/`prefix_into`, zodat een
    CLG-marker regel-voor-regel hetzelfde rendert als een tabelmarker. Géén `_is_marker`-filter:
    bij een tabel is de vorm het enige houvast, hier zegt de klassenaam het al — en dat filter
    zou `c bis)` juist stil laten verdwijnen. Staat er een wijzigingsmarkering (`▼M1`) vóór de
    tekst, dan wordt die eerst naar buiten getild: hij houdt zijn eigen regel, maar mag het
    lidnummer niet opslokken.
  - **Witruimte uit de bron is geen regeleinde.** De CLG-bron is pretty-printed, en markdownify
    neemt een newline letterlijk over. Een voetnootanker (`(<a>\n<span>1</span>\n</a>)`) en een
    inline `►M1` midden in een alinea vielen zo uiteen over drie regels — `strip=["a"]` haalt de
    tag weg maar niet de regeleinden, en `tidy()` voegt regels nooit samen.
    `_collapse_source_newlines()` maakt daar weer gewone witruimte van; binnen een nootanker
    verdwijnt ze helemaal, anders zou `(1)` als `( 1 )` lezen.
  - **De volgorde is de ingreep**, niet een detail. `_prepare_consolidated()` = markers
    samenvoegen → preambule invoegen → witruimte normaliseren. Het samenvoegen selecteert op
    CLG-klassen en mag de ingevoegde preambule (OJ-markup: `span.oj-super`) niet raken; het
    normaliseren van witruimte geldt juist óók daarvoor, want anders breekt de net teruggezette
    `p.oj-note` alsnog tussen nummer en tekst. Dat staat vast in de functiecompositie, niet in
    een opmerking die iemand kan negeren.
  - **Bewust beperkt tot sector 0 + datum.** De selectors zijn zelf-gated (0 treffers zonder die
    klassen), dus verruimen is later één `if` minder. Nu al verruimen zet de
    EU-rechtspraakroute (`_fetch_hof`, sinds 21 september 2026 Formex) in de vuurlinie zonder dat
    daar een meting voor is.
  - **Cellar 300 gaat door dezelfde poort.** `_fetch_multipart()` sloeg de hele voorbewerking
    over, dus juist een grote geconsolideerde tekst kreeg preambule noch notitie. Elk onderdeel
    loopt nu door `_prepare_consolidated()`, met `with_preamble=(index == 0)`: de preambule hoort
    één keer, in de handeling zelf, niet nog eens bij elke bijlage.
  - `detect_source` heeft een **CELEX-uitsluiting** nodig bij de HUDOC-item-id-test:
    `01999L0001-20040501` bevat "001-20040501", precies de vorm van een HUDOC-id. Dezelfde
    volgorde-val zit in `deriveName()` in `app.js` (daar staat de CELEX-test daarom vóór de
    HUDOC-test).
- **EUR-Lex koppen**: koppen komen als `<p>` binnen; `promote_headings` promoot volledig-geankerde
  regels ("Artikel N", "HOOFDSTUK I") naar `##`/`###`. Genummerde alinea's (overwegingen, arrest-
  punten) staan in de xhtml als **tweekoloms-tabellen**; `_unwrap_marker_tables` — dat
  **uitsluitend** op `<table>` met exact 2 directe cellen per rij werkt — zet die om naar
  alinea's/lijst-items (nummer ín het bestaande blok, niet nesten).
- **HUDOC** (sinds 21 september 2026 via de DOCX): de tekst komt uit
  `hudoc.echr.coe.int/app/conversion/docx/?library=ECHR&id={itemid}&filename={itemid}.docx`,
  de metadata uit de zoek-API. Die is kieskeurig: `…/app/query/results` met `query`, `select`
  (komma-gescheiden, kleine letters), `sort`, `start`, `length`, `rankingmodelid=11111_Ranking` en
  `facetquery` (laat er één weg en je krijgt 404), én een browser-`User-Agent` (de standaard van
  urllib krijgt 403). 403/429 is een beperking op het aantal verzoeken en geen "niet gevonden":
  `hudoc._get` herhaalt één keer na vier seconden en meldt het daarna als storing. Eén ECLI →
  meerdere documenten (EN=`HEJUD`, FR=`HFJUD`, vertalingen, samenvattingen); alleen het Engelse
  origineel wordt ondersteund, de rest is een weigering met de soort erbij. `id` is de ECLI uit
  de API, geen slug. Sommige uitspraken geven bij het Word-bestand HTTP 500 (gemeten, ook recente
  zoals `001-160044`); dat is een weigering met die reden, geen terugval op de HTML-body.
  `hudoc_docx.py` leest `word/document.xml` met `zipfile` en `xml.etree`: koppen uit de stijl
  (`KOP`-tabel, allemaal `##`), randnummer `N.` plus **één gewone spatie** (de bron heeft twee
  harde spaties; het profiel herkent het randnummer alleen zo), voetnoten native en genummerd in
  de volgorde van hun verwijzing, velden met hun opgeslagen resultaat, en een gate tegen een
  regelbestand (minder dan 50% van de alinea's eindigt op een leesteken). Wat het weigert:
  een stijl zonder eigen behandeling, Word-autonummering die geen opsommingsteken is (het
  nummer staat dan niet in de tekst; gemeten in 3 van 35, waaronder Big Brother Watch),
  Franse uitspraken, beslissingen en samenvattingen. De zelfcontrole telt de woorden in de bron
  met een eigen doorloop en eist gelijke woordverzamelingen en aaneengesloten bladalinea's.
- **Hof van Justitie en Gerecht** (sinds 21 september 2026 via Formex): `eurlex._fetch_hof` vraagt
  `application/zip;mtype=fmx4` met `Accept-Language: nld` op de ECLI-resource of het CELEX-nummer
  en geeft de zip aan `formex_hof.omzetten`. Een arrest dat nog geen Formex heeft levert 404 en
  wordt geweigerd, met de reden erbij; er is bewust geen terugval op de Curia-HTML. Rechtspraak
  heeft een andere Formex-vorm dan wetgeving: geen `.doc.xml`, één XML met wortel `JUDGMENT` of
  `ORDER`. De raw-vorm is die van het profiel: kale sectieregels, `NO.P` plus één spatie voor een
  overweging, `NO.P` plus **drie harde spaties** voor een geciteerd punt (binnen `QUOT.S` of een
  lijst — zonder dat onderscheid krijgt een geciteerde bijlage `ro`-ankers), het dictum uit
  `JURISDICTION/INTRO` als alinea's, de procestaalnoot als `[^procestaal]` met een definitie. De
  zelfcontrole is dezelfde als bij `hudoc_docx`. Weigeringen: `CONCLUSION`, `OPINION`,
  `JUDGMENT.NP`, `CASE`, `REPORT.HEARING`, `SUMMARY.*`, een zip met meer dan één XML of met een
  afbeelding, een onbekende aanhalingscode en `DLIST`. Gemeten op 143 Cellar-zips: 73 arresten of
  beschikkingen in één XML, 71 omgezet.
- **Rechtspraak.nl**: `https://data.rechtspraak.nl/uitspraken/content?id={ECLI}` geeft schone XML
  (`<uitspraak>` met `section`/`title`/`parablock`/`para`), en die XML **is** het bronbewijs:
  `record_source(..., source_format="rechtspraak-xml")`, met een `Herkomst` op de ECLI en een
  kb-bundel. Wat de route bewust niet doet: `<emphasis>` levert geen `**`/`*` op (een kop als
  `**De beslissing**` matcht `SECTION_ANCHORS` aan de kb-kant niet meer) en kopniveaus volgen de
  nesting van de bron. Inhoudsafbeeldingen (`imagedata` met `depth` > 2 — gemeten over 300
  uitspraken is de verdeling bimodaal: 66 spacers van 1–2 pixels tegen 28 foto's van 16 pixels
  en hoger) worden niet overgenomen; de conversie gaat door met een waarschuwing en legt aantal,
  afmetingen en bron-id als `afbeeldingen_weggelaten` in het zijbestand vast. Wat ze nog weigert
  in plaats van raadt: een element met tekst zonder eigen behandeling, een tabel die niet
  rechthoekig te maken is, en een ECLI die niet is wie hij zegt
  (`dcterms:identifier` moet de gevraagde ECLI zijn). Noten worden native: `<footnote-ref
  linkend>` → `[^n]`, `<footnote label>` → `[^n]: …`. Genummerde lijsten (`orderedlist`) krijgen
  de tekens die `numeration` noemt; die gegenereerde markers worden apart geteld, zodat de
  woordcontrole ze niet voor brontekst aanziet — net als de kopieën van een overspannen
  tabelcel.
- **wetten.overheid.nl**: eerst de officiële **BWB-XML** van KOOP (`repository.officiele-
  overheidspublicaties.nl/bwb/{BWB}/manifest.xml` → de toestand op de peildatum, `bwb_xml.py`);
  alleen als die route er niet is (`_BwbXmlNietBeschikbaar`) de **portal-HTML**, server-rendered met
  de volledige tekst in `#regeling` (h1 titel, h3 hoofdstuk, h4 artikel): `wetten.py` pakt die
  container, strip't werkbalk-ruis (`[class*=action--]`, `.visually-hidden`) en markdownify't.
  Een weigering van de XML-omzetter valt bewust **niet** terug op de HTML: met een BWB-identiteit
  is de weigering het antwoord. URL wordt herbouwd uit BWB-id + optionele versiedatum
  (`/{jjjj-mm-dd}`). Gemeten op de Awb (`BWBR0005537`): een `<tussenkop kopopmaak="cur">` scheidt
  de digitale en de papieren variant van artikel 8:36c en wordt een cursieve alinea, geen kop en
  geen eenheid (als kop gaf het een tweede `art-8-36c`); Bijlage 2 deelt haar artikelen in
  `divisie`s in, en `divisie()` krijgt daarvoor van `bijlage()` het kopniveau en het ankerpad mee
  (`annex-2-art-7`). De leden van de tweede variant van 8:36c dragen dezelfde ankers als de eerste:
  dat zegt de bron, en de BWB-route controleert ankers niet op dubbelen.
- **Duitse rechtspraak** — twee lagen, met een gedeelde parser:
  - **Primair: OpenLegalData** (`de_openlegaldata.py`, `de.openlegaldata.io`) — een gratis,
    **sleutelloze** JSON-API, rechtstreeks doorzoekbaar op ECLI (`?ecli=<ECLI>`, dan een
    detail-GET voor het volledige `content`-veld), géén sessie/tokendans nodig, en met een veel
    bredere dekking (~424.000 zaken, ook deelstaatgerechten) dan alleen de zeven federale
    gerechten. OpenLegalData aggregeert echter meerdere bronformaten in dat `content`-veld, dus
    `_content_to_markdown()` proeft drie lagen: (1) de federale "RspDL"-conventie (zie hieronder)
    als HTML-fragment (`<h2>`-sectiekoppen + een `<div>` met `<dl class="RspDL">`), (2) een
    afwijkende deelstaatconventie (geverifieerd: OVG Nordrhein-Westfalen) met
    `<span class="absatzRechts">N</span>` gevolgd door een **sibling** `<p class="absatzLinks">`
    — het randnummer staat dus náást de alinea, niet erin — samengevoegd door
    `_merge_absatz_pairs()`, en (3) een generieke `container_to_markdown()`-fallback voor een
    nog onbekende conventie. Een bekend data-kwaliteitsgat: `court.name` is voor sommige
    (vooral oudere) zaken letterlijk `"Unknown court"`; dan wordt het gerecht in plaats daarvan
    afgeleid uit het 3e ECLI-onderdeel. Levert OpenLegalData geen (bruikbaar) resultaat, dan
    valt `fetch()` intern terug op `de_rechtsprechung.fetch()`.
  - **Terugval: rechtsprechung-im-internet.de** (`de_rechtsprechung.py`, BMJ) — publiceert
    geselecteerde uitspraken van BGH/BVerfG/BVerwG/BFH/BAG/BSG/BPatG sinds 2010, als schone XML
    met een eigen DTD, maar zonder directe "haal-op-met-ECLI"-URL. Het is een Java-portlet-app
    die eerst doorzocht moet worden: (1) GET het zoekfragment
    (`/js_pane/Suchportlet1/media-type/html`) en lees de verborgen formuliervelden
    (`sugportal`/`sughashcode`/…) uit — die zijn **sessiegebonden** en server-gegenereerd; zonder
    exact die velden geeft de site alleen het lege formulier terug. (2) GET hetzelfde fragment,
    nu met die velden + `query=<ECLI>`, **in dezelfde sessie** (cookies) → de HTML bevat
    `doc.id=<ID>` (of "0 Treffer"). Dit gebeurt met een **eigen `requests.Session`**, niet de
    gedeelde `net.documents()` — die wordt gelijktijdig door andere documenten gebruikt (de tool
    haalt meerdere documenten parallel op) en twee gelijktijdige zoekopdrachten op dezelfde
    JSESSIONID zouden elkaars tussenstaat overschrijven. Elke gevonden `doc.id` heeft daarna een
    vaste, **stateloze** `.../docs/bsjrs/{doc.id}.zip` met één XML erin (dus wél via de gedeelde
    sessie). `_resolve_doc_id()` onderscheidt een bevestigde "0 Treffer"-melding (échte lege
    uitkomst, geen nieuwe poging) van een technische hapering zonder die melding (bv. een
    gewijzigd formulierveld) — dat laatste wordt één keer opnieuw geprobeerd
    (`_SEARCH_ATTEMPTS`) voordat de tool concludeert dat de uitspraak niet gevonden is.
  - **Gedeelde "RspDL"-parser** (`juris_markup.py`): beide bronnen leveren voor federale/
    juris-gebaseerde uitspraken dezelfde onderliggende structuur —
    `<dl class="RspDL"><dt>…</dt><dd>…</dd></dl>`-paren, `<dt>` het randnummer
    (`<a name="rd_N">N</a>`), `<dd>` de alinea of een `<table>` (bv. het handtekeningenblok) —
    alleen als XML (rechtsprechung-im-internet.de) versus HTML-fragment (OpenLegalData). De
    walker (`walk_dl_section`/`render_dd`/`inline`/`table_to_markdown`) staat daarom éénmalig in
    deze module, want `lxml.etree`- en `lxml.html`-elementen delen dezelfde
    `.tag`/`.text`/`.tail`/iteratie-interface.
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
- **Franse rechtspraak** — `fr_conseil_constitutionnel.py` is het registratiepunt voor `FR` en
  routeert op het gerecht-onderdeel van de ECLI:
  - **Conseil constitutionnel**: publiceert op zijn **eigen site** (niet Légifrance, dus geen
    Cloudflare-blokkade), met een **deterministische URL** rechtstreeks uit de ECLI — analoog
    aan `eli_to_celex()`: `ECLI:FR:CC:{jaar}:{jaar}.{nummer}.{type}` →
    `.../decision/{jaar}/{jaar}{nummer}{type}.htm` (het 5e ECLI-onderdeel met de punten eraf).
    Geverifieerd op twee besluittypes (QPC en DC): de pagina bevestigt de aangevraagde ECLI
    letterlijk in de tekst. De pagina is verder gewone semantische HTML (p/ul/li/blockquote/
    strong) — geen bespoke walker nodig, gewoon `container_to_markdown()` (dezelfde
    markdownify-route als `wetten.py`) op de container met class
    `field--name-field-contenu-original`.
  - **Cour de cassation** (`fr_judilibre.py`): via de officiële **Judilibre**-API op het
    PISTE-portaal (`piste.gouv.fr`) — vereist een geregistreerde applicatie mét een
    goedgekeurde **souscriptie** op de Judilibre-API (los van het aanmaken van de OAuth-
    credentials zelf; zonder die souscriptie authenticeert de app wel, maar geeft de API
    consequent **403** terug op elk endpoint). OAuth2 `client_credentials`-token via
    `oauth.piste.gouv.fr` (production; **sandbox-Judilibre bevat alleen demodata**, dus
    productie-toegang is voor echte opzoekingen sowieso vereist). Elke API-aanroep gaat met
    zowel `Authorization: Bearer <token>` als `KeyId: <client_id>`; `_get_token()` cachet het
    token (1 uur geldig) client-side. **ECLI-zoeken werkt wél**, in weerspraak met eerdere
    aanname: geeft `/search` een `query`-parameter die exact een ECLI-string is, dan herkent
    Judilibre dat intern en herschrijft het naar een exacte `terms`-filter op het `ecli`-veld
    (zichtbaar in de `searchQuery`-debugkey van de respons) — geen apart ECLI-parameter nodig.
    `/decision?id=<id>` geeft platte tekst (`text`-veld, geen HTML) terug, dus geen
    structuurwalker nodig — alleen op lege regels in alinea's splitsen.
  - Overige Franse gerechten (Conseil d'État, cours d'appel) geven een expliciete, uitleggende
    foutmelding in plaats van een gok — die staan (ook) op Légifrance, achter de
    Cloudflare-blokkade, zonder een vergelijkbare eigen-site- of API-route.
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
- **Losse afbeeldingen extraheren** (`extract_images=1` op `/api/convert/file` en
  `/api/convert/file-url`, alleen voor `.pdf`, bij Documentupload): een **aanvulling** op de
  normale PDF-tekst (pdf-inspector/MarkItDown hierboven), geen alternatief — de UI-toggle
  (`#extract-images`, alleen zichtbaar als `/api/config` `extract_images_available: true`
  teruggeeft) staat naast de normale invoer en verandert niets aan hóe de tekst zelf wordt
  omgezet.
  - **`mdconv/sources/pdf_images.py`** (`pdfimages`/`pdfinfo`, poppler-utils — systeembinaries,
    niet via pip: Homebrew lokaal, `apt-get` in de Dockerfile) extraheert de ingesloten
    rasterafbeeldingen (grafieken, screenshots). **Hele pagina's als scan worden bewust
    overgeslagen**: `_is_full_page()` vergelijkt de fysieke afmeting van elke afbeelding
    (pixels ÷ eigen ppi uit `pdfimages -list`) met de paginaomvang uit `pdfinfo -f N -l N`
    (let op: dat commando meldt de paginagrootte als `"Page    N size: …"`, niet
    `"Page size: …"` zoals zonder `-f`/`-l` — een eerdere regex miste dat verschil). Beslaat
    een afbeelding op beide assen ≥ 85% van de pagina, dan is het vrijwel zeker de hele
    pagina, geen losse figuur — anders zou elke gescande pagina de eigen tekst als "bijlage"
    dupliceren. `pdfimages -j` levert alleen écht al-JPEG-gecodeerde afbeeldingen als `.jpg`;
    een rauwe pixmap (typisch voor grafieken/screenshots) komt er als ongecomprimeerde
    `.ppm`/`.pbm` uit en wordt hier met Pillow herschreven naar PNG (klein, lossless).
  - **Bestandsnamen**: elke afbeelding heet `p{paginanummer}[-n].ext` (bv. `p12.png`, of
    `p12-2.png` bij meerdere op één pagina).
  - **Plaatsing: op de pagina waar de afbeelding vandaan komt, niet allemaal onderaan.**
    `files.convert_pdf_pages()` is de tweede, per-pagina variant van pdf-inspectors
    extractie (`extract_pages_markdown_bytes`, naast het bestaande `process_pdf_bytes` dat
    ín één samengevoegde string levert) — dat geeft de paginagrenzen die nodig zijn om een
    afbeelding ná de tekst van precies díe pagina te zetten. `sources._attach_pdf_images_inline()`
    plakt de pagina's weer aan elkaar en voegt na elke pagina de wikilink-embeds
    (`![[p{n}.ext]]`) van de afbeeldingen van díe pagina toe — vóór de eerste
    tekst van de volgende pagina, dus zo dicht bij "de plek in de PDF" als haalbaar zonder
    coördinaten (paginagranulariteit, niet positie-binnen-de-pagina).
    **Terugval**: kan pdf-inspector geen per-pagina tekst geven (bv. een PDF zonder
    tekstlaag die alsnog via MarkItDown gaat, dat één doorlopende tekst zonder
    paginascheiding teruggeeft), dan is de pagina van geen enkele alinea bekend — dan
    valt het terug op de oude, grove plaatsing: alle afbeeldingen samen onder één losse
    `## Bijlagen`-sectie aan het eind (`sources._attach_pdf_images()`), beter een
    duidelijk-grove plek dan een gok.
  - **Bijlagen en de zip-download** (`mdconv/attachments.py`): binaire afbeeldingsdata gaat
    nooit in de conversie-JSON mee. `_doc_payload()` in `api.py` slaat `doc.attachments` op
    onder een token (`attachments.store()`, een tempdir per set) en stuurt alleen
    `attachments_token` + `attachment_count` terug; de front-end onthoudt dat op het
    document (`doc.attachmentsToken`) en stuurt het bij het downloaden terug mee.
    `/api/download` bouwt dan een `.zip` (de markdown + een `attachments/`-submap) i.p.v.
    een los `.md`-bestand — of, bij een `documents`-array (de knop "Alles downloaden"),
    één zip met alle documenten en per document een eigen `attachments/<naam>/`-map. `attachments.get()` **verwijdert niets** — nogmaals downloaden mag
    gewoon; opruimen gebeurt lui, bij elke nieuwe `store()`-aanroep worden sets ouder dan
    2 uur weggegooid (geen cron/achtergrondtaak nodig voor deze single-user lokale tool).
  - **Kennisbankbundel** (`mdconv/kb_bundle.py`): BWB-documenten, wetgevings-CELEX-
    nummers (sector 0/3) en uitspraken met een ECLI krijgen naast hun editorinhoud alleen
    een tijdelijk `bundle_token`. Bij downloaden bouwt de server een zip met
    `raw/<profiel>/<id>.md`, het herkomstzijbestand en de hashgebonden oorspronkelijke
    HTML/XML/Formex-bron onder `raw/source-evidence/<id>/`. De teruggestuurde markdown
    bepaalt bij dat moment `markdown_changed`; bronbytes en herkomst gaan nooit door de
    conversie-JSON. Een bundel draagt twee namen: `document_id` is de identiteit zoals de
    kennisbank hem in `id` zet (`ECLI:NL:RBROT:2025:15669`), `pad_id` diezelfde identiteit
    als bestandsnaam (`ECLI-NL-RBROT-2025-15669`) — dubbele punten zijn geen bestandsnaam.
    Een CELEX uit sector 6 zonder ECLI in de herkomst wordt zelf de identiteit (`SKILL.md`
    schrijft dat voor het Hof voor); een ECLI wordt nooit uit een patroon opgebouwd. De
    bundel-extensie volgt het bronformaat: `fmx4.zip` voor `formex` en `formex-hvj`, `xml` voor
    `bwb-xml` en `rechtspraak-xml`, `docx` voor `hudoc-docx`.
    Voorstellen, documenten en geplakte tekst houden de platte download. De tokens
    verlopen lui na twee uur, net als afbeeldingtokens.
- **Tekst plakken** (`pasted_text.py`, endpoint `/api/convert/text`): de front-end stuurt
  zowel `html` (`element.innerHTML` van het `contenteditable`-vak, dus de klembord-opmaak
  zoals de browser die bij plakken invoegt) als `text` (`element.innerText`, kaal) mee.
  `_has_structure()` beslist welke wordt gebruikt: alleen als de HTML échte structuurtags
  bevat (koppen, lijsten, tabellen, nadruk, `<br>`) is ze de moeite waard — anders is de kale
  tekst betrouwbaarder. **Waarom niet altijd de HTML gebruiken**: sommige plak-bronnen leveren
  voor kale tekst een klembord-HTML die niet meer is dan één `<span>`/`<div>` om de hele tekst
  heen, met regeleindes als kale `\n`-tekens i.p.v. `<br>`/`<p>` — `markdownify` normaliseert
  witruimte binnen zo'n inline-element en zou dan de eigen regelindeling van de gebruiker
  laten verdwijnen. Bevat de geplakte tekst een ECLI, dan komt die in de bronvermelding
  terecht (`"Geplakte tekst • ECLI:…"`) zodat `kind_for_source()` — dat al op ECLI-patronen in
  de bronvermelding matcht — dit automatisch als rechtspraak herkent (met de Obsidian-optie).
  Dit tabblad heeft geen herhaalbare rijen zoals de andere drie: één `contenteditable`-vak,
  één document per klik op "Opmaken".
  **"Plakken"-knop** (`pasteFromClipboard()`): leest rechtstreeks van het systeemklembord via
  de Clipboard API, zodat de gebruiker niet zelf Cmd/Ctrl+V hoeft te doen. Probeert eerst
  `clipboard.read()` voor zowel `text/html` (verrijkt) als `text/plain`; zonder HTML-variant
  valt de methode terug op `clipboard.readText()`. Vereist een secure context (https/
  localhost) en kan de browser om toestemming laten vragen; weigert de browser (of geen
  toestemming), dan een duidelijke foutmelding met het advies handmatig te plakken — nooit
  een stille misser.

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

## Front-end (`static/app.js` + `static/app.css` + `templates/index.html`)

Geen framework, geen build-stap. Eén expliciete `state` en per gebied een render-functie
die die state naar de DOM schrijft; wat de gebruiker verandert gaat **eerst** in `state`
en dan door een render. Nooit rechtstreeks de DOM patchen — daar liep de vorige versie
op stuk, doordat dezelfde gegevens in een variabele, in een DOM-waarde én in het
document zelf stonden en uit elkaar liepen bij het wisselen van tabblad.

- **Meerdere documenten**: elk tabblad heeft herhaalbare invoerrijen (`makeRow()`/
  `initRows()`), met "+ toevoegen" en per rij een ×-knop (minstens 1 rij blijft staan).
  `runBatch()` haalt de ingevulde rijen op met een **kleine pool**
  (`BATCH_CONCURRENCY = 4`, géén `Promise.allSettled` over alles tegelijk meer),
  toont voortgang (`3/5 opgehaald…`) en meldt per mislukte rij precies wat faalde —
  één fout blokkeert de rest niet. `#file` heeft `multiple`; slepen en de bestandskiezer
  lopen over alle bestanden.
  - **Waarom een pool en geen "alles tegelijk"**: bij een aangeleverde lijst van dertig
    links waren dat dertig gelijktijdige verzoeken naar dezelfde bron (EUR-Lex,
    wetten.overheid.nl) — precies hoe je throttling of een blokkade uitlokt, nog voordat
    de eerste conversie klaar is.
  - **Volgorde en niet-meespringen.** De bronnen antwoorden in willekeurige volgorde, dus
    elk document krijgt zijn plaats in de lijst mee (`doc.batchIndex`, meegegeven door
    `run(item, index)`) en `finishBatch()` zet de batch aan het eind terug in
    invoervolgorde. `addDoc({activate: false})` zorgt dat de editor **tijdens** het
    ophalen niet meespringt met elk document dat binnenkomt (dat volgde de
    afrondingsvolgorde); pas `finishBatch()` opent het eerste document van de lijst. De
    tab verschijnt wél meteen (`renderDocTabs()`), zodat je de lijst ziet vollopen.
- **Batch-import: een lijst aanleveren** (Wetgeving en Jurisprudentie; `LIST_PASTE_KINDS`
  plus een `initListMode(kind)`-aanroep en de `#bulk-<kind>-*`-id's in `index.html` zijn
  de plekken om dat uit te breiden — `test_list_paste_kinds_match_initialised_list_modes`
  bewaakt dat ze gelijk blijven). Twee wegen naar dezelfde lijst:
  - **Plakken splitst zich uit over de rijen.** Plak je meerdere regels in één
    invoerveld, dan vult regel 1 dat veld en verschijnt er voor elke volgende regel een
    nieuwe rij (`spreadList()`), met de taalkeuze van de rij waarin je plakte. Alleen bij
    een **échte** lijst (meerdere regels én ≥ 2 herkende items) wordt het plakken
    overgenomen; een gewone plak van één regel, of midden in een bestaande waarde, blijft
    een gewone plak. Zo hoeft de gebruiker niets te leren: één Cmd/Ctrl+V.
  - **"Lijst plakken"** (`.seg`-schakelaar) ruilt de rijen om voor één tekstvak met één
    taalkeuze voor de hele lijst. Rijen en tekstvak zijn **twee weergaven van dezelfde
    lijst**: `switchListMode()` neemt de inhoud mee in beide richtingen, zodat je nooit
    werk kwijt bent en "Ophalen" altijd leest wat je op dat moment ziet. De gekozen
    weergave blijft bewaard in `localStorage` (`listMode:wet` / `listMode:jur`). Enter maakt in een
    tekstvak een regel, dus **Cmd/Ctrl+Enter** haalt op.
  - **De parser is vergevingsgezind maar voorspelbaar** (`parseList()`/
    `pickIdentifier()`), per regel in deze volgorde: een URL in de regel (dus een
    geplakte bullet mét omringende tekst werkt gewoon, sluitleestekens van een
    markdown-link of prozapunt gaan eraf), anders een ECLI/BWB/CELEX/HUDOC-item-id in de regel, anders
    de regel zelf zonder opsommingsteken of nummering. Onbekende invoer wordt dus **nooit
    stil weggegooid** — die gaat door naar de server, die in het Nederlands uitlegt wat
    er mis is. Lege regels en markdown-koppen (`## EU-wetgeving`) worden overgeslagen,
    want zo ziet een lijst uit een notitie eruit. Een live teller onder het tekstvak
    (`updateListCount()`) meldt "18 regelingen herkend · 2 dubbele weggelaten" **vóórdat**
    je achttien verzoeken afvuurt.
  - **Dubbele invoer gaat eruit op invoer *én* taal** (`readInput()`): dezelfde regeling
    in NL en EN zijn juist wél twee documenten.
  - **Één patroon per identificatievorm** (`RE_URL`/`RE_ECLI`/`RE_BWB`/`RE_CELEX`/
    `RE_HUDOC`), gedeeld door de lijst-parser en `deriveName()`. Dezelfde les als
    `_CELEX_BODY` aan de serverkant: los uitgeschreven liep de CELEX-vorm uit elkaar
    zodra de consolidatiedatum erbij kwam. Let ook hier op de volgorde — een
    geconsolideerde CELEX (`02014R0910-20241018`) matcht óók `RE_HUDOC`, andersom niet.
  - **`[hidden]` doet het schakelen**, dus de `[hidden] { display: none !important }`-regel
    in `app.css` is ook hier voorwaarde: `.rows` heeft `display: flex`.
- **"Alles downloaden (n)"** (`#download-all`, zichtbaar vanaf 2 documenten): bij een lijst
  van twintig is per document downloaden het nieuwe handwerk. `downloadAll()` stuurt alle
  documenten in één `documents`-array naar `/api/download`, dat er één zip van maakt —
  voor wetgeving met een bundeltoken de volledige `raw/`-boom, anders `<naam>.md` per
  document, en de bijlagen van een document onder `attachments/<naam>/`,
  want twee PDF's leveren allebei een `p01.png`. Gelijke documentnamen krijgen een
  `-2`-suffix (`_unique_name()`), anders zou het tweede het eerste overschrijven en was
  dat document stil verdwenen. `saveDownload()` is de gedeelde helper van
  `downloadActive()` en `downloadAll()`.
- **State per document**: `{id, title, filenameBase, source, kind, allowObsidian,
  obsidian, model, markdown, cleaned}` in `state.docs`. `obsidian`/`model` zijn **per
  document**, dus je kunt het ene document als Obsidian-notitie opschonen en het andere
  met het standaardprofiel. `setActive()` bewaart eerst de live-bewerkte tekst
  (`saveEdits()`) voordat het volgende document wordt geladen.
- **"Opmaken voor Obsidian" is een checkbox** (`#obsidian`), geen dropdown. Zichtbaar bij
  `doc.allowObsidian`, dat `addDoc()` zet op `kind === "caselaw"` (automatisch herkende
  rechtspraak) **of** een expliciete `allowObsidian: true` vanuit de aanroep — die geven
  `fetchFileUrls()`, `uploadFiles()` en `fetchPastedText()` altijd mee, want een geüpload
  document of geplakte tekst kán een uitspraak zijn zonder dat de tool dat automatisch
  herkent (bv. een land zonder eigen bron, handmatig gevonden). Aanvinken zet
  `doc.obsidian` en zet `cleaned` terug op false (ander profiel = opnieuw opschonen mag),
  en vernieuwt de kostenraming.
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

De opmaak is **"liquid glass"**: het navigatie-chrome (kop, tabbalk, dialoog,
statusregel, opschoonpaneel, documentchips, sleepzone) is vertaald glas —
`backdrop-filter` + een lichtrand boven + een zachte specular highlight —
dat drijft over een zacht gekleurde achtergrondgloed (`body::before`, drie
vaste `radial-gradient`-vlekken). Het onderliggende kleurenpalet blijft de
12-stapsschaal van Radix Themes, met de hand in platte CSS (geen React/npm),
met de vaste betekenis per stap: 1 paginablad · 2 subtiel blad · 3 vulling ·
4 hover · 5 actief · 6 zachte rand · 7 rand/ring · 8 hover-rand **en de
focusring** · 9 volvlak · 10 volvlak-hover · 11 secundaire tekst ·
12 primaire tekst.

**Glas versus vlak — nooit stapelen.** De `.glass`-klasse (blur + lichtrand +
specular-`::before`) staat alleen op drijvend chrome dat direct op de gloed
zit. Inhoudspanelen (`.card`, invoervelden, de editor) blijven bewust
**ondoorzichtig**: twee doorzichtige lagen op elkaar (bv. een glazen knop
binnen een al glazen dialoog) laat de leesbaarheid instorten — exact de
reden dat knoppen zelf geen `backdrop-filter` hebben, alleen een niet-
doorzichtige gradient-"sheen" (`.btn-solid::before`/`.btn-soft::before`) voor
het glanzende effect zonder een tweede blur-laag.

**De tabbalk is het enige echt "liquid" moment.** `.tabs-indicator` is een
tweede, accent-getinte glazen pil die achter het actieve tabblad naar de
juiste breedte/positie toe **vloeit** — met een klein beetje overshoot
(`cubic-bezier(0.34, 1.56, 0.64, 1)`), bewust de enige plek met bounce.
Overal elders is de beweging overshoot-vrij (`--ease-standard`), want
overshoot op bv. een dialoog-intro leest als een fout, niet als vloeibaar.
JS (`moveTabsIndicator()` in `app.js`) meet de `getBoundingClientRect()` van
het geselecteerde tabblad en zet dat om in een `transform: translateX()` +
`width` op de indicator — compositor-vriendelijk, werkt vanzelf mee bij elke
schermbreedte.

Dark/light volgt `prefers-color-scheme`; er is bewust **geen** knop. Drie dingen
kantelen van betekenis tussen de modi — zonder die omkering leest het niet als Radix:

1. Een paneel is in donker **lichter** dan de pagina (`--gray-2` op `--gray-1`), in licht
   wit-op-wit met alleen een haarlijn.
2. Een invoerveld is in licht een translucent **wit** (opgetild vlak) en in donker een
   translucent **zwart** (verzonken vlak).
3. Stap 9 is identiek in beide modi, maar stap 10 beweegt tegengesteld (donkerder in
   licht, lichter in donker) — daardoor werkt "hover = stap 10" zonder conditionele CSS.

**Toegankelijkheid is geen ander thema, maar dezelfde schakelaar.**
`prefers-reduced-transparency: reduce` maakt elk `.glass`-element ondoorzichtig
(geen blur, geen specular) en verbergt de achtergrondgloed helemaal — die
bestaat immers alleen om door glas heen gezien te worden. `prefers-reduced-
motion: reduce` zet alle transitie-/animatieduur op nagenoeg 0 (één globale
regel), inclusief de vloeiende tabbalk-indicator.

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
- **poppler-utils** (apt) wordt in de Dockerfile meegeïnstalleerd voor het extraheren van
  losse afbeeldingen (`mdconv/sources/pdf_images.py`). Lokaal (macOS via `run.sh`):
  `brew install poppler`.
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
`.venv/bin/python -m pytest tests/ -q` — 381 karakteriseringstests die het gedrag
vastleggen in plaats van het te beschrijven: `detect_source`-precedentie, ELI→CELEX,
de CLG-markupnormalisatie (lidnummers, lettermarkers, voetnootankers), de
voetnootdefinities die met de preambule meereizen, de notitievorm die een intakepoort
passeert, de geconsolideerde-CELEX-afhandeling (datum behouden, preambule invoegen, en de vier
terugvalpaden als dat niet lukt), de versie-terugvalladder (nieuwste versie op of vóór de
gevraagde datum, nooit een latere, en een notitie die niet beweert dat een bestaande versie
niet bestaat), de chunking-ladder (ook zonder witregels en met één te
lang woord), de PDF-reflow, de losse Formex-parser en de Cellar-Formex-route (inclusief
bronbytes, manifestidentiteit en fail-closed tabellen), de settings-semantiek (leeg wist terug naar
standaard), de batch-zip (eigen naam en eigen `attachments/`-map per document) en de
Nederlandse foutmeldingen. Twee tests pinnen de front-end vast waar Python niet bij de
JS kan: de id's die `app.js` per conventie opbouwt (`#bulk-wet-text` enz.) moeten in
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


## Documenten voor de kennisbank (profiel `documenten`)

Een geüpload bestand of een link naar een Kamerstuk komt als document in de kennisbank, met bronbewijs waar de bron een boom heeft. Wat de route doet hangt af van het formaat:

| Invoer | Route | Bewaarde bron (`source_format`) | Weigert bij |
|---|---|---|---|
| `kst-…` of link | `officiele_bekendmakingen.py` | `op-xml` | geen XML (404), ander worteldocument dan `kamerstuk`, een element zonder behandeling, een woordtelling die niet klopt |
| `.docx` | `docx.py` (`WORD_KAART`) | `docx` | stijl zonder herkomst naar `Normal`, automatische nummering, tekstvak/afbeelding, eindnoot, bijgehouden wijziging, samengevoegde cel, tabel in tabel |
| `.html`/`.htm` | `html_document.py` | `html` (de gekozen container, niet de hele pagina) | geen eenduidige `<main>`/`<article>`/`[role=main]`, samengevoegde cellen zonder sluitend raster |
| `.pdf` | pdf-inspector | `pdf` (alleen als herkomst; de kennisbank herberekent er niets uit) | geen tekstlaag (scan) |

**Het documentnummer komt nooit uit de bestandsnaam.** `from_file(..., document_id=…)` en de API-velden `document_id` (form-veld bij `/api/convert/file`, JSON-veld bij `/api/convert/file-url`) leveren de slug (`identifiers.md` van de kennisbank: `a-z`, `0-9`, `-`). Zonder slug blijft de download een los `.md`; bij een Kamerstuk levert de bron de slug (`kst-<dossier>-nr-<n>` uit `metadata.xml`). **De voorkant heeft er (nog) geen veld voor** (AGENTS.md, regel 5): tot iemand daar om vraagt, is de API het enige pad.

**Zonder documentnummer valt een weigering terug op MarkItDown, zichtbaar.** De strikte route mag een pagina met twee `<article>`s of een Word-bestand met automatische nummering niet zomaar laten stoppen met werken voor wie alleen een `.md` wil. `sources._terugval()` zet de reden in `Document.warnings` en legt niets vast als bron. Met een documentnummer is de weigering het antwoord.

**Noten zijn native** (`[^1]` en `[^1]: …`), zoals bij de rechtspraak, en niet de `(n)`-vorm die een eerste lezing van AGENTS.md regel 3 suggereert: het documenten-profiel koppelt `(n)` alleen met een aangewezen notenblok, terwijl de kern native noten al aankan. Een tabelnoot in de KOOP-XML heet `t<tabel>-<nr>` (elke tabel telt opnieuw bij 1).

Gemeten op 11 Kamerstukken van KOOP: 5 komen door (waaronder `kst-34851-3`, 65.815 woorden, 102 noten); de weigeringen zijn `box`, `datumtekst`, `voorstel-wet`, `aanhef`, `label` in een kop en `dossierref` in een alinea: vocabulaire dat niet is gemeten en dus niet wordt geraden. Van zeven echte Word-modelovereenkomsten (ARVODI van PIANOo, een model van NVRR) komt er geen enkele door: zes hebben een tekstvak (`mc:AlternateContent`), één automatische nummering. De EHRM-Word-bestanden komen wel door de generieke kaart (42 koppen tegenover 0 bij MarkItDown).
