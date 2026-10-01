# PDF, losse afbeeldingen en geplakte tekst

Verplaatst uit `CLAUDE.md` (WP-60, 29 september 2026), tekst ongewijzigd. `CLAUDE.md` bevat de
architectuur en verwijst hiernaartoe.

- **PDF-conversie** (`mdconv/sources/files.py`): een geüploade/gelinkte `.pdf` gaat eerst door
  **pdf-inspector** (Rust-library van Firecrawl, `process_pdf_bytes()`) — layout-aware Markdown
  (koppen/lijsten/tabellen) zonder de losse-regeleinde-reflow-hack die MarkItDown nodig heeft.
  `result.pdf_type` classificeert de PDF (`text_based`/`scanned`/`image_based`/`mixed`); bij
  `scanned`/`image_based` (geen tekstlaag) of een lege/foutieve extractie valt de code terug op
  MarkItDown (die óók geen OCR doet, maar wel de bestaande gedrag is voor dat geval). Alle andere
  formaten (Word/Excel/PowerPoint/HTML/CSV/JSON/…) blijven altijd via MarkItDown lopen — behalve
  EPUB, zie hieronder. `files.convert()` geeft `(markdown, engine)` terug zodat de UI kan tonen
  welke engine het document daadwerkelijk verwerkte (`"pdf-inspector"`/`"epub"`/`"MarkItDown"`
  in het bronveld).
  - **Onvertaalde glyphs worden niet stilzwijgend doorgelaten.** Sommige lettertypen slaan
    een typografische ligatuur (bv. "fi", "ft", "th") op als één samengesteld glyph, zónder
    tekstcodering (`ToUnicode`) naar de onderliggende letters — de PDF "weet" dan zelf niet
    meer welke tekens het zijn, dus geen extractie-engine kan dat achteraf herstellen. Zowel
    pdf-inspector als MarkItDown zetten daar dan een `�` (replacement character) neer,
    bv. "these" → "�ese", "often" → "o�en". `files.warn_if_unmapped_glyphs()` (aangeroepen
    vanuit `sources.from_file()`, op alle PDF-routes: gewoon, per-pagina-inline en de
    MarkItDown-terugval) zet daarom een waarschuwing boven de tekst zodra `�` erin
    voorkomt — anders zou een gebruiker een verkeerd citaat kunnen overnemen zonder dat te
    weten. Geen poging tot giswerk-herstel: welke letters het precies waren staat nergens in
    het bestand, dus alleen handmatig tegen het origineel controleren is betrouwbaar.
