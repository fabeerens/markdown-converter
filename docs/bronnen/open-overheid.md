# Open overheid: Kamerstukken, Woo, consultaties en de wetgevingskalender

Verplaatst uit `CLAUDE.md` van de hoofdversie (WP-77, 1 oktober 2026), tekst ongewijzigd; geschreven door
Floris bij het bouwen van het tabblad Open overheid. `CLAUDE.md` bevat de architectuur en verwijst hiernaartoe.
Sinds WP-77 loopt de omzetting van een Kamerstuk uit XML via `officiele_bekendmakingen.py` (met herkomst en
bronbewijs); wat hieronder over `xml_to_markdown` staat, beschrijft de omzetting zoals die tot de samenvoeging was.

- **Open overheid — kamerstukken** (`mdconv/sources/kamerstuk.py`, endpoint
  `/api/convert/overheid`, tab "Open overheid" tussen Wetgeving en Documentupload; interne
  sleutel `oo`): wat Tkconv's `tkgetxml` doet is alleen de
  SyncFeed van opendata.tweedekamer.nl binnenhalen (metadata); de **tekst** zit niet in die feed.
  De gestructureerde tekst staat in de **officiële XML** op
  `https://zoek.officielebekendmakingen.nl/{id}.xml` (schema `op-xsd-2012-2`, keyless, geen WAF-
  blokkade — anders dan EUR-Lex). Eén parser voor `kst-` (kamerstukken), `ah-tk-`/`ah-ek-`
  (Kamervragen + antwoord) en `h-tk-`/`h-ek-` (Handelingen); onbekende elementen worden als
  alinea/container gelezen, nooit weggelaten (`_is_container`/`_INLINE`). `<metadata.xml>` naast
  het stuk (soort, indiener, datum, bijlage-id's) is best-effort verrijking, geparallelliseerd met
  de XML-fetch.
  - **Invoer** (`parse_reference`): publicatie-id (hoofdletters genormaliseerd), URL, dossiernotatie
    ("36600-VII, nr. 1", "21501-02 nr. 3174", "36836 D"), D-nummer (`2024D40329`), Document-GUID of
    tweedekamer.nl-link met `did=`. Een kaal dossiernummer zonder stuknummer krijgt een eigen
    foutmelding i.p.v. een gok. `detect_source` claimt alleen de **ondubbelzinnige** vormen (id's en
    links), vóór de HUDOC-test (de cijfers in zo'n id mogen niet als item-id gelezen worden); losse
    dossiernotaties horen bij het eigen tabblad.
  - **D-nummer/GUID → publicatie** via de OData-API (`gegevensmagazijn.tweedekamer.nl/OData/v4/2.0/
    Document`, `$expand=Kamerstukdossier`): dossier+toevoeging+`Volgnummer` → `kst-…`;
    `Aanhangselnummer` `242501244` → `ah-tk-20242025-1244`. Heeft het document geen van beide (een
    brief buiten een dossier, `Volgnummer` -1), of staat de XML er (nog) niet, dan **terugval op het
    originele bestand** (`Document({id})/resource`, DOCX/PDF) via `files.convert` — mét een
    cursieve notitie bovenaan, nooit stil.
  - **Koppen**: `divisie`/`kop`. Genummerde koppen ("1.", "2.1") krijgen hun niveau uit de
    nummering (de bron nest "2.1" niet altijd in "2."); ongenummerde `tussenkop`pen uit hun opmaak
    (vet > halfvet > vetcur > cur > rom > ondlijn), **relatief per container**: gebruikt een
    sectie maar één stijl, dan is dat één niveau. H1 = dossiertitel, H2 = stuktitel, inhoud vanaf H3.
  - **Voetnoten**: `noot` staat inline in de bron → `[^nr]` + definities onderaan; een nummer dat
    opnieuw begint (bijlagen) krijgt een `-2`-suffix zodat labels uniek blijven. **Links**: `extref`
    → `[tekst](url)` (`kst-…` → `…/{id}.html`, `dossier/…` → `…/dossier/…`, `soort=URL` letterlijk).
  - **Tabellen** (CALS): col-/rowspans uitgevouwen, meerdere kopregels per kolom samengevoegd
    (met herhaalde span-tekst), lege afstandsrijen weggelaten.
  - **Afbeeldingen** (`illustratie naam=…`) worden gedownload van `…/officielebekendmakingen.nl/{naam}`
    en als `![[naam]]` + bijlage meegegeven (zelfde `attachments`-mechanisme als PDF-afbeeldingen;
    max. 30 stuks / 10 MB per stuk / 50 MB totaal). Wat niet lukt blijft als gewone
    `![naam](bron-url)` staan — zichtbaar, geen dode embed.
  - **Veiligheid**: de XML-parser resolve't geen entiteiten en doet geen netwerk (`_parser()`);
    pinned door een test met een externe entiteit.
  - **Geen XML? Dan de PDF.** Nieuwe publicaties (`blg-…`, `ah-<nummer>`, ook recente stukken)
    bestaan alleen als PDF: `zoek.officielebekendmakingen.nl/{id}.xml` geeft 404. `fetch()` slaat de
    XML-poging voor `blg-`/`ah-<cijfers>` over en valt voor andere ids terug op het SRU-record
    (`sru.by_identifier`) → de `pdf`-manifestatie → `files.convert`, met kopblok uit het record en een
    cursieve notitie ("geen gestructureerde XML"). Download is begrensd op 100 MB.
  - **Bijlagen**: `sru.attachments_of(id)` (CQL `w.hoofddocument==<id>`) geeft de
    `blg-…`-bijlagen mét titel; een bijlage wijst zelf terug naar zijn hoofddocument. Best-effort
    (een storing geeft een lege lijst, nooit een mislukte conversie); zonder SRU-resultaat vallen
    we terug op `OVERHEIDop.bijlage` uit metadata.xml. Elk item is `{query, titel, rol, open_url}`;
    `query` gaat weer naar `/api/convert/overheid`, dus een bijlage is zelf een volwaardig document.
  - Niet gebouwd: bijlagen van bijlagen volgen, Handelingen-structuur (sprekers) verder dan platte
    tekst, Staatscourant/Staatsblad (`stcrt-`/`stb-`, ander schema).
- **Open overheid — Woo** (`mdconv/sources/woo.py`): open.overheid.nl heeft een keyless JSON-API
  (dezelfde als de eigen SPA, afgelezen uit de JS-bundel; de SRU-zoekdienst bevat Woo **niet**):
  `/overheid/openbaarmakingen/api/v0/zoek?zoektekst=…` (zoeken + facetten), `/zoek/{id}` (metadata),
  `/documenten/{id}` (het bestand). Waar je op moet letten:
  - Parameternamen: `zoektekst` (niet `zoekterm` — dat wordt stilzwijgend genegeerd en geeft alle
    700k documenten), `aantalResultaten` ∈ {10, 20, 50} (anders 400), `start` = offset,
    `sort=publicatiedatum` + `order=asc`, datums als **dd-mm-jjjj** (de UI levert ISO; `woo.search`
    zet om), filters (`documentsoort` e.d.) **dubbel URL-gecodeerd** en de URL zelf bouwen (niet via
    `requests`' `params=`, dat codeert nog eens).
  - Een id met `_2`-suffix is het **versienummer**; het bestand zit onder het id zónder suffix. Een
    bestand kan ook een eigen `url` hebben (bv. opendata.rijksoverheid.nl) — alleen
    overheidshosts worden gevolgd (`_TRUSTED_HOSTS`), anders de eigen `/documenten/`-route.
    `_pick_file` kiest PDF/Office/tekst en slaat zips over; geen bruikbaar bestand → duidelijke fout.
  - Gescande pdf's zonder tekstlaag geven vrijwel geen tekst: dan een cursieve waarschuwing met het
    advies de OCR-/wiskunde-modus bij Documentupload te gebruiken.
  - **Relaties** (`documentrelaties`) worden de bijlagenlijst: rollen uit de TOOI-thesaurus
    (`c_05f4a5f3` = "heeft bijlage", `c_4d1ea9ba` = "is bijlage bij", plus bundel/onderdeel; de
    identiteitsgroep valt weg). Titels worden parallel opgehaald (max. 30).
  - Een **kale UUID** is ook een Tweede Kamer-Document-Id: `sources.from_overheid` vraagt het aan
    open.overheid.nl (`woo.is_known_id`, één verzoek) en valt anders terug op de TK-open data; een
    `_n`-suffix of link is altijd Woo.
- **Zoeken** (`mdconv/search.py`, `GET /api/search`, `GET /api/search/soorten`): standaard
  **`alles`** = beide bronnen samengevoegd tot één lijst (`_search_all`); daarnaast `pub` en `woo`
  apart. Eén resultaatvorm `{id, query, titel, soort, datum, bron, meta, snippet, open_url}` — `query`
  gaat direct naar `/api/convert/overheid`.
  - `pub` = SRU (`https://repository.overheid.nl/sru`, keyless; `sru.py`). CQL die werkt:
    `cql.textAndIndexes="…"` (volledige tekst), `w.dossiernummer=="36600-VII"` (een invoer die op een
    dossiernummer lijkt wordt automatisch een dossierzoekopdracht), `w.publicatienaam==`,
    `dt.type==Bijlage`, `dt.date>=…`, `A NOT B` (**niet** `AND NOT`), sorteren met
    `sortBy dt.date/sort.descending` (de `sortKeys`-parameter wordt genegeerd). Zoektekst gaat
    alleen tussen quotes mee nadat `"` en `\` eruit zijn. Zonder zoektekst is "nieuwste eerst" de
    standaard. De "soorten" (`sru.SOORTEN`) zijn vast; sluit Staatscourant e.d. uit omdat de
    converter daar niets mee kan.
  - **`alles`**: per bron de eerste `start + n` resultaten (parallel; `MERGE_LIMIT` = 200 per bron,
    daarom bladert de UI niet dieper), samengevoegd en gesneden. Bij "nieuwste/oudste" sorteert het op
    datum; bij "relevantie" worden de bronnen afwisselend gelegd (scores zijn niet vergelijkbaar).
    **Dubbelen** (`_merge`): dezelfde titel binnen 21 dagen — Woo zet "dossier, nr. X - " voor de titel
    (`_WOO_PREFIX`, wordt eraf gehaald) en een kamerstuk heeft "dossiertitel; soort; stuktitel", dus
    er wordt ook op het laatste deel vergeleken — blijft één keer staan, als officiële publicatie met
    `ook_woo: true`. Valt één bron uit, dan komt de rest mét een `waarschuwing`; beide uit = fout.
    Geen soortfilter (de soorten verschillen per bron).
  - **Dossiernummer als zoekterm** (`sru.dossier_of`: `36600`, `36 600-VII`, `dossier 36600`; vijf
    cijfers, want vier is een jaar): geen tekstzoekopdracht maar de **hele dossierlijst** uit de
    officiële publicaties (`w.dossiernummer`), oudste eerst, pagina's van 50, in `alles` én `pub`
    (Woo-kopieën zijn dubbelen, dus Woo blijft erbuiten). Het antwoord heeft `dossier`; de UI toont
    "Dossier X — n stukken". `_stuk_label` zet "36600-VII, nr. 1 - " voor de titel zodat een lange
    lijst leesbaar is.
  - `woo` = `woo.search`; het soortfilter komt uit de facetten van het laatste antwoord (meeste
    eerst, max. 40). Zonder zoekterm én zonder filter weigert de zoekfunctie (anders 700k treffers).
  - Front-end (`oo`-state + `renderOO`/`renderResults`/`runSearch` in `app.js`): drie **subtabs**
    (Stukken | Consultaties | Wetgevingskalender; `oo.sub`, onthouden) — `ooScope()` leidt de
    zoekbron af (bij Stukken de keuze alles/pub/woo). Bron-specifieke filters staan data-gedreven in
    `OO_FILTERS` (consultaties: titel/tekst; wgk: status, fase, soort). Bij Stukken modus "Ophalen" ↔
    "Zoeken"; selectievakjes + "Geselecteerde ophalen"; een verzoek-token zodat alleen het laatste
    antwoord de lijst bijwerkt; paginering. Een document dat al open staat (`doc.ident`) wordt
    getoond i.p.v. dubbel opgehaald. **Na ophalen klapt de resultaatlijst in** (`oo.collapsed`, de
    lijst blijft in de DOM; balk "Zoekresultaten tonen (n)"); een nieuwe zoekopdracht klapt hem uit.
- **Bijlagen = linklijst in de Markdown** (`common.bijlagen_section`/`with_bijlagen`): er is geen
  paneel meer (eerst gebouwd, bleek weinig toe te voegen). Elke bron levert `Fetched.bijlagen`
  (`{query, titel, rol, open_url}`); `sources._document_from` zet die onderaan als
  `## Bijlagen en gerelateerde documenten` (tenzij de bron de lijst al op de juiste plek heeft
  gezet, zoals kamerstukken vóór de voetnoten). Elke link is een adres dat de tool zelf begrijpt:
  plakken bij Stukken → Ophalen zet dat document apart om. Zit niet in de JSON-respons.
- **Consultaties** (`mdconv/sources/consultatie.py`, subtab "Consultaties"): internetconsultatie.nl is
  server-gerenderde HTML zonder API, dus scrapen met BeautifulSoup. Adresvormen en de zoek-URL staan
  in de module-docstring. Wat je moet weten:
  - De consultatie wordt Markdown vanaf "In het kort" (titel/labels erboven en de feitentabel zitten
    in het kopblok; **voorouders van die kop blijven staan** bij het wegknippen, anders verdwijnt de
    hele inhoud). Bijlagenlijst: documenten (`/document/{id}`), "Alle reacties (n)" en de
    wetgevingskalender-fiche (uit de link op de pagina).
  - **Reacties** (`fetch_reacties`): één Markdown-document met de tekst van elke openbare reactie.
    De lijst is te pagineren met `/reacties/datum/{pagina}/100` (de site laat 10/25/100 toe; met
    10 per pagina duurde het veel langer). Elke reactie is een eigen pagina (12 parallel, 144 stuks
    ≈ 1 minuut — de site is traag); max. `MAX_REACTIES` (400), meer wordt gemeld. De standaardvraag
    "Wilt u reageren…" valt weg, specifieke vragen blijven (vet) staan; een bijlage wordt een link.
  - **Zoeken**: `GET /zoeken/resultaat?Trefwoorden=…&TrefwoordenSearchScope=Titel|TitelEnTekst`
    `&ConsultatiedatumVan=d-m-jjjj 00:00:00&…TotEnMet=…&Pagina=n`, vast 10 per pagina (de UI neemt
    `n` uit het antwoord over). De filters kwamen uit de redirect van het POST-formulier
    (ASP.NET-viewstate niet nodig). Zoekterm leeg mag.
- **Wetgevingskalender** (`mdconv/sources/wgk.py`, subtab "Wetgevingskalender"): per regeling is er een
  **XML-versie** (`/Regeling/WGKnnn/xml`, `regelgevingFiche`) met metadata, fasen, mijlpalen en
  documenten (download-url) — die is de bron, niet de HTML. Zoeken: `/Regeling/ZoekResultaten?
  Zinsdeel=…&Type=Regeling&Status=inwording|naderend|beeindigd&Fase=…&RegelgevingType=Wet|Amvb
  &Pagina&Paginagrootte=10|25|100` (de site gebruikt GET-parameters die uit de formuliervelden
  komen; filterwaarden worden gevalideerd tegen vaste lijsten). **Eén treffer** stuurt de site door
  naar de regeling zelf; de titel komt dan uit `<title>`. Een document-download-URL (`…/Download/guid.pdf`)
  wordt omgezet via `consultatie.fetch_file`, met de titel uit de fiche in plaats van de GUID.
- **Weergave** (`static/mdview.js`, schakelaar "Ruwe tekst | Naast elkaar | Weergave" in de uitvoerbalk,
  dus voor alle vijf de tabbladen): een eigen kleine Markdown→HTML-renderer zonder dependency (geen
  build, geen externe verzoeken). Alles wordt geëscaped; alleen `<br>`, `<sup>`, `<sub>` blijven;
  links alleen http(s)/mailto/relatief (nooit `javascript:`/`data:`). Dekt koppen, lijsten (genest),
  tabellen, citaten, code, voetnoten (met terugkeerlink), links, Obsidian-embeds
  (`![[p01.png]]` → `/api/attachments/<token>/<naam>`) en -wikilinks. Alleen gerenderd als hij
  zichtbaar is; tijdens typen/streamen debounced (max. 600 ms oud), scrollen loopt evenredig mee,
  de keuze staat in `localStorage` (`mdView`). Een Node-gestuurde test (`skipif` zonder `node`)
  draait de renderer op vaste gevallen, incl. XSS-pogingen.
