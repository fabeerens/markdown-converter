# Meetlat (Formex)

Verplaatst uit `CLAUDE.md` (WP-60, 29 september 2026), tekst ongewijzigd. `CLAUDE.md` bevat de
architectuur en verwijst hiernaartoe.

## Meetlat (Formex): de dekking meten vóór de gebruiker het doet

`meetlat/` meet hoeveel van een vaste verzameling EU-documenten door de Formex-route komt:
`corpus.txt` (347 stuks: de eindtest, 79 belangrijke handelingen, 25 geconsolideerde
versies, en een aselecte steekproef van 153 handelingen en 64 uitspraken van het Hof),
de zips in `meetlat/cache/` (niet in git) en de uitkomst per document in
`basislijn.json` (wel in git).

```
.venv/bin/python meetlat/meetlat.py ophalen              # ontbrekende zips downloaden
.venv/bin/python meetlat/meetlat.py meten                # offline, vergelijkt met de basislijn
.venv/bin/python meetlat/meetlat.py meten -v             # met elke weigering en haar melding
.venv/bin/python meetlat/meetlat.py meten --bijwerken    # uitkomst wordt de nieuwe basislijn
```

- **Waarom.** De omzetter werd per incident gerepareerd en getest op de handvol zips die
  er lagen. Op 23 september 2026 gemeten kwam **48% van de wetgeving** door (127 van 266
  met een Formex), en van de uitspraken 40 van 41; de eindtest faalde op precies de zes
  documenten die de meetlat ook weigert. Elke nieuwe lijst raakte dus een nieuw gat.
- **`meten` raakt het netwerk nooit** en roept dezelfde functies aan als de tool
  (`eurlex._fetch_formex`, `eurlex._fetch_hof`), met een vervangen `net.documents()` die
  alleen uit de cache antwoordt. Omdat `_fetch_formex` een netwerkfout zelf afvangt en dan
  naar HTML gaat, telt een gemist cachebestand als `niet-in-cache`, nooit als "geen Formex".
- **Exitcode 1** bij een document dat in de basislijn doorkwam en nu geweigerd wordt, bij
  andere uitvoer voor een document dat doorkwam (sha256 van de Markdown), bij een crash of
  bij `niet-in-cache`. Een bewuste uitvoerwijziging leg je vast met `--bijwerken`; oud en
  nieuw staan dan in `meetlat/uitvoer/basis/` en `meetlat/uitvoer/laatste/`.
- **Oorzaken** worden gegroepeerd naar wat dezelfde reparatie vraagt (`oorzaak()`): een
  element en de context waarin het niet behandeld wordt (`element inline:DIVISION`), een
  inclusietype, en de woordcontrole in drie soorten (`tekst valt weg`, `woorden aan
  elkaar`, `tekst dubbel`).
- Alleen de Formex-tak wordt gemeten. Een handeling zonder Formex (vóór ± 2004) telt als
  `geen-formex`; in de tool gaat die naar de HTML-ladder.
