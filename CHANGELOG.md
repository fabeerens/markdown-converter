# Wijzigingen — markdown converter

Nieuwste bovenaan. De inhoudelijke uitleg staat in `CLAUDE.md`; hier alleen wat er
veranderde en waarom.

## 29 september 2026 — BWB-constructies die wegvielen of weigerden (kb WP-43, deel A)

- **BWB-XML (`bwb_xml.py`)**, gevonden door de bronlezing van kb WP-30 en test 6 (T6-F3, T6-F4):
  - `<afk>` en `<organisatie>` zijn gewone inline tekst (BWBR0042755 weigerde op `inline:afk`);
  - de lijsttekens `−` (U+2212), `○` en `□` zijn ongemarkeerd en geven geen anker (BWBR0043632,
    BWBR0049314 weigerden op `annex-1-`);
  - een `<sup>` met cijfers is alleen een nootmarker als dezelfde bijlage er een definitie voor heeft,
    anders een macht `^n^` (Archiefregeling: `kg/m[^3]` wees naar niets);
  - `<table><title>` staat als alinea boven de tabel, `<kop><subtitel>` als alinea onder de kop;
    een ander kind van `<table>` of `<kop>` is een weigering in plaats van stil verlies;
  - een ondertekening is één regel, met de losse tekst ertussen (`De Minister van Justitie, J. P. H.
    Donner`); `<naam>` zet een spatie tussen voornaam en achternaam;
  - witruimte aan de rand van `<nadruk>` blijft, buiten de markering (`*zorgverlener* die`).
- Gemeten over de 79 BWB-bronnen in kb `raw/`: ankers gelijk; op woordniveau verandert alleen de
  uitvoer van de acht documenten van het WP, bij de andere 69 alleen de regelindeling en de komma's
  van de ondertekening. De Formex-meetlat is ongewijzigd (287/307). Tests: 613 (één
  karakteriseringstest, de ondertekening per kind, bewust aangepast).

## 29 september 2026 — aanbevelingen met punten `(1)`, `a)` en `1.1.`, en tabelnoten (kb WP-42)

- **Formex, het dispositief van een aanbeveling (`formex_xml.dispositief()`)**, in de raw-vorm van
  `md-clean-eurlex/references/patronen.md` §9 (T4-F5):
  - een punt `(1)` of `1.1.` houdt zijn gedrukte markering, met drie harde spaties erachter
    (`pt-1`, `pt-1-1`); in een bijlage blijft `(1)` één spatie;
  - een `a)`-lijst direct onder een groepstitel wordt `a) tekst`, anker `pt-a`, een nieuwe reeks
    `pt-al2-a`; `I.` als punt blijft een weigering.
- **Inhoudsopgave in een bijlage**: `TOC.HD` is metadata, zoals `ITEM.REF`; één TOC vóór CONTENTS mag.
- **Tabelnoten** (T4-F3): de noten van `GR.NOTES` krijgen hun nummer vóór de rijen, in bronvolgorde,
  zoals het Publicatieblad en de kb-lezer (32018R1724).
- Gemeten: de zeven aanbevelingen van test 4 en van de steekproef van kb WP-25 zetten alle zeven om; over
  de 93 Formex-bronnen in kb `raw/` verandert alleen hun uitvoer en die van 32018R1724. Meetlat 287/307,
  andere uitvoer bij 02018R1724-20260520 en 32019R0089 (alleen nootnummers). Tests: 596.

## 29 september 2026 — de OP-XML-woordenschat van Kamerstukken (kb WP-41)

- **OP-XML (`officiele_bekendmakingen.py`)**, in de raw-vorm van `md-clean-documenten/references/
  patronen.md` §4 (T6-F5, T6-F6):
  - `<datumtekst>` wordt een gewone regel onder de titelregel (`Ontvangen 5 maart 2025`);
  - een verwerkingsinstructie (`<?xpp ep?>`, `<?xpp witregel?>`) in een tabel, een rij of een cel is
    onzichtbaar; tekst erachter buiten een cel is een weigering;
  - `<plaatje>`: de afbeelding niet overnemen, wel vastleggen (`afbeeldingen_weggelaten`, een
    waarschuwing), een `bijschrift` is tekst; dezelfde afspraak als `bwb_xml.plaatje()`;
  - `<kop><label>Hoofdstuk</label><nr>1.</nr>…` wordt `## Hoofdstuk 1. Inleiding`; een label ná het
    nummer of zonder nummer blijft een weigering;
  - een stuk in meer `<dossier>`s: elk paar in bronvolgorde in de titelregel. De metadata splitst
    `22112;32761` in `dossiernummers` en houdt het eerste als `dossiernummer`; ongesplitst was de
    identiteit `kst-2211232761-nr-4304`. Noemt het publicatie-id een ander dossier dan het eerste,
    dan is dat een weigering;
  - een lege `<sup/>` laat niets achter; een `sup` of `inf` met tekst blijft een weigering (T6-F3).
