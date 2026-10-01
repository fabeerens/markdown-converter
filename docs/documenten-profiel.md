# Documenten voor de kennisbank (profiel `documenten`)

Verplaatst uit `CLAUDE.md` (WP-60, 29 september 2026), tekst ongewijzigd. `CLAUDE.md` bevat de
architectuur en verwijst hiernaartoe.

## Documenten voor de kennisbank (profiel `documenten`)

Een geüpload bestand of een link naar een Kamerstuk komt als document in de kennisbank, met bronbewijs waar de bron een boom heeft. Wat de route doet hangt af van het formaat:

| Invoer | Route | Bewaarde bron (`source_format`) | Weigert bij |
|---|---|---|---|
| `kst-…` of link | `officiele_bekendmakingen.py` | `op-xml` | geen XML (404), ander worteldocument dan `kamerstuk`, een element zonder behandeling, een woordtelling die niet klopt |
| `.docx` | `docx.py` (`WORD_KAART`) | `docx` | stijl zonder herkomst naar `Normal`, automatische nummering, tekstvak/afbeelding, eindnoot, bijgehouden wijziging, samengevoegde cel, tabel in tabel |
| `.html`/`.htm` | `html_document.py` | `html` (de gekozen container, niet de hele pagina) | geen eenduidige `<main>`/`<article>`/`[role=main]`, samengevoegde cellen zonder sluitend raster |
| `.pdf` | pdf-inspector | `pdf` (alleen als herkomst; de kennisbank herberekent er niets uit) | geen tekstlaag (scan) |

**Het documentnummer komt nooit uit de bestandsnaam.** `from_file(..., document_id=…)` en de API-velden `document_id` (form-veld bij `/api/convert/file`, JSON-veld bij `/api/convert/file-url`) leveren de slug (`identifiers.md` van de kennisbank: `a-z`, `0-9`, `-`). Zonder slug blijft de download een los `.md`; bij een Kamerstuk levert de bron de slug (`kst-<dossier>-nr-<n>` uit `metadata.xml`). **De voorkant heeft er (nog) geen veld voor** (AGENTS.md, regel 5): tot iemand daar om vraagt, is de API het enige pad.

**Zonder documentnummer valt een weigering terug op MarkItDown, zichtbaar.** De strikte route mag een pagina met twee `<article>`s of een Word-bestand met automatische nummering niet zomaar laten stoppen met werken voor wie alleen een `.md` wil. `sources._terugval()` zet de reden in `Document.warnings` en legt niets vast als bron. Met een documentnummer is de weigering het antwoord.

**Noten zijn native** (`[^1]` en `[^1]: …`), zoals bij de rechtspraak, en niet de `(n)`-vorm die een eerste lezing van AGENTS.md regel 3 suggereert: het documenten-profiel koppelt `(n)` alleen met een aangewezen notenblok, terwijl de kern native noten al aankan. Een tabelnoot in de KOOP-XML heet `t<tabel>-<nr>` (elke tabel telt opnieuw bij 1).

Gemeten op 11 Kamerstukken van KOOP: 5 komen door (waaronder `kst-34851-3`, 65.815 woorden, 102 noten); de weigeringen zijn `box`, `datumtekst`, `voorstel-wet`, `aanhef`, `label` in een kop en `dossierref` in een alinea: vocabulaire dat niet is gemeten en dus niet wordt geraden. Sinds kb WP-13 (25 september 2026) zijn `dossierref` (de tekst blijft, zoals bij `extref`) en `nootref` (de marker van de noot waar `@refid` naar wijst, zonder tweede definitie; gemeten in `kst-36764-3`) gemeten. Sinds kb WP-41 (29 september 2026) ook `datumtekst` (een gewone regel onder de titel), een verwerkingsinstructie in een tabel (onzichtbaar), `plaatje` (niet overgenomen, wel gemeld in `afbeeldingen_weggelaten`), `label` vóór het nummer in een kop (`## Hoofdstuk 1. Inleiding`), een lege `<sup/>` en een stuk in meer dossiers (elk paar in de titelregel, `dossiernummers` in de metadata, de identiteit uit het eerste); gemeten op de tien Kamerstukken van test 6 die daarop weigerden. Een `sup` of `inf` met tekst blijft een weigering. Een `<staatsblad>` blijft een weigering: behalve het vocabulaire ontbreekt er een afgesproken slug. Van zeven echte Word-modelovereenkomsten (ARVODI van PIANOo, een model van NVRR) komt er geen enkele door: zes hebben een tekstvak (`mc:AlternateContent`), één automatische nummering. De EHRM-Word-bestanden komen wel door de generieke kaart (42 koppen tegenover 0 bij MarkItDown).
