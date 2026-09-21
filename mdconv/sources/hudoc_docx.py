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
}
TOC = {"TOC1": 0, "TOC2": 1, "TOC3": 2, "TOC4": 3, "TOC5": 4}
# Alleen deze alinea's dragen een randnummer van het lichaam. Een citaat, een lijst en
# een afwijkende mening houden hun nummering zoals de bron haar schrijft.
RANDNUMMER = {"Normal", "JuPara", "JuParaLast", "jupara", "jupara0", "ECHRPara"}
WEIGER_VOORVOEGSEL = ("Su",)


# De naam waaronder de lezer zich meldt in foutmeldingen; die tekst stond hier al.
NAAM = "HUDOC-uitspraak"

# De lezer stond tot september 2026 in dit bestand. Tests en aanroepers die hem hier zoeken
# vinden dezelfde klasse.
Docx = _docx.Docx
_Lezer = _docx.Lezer


def _soort(docx, p, stijl: str) -> str | None:
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
