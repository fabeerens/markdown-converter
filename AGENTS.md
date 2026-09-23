# Werkafspraken voor agents — markdown converter

Geldt voor elke agent die in deze map werkt (Claude Code, Codex, wie dan ook).
**`CLAUDE.md` in deze map is de inhoudelijke documentatie: lees die eerst.** Ze
beschrijft de architectuur, elke bronroute en de redenen achter de keuzes. Dit
bestand zegt alleen hoe je hier werkt.

## Wat dit is

Een lokale Flask-tool die jurisprudentie, wetgeving en documenten omzet naar
Markdown, met een HTML-interface op `http://127.0.0.1:5001`. Deze kopie is een
**fork**: hij levert daarnaast de invoer voor de juridische kennisbank in
`~/Documents/kb`. De vier tabbladen en de bediening blijven zoals ze zijn; wat
verandert zit aan de achterkant.

## Regels

1. **Importeer nooit code uit `~/Documents/kb/md-clean-core`.** De kennisbank
   herbouwt het bronbewijs met haar eigen parser uit de bewaarde bronbytes. Twee
   onafhankelijke implementaties die het oneens zijn, wijzen de fout aan;
   gedeelde code maakt dat bewijs waardeloos. Gedeeld worden alleen het formaat
   en vaste testbestanden.
2. **Een XML-route schrijft de raw-vorm die het profiel van de kennisbank al
   aankan**, niet een mooiere vorm. Voor EUR-Lex: lid = `1.` plus drie harde
   spaties, overweging = `(1)` plus één spatie, nootdefinitie = `(1)` plus twee
   harde spaties, kale artikelkop met het opschrift op de regel eronder,
   titelregels in kapitalen. Zie `kb/md-clean-eurlex/references/patronen.md`.
3. **Bronspecifieke voorbewerking hoort in de bronmodule, niet in `render.py`.**
   Die module is bewust bronloos en wordt door alle HTML-routes gedeeld; er is
   een test die afdwingt dat bronklassenamen er niet in terechtkomen.
4. **Fail-closed blijft fail-closed.** `table_structure.normalize_data_tables`
   en `sources/wetten_footnotes.py` weigeren bij twijfel in plaats van te vullen
   of te raden. Nieuwe routes krijgen dezelfde weigeringen; versoepel er nooit
   een om een test groen te krijgen.
5. **De voorkant verandert niet** zonder dat de gebruiker daarom vraagt. Raakt
   een wijziging toch `templates/index.html` of `static/app.js`, houd het dan
   klein en zeg erbij waarom het onvermijdelijk was.

## Hoe je werkt

- **Tests zijn karakteriseringstests**: ze leggen het *bestaande* gedrag vast.
  Verandert er gedrag, dan verander je de test bewust en zeg je dat in de
  commit. Draai `.venv/bin/python -m pytest tests/ -q` (nu 409 tests) vóór
  je klaar bent.
- **Raak je een Formex-omzetter** (`formex_xml.py`, `formex_hof.py`,
  `xml_gedeeld.py` of de Formex-tak van `eurlex.py`), **draai dan de meetlat**:
  `.venv/bin/python meetlat/meetlat.py meten`. Geen document dat doorkwam mag
  nu geweigerd worden, en andere uitvoer leg je alleen vast met `--bijwerken`
  als je in de commit zegt waarom. Een reparatie begint bij de oorzaken die de
  meetlat telt, niet bij het ene document dat iemand tegenkwam; noem in de commit
  de doorlaat vóór en na (bv. "wetgeving 127/266 → 139/266").
- **Geen netwerk in tests.** Vervang `net.documents` met `monkeypatch`; zie
  `_fake_cellar` in `tests/test_characterisation.py`.
- **Lui laden blijft lui.** MarkItDown en pdf-inspector kosten honderden ms bij
  het importeren; die horen pas bij het eerste gebruik te laden.
- **Geen buildstap, geen Node.** De UI is platte HTML, CSS en JS.
- **Eén onderwerp per commit**, boodschap in het Nederlands en in de gebiedende
  wijs, met de reden erbij.

## Schrijfstijl

Nederlands, ook in commentaar, docstrings en foutmeldingen. Commentaar legt uit
**waarom** iets zo is — met de meting of het geval waar het vandaan komt — niet
wat de regel doet. `CLAUDE.md` is daar de maatstaf voor.

## Waar de kennisbank begint

De volgende stappen (de Formex-omzetter hierheen verhuizen, de download als zip
die in de kb-map past, wetten.nl via BWB-XML) staan uitgeschreven in
`~/Documents/kb/foutlog/2026-09-20-vervolgstappen.md`, met per opdracht de
bestanden, de controle en de bekende valkuilen. De kant-en-klare opdrachten
staan in `~/Documents/kb/foutlog/2026-09-20-prompts.md`.
