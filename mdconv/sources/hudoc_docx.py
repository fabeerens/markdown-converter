"""Uitspraken van het EHRM uit het Word-bestand van HUDOC.

HUDOC bewaart elke uitspraak als DOCX, en dat bestand draagt wat een PDF- of HTML-
afgeleide kwijtraakt: koppen als **stijl** (`JuHHead`, `ECHRHeading2`), echte
voetnoten in `word/footnotes.xml`, en tabellen als `w:tbl`. De raw-vorm blijft die
waar `md-clean-jurisprudentie` (variant `ehrm`) al op leunt (AGENTS.md, regel 3):

- de titel als `# CASE OF X v. Y`; alle koppen als `##`, want het profiel herstelt
  de diepte niet en de bron toont die bij de `Ju*`-stijlen alleen via de naam;
- een randnummer als `1.` plus **één gewone spatie**. In de DOCX staat het als `1.`
  plus twee harde spaties, en daar herkent het profiel geen randnummer aan; het
  verschil is witruimte en verandert geen woord;
- voetnoten native (`[^1]` en `[^1]: …`), doorgenummerd in de volgorde van de
  verwijzingen zoals Word ze telt.

**De lezer zelf staat in `docx.py`.** Dit bestand is de HUDOC-stijlkaart daarbovenop: welke
stijl een kop, een alinea of een inhoudsopgaveregel is, en dat een randnummer met twee harde
spaties naar één gewone spatie gaat. Het gedrag is niet veranderd; `tests/test_hudoc_docx.py`
en de drie gouden EHRM-bestanden (byte voor byte) zijn de regressie.

**Wat de bron zegt en wat niet.** Een alinea zonder stijl (`Normal`) is een gewone
alinea; dat is geen structuurverlies maar de vorm van het lichaam bij Grote-Kamer-
uitspraken en van de omslag bij vrijwel alle andere. De omslag draagt de titel soms
over twee regels (`CASE OF BIG BROTHER WATCH AND OTHERS` / `v. THE UNITED KINGDOM`);
de titel als kop komt daarom uit de HUDOC-record (`docname`), niet uit een patroon, en
zijn woorden tellen als gegenereerd zodat de omslag ongemoeid blijft.

Wat deze route **weigert** in plaats van raadt: een stijl zonder eigen behandeling
(de tabel hieronder is de volledige lijst die is gemeten), samenvattingen (`Su*`),
automatische nummering die geen opsommingsteken is (het nummer staat dan niet in de
tekst), een symbool dat hier niet bekend is, een bijgehouden wijziging met tekst,
een eindnoot, een samengevoegde tabelcel, en een bron waarvan de tekst na omzetting
niet meer woord voor woord klopt.
"""

from __future__ import annotations

from . import docx as _docx
from .docx import Kaart

