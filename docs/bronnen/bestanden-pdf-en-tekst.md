# PDF, losse afbeeldingen en geplakte tekst

Verplaatst uit `CLAUDE.md` (WP-60, 29 september 2026), tekst ongewijzigd. `CLAUDE.md` bevat de
architectuur en verwijst hiernaartoe.

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
