# Front-end en designsysteem

Verplaatst uit `CLAUDE.md` (WP-60, 29 september 2026), tekst ongewijzigd. `CLAUDE.md` bevat de
architectuur en verwijst hiernaartoe.

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