# Wat elke gemeten stijl is. Gemeten over 35 uitspraken uit 1962 tot 2026, drie
# sjablonen door elkaar: `Ju*` (de oudere en de Grote-Kamer-koppen), `ECHR*` (het
# sjabloon vanaf 2015), `Opi*` (afwijkende meningen) en `Dec*`. Een stijl die hier niet
# staat is een weigering: dan wordt de tabel eerst aangevuld, na een meting.
KOP = {
    "JuHHead", "JuHIRoman", "JuHIroman", "JuHA", "JuH1", "JuHa0", "JuHi", "JuHalpha", "JuH",
    "JuHArticle", "ECHRHeading1", "ECHRHeading2", "ECHRHeading3", "ECHRHeading4",
    "ECHRHeading5", "ECHRHeading6", "ECHRHeading7", "ECHRTitle1", "ECHRTitleCentre1",
    "ECHRTitleCentre3", "OpiHHead", "OpiHA0", "OpiHa0", "OpiHA", "OpiHi", "OpiH1", "DecHTitle",
}
ALINEA = {
    "Normal", "JuPara", "JuParaLast", "jupara", "jupara0", "ECHRPara", "ECHRParaSpaced",
    "ECHRParaQuote", "JuQuot", "JuQuotSub", "JuQuotList", "JuList", "JuLista", "JuListi",
    "JuParaSub", "JuCourt", "JuCase", "jucase0", "JuJudges", "JuSigned", "JuInitialled",
    "ECHRDecisionBody", "DecHCase", "OpiPara", "OpiParaSub", "OpiQuot", "OpiTranslation",
    "ECHRCoverTitle4", "JuTitle", "ListParagraph",
    # Gemeten in test 2 van 25 september 2026 (kb WP-20, T2-F19): `Header` is in Rotaru
    # (001-58586) één lege alinea; `Default` zijn in Copland (001-79996) twee alinea's van
    # het lichaam met een randnummer (`26.  The “data protection principles” …`).
    "Header", "Default",
    # López Ribalda (001-197098): `Title4` (`_Title_4`, op JuPara) is de regel met de
    # klachtnummers onder de titel, `(Applications nos. 1874/13 and 8567/13)`.
    "Title4",
    # Centrum för Rättvisa (001-210078): `Jupara0` (`Ju para`, op Normal) is alinea 22 van het
    # lichaam, met randnummer; dezelfde rol als `jupara0`, andere schrijfwijze (kb WP-43).
    "Jupara0",
}
# `TOC6`: de inhoudsopgave van Hurbain (001-225814) gaat zes niveaus diep (kb WP-20).
TOC = {"TOC1": 0, "TOC2": 1, "TOC3": 2, "TOC4": 3, "TOC5": 4, "TOC6": 5}
# Alleen deze alinea's dragen een randnummer van het lichaam. Een citaat, een lijst en
# een afwijkende mening houden hun nummering zoals de bron haar schrijft.
RANDNUMMER = {"Normal", "JuPara", "JuParaLast", "jupara", "jupara0", "Jupara0", "ECHRPara", "Default"}
WEIGER_VOORVOEGSEL = ("Su",)


# De naam waaronder de lezer zich meldt in foutmeldingen; die tekst stond hier al.
NAAM = "HUDOC-uitspraak"

# De lezer stond tot september 2026 in dit bestand. Tests en aanroepers die hem hier zoeken
# vinden dezelfde klasse.
Docx = _docx.Docx
_Lezer = _docx.Lezer


# `ECHRPlaceholder` (`_Placeholder`, op `JuSigned`, witte tekst) is in Podchasov (001-230854) en
# NOS (001-249690) één lege alinea onder de ondertekening (kb WP-43). Alleen leeg is ze niets;
# met tekst zou witte, onzichtbare tekst zichtbaar worden, en dat blijft een weigering.
LEEG_TOEGESTAAN = {"ECHRPlaceholder"}


def _soort(docx, p, stijl: str) -> str | None:
    if stijl in LEEG_TOEGESTAAN:
        tekst = "".join(t.text or "" for t in p.iter(_docx.W + "t"))
        return "alinea" if not tekst.strip() and p.find(f".//{_docx.W}sym") is None else None
    if stijl in KOP:
        return "kop"
    if stijl in TOC:
        return "toc"
    if stijl in ALINEA:
        return "alinea"
    return None


def _weiger(stijl: str) -> str | None:
    if stijl.startswith(WEIGER_VOORVOEGSEL):
        return f"de stijl {stijl} hoort bij een samenvatting van rechtspraak, geen uitspraak"
    return None


# Alle koppen zijn `##`: het profiel herstelt de diepte niet en de bron toont die bij de
# `Ju*`-stijlen alleen via de naam.
HUDOC_KAART = Kaart(naam=NAAM, classificeer=_soort, kopprefix=lambda docx, p, stijl: "## ",
                    toc_niveau=lambda stijl: TOC[stijl], randnummer=frozenset(RANDNUMMER),
                    weiger=_weiger, regelbestand=True)


def omzetten(data: bytes, docname: str) -> tuple[str, dict]:
    """De uitspraak als Markdown, plus wat de herkomst erover vastlegt.

    `docname` is de titel uit de HUDOC-record (`CASE OF X v. Y`); zie de moduledocstring.
    """
    return _docx.omzetten(data, HUDOC_KAART, titel=docname)
