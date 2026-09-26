# Wijzigingen — markdown converter

Nieuwste bovenaan. De inhoudelijke uitleg staat in `CLAUDE.md`; hier alleen wat er
veranderde en waarom.

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
