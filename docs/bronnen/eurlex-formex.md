# EUR-Lex en Formex

Verplaatst uit `CLAUDE.md` (WP-60, 29 september 2026), tekst ongewijzigd. `CLAUDE.md` bevat de
architectuur en verwijst hiernaartoe.

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
  **Geschrapte tekst** (kb WP-64, 30 september 2026). Een consolidatie laat een geschrapte passage
  staan tussen `<?CLG.MDFO … ACTION="DELETED" …?>` en de `<?CLG.MDFC …?>` die ernaar verwijst, en
  ElementTree gooit die instructies weg. Tot WP-64 schreef de omzetter die oude tekst dus als
  geldende tekst: de artikelen 17 tot en met 19 van eIDAS (02014R0910-20241018), en in de
  kennisbank 40 passages in acht documenten, die de poort daar sinds WP-63 tegenhoudt
  (`source-deleted-text`). Nu leest `_zonder_geschrapte_tekst()` elk onderdeel mét zijn
  instructies, schrijft op die plek wat EUR-Lex toont en haalt daarna alle instructies weg, zodat
  de rest van de omzetter dezelfde boom ziet als voorheen. Gemeten in de HTML van dezelfde
  consolidaties: een bereik op `LEVEL="STRUCTURE"` (artikel, lid, punt, afdeling) toont EUR-Lex als
  één alinea `▼M2 —————` (eIDAS 11 van 11, Europol 02016R0794-20260111 15 van 15, AI-verordening
  02024R1689-20260727 4 van 4). Hier wordt dat een alinea `—————`, want de Formex-route schrijft geen
  ▼-markering, en preclean V9 van de kennisbank maakt van de HTML-vorm precies dat. Een geschrapt
  opschrift (`STI` van HOOFDSTUK IV in de AVMD) toont EUR-Lex niet; in een kop kan geen alinea
  staan, dus daar verdwijnt alleen de tekst. Een bereik op `LEVEL="TEXT"` met hooguit één woord laat
  EUR-Lex staan (`3.` in artikel 35 van Europol, `.` in bijlage I van de AI-verordening, ` en` in
  artikel 46 van de EES-verordening), en hier blijft het dus ook; een langer tekstbereik wordt
  `—————` op de plek van de tekst (artikel 28, lid 2 van MiFIR, 02014R0600-20251123: `2.   —————`).
  Een bereik dat vlak vóór een element opent en daarbinnen sluit (de punten a) tot en met d) van
  artikel 52, lid 15 van MiFIR; punt f) van artikel 50 quater CRR, waarvan de alinea
  erna blijft), opent één niveau dieper. Een bereik dat niet in één element opent en sluit, een
  ander niveau, of een geschrapt element op een plek waar geen alinea kan staan (een tabelrij) is
  een weigering: `omzetten()` telt na of elke plek als `—————` is geschreven. Wat geschrapt is,
  telt ook voor de zelfcontrole niet meer als brontekst. In de meetlat veranderde de uitvoer van
  tien consolidaties, alleen op die plekken (en de nootnummers na een noot in een geschrapt
  artikel van 02018L1972); de doorlaat bleef 287 van 307.
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
  Een **herhaalde markering binnen één opsomming** weigert niet. De Nederlandse Formex van
  de AVG (`32016R0679`, `L_2016119NL.01000101.xml`) nummert in artikel 13, lid 1 de onderdelen
  a), b), c), d), d), e) waar het Publicatieblad a) t/m f) heeft — een bronfout die alleen in
  de NL-manifestatie zit (de Engelse heeft (a)–(f)). Een `ITEM`/`NP` draagt geen `IDENTIFIER`
  (0 van 558 in die bron), dus de machine-identiteit waarmee artikel 73 van 2024/1689 zijn
  twee leden `11.` uniek houdt bestaat hier niet. Wat de bron wél geeft is de volgorde:
  `FormexOmzetter.dubbele_markering()` geeft het tweede d) zijn volgnummer als anker
  (`art-13-1-d-2`), laat de gedrukte markering en de tekst ongewijzigd (de omzetter corrigeert
  de bron niet tot e); dat zou raden zijn) en zet de bronfout — artikel, lid, markering, anker —
  als waarschuwing in `Herkomst.waarschuwingen`. Tot 23 september 2026 weigerde de
  structuurcontrole hier de hele AVG met "dubbele structurele ankers: art-13-1-d", terwijl
  de tekst van beide onderdelen wél volledig aankwam. Elk ánder dubbel anker (twee artikelen
  met hetzelfde nummer, een lid zonder onderscheidende IDENTIFIER, of een volgnummer dat botst
  met een genest punt `d) … 2.`) blijft fail-closed; de melding zegt nu wat zo'n dubbel anker
  betekent en dat alleen een herhaalde markering in één opsomming wordt onderscheiden.
  De **titel van een tabel** (`TBL/TITLE`) komt als losse alinea boven de tabel, zonder opmaak —
  de vorm die de raw van NIS 2 al had. Tot 23 september 2026 viel hij weg en weigerde de
  woordcontrole 12 van de 347 documenten in de meetlat op één woord: "CONCORDANTIETABEL".
  De **ankers in een bijlage volgen de boom van de bron**. Ze zijn voor de zelfcontrole, niet
  voor het profiel, en weigerden 13 van de 347 documenten op dubbele ankers doordat een niveau
  ontbrak. Een onderdeel met een nummer in zijn kop (`A.`, `1.`, `8.1.`, `Deel A`, `AFDELING II`,
  `Bepaling 8`, `— Prioritair gebied I`; zie `ONDERDEELKOP`) is een eigen niveau; een ongenummerd
  onderdeel naast andere onderdelen krijgt zijn plaats (`s2`); een ongenummerde bijlage is
  `annex-o<volgnummer>`, zodat ze niet botst met `BIJLAGE I` (SCC's). Een tweede reeks losse
  punten in één blok draagt `al<k>`, net als in het profiel, en een DLIST telt mee als tweede
  opsomming in een lid. `HOOFDSTUK IX bis` is `hfd-9bis`. Een **herhaald hoofdstuknummer**
  (twee keer `HOOFDSTUK III` in de Nederlandse DORA, 32022R2554, waar het Publicatieblad VII
  heeft) krijgt dezelfde regel als de dubbele `d)` in de AVG: `dubbele_divisie()` geeft het
  tweede zijn volgnummer (`hfd-3-2`, en dat loopt door in het ankerpad van zijn afdelingen),
  kop en tekst blijven ongewijzigd, en de bronfout staat als waarschuwing in de herkomst.
  Een dubbel artikelnummer blijft een weigering.
  Een **geciteerd blok als kind van een bijlage** (`CONTENTS/QUOT.S` met een tabel, onderdelen of
  alinea's: een bijlage die een bijlage van een andere handeling vervangt) gaat door
  `bijlage_inhoud(…, geciteerd=True)`, net als een ingesloten bijlage: brontekst, geen eenheden.
  Een **afbeelding** (inclusie van het type `TIFF`: een formulier, pictogram, handtekening of
  aankruisvakje als lijstteken) wordt niet overgenomen maar wel vastgelegd, zoals bij de
  rechtspraakroute: een waarschuwing en `Herkomst.extra["afbeeldingen_weggelaten"]`. Draagt ze
  haar tekst mee (`IMG.CNT`, de certificaten van Brussel I bis), dan komt die tekst als alinea's
  mee, zonder eenheden. Een ander afbeeldingstype, een afbeelding die niet in de zip zit, en een
  `IMG.CNT` midden in een zin blijven een weigering; een `FORMULA` in `IMG.CNT` ook.
  Een **geciteerde afdeling, definitielijst of bijlageonderdeel** (`DIVISION`, `DLIST` of `GR.SEQ`
  binnen `QUOT.S`, in de bron vrijwel altijd als `<P><QUOT.S>…</QUOT.S></P>` naast de TXT van een
  wijzigingspunt) loopt inline door, net als een geciteerd artikel. eIDAS 2 (32024R1183) voegt zo
  zes afdelingen met hun artikelen in, CRD VI (32024L1619) hele titels, de
  interoperabiliteitsverordening (32019R0817) definities. Er komt geen `##`-kop, geen eigen alinea
  per punt en geen eenheid: de kennisbank zou dat lezen als structuur van déze handeling. De kop
  van een geciteerde afdeling verliest haar opmaak (`kop_tekst`). PREFIX, TERM en DEFINITION, en
  losse NP's die in de bron tegen elkaar aan staan (`cyberbeveiliging3.2.`), krijgen een spatie
  ertussen. Buiten een citaat blijven deze elementen een weigering (`citaatdiepte`). Een
  **geciteerde tabel** kan niet inline. `geciteerde_tabel()` bouwt haar op waar ze staat, zodat
  haar noten in documentvolgorde genummerd worden, en laat een `TABELMARKER` (U+0000) in de tekst
  achter. `schrijf()` in `inhoud()` breekt de alinea daar: de tekst ervoor, de tabel als blok, de
  tekst erna. Een marker die niet door `schrijf()` gaat (in een cel, een definitie of de TXT van
  een onderdeel) laat de tabel ongeschreven, en dan weigert de omzetting; een lid dat met een
  geciteerde tabel begint weigert ook. `DEEL A`/`DEEL B` als `GR.SEQ` in een tabelcel (de lijst
  van werkzame stoffen, 32011R0704) worden tekst van die cel. Een `ARTICLE` in een bijlage (de
  statuten van een ERIC, 32022D0289) is een artikel met de bijlage in zijn anker
  (`annex-o1-art-3`, patronen.md §6). Tot 23 september 2026 weigerde de meetlat op deze vormen 31
  documenten (inline:DIVISION, inline:DLIST, inline:GR.SEQ, inline:TBL, inhoud:ARTICLE); 25 komen
  nu door, de overige 6 op een andere oorzaak.
  Een **annotatie** (`GR.ANNOTATION`/`ANNOTATION`) is in Formex de noot die geen voetnoot is: een
  NB, een opmerking, een technische noot, een legenda. Ze komt als gewone alinea's op de plek waar
  de bron haar zet. De titel (`Noot 1`, `Technische noot:`) wordt een eigen alinea, zoals de
  HTML-route haar als `oj-ti-annotation` levert, en nooit een kop. De inhoud gaat door `inhoud(…,
  basis="")` en krijgt dus geen eenheden, want haar `a)` of `NB:` is geen onderdeel van de
  handeling. Een P die alleen een annotatie omhult (57 van de 58) splitst zoals bij een lijst. In
  `TBL/GR.NOTES` staat ze als alinea direct onder de tabel (de HTML-route maakt er de laatste
  tabelrij van); de genummerde tabelnoten gaan zoals altijd naar het notenblok. In een cel is ze
  een blok van die cel, en boven de titel van een handeling (`ONTWERP`, 32015D0926) een alinea.
  Een annotatie aan het begin van een lid is niet gemeten en weigert. **Kanttekst** (`MARGIN`: de
  categorie `ML1` in de kantlijn van de militaire lijst, 32017L0433) staat vooraan op de regel van
  haar alinea of kop, met een spatie ervoor zodat ze niet aan het eerste woord plakt; de
  HTML-route zet er een `<br/>` achter. Een **groep tabelrijen** (`BLK`, de PRODCOM-lijst
  32010R0860) krijgt voor haar titel (`TI.BLK`) een rij met één samengevoegde cel over `COL.START`
  tot en met `COL.END`. Zoals elke samengevoegde cel staat die op elke bezette plek, en
  `_bronwoorden` herhaalt haar woorden even vaak. Een BLK zonder titel groepeert alleen. Een regel
  die met `*`, `+` of `-` en een spatie zou beginnen, zoals het lijstteken `*` in 32025D2554 of de
  legendaterm `*` in 32010R0860, krijgt een harde spatie achter de markering (`_geen_opsomming`).
  Zo maakt Markdown er geen opsomming van en blijft het teken staan; het profiel doet hetzelfde
  met markeercellen uit de HTML-route. Een `TITEL` onder een `DEEL` draagt dat deel in haar anker
  (`tit-2-1`, `hfd-2-1-3`), omdat het Europees wetboek voor elektronische communicatie
  (32018L1972) elk deel opnieuw bij TITEL I begint. Tot 23 september 2026 weigerde elk van deze
  gevallen het hele document; de wetgeving in de meetlat ging daardoor van 167/266 naar 176/266.
  Een **groep overwegingen** (`DIV.CONSID`: adequaatheids-, staatssteun- en antidumpingbesluiten,
  tot zes niveaus genest in 2021/1772) blijft overwegingen. Elke `CONSID` wordt `(n)` plus één
  spatie met haar `rec-`-eenheid, in bronvolgorde en over de groepen heen doorgenummerd. De kop
  van de groep wordt een gewone alinea: `NO.P` plus drie harde spaties plus de tekst zonder vet of
  cursief (`1. INLEIDING`, `2.1 Toepassingsgebied`), de vorm van een bijlageonderdeelkop; een kop
  zonder nummer (`Referentiestelsel`, 2017/2116) staat er kaal. Geen `##`, want in de considerans
  plant het profiel alleen overwegingen (`plan_recitals`), en een andere kop zou daar een
  kopniveau zonder anker zijn. Een **adresblok** (`ADDR.S`) wordt één alinea per P; in een
  tabelcel is het een blok als een P, met de regels door een spatie gescheiden (dezelfde celtekst
  die de kennisbank uit de bron herberekent). Een **slotformule in een bijlage** (`FINAL >
  SIGNATURE > SIGNATORY`: de zes brieven in de bijlagen van het Data Privacy Framework, 2023/1795)
  wordt één alinea per P; de handtekening is een TIFF en gaat de gewone afbeeldingsweg. Wat
  daarbuiten valt, blijft een weigering: ander groeps- of kopinhoud (`overwegingen:P`,
  `overwegingenkop:STI`), een adresblok midden in een zin of met losse tekst tussen zijn P's, en
  `PL.DATE` of losse tekst in de slotformule van een bijlage. Tot 23 september 2026 weigerde
  `DIV.CONSID` 9 en `ADDR.S` 2 van de 347 documenten in de meetlat. Nu komen er 8 door, waaronder
  2023/1795 uit de eindtest; 2025/1330 weigert nog op `GR.ANNOTATION` en 2020/1675 op de
  inclusie-aanroep `<P><INCL.ELEMENT/>.</P>`.
  Een **tabelgroep** (`GR.TBL`: artikel 224 CRR met `VOLATILITEITSAANPASSINGEN` boven vier
  tabellen, de concordantietabel van bijlage II bij 2011/83) wordt een losse alinea met de
  groepstitel, zonder opmaak zoals een tabeltitel, en daarna elke tabel via `tabel()`. Iets anders
  dan een titel of tabel in de groep weigert. Een **algemeen document** (`GENERAL`) is een
  verklaring die het manifest als `DOC.SUB.PUB TYPE="ASSOCIATION"` achter de handeling zet (Rome
  II 32007R0864, geoblocking 32018R0302, Galileo 32008R0683), of het hele document (de
  samenvatting van de AstraZeneca-beschikking, 32006D0857, met een `PROLOG`). `algemeen()`
  schrijft de titelregels als die van een handeling en zet `PROLOG` en `CONTENTS` om via de
  blokweg (`bijlage_inhoud(…, geciteerd=True)`), zonder eenheden: het profiel kent geen anker voor
  een verklaring. De noten tellen opnieuw vanaf (1). Als hoofddocument draagt een GENERAL de
  vindplaats. Een lid dat meteen met een opsomming begint, is `3.` plus drie harde spaties op een
  eigen regel, de vorm waaraan het profiel een kaal lidnummer herkent. Dat blok gaat buiten
  `Uitvoer.blok()` om, dat harde spaties achteraan wegknipt. Tot 23 september 2026 werd het `3.
  3.` (artikel 6, lid 3 Rome II, het enige geval in de meetlat). Een aanwijzing onder een
  genummerde bijlagekop (`<NP><NO.P>B.</NO.P><TXT>…</TXT><P>(dit formulier …)</P></NP>`) komt als
  alinea onder de kopregel. Een **formule** (`FORMULA`, `FORMULA.S`) blijft bewust een weigering,
  en de melding zegt dat. Van de 372 formules in de meetlat hebben er 307 een index. `PD_pp` en
  `PDₚₚ` zijn voor de woordcontrole één woord, `<sub>` voegt woorden toe en `PD~pp~` is in
  GitHub-Markdown doorgehaalde tekst. Operatoren zijn lege elementen die de woordcontrole niet
  ziet: transparant werd `Risk − weighted exposure amount` stil `Risk weighted exposure amount`.
  En de `OVER`-breuk van 2005/66 zegt niet waar de noemer eindigt. Zie `FORMULE_ELEMENTEN`; maak
  die elementen niet transparant.
  De **nummers van bijlageonderdelen komen uit de bron**. Een onderdeel zonder kop draagt zijn
  nummer in `GR.SEQ/NO.GR.SEQ`, met de tekst in de P erna (902 keer in 12 documenten van de
  meetlat, 294 keer in de MDR, altijd in een bijlage). Het nummer komt vóór die tekst met drie
  harde spaties, in de vorm van het nummer van een onderdeel met een kop: `10.` (kop) en `10.1.`
  (NO.GR.SEQ) in bijlage I van de MDR lezen gelijk, en `2) …` wordt zo geen Markdown-lijst. Begint
  het onderdeel meteen met een opsomming (punt 6.4. van 2008/1), dan staat het nummer op een eigen
  regel, zoals bij een lid. Tot 23 september 2026 schreef die tak het nummer twee keer (`3. 3.`,
  ook bij artikel 6, lid 3 van Rome II). Het nummer is ook het ankersegment. Alleen de gemeten
  vormen gaan door: `1.`, `1.1.`, `1.1.1.`, `2)` en `d)` (`ONDERDEELNUMMER`), als eerste kind,
  zonder kop ernaast en met een P of LIST erna. Een andere vorm (`(1)` leest in het profiel als
  overweging), een tabel of inclusie als eerste inhoud, of een nummer zonder tekst blijft een
  weigering. Het nummer van een onderdeelkop wordt uit de bron gelezen (`_plat_bron`), niet met
  een tweede `inline()`: die schreef een noot in de kop dubbel (32013R0503, 32023L2225,
  32018L0100). Begint binnen één blok een tweede reeks bij 1, a of i terwijl dat anker al is
  uitgegeven, dan draagt die reeks `al<k>`, met dezelfde teller als losse punten. Voorbeelden:
  twee inleidende punten 1. en 2. en dan de categorieën 1.–6. in bijlage I bij 2008/1; de
  opsomming a), b) en dan `Titel A`, `Titel B` in 2025/2205. Een dubbel nummer midden in een reeks
  blijft een weigering. Onder één los punt (NP) delen de opsommingen één teller (bijlage V, punt 4
  van 2012/27). Een **inhoudsopgave** (`TOC`) wordt per `TOC.ITEM` één alinea `nummer tekst`,
  zonder kop, eenheid of lijst, in bronvolgorde en zonder opmaak. Ze staat als kind van een
  CONS.ANNEX `BIJLAGEN` in de geconsolideerde MDR, of in CONTENTS in 2005/66. De
  Publicatiebladversie van de MDR, waar dezelfde opgave als NP staat, leest daardoor regel voor
  regel gelijk. `ITEM.REF` (het bladzijdenummer) en `TOC.HD` (de kolomkoppen erboven) vallen als
  metadata weg (kb WP-20 en WP-42); één TOC vóór CONTENTS mag (32022H2510, kb WP-42); een TOC ná
  CONTENTS, twee TOC's of iets anders in een TOC.ITEM blijft een weigering. Gemeten gevolg: wetgeving 167 → 174 van 266, onder meer de MDR, de IVDR en de
  machineverordening.
  Een **inclusie** wordt niet altijd kaal aangeroepen zoals in 32026R1744
  (`<P><QUOT.S><INCL.ELEMENT/></QUOT.S></P>`). Gemeten in de meetlat: `<P><INCL.ELEMENT/></P>`
  zonder QUOT.S onder `Bijlage IV wordt vervangen door:` (32013R0390),
  `<QUOT.S><INCL.ELEMENT/></QUOT.S>` als kind van CONTENTS (32013D0287), en de aanhalingstekens in
  de aanroepende P: `<P><QUOT.START/><INCL.ELEMENT/><QUOT.END/>.</P>` (32018D0187, 32020D1402),
  ook met twee inclusies tussen één paar tekens (32018L0100) of met het paar over twee P's
  verdeeld (32019L0114). `_inclusie_in` herkent die vormen; `geciteerde_inclusies` schrijft elke
  inclusie precies één keer als blok op haar plek, zet het openingsteken vóór het eerste blok en
  het sluitteken met de staart (`”.`) achter het laatste, en neemt het teken uit `CODE`
  (`AANHALING`: 201E „, 201C “, 201D ”; een andere code weigert). Is dat blok een tabel, dan staat
  het teken als eigen alinea. Tekst naast de inclusie, of een staart met woorden, blijft 'midden
  in een zin'. Een `NOTE.ID` is uniek per bestand, niet per zip: binnen een inclusie krijgt de
  nootsleutel de bestandsnaam als voorvoegsel (`nootruimte`), anders kregen de vier `E0001`'s van
  32019L0114 allemaal `(1)`. Een geconsolideerde tekst noemt haar afbeeldingen in
  `CONS.DOC/BIB.INSTANCE/INCLUSIONS`, niet onder de wortel (Brussel I bis 02012R1215-20150226:
  zeven certificaten; CRR 02013R0575-20270101: 167). Bij het Hof mag een zip naast de XML alleen
  bestanden bevatten die de uitspraak als `<P><INCL.ELEMENT TYPE="TIFF"/></P>` aanroept
  (62012TJ0235, Żubrówka); die worden weggelaten met een waarschuwing en
  `afbeeldingen_weggelaten`, net als bij wetgeving. Een bestand dat niet wordt aangeroepen, een
  aanroep zonder bestand, een afbeelding midden in een zin of met IMG.CNT blijft een weigering.
  De **woordcontrole** weigerde op 23 september 2026 negen documenten. Vier daarvan waren echte
  fouten van de omzetter. Een NP in een tabelcel droeg na zijn TXT nog P's of een geneste lijst,
  en die tekst viel stil weg (`CAS-nr.` onder `1. Kwik` in de batterijverordening 32023R1542, een
  normenlijst i)–xxviii) in 32021D1402). `cel_tekst()` schrijft die rest nu in bronvolgorde in
  dezelfde cel. Een enkele ongenummerde overweging (`<CONSID><P>` zonder NP, 32011R1042) stond er
  twee keer in. Ze komt nu één keer en telt als overweging zonder anker, want het profiel herkent
  een overweging aan haar nummer. De kop van een bijlagedeel ging twee keer door `inline()`, voor
  de tekst en voor het nummer, waardoor een noot in die kop twee definities kreeg (bijlage II van
  32023L2225); het nummer komt nu uit `_plat_bron` (zie hierboven). Twee reeksen i)–iii) onder
  hetzelfde onderdeel (artikel 2, lid 2, onder h) van 32023L2225) delen één teller, zodat de
  tweede `al2` krijgt. De andere vijf zijn géén fout van de omzetter: hun Markdown is gelijk aan
  de authentieke PDF. Het gaat om `19 augustus 2015inzake` (32021L2167), `27 april
  2016betreffende` (de noten van 2024/1183, en daarmee de geconsolideerde eIDAS), `8,9Z-MA4`
  (32007L0011), en MiFIR artikel 53, waar een `NOTE.REF` de tekst van een eerdere noot herhaalt
  terwijl de druk die noot één keer afdrukt, met twee `(*)`. Hier wijkt de brontelling
  `_plat_bron` af: ze zet om elk element behalve `HT` een woordgrens en telt de herhaalde
  nootinhoud mee. Voor alle vijf heeft de gebruiker besloten dat de brontelling meebeweegt (zie hieronder). De
  omzetter corrigeert de bron niet.
  Een **eigen bijlage die alleen een inclusie draagt** (`<CONTENTS><INCL.ELEMENT TYPE="FORMEX.DOC"/>`,
  zonder QUOT.S: bijlage V, VI en VII van eIDAS 2, 32024R1183) is geciteerde tekst als de inclusie
  zelf met een aanhalingsteken begint (`“BIJLAGE V`, `begint_met_aanhaling()`); dan gaat ze door
  `geciteerde_inclusies()`. Zonder dat teken blijft een losse inclusie een weigering.
  **Een datum of getal dat de bron aan een woord vastschrijft blijft vast, met een melding.** De
  brontelling van de woordcontrole (`_plat_bron`) zette om elk inline element behalve `HT` een
  woordgrens, maar de bron schrijft soms `27 april 2016</DATE>betreffende` (de noten van
  2024/1183, en daarmee de geconsolideerde eIDAS), `19 augustus 2015</DATE>inzake` (32021L2167)
  en `8,9</FT>Z-MA4` (32007L0011), en de authentieke PDF drukt ze net zo. Op besluit van de
  gebruiker (23 september 2026) volgt de telling daar de bron: `DATE` en `FT` zijn, net als `HT`,
  geen woordgrens (`AANEEN_IN_BRON`). De omzetter corrigeert de bron niet, maar `let_op_aaneen()`
  en `meld_aaneen()` zetten elk zo'n woord (`'2016betreffende'`) als waarschuwing in de herkomst,
  zoals bij de dubbele `d)` in de AVG. Staat er in de bron wél een spatie, dan staat die in
  `.tail` en telt ze gewoon mee.
  **Een nootverwijzing die de tekst van haar noot herhaalt** (`NOTE NOTE.REF=…` mét inhoud: MiFIR
  600/2014 artikel 53, punt 3, de geconsolideerde MiFIR, 2011/83) krijgt geen tweede definitie: de
  druk zet de noot één keer, met twee verwijzingen, en zo doet de omzetter het al. Op besluit van
  de gebruiker (23 september 2026) telt `_plat_bron` die herhaling niet mee, en `noot()` weigert
  als ze woord voor woord afwijkt van de noot waarnaar ze verwijst, of als die noot op dat punt nog
  niet bekend is (`nootinhoud`).
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
