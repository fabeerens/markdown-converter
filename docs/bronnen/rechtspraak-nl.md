# Rechtspraak.nl

Verplaatst uit `CLAUDE.md` (WP-60, 29 september 2026), tekst ongewijzigd. `CLAUDE.md` bevat de
architectuur en verwijst hiernaartoe.

- **Rechtspraak.nl**: `https://data.rechtspraak.nl/uitspraken/content?id={ECLI}` geeft schone XML
  (`<uitspraak>` met `section`/`title`/`parablock`/`para`), en die XML **is** het bronbewijs:
  `record_source(..., source_format="rechtspraak-xml")`, met een `Herkomst` op de ECLI en een
  kb-bundel. Wat de route bewust niet doet: `<emphasis>` levert geen `**`/`*` op (een kop als
  `**De beslissing**` matcht `SECTION_ANCHORS` aan de kb-kant niet meer) en kopniveaus volgen de
  nesting van de bron. Inhoudsafbeeldingen (`imagedata` met `depth` > 2 — gemeten over 300
  uitspraken is de verdeling bimodaal: 66 spacers van 1–2 pixels tegen 28 foto's van 16 pixels
  en hoger) worden niet overgenomen; de conversie gaat door met een waarschuwing en legt aantal,
  afmetingen en bron-id als `afbeeldingen_weggelaten` in het zijbestand vast. Wat ze nog weigert
  in plaats van raadt: een element met tekst zonder eigen behandeling, een tabel die niet
  rechthoekig te maken is, en een ECLI die niet is wie hij zegt
  (`dcterms:identifier` moet de gevraagde ECLI zijn). Noten worden native: `<footnote-ref
  linkend>` → `[^n]`, `<footnote label>` → `[^n]: …`. Genummerde lijsten (`orderedlist`) krijgen
  de tekens die `numeration` noemt; die gegenereerde markers worden apart geteld, zodat de
  woordcontrole ze niet voor brontekst aanziet — net als de kopieën van een overspannen
  tabelcel.
