# Hof van Justitie en Gerecht

Verplaatst uit `CLAUDE.md` (WP-60, 29 september 2026), tekst ongewijzigd. `CLAUDE.md` bevat de
architectuur en verwijst hiernaartoe.

- **Hof van Justitie en Gerecht** (sinds 21 september 2026 via Formex): `eurlex._fetch_hof` vraagt
  `application/zip;mtype=fmx4` met `Accept-Language: nld` op de ECLI-resource of het CELEX-nummer
  en geeft de zip aan `formex_hof.omzetten`. Een arrest dat nog geen Formex heeft levert 404 en
  wordt geweigerd, met de reden erbij; er is bewust geen terugval op de Curia-HTML. Rechtspraak
  heeft een andere Formex-vorm dan wetgeving: geen `.doc.xml`, één XML met wortel `JUDGMENT` of
  `ORDER`. De raw-vorm is die van het profiel: kale sectieregels, `NO.P` plus één spatie voor een
  overweging, `NO.P` plus **drie harde spaties** voor een geciteerd punt (binnen `QUOT.S` of een
  lijst — zonder dat onderscheid krijgt een geciteerde bijlage `ro`-ankers), het dictum uit
  `JURISDICTION/INTRO` als alinea's, de procestaalnoot als `[^procestaal]` met een definitie. De
  zelfcontrole is dezelfde als bij `hudoc_docx`. Weigeringen: `CONCLUSION`, `OPINION`,
  `JUDGMENT.NP`, `CASE`, `REPORT.HEARING`, `SUMMARY.*`, een zip met meer dan één XML of met een
  bestand dat de uitspraak niet als afbeelding aanroept (een aangeroepen TIFF wordt weggelaten
  met een melding), een onbekende aanhalingscode en `DLIST`. Gemeten op 143 Cellar-zips: 73 arresten of
  beschikkingen in één XML, 71 omgezet.
