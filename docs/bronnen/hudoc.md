# HUDOC (EHRM)

Verplaatst uit `CLAUDE.md` (WP-60, 29 september 2026), tekst ongewijzigd. `CLAUDE.md` bevat de
architectuur en verwijst hiernaartoe.

- **HUDOC** (sinds 21 september 2026 via de DOCX): de tekst komt uit
  `hudoc.echr.coe.int/app/conversion/docx/?library=ECHR&id={itemid}&filename={itemid}.docx`,
  de metadata uit de zoek-API. Die is kieskeurig: `…/app/query/results` met `query`, `select`
  (komma-gescheiden, kleine letters), `sort`, `start`, `length`, `rankingmodelid=11111_Ranking` en
  `facetquery` (laat er één weg en je krijgt 404), én een browser-`User-Agent` (de standaard van
  urllib krijgt 403). 403/429 is een beperking op het aantal verzoeken en geen "niet gevonden":
  `hudoc._get` herhaalt één keer na vier seconden en meldt het daarna als storing. Eén ECLI →
  meerdere documenten (EN=`HEJUD`, FR=`HFJUD`, vertalingen, samenvattingen); alleen het Engelse
  origineel wordt ondersteund, de rest is een weigering met de soort erbij. `id` is de ECLI uit
  de API, geen slug. Sommige uitspraken geven bij het Word-bestand HTTP 500 (gemeten, ook recente
  zoals `001-160044`); dat is een weigering met die reden, geen terugval op de HTML-body.
  `hudoc_docx.py` leest `word/document.xml` met `zipfile` en `xml.etree`: koppen uit de stijl
  (`KOP`-tabel, allemaal `##`), randnummer `N.` plus **één gewone spatie** (de bron heeft twee
  harde spaties; het profiel herkent het randnummer alleen zo), voetnoten native en genummerd in
  de volgorde van hun verwijzing, velden met hun opgeslagen resultaat, en een gate tegen een
  regelbestand (minder dan 50% van de alinea's eindigt op een leesteken). Wat het weigert:
  een stijl zonder eigen behandeling, Word-autonummering die geen opsommingsteken is (het
  nummer staat dan niet in de tekst; gemeten in 3 van 35, waaronder Big Brother Watch),
  Franse uitspraken, beslissingen en samenvattingen. De zelfcontrole telt de woorden in de bron
  met een eigen doorloop en eist gelijke woordverzamelingen en aaneengesloten bladalinea's.
  Sinds kb WP-43 leest `docx.Teller` een `numFmt` in `mc:AlternateContent` als de `mc:Choice`
  (`custom`, `α, β, γ, ...`: de `(α)`-koppen van Big Brother Watch), draagt een `w:sdt` in een alinea
  zijn tekst, is `Symbol F069` een ι en een lege `ECHRPlaceholder` niets.
  **Cloudflare en de lokale route** (25 september 2026, T2-F5 in de foutlog van de
  kennisbank): HUDOC geeft de Python-client nu elke keer 403 met `server: cloudflare` en de
  wachtpagina "Just a moment..."; curl met dezelfde User-Agent kreeg 200, dus het is een
  botcontrole op de client (vermoedelijk de TLS-handdruk) en geen verzoeklimiet. `_botcontrole()`
  meldt dat apart ("geen verzoeklimiet, opnieuw proberen helpt niet") in plaats van "probeer
  het over een minuut". De controle wordt bewust **niet** omzeild. De gebruiker downloadt de
  Word-bestanden en het zoekresultaat zelf in de browser; `kb_fetch --hudoc-map` zet ze om via
  `hudoc.uit_bestand()` → `omzetten_record()`, precies het deel van `fetch()` na het ophalen,
  dus dezelfde Markdown en hetzelfde bronbewijs. Wat de herkomst anders zegt: `requested_url`
  is de DOCX-URL, `fetched_at` de wijzigingstijd van het bestand, een extra waarschuwing, en
  `extra.handmatig` (bestand, recordbestand en zijn sha256). Proef op de twaalf arresten uit
  de jurisprudentietest: vijf bundels, vijf weigeringen op `JuHIRoman` met Word-autonummering
  en twee op een onbekende stijl (`Header`, `Default`).