- Gemeten: de tien Kamerstukken van test 6 die weigerden, zetten alle tien om (`kb_fetch`); de meetlat
  (Formex) is ongewijzigd, 287/307, geen verschil. Tests: 588.

## 26 september 2026 — een handeling zonder artikelen: de aanbeveling (kb WP-25)

- **Formex (`formex_xml.py`)**: een `ENACTING.TERMS` zonder eigen `ARTICLE` gaat door
  `dispositief()` in plaats van te weigeren op `bepalingen:GR.SEQ`. Een groepstitel wordt een H2
  zonder eenheid (`## 1. TOEPASSINGSGEBIED EN DOELSTELLINGEN`), elk punt — los of uit een
  `LIST` — `n.` plus drie harde spaties via de NP-tak van `inhoud()` met basis `pt`
  (eenheden `pt-<n>`, onderdelen `pt-<n>-<letter>`, een herstart `pt-al2-<n>`), de vorm die
  het eurlex-profiel leest (`md-clean-eurlex/references/patronen.md` §9). Een markering die
  dat profiel niet kent (`(1)`, `1.1.`, `I.` als punt), een groep zonder titel of met een
  `NO.GR.SEQ`, en een genummerd punt naast artikelen blijven een weigering. Gemeten op de 30
  aanbevelingen van kb WP-13 plus 32024H1101: 28 omgezet, 3 geweigerd (twee keer `(1)`, één
  `TOC.HD`); meetlat ongewijzigd. Tests: 582.

## 25 september 2026 — elf klassen uit de drie tests van de kennisbank (kb WP-20)

Per klasse één commit; de meting staat in `~/Documents/kb/foutlog/2026-09-25-WP-20-converter.md`.

- **Formex (`formex_xml.py`)**: het notenblok van een geconsolideerde handeling zonder `FINAL`
  komt vóór de eerste bijlage, en `bijlage()` weigert als er nog noten wachten (T1-F3); een
  opsomming in een `P` binnen een `DEFINITION` wordt blokken (T1-F6, 2019/1150 art. 2 punt 2);
  `ITEM.REF` is metadata (het bladzijdenummer in een inhoudsopgave), de titel van een `TOC` een
  alinea, `CAPTION` het bijschrift van een afbeelding op de plek van het beeld, `LETTER` een
  brief in een bijlage, een adresblok als enige inhoud van een `P` een blok, en een romeins
  onderdeelnummer met deelnummer (`IV.1`) draagt dat deelnummer in het anker (T1-F16).
- **HvJ (`eurlex._fetch_hof`)**: een arrest zonder ECLI in zijn Formex krijgt hem uit de
  Cellar-metadata (`cdm:case-law_ecli`), met `extra.ecli_herkomst` (T2-F14, Satamedia).
- **HUDOC (`docx.Teller`, `hudoc_docx.py`)**: de automatische nummering van koppen en lijsten
  wordt nagerekend zoals Word haar toont, met een reekscontrole; de stijlen `Header`,
  `Default`, `Title4` en `TOC6` (T2-F19 en de `JuHIRoman`-klasse).
- **Rechtspraak (`rechtspraak.py`)**: een spatie tussen `<nr>` en de titel (T2-F17).
- **BWB (`bwb_xml.py`)**: de onderdelen van een nog niet geldend lid ingesprongen onder het lid
  (T3-F1, besluit 8); het artikelnummer uit het label als `<nr>` ontbreekt, en een leeg
  ankersegment van een artikel, lid of onderdeel is een weigering (T3-F3, Wet RO); een
  `plaatje` valt weg met een melding en `afbeeldingen_weggelaten` (T3-F16, Wet BIG, Opiumwet).

## 25 september 2026 — HUDOC uit een lokale map (`kb_fetch --hudoc-map`)

HUDOC houdt de Python-client tegen met een Cloudflare-botcontrole (T2-F5 in
`~/Documents/kb/foutlog/Fouten_test_25_09.md`); die wordt niet omzeild. Zelf in de browser
gedownloade `<itemid>.docx` plus `hudoc-records.json` gaan nu door dezelfde omzetting als
online (`hudoc.omzetten_record()`) naar een gewone kennisbankbundel, met de herkomst
"handmatig gedownload" in het zijbestand. De botcontrole heeft een eigen melding in plaats
van "probeer het over een minuut opnieuw".
