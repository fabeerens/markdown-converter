# Buitenlandse rechtspraak (DE, BE, FR; ES en AT niet haalbaar)

Verplaatst uit `CLAUDE.md` (WP-60, 29 september 2026), tekst ongewijzigd. `CLAUDE.md` bevat de
architectuur en verwijst hiernaartoe.

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