- **EPUB-conversie** (`mdconv/sources/epub.py`, zonder AI): een EPUB is een zip met
  XHTML-hoofdstukken plus een package-document (OPF) dat de leesvolgorde (`spine`) en de
  bestanden (`manifest`) beschrijft. MarkItDown kan een EPUB al lezen (`_epub_converter.py`
  in die dependency, zelf ook al container.xml/OPF/spine-bewust), maar behandelt elk
  hoofdstuk als losse HTML: interne links (tussen hoofdstukken, voetnoten, de
  inhoudsopgave) blijven dan gewone relatieve `href`'s — kapotte links zodra alle
  hoofdstukken tot één Markdown-bestand worden samengevoegd. `files._convert_epub()`
  probeert daarom eerst de eigen parser; lukt dat niet (geen geldige/ondersteunde
  EPUB-structuur — bv. corrupte zip of ontbrekende OPF), dan valt de conversie terug op
  MarkItDown, net als bij een PDF zonder tekstlaag.
  - **Koppen: échte `<h1>`-`<h6>` tags, én koppromotie op typografie.** Echte kop-tags
    komen via `markdownify` gewoon als `#`-`######` uit. Maar veel professioneel gezette
    EPUB's (InDesign-export, bv. uitgeversboeken) hebben **géén** echte kop-tags —
    hoofdstuktitels en paragraafkoppen zijn gewoon `<p class="...">` met een eigen
    alinea-stijl. Bevestigd met een echt boek (CIPP-M, IAPP): zonder koppromotie leverde
    dat **nul** koppen op in een boek van 750k tekens platte tekst. Tekstueel giswerk
    ("lijkt deze zin op een titel?") zou hier onvoorspelbaar zijn op willekeurige
    boektekst — precies waarom de structuurwoorden-aanpak van `render.promote_headings()`
    (EUR-Lex: "HOOFDSTUK", "Artikel N", een vaste woordenlijst in de grote EU-talen) hier
    niet herbruikt kan worden. Wat wél betrouwbaar is: de CSS zelf zegt hoe groot/vet/
    welk lettertype elke alinea-stijl heeft — een meetbaar feit, geen gok.
    - **`_load_css_classes()`** leest alle `.css`-bestanden in de zip met een simpele,
      niet-geneste regex (`selector { declaraties }`) — de auto-gegenereerde CSS van
      digitale-uitgeverssoftware heeft geen `@media`/geneste selectors, dus dat is
      voldoende; `@font-face`/`@page`-blokken worden gewoon als (nooit matchende) "klasse"
      meegelezen, geen probleem. Voor elke `.KlasseNaam` wordt `font-size` (naar een
      em-equivalent: `px/16`, `pt/12`, `%/100`), `font-weight` (`bold`→700, `normal`→400,
      cijfers direct) en het eerste `font-family`-token opgeslagen.
    - **`_dominant_style()`** bepaalt de lettergrootte/lettertype die de méeste tekens in
      het hele boek beslaat (over alle hoofdstukken se `<p>`'s heen, gewogen naar
      tekstlengte) — in de praktijk de hoofdtekst, ongeacht hoe de uitgever die stijl
      noemt. Dat is de baseline waar elke andere stijl tegen wordt afgezet; een aanpak die
      werkt ongeacht de klassennamen-conventie van de specifieke uitgever/InDesign-sjabloon.
    - **`_qualifying_heading_classes()`** promoveert een stijl alleen als hij **groter**
      is dan de baseline (harde eis — sluit bv. een kleine "Chap-Num"-bijschriftstijl
      altijd uit) én een samengestelde score van ≥2 haalt over drie signalen:
      grootte-ratio (≥1,5× → 2 punten, ≥1,15× → 1 punt), `font-weight` ≥ 600 (1 punt), en
      een ander lettertype dan de hoofdtekst (1 punt). Dit onderscheidt bv. een vette
      auteursnaam-stijl (zelfde grootte als de hoofdtekst, dus 0 punten op grootte) van een
      echte titelstijl (groter én vet én een ander lettertype) — puur op grootte of puur
      op vet zou de auteursnaam ten onrechte ook promoveren.
    - **Kopniveau via rangorde, niet via vaste ratio's**: de kwalificerende stijlen worden
      gesorteerd op grootte (groot → `h1`, volgende → `h2`, …, maximaal `h6`) — vaste
      ratio-afkappunten (bv. "≥2× = h1") zouden niet overdragen naar een ander boek met
      een andere typografische schaal.
    - **`_promote_headings_by_style()`** promoveert een `<p>` alleen als de tekst ≤ 150
      tekens is — een hele alinea die toevallig een "kop"-stijlklasse hergebruikt (kan
      voorkomen) blijft zo een alinea, geen kop.
  - **Interne links → Obsidian-wikilinks, externe links blijven gewoon.** Vóór de
    HTML→Markdown-conversie rewrite `_rewrite_links()` elke `<a>` met een relatieve `href`
    (geen `scheme:` zoals `http:`/`mailto:`) naar de kop waar hij naar verwijst:
    `[[#Kop]]`, of `[[#Kop|linktekst]]` als de linktekst afwijkt van de koptekst. Een link
    zonder anker (naar het hele hoofdstuk) valt terug op de titel-kop van dat hoofdstuk; een
    anker dat zelf geen kop is (bv. een voetnootmarkering) valt terug op de dichtstbijzijnde
    voorafgaande kop — een betekenisvolle wikilink in plaats van een dode interne id. Is er
    na die twee terugvallen nog niets te vinden, dan blijft de platte linktekst staan, geen
    kapotte link. `_index_headings()` bouwt deze `(hoofdstuk, anker) → koptekst`-opzoektabel
    in één keer over alle hoofdstukken vóór het herschrijven begint.
  - **De nav/inhoudsopgave komt niet als apart "hoofdstuk" mee.** EPUB3 markeert die in de
    spine met `linear="no"` (geen gewone leesvolgorde-pagina); `_spine_hrefs()` slaat zulke
    `itemref`'s over.
  - **Afbeeldingen worden weggelaten, niet als kapotte link.** Een `<img src="images/…">`
    verwijst naar een pad binnen de zip; zonder een bijlage-mechanisme zoals bij PDF's zou
    dat een dode `![alt](pad/in/de/zip.jpg)` opleveren. Buiten scope van deze functie (die
    vroeg specifiek om koppen + wikilinks) — `<img>`-tags worden vóór de conversie
    gedecomposet.
  - **`BeautifulSoup(..., "xml")`** (lxml's XML-parser) voor container.xml/OPF, niet de
    gewone `"lxml"` HTML-parser — die laatste zou de namespace-declaraties (`xmlns=`) in de
    OPF niet betrouwbaar even goed verwerken. De hoofdstukken zelf gaan wél door `"lxml"`
    (HTML-modus, vergevingsgezind bij ontbrekende `<body>` e.d.), zoals de rest van het
    project al doet.
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
    **Élke gedeclareerde bron wordt gearchiveerd, niet alleen de hoofdbron.** Een
    geconsolideerde handeling haalt haar considerans uit de basishandeling
    (`role: "preamble"`), en een meerdelige handeling bestaat uit meerdere
    `document-part`-onderdelen; tot 22 september 2026 koos `_bronnen()` er één en belandde
    de rest nergens. Gemeten gevolg bij de AVG (`02016R0679-20160504`): het bronbewijs
    noemde twee zips, het archief bevatte er één, de basiszip was daarna nergens meer op
    de Mac te vinden — en zonder haar mist de herbouwde Markdown 408 regels considerans.
    Elke bron staat nu onder haar eigen SHA-256; twee bronnen met dezelfde bytes delen één
    bestand en houden allebei hun regel. `fetch.json` houdt zijn bovenste sleutels
    (`resolved_url`, `sha256`, `source_format`, `media_type`) bij de hoofdbron — harde
    regel 1 van de kennisbank verbiedt een schemabump, en `tools/xml_meting/ophalen.py`
    leest `resolved_url` daar — en krijgt er één **optionele** sleutel bij: `sources`, met
    per bron `role`, `file`, `sha256`, `resolved_url`, `source_format`, `media_type`,
    `identifier` en `language`. Wie `sources` niet kent, ziet precies wat hij eerder zag.
    Een gedeclareerde bron zonder bruikbare bytes weigert de hele download: een half
    archief is erger dan geen download, en zo raakte het bewijs ongemerkt incompleet.
    Voorstellen, documenten en geplakte tekst houden de platte download. De tokens
    verlopen lui na twee uur, net als afbeeldingtokens.
- **Wiskunde-modus** (`mdconv/ocr.py`, endpoints `/api/convert/file/ocr` +
  `/api/convert/file-url/ocr`): een **opt-in** route bij Documentupload (checkbox `#ocr-mode`),
  alleen voor PDF en alleen met een OpenRouter-sleutel. De gewone tekstextractie
  (pdf-inspector/MarkItDown) leest de tekstlaag lineair; LaTeX-wiskunde uit een Beamer-PDF
  komt daar onbruikbaar uit (sub-/superscripts weg, `\underbrace`/grote accolades als
  glyph-brij, de index *i* als Private-Use-glyph `U+EBE9` zonder `ToUnicode`). De semantische
  wiskunde staat niet in de tekstlaag — alleen visueel herlezen helpt.
  - **`pdf_images.render_pages()`** rastert elke pagina met `pdftoppm -png -r <dpi>` (poppler,
    dezelfde binaries als `extract_images`; `render_available()` checkt `pdftoppm`/`pdfinfo`).
    `page_count()` (via `pdfinfo`) weigert eerst een PDF > `_MAX_PAGES` (100) — een bewust
    trage modus hoort een harde grens te hebben.
    `_DPI` = 200, bij te stellen met de env-var `OCR_DPI`. Paginasortering is **numeriek**
    (`page-2` vóór `page-10`), niet lexicaal.
  - **`openrouter.ocr_pages_stream(images, …)`** is `stream_chunk` met één of meer
    `image_url` data-URI's als user-content i.p.v. tekst — het model moet dus multimodaal
    zijn. Géén "lege stream = fout"-check (een blanco pagina levert legitiem niets op);
    `finish_reason == "length"` betekent dat de pagina's in dít verzoek niet in het
    uitvoerplafond pasten → melding met het advies "pagina's per verzoek" te verlagen.
  - **`ocr.ocr_pdf_stream()`** verdeelt de pagina's in groepen van
    `config.get_ocr_pages_per_request()` (standaard 5, instelbaar in het paneel) en laat
    tot `_MAX_PARALLEL_BATCHES` (3, env `OCR_PARALLEL`) van die verzoeken **parallel** lopen
    (`ThreadPoolExecutor`, zoals `cleanup.clean()`), maar levert de tekst **in
    documentvolgorde** uit — een groep die eerder klaar is wacht op zijn beurt, en zijn
    tekst verschijnt dan in één keer. `\n\n` tussen groepen, één `Progress` per groep
    (`produced_tokens` = pagina's tot nu toe), één opgeteld `Usage` aan het eind. Een fout
    of `GeneratorExit` (client weg) zet de annuleringsvlag zodat de nog lopende groepen bij
    hun eerstvolgende SSE-regel stoppen; `_cancel.clear(request_id)` in een `finally`.
    Een korte PDF (≤ groepgrootte) is dus gewoon één verzoek.
  - **Streaming + annuleren hergebruiken de opschoon-infrastructuur volledig**: dezelfde
    `_frame`/`STREAM_ERROR_SENTINEL` met de `CLEAN_`-tagnamen (bewust niet hernoemd — dan
    hoeft `makeStreamParser` niet te wijzigen), dezelfde proces-brede `mdconv.cleanup.cancel`-
    set en hetzelfde `/api/clean/cancel`-endpoint, en aan de front-end de `activeCleans`-Map
    + `#cancel-clean`-knop.
  - **Modellen, prompt én pagina's-per-verzoek staan in het instellingenpaneel**
    (`DEFAULT_OCR_MODELS`, `prompts.OCR`, `DEFAULT_OCR_PAGES_PER_REQUEST` = 5; keys
    `ocr_models`/`ocr_prompt`/`ocr_pages_per_request` in `settings.json`) met dezelfde "leeg
    = standaard"-semantiek als de opschoonmodellen. `DEFAULT_OCR_MODELS`:
    `qwen/qwen3.7-flash` en `openai/gpt-5.6-luna-pro`. Env-terugval `OCR_MODEL`. Deze prompt
    zit **niet** in `prompts.DEFAULTS`/`PROFILES` (dat stuurt de opschoon-dropdown).
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
