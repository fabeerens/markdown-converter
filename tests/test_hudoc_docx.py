"""De HUDOC-route: een arrest van het EHRM uit zijn Word-bestand.

Geen netwerk: `net.documents` wordt vervangen, net als bij `_fake_cellar` in
`test_characterisation.py`. De fixture is een klein, met de hand gebouwd DOCX met de
constructies die in 35 uitspraken (1962 tot 2026, gemeten op 21 september 2026) de
raw-vorm bepalen: een omslag zonder stijl, koppen als stijl, een randnummer met twee
harde spaties, een veld met een opgeslagen resultaat (`SEQ`), een voetnoot, een tabel,
een opsomming uit `numbering.xml`, een tab en een regelval tussen rechters.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from mdconv.errors import ConversionError
from mdconv.sources import from_link, hudoc, hudoc_docx

NBSP = " "
NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def p(tekst: str = "", stijl: str | None = None, inhoud: str | None = None, ppr: str = "") -> str:
    """Eén alinea: een stijl, en tekst of ruwe run-XML."""
    st = f'<w:pStyle w:val="{stijl}"/>' if stijl else ""
    pr = f"<w:pPr>{st}{ppr}</w:pPr>" if (st or ppr) else ""
    run = inhoud if inhoud is not None else f'<w:r><w:t xml:space="preserve">{tekst}</w:t></w:r>'
    return f"<w:p>{pr}{run}</w:p>"


def docx(body: str, *, noten: str = "", numbering: str = "") -> bytes:
    onderdelen = {
        "word/document.xml": f'<?xml version="1.0" encoding="UTF-8"?><w:document {NS}><w:body>{body}</w:body></w:document>',
        "word/styles.xml": f'<?xml version="1.0" encoding="UTF-8"?><w:styles {NS}/>',
        "[Content_Types].xml": "<Types/>",
    }
    if noten:
        onderdelen["word/footnotes.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8"?><w:footnotes {NS}>'
            '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
            f"{noten}</w:footnotes>")
    if numbering:
        onderdelen["word/numbering.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8"?><w:numbering {NS}>{numbering}</w:numbering>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for naam, inhoud in onderdelen.items():
            zf.writestr(naam, inhoud)
    return buf.getvalue()


VELD_SEQ = ('<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText xml:space="preserve"> SEQ level0 \\*arabic </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            '<w:r><w:t>3</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>')

VOETNOOT = '<w:r><w:footnoteReference w:id="2"/></w:r>'

NUMBERING_BULLET = (
    '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl></w:abstractNum>'
    '<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>'
    '<w:start w:val="1"/></w:lvl></w:abstractNum>'
    '<w:abstractNum w:abstractNumId="2"><w:lvl w:ilvl="0"><w:lvlText w:val="%1."/></w:lvl></w:abstractNum>'
    '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
    '<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>'
    '<w:num w:numId="3"><w:abstractNumId w:val="2"/></w:num>')

# De nummering van de koppen zoals HUDOC die sinds 2019 aan de stijlen hangt (López Ribalda,
# 001-197098): niveau 0 toont niets (JuHHead), 1 is romeins (JuHIRoman), 2 een letter (JuHA),
# 3 decimaal (JuH1). Een tweede `num` op dezelfde abstractNum met een startOverride herstart
# de reeks, zoals Big Brother Watch dat doet bij `I. RELEVANT DOMESTIC LAW`.
NUMBERING_KOPPEN = (
    '<w:abstractNum w:abstractNumId="15">'
    '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="none"/><w:lvlText w:val="%1"/></w:lvl>'
    '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="upperRoman"/><w:lvlText w:val="%2."/></w:lvl>'
    '<w:lvl w:ilvl="2"><w:start w:val="1"/><w:numFmt w:val="upperLetter"/><w:lvlText w:val="%3."/></w:lvl>'
    '<w:lvl w:ilvl="3"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%4."/></w:lvl>'
    '<w:lvl w:ilvl="4"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="(%5)"/></w:lvl>'
    '</w:abstractNum>'
    '<w:num w:numId="4"><w:abstractNumId w:val="15"/></w:num>'
    '<w:num w:numId="27"><w:abstractNumId w:val="15"/>'
    '<w:lvlOverride w:ilvl="1"><w:startOverride w:val="1"/></w:lvlOverride></w:num>'
    '<w:num w:numId="28"><w:abstractNumId w:val="15"/>'
    '<w:lvlOverride w:ilvl="1"><w:startOverride w:val="3"/></w:lvlOverride></w:num>')


def genummerd(tekst: str, stijl: str, num: str, ilvl: str) -> str:
    return p(tekst, stijl, ppr=f'<w:numPr><w:ilvl w:val="{ilvl}"/><w:numId w:val="{num}"/></w:numPr>')

BODY = "".join([
    p("FIFTH SECTION"),
    p("CASE OF DIMITROVA AND OTHERS v. BULGARIA"),
    p("JUDGMENT", "JuPara"),
    p("PROCEDURE", "JuHHead"),
    p(f"1.{NBSP}{NBSP}The case originated in an application.", "JuPara"),
    p(inhoud=f'<w:r><w:t>2.{NBSP}{NBSP}On 29 January 2009 the President</w:t></w:r>{VOETNOOT}<w:r><w:t xml:space="preserve"> decided.</w:t></w:r>',
      stijl="JuPara"),
    p(inhoud=f'<w:r><w:t xml:space="preserve">Paragraph </w:t></w:r>{VELD_SEQ}<w:r><w:t xml:space="preserve">. The applicants complained.</w:t></w:r>',
      stijl="JuPara"),
    p(f"I.{NBSP}{NBSP}THE CIRCUMSTANCES OF THE CASE", "JuHIRoman"),
    p(f"“1.{NBSP}{NBSP}Everyone has the right to respect.”", "JuQuot"),
    p("The Court decides:", "JuPara"),
    p(f"the first item", "JuPara", ppr='<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'),
    p(inhoud='<w:r><w:t>Peer Lorenzen, President,</w:t></w:r><w:r><w:br/></w:r><w:r><w:tab/><w:t>Karel Jungwiert,</w:t></w:r>',
      stijl="JuJudges"),
    p("FOR THESE REASONS, THE COURT", "JuHHead"),
    p(f"1.{NBSP}{NBSP}Declares the application admissible;", "JuList"),
    "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>No.</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Applicant</w:t></w:r></w:p></w:tc></w:tr>"
    "<w:tr><w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Ms Rayna</w:t></w:r></w:p></w:tc></w:tr></w:tbl>",
    p("Done in English, and notified in writing on 27 January 2011.", "JuParaLast"),
])

NOTEN = '<w:footnote w:id="2"><w:p><w:r><w:footnoteRef/></w:r><w:r><w:t xml:space="preserve"> See the Commission report.</w:t></w:r></w:p></w:footnote>'

DOCNAME = "CASE OF DIMITROVA AND OTHERS v. BULGARIA"


def omzet(body: str = BODY, **kw):
    return hudoc_docx.omzetten(docx(body, noten=kw.pop("noten", NOTEN), numbering=kw.pop("numbering", NUMBERING_BULLET)),
                               DOCNAME)


def test_de_rawvorm_is_die_van_het_profiel():
    markdown, meta = omzet()
    blokken = markdown.split("\n\n")

    # De titel als kop komt uit de HUDOC-record; de omslag staat er ongewijzigd onder.
    assert blokken[0] == "# CASE OF DIMITROVA AND OTHERS v. BULGARIA"
    assert blokken[1:4] == ["FIFTH SECTION", "CASE OF DIMITROVA AND OTHERS v. BULGARIA", "JUDGMENT"]
    # Koppen zijn stijlen en worden `##`, op één niveau.
    assert "## PROCEDURE" in blokken
    assert f"## I.{NBSP}{NBSP}THE CIRCUMSTANCES OF THE CASE" in blokken
    # Een randnummer: nummer, punt, één gewone spatie - het profiel herkent er de anker aan.
    assert "1. The case originated in an application." in blokken
    # Een veld draagt zijn opgeslagen resultaat, niet zijn instructie.
    assert "Paragraph 3. The applicants complained." in blokken
    assert not any("SEQ" in b for b in blokken)
    # Een citaat houdt zijn nummering met de twee harde spaties: het is geen randnummer.
    assert f"“1.{NBSP}{NBSP}Everyone has the right to respect.”" in blokken
    # Een opsomming uit numbering.xml met numFmt bullet.
    assert "- the first item" in blokken
    # Een lijst in het dictum houdt zijn nummer zoals de bron het schrijft.
    assert f"1.{NBSP}{NBSP}Declares the application admissible;" in blokken
    # Een tab is een spatie en een regelval een regeleinde: de rechters blijven één blok.
    assert "Peer Lorenzen, President,\nKarel Jungwiert," in blokken
    # Een tabel wordt een pipe-tabel.
    assert "| No. | Applicant |" in markdown and "| 1 | Ms Rayna |" in markdown
    # De voetnoot is native, met een echte definitie.
    assert "2. On 29 January 2009 the President[^1] decided." in blokken
    assert markdown.rstrip().endswith("[^1]: See the Commission report.")
    assert meta["randnummers"] == 2 and meta["noten"] == 1 and meta["tabellen"] == 1 and meta["velden"] == 1


def test_een_veld_dat_over_alineas_loopt_blijft_een_veld():
    # De inhoudsopgave is één veld met alle regels als resultaat.
    body = "".join([
        p(inhoud='<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> TOC \\o "1-3" </w:instrText></w:r>'
                 '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>PROCEDURE</w:t></w:r>', stijl="TOC1"),
        p(inhoud='<w:r><w:t>THE FACTS</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>', stijl="TOC1"),
        p(f"1.{NBSP}{NBSP}The case originated.", "JuPara"),
    ])
    markdown, _ = hudoc_docx.omzetten(docx(body), DOCNAME)
    assert "- PROCEDURE" in markdown and "- THE FACTS" in markdown
    assert "TOC" not in markdown.replace("TOC1", "")


@pytest.mark.parametrize("wijziging, reden", [
    (lambda b: b + p("tekst", "OnbekendeStijl"), "stijlen zonder eigen behandeling: OnbekendeStijl"),
    (lambda b: b + p("SUMMARY", "SuSummary"), "samenvatting"),
    # WP-20: een decimale nummering wordt nagerekend (`docx.Teller`); wat niet gemeten is,
    # weigert nog: een vorm zonder numFmt, een niet-aansluitende reeks, een onbekende num.
    (lambda b: b + p("tekst", "JuPara", ppr='<w:numPr><w:ilvl w:val="0"/><w:numId w:val="3"/></w:numPr>'),
     "vorm None"),
    (lambda b: b + p("tekst", "JuPara", ppr='<w:numPr><w:ilvl w:val="0"/><w:numId w:val="9"/></w:numPr>'),
     "numbering.xml niet kent"),
    (lambda b: b + p(inhoud='<w:ins w:id="1"><w:r><w:t>toegevoegd</w:t></w:r></w:ins>', stijl="JuPara"),
     "bijgehouden wijziging"),
    (lambda b: b + p(inhoud='<w:del w:id="1"><w:r><w:delText>weg</w:delText></w:r></w:del>', stijl="JuPara"),
     "bijgehouden verwijdering"),
    (lambda b: b + p(inhoud='<w:r><w:sym w:font="Webdings" w:char="F021"/></w:r>', stijl="JuPara"),
     "onbekend symbool Webdings F021"),
    (lambda b: b + p(inhoud='<w:r><w:footnoteReference w:id="99"/></w:r>', stijl="JuPara"), "zonder noot"),
    (lambda b: b + p(inhoud='<w:r><w:endnoteReference w:id="1"/></w:r>', stijl="JuPara"), "eindnoot"),
    (lambda b: b + p(inhoud='<w:r><w:fldChar w:fldCharType="begin"/></w:r>', stijl="JuPara"), "niet is afgesloten"),
    (lambda b: b + "<w:sdt><w:p/></w:sdt>", "zonder eigen behandeling in de body"),
    (lambda b: b + "<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val=\"2\"/></w:tcPr><w:p><w:r><w:t>x</w:t></w:r></w:p></w:tc></w:tr></w:tbl>",
     "gridSpan"),
])
def test_weigeringen(wijziging, reden):
    with pytest.raises(ConversionError, match=reden):
        omzet(wijziging(BODY))


def test_een_regelbestand_wordt_geweigerd_en_een_gewoon_bestand_niet():
    # Elke gedrukte regel een alinea: gemeten 18% op een leesteken, tegen 81% of meer.
    regels = "".join(p(f"she applied first to the Sapri Magistrate Court number {i} and", "JuPara")
                     for i in range(120))
    with pytest.raises(ConversionError, match="elke gedrukte regel een alinea"):
        hudoc_docx.omzetten(docx(regels), DOCNAME)
    zinnen = "".join(p(f"She applied first to the Sapri Magistrate Court number {i}.", "JuPara")
                     for i in range(120))
    hudoc_docx.omzetten(docx(zinnen), DOCNAME)


def test_de_zelfcontrole_weigert_verloren_tekst(monkeypatch):
    oorspronkelijk = hudoc_docx._Lezer.tekst

    def slordig(self, para, **kw):
        t = oorspronkelijk(self, para, **kw)
        return t.replace("originated", "") if "originated" in t else t

    monkeypatch.setattr(hudoc_docx._Lezer, "tekst", slordig)
    with pytest.raises(ConversionError, match="mist of verdubbelt tekst"):
        omzet()


# --------------------------------------------------------------------------
# De route: zoeken, ophalen, herkomst en de weigeringen
# --------------------------------------------------------------------------

RIJ = {"itemid": "001-103117", "ecli": "ECLI:CE:ECHR:2011:0127JUD004486204", "appno": "44862/04",
       "docname": "CASE OF DIMITROVA AND OTHERS v. BULGARIA [Extracts]", "doctype": "HEJUD",
       "kpdate": "2011-01-27T00:00:00", "originatingbody": "4", "languageisocode": "ENG",
       "importance": "3", "respondent": "BGR"}


def _fake(monkeypatch, rijen, docx_status=200, docx_data=None, zoek_status=200):
    calls = []

    class Antwoord:
        def __init__(self, status, data=b"", js=None):
            self.status_code, self.content, self._js = status, data, js
            self.text = ""
            self.apparent_encoding = "utf-8"
            self.url = ""

        def json(self):
            return self._js

    def get(url, params=None, timeout=None, headers=None, allow_redirects=None):
        calls.append((url, dict(params or {})))
        if "query/results" in url:
            return Antwoord(zoek_status, js={"results": [{"columns": r} for r in rijen]})
        return Antwoord(docx_status, docx_data if docx_data is not None else docx(BODY, noten=NOTEN,
                                                                                  numbering=NUMBERING_BULLET))

    monkeypatch.setattr(hudoc.net, "documents", lambda: type("S", (), {"get": staticmethod(get)})())
    monkeypatch.setattr(hudoc, "_pauze", lambda s: None)
    return calls


def test_ecli_en_itemid_geven_dezelfde_route(monkeypatch):
    calls = _fake(monkeypatch, [RIJ])
    doc = from_link("ECLI:CE:ECHR:2011:0127JUD004486204")
    zoek = calls[0]
    # De zoek-API krijgt alle verplichte parameters, en de metadata die we nodig hebben.
    assert zoek[1]["query"] == 'contentsitename=ECHR AND ecli:"ECLI:CE:ECHR:2011:0127JUD004486204"'
    assert {"sort", "start", "length", "rankingmodelid", "facetquery", "select"} <= set(zoek[1])
    assert "itemid" in zoek[1]["select"] and "docname" in zoek[1]["select"]
    assert calls[1][0].endswith("docx/?library=ECHR&id=001-103117&filename=001-103117.docx")

    assert doc.kind == "caselaw"
    assert doc.source == "HUDOC (EHRM) • 001-103117 • ECLI:CE:ECHR:2011:0127JUD004486204"
    h = doc.provenance
    assert (h.format, h.ecli, h.celex) == ("hudoc-docx", "ECLI:CE:ECHR:2011:0127JUD004486204", None)
    # De notitie van HUDOC valt uit de kop; de volledige docname blijft in het zijbestand.
    assert doc.markdown.startswith("# CASE OF DIMITROVA AND OTHERS v. BULGARIA\n")
    assert h.title == "CASE OF DIMITROVA AND OTHERS v. BULGARIA"
    assert h.extra["docname"].endswith("[Extracts]")
    assert h.extra["itemid"] == "001-103117" and h.extra["appno"] == ["44862/04"]
    assert h.extra["uitspraakdatum"] == "2011-01-27"
    bewijs = h.extra["source_structure"]["sources"]
    assert len(bewijs) == 1 and bewijs[0]["source_format"] == "hudoc-docx"

    doc2 = from_link("https://hudoc.echr.coe.int/eng#{%22itemid%22:[%22001-103117%22]}")
    assert doc2.provenance.ecli == h.ecli
    assert 'itemid:"001-103117"' in calls[2][1]["query"]


@pytest.mark.parametrize("rij, reden", [
    (dict(RIJ, doctype="HFJUD", languageisocode="FRE", docname="AFFAIRE X c. Y"), "Franstalige uitspraken"),
    (dict(RIJ, doctype="HEDEC", docname="DECISION X v. Y"), "Alleen het Engelse origineel"),
    (dict(RIJ, doctype="HJUDDUT"), "Alleen het Engelse origineel"),
])
def test_alleen_het_engelse_origineel(monkeypatch, rij, reden):
    _fake(monkeypatch, [rij])
    with pytest.raises(ConversionError, match=reden):
        from_link("001-103117")


def test_een_ecli_kiest_het_origineel_en_niet_de_vertaling(monkeypatch):
    vertaling = dict(RIJ, itemid="001-999999", doctype="HJUDDUT", languageisocode="DUT")
    _fake(monkeypatch, [vertaling, RIJ])
    assert from_link("ECLI:CE:ECHR:2011:0127JUD004486204").provenance.extra["itemid"] == "001-103117"
    # Twee Engelse originelen onder één ECLI is een keuze die de gebruiker moet maken.
    _fake(monkeypatch, [RIJ, dict(RIJ, itemid="001-111111")])
    with pytest.raises(ConversionError, match="2 Engelse originelen"):
        from_link("ECLI:CE:ECHR:2011:0127JUD004486204")


def test_hudoc_die_weigert_is_geen_niet_gevonden(monkeypatch):
    calls = _fake(monkeypatch, [], zoek_status=403)
    with pytest.raises(ConversionError, match="geen 'niet gevonden'"):
        from_link("001-103117")
    # Eén herhaling na een pauze, niet meer: meer verzoeken verlengen de beperking.
    assert len(calls) == 2


def test_geen_word_bestand_is_een_weigering_zonder_terugval_op_html(monkeypatch):
    calls = _fake(monkeypatch, [RIJ], docx_status=500, docx_data=b"")
    with pytest.raises(ConversionError, match="geen terugval op HTML"):
        from_link("001-103117")
    assert not any("docx/html/body" in url for url, _ in calls)


def test_een_uitspraak_krijgt_een_kb_bundel_met_de_docx(monkeypatch):
    from mdconv import kb_bundle
    from mdconv.api import _doc_payload

    _fake(monkeypatch, [RIJ])
    doc = from_link("001-103117")
    payload = _doc_payload(doc)
    assert "bundle_token" in payload
    stream, pad_id = kb_bundle.build(payload["bundle_token"], doc.markdown, bewerkt_met_ai=False)
    assert pad_id == "ECLI-CE-ECHR-2011-0127JUD004486204"
    with zipfile.ZipFile(stream) as archive:
        namen = archive.namelist()
        assert f"raw/jurisprudentie/{pad_id}.md" in namen
        assert f"raw/jurisprudentie/{pad_id}.source.json" in namen
        assert any(n.startswith(f"raw/source-evidence/{pad_id}/") and n.endswith(".docx") for n in namen)


def test_de_nummering_van_de_koppen_wordt_nagerekend_zoals_word_die_toont():
    """T2-F19 (kb WP-20): vijf van de twaalf arresten van test 2 droegen het nummer van hun
    koppen niet in de tekst maar in de nummering van de stijl. De tellers horen bij de
    abstractNum, een hoger niveau herstart de diepere, en een startOverride herstart een reeks."""
    body = "".join([
        p("CASE OF X v. Y"),
        genummerd("THE FACTS", "JuHHead", "4", "0"),
        genummerd("THE CIRCUMSTANCES OF THE CASE", "JuHIRoman", "4", "1"),
        genummerd("The applicants", "JuHA", "4", "2"),
        genummerd("The proceedings", "JuH1", "4", "3"),
        genummerd("The appeal", "JuH1", "4", "3"),
        genummerd("The Government", "JuHA", "4", "2"),
        genummerd("RELEVANT DOMESTIC LAW", "JuHIRoman", "4", "1"),
        genummerd("The Constitution", "JuHA", "4", "2"),
        genummerd("THE LAW", "JuHHead", "4", "0"),
        genummerd("PRELIMINARY ISSUES", "JuHIRoman", "4", "1"),
        genummerd("Locus standi", "JuHA", "4", "2"),
        genummerd("The applicants", "JuHa0", "4", "4"),
        genummerd("RELEVANT INTERNATIONAL LAW", "JuHIRoman", "27", "1"),   # herstart: I.
        genummerd("EUROPEAN UNION LAW", "JuHIRoman", "4", "1"),              # telt door: II.
        p(f"1.{NBSP}{NBSP}The case originated in an application.", "JuPara"),
    ])
    markdown, meta = hudoc_docx.omzetten(docx(body, numbering=NUMBERING_KOPPEN), "CASE OF X v. Y")
    koppen = [r for r in markdown.splitlines() if r.startswith("## ")]
    assert koppen == [
        "## THE FACTS", "## I. THE CIRCUMSTANCES OF THE CASE", "## A. The applicants", "## 1. The proceedings",
        "## 2. The appeal", "## B. The Government", "## II. RELEVANT DOMESTIC LAW", "## A. The Constitution",
        "## THE LAW", "## I. PRELIMINARY ISSUES", "## A. Locus standi", "## (a) The applicants",
        "## I. RELEVANT INTERNATIONAL LAW", "## II. EUROPEAN UNION LAW",
    ]
    assert meta["koppen"] == 14 and meta["randnummers"] == 1


def test_een_gerekend_nummer_voor_een_gewone_alinea_is_geen_randnummer():
    """De lijst van verzoekers in de bijlage van López Ribalda: `Normal` met decimale
    nummering. Het nummer komt in de vorm die de bron voor een getypt lijstnummer gebruikt
    (nummer plus twee harde spaties) en telt niet als randnummer."""
    body = "".join([
        p("CASE OF X v. Y"),
        p(f"1.{NBSP}{NBSP}The case originated in an application.", "JuPara"),
        genummerd("Isabel LÓPEZ RIBALDA, born in 1963", "Normal", "2", "0"),
        genummerd("María GANCEDO, born in 1967", "Normal", "2", "0"),
    ])
    markdown, meta = hudoc_docx.omzetten(docx(body, numbering=NUMBERING_BULLET), "CASE OF X v. Y")
    assert f"\n1.{NBSP}{NBSP}Isabel LÓPEZ RIBALDA, born in 1963\n" in markdown
    assert f"\n2.{NBSP}{NBSP}María GANCEDO, born in 1967\n" in markdown
    assert meta["randnummers"] == 1


@pytest.mark.parametrize("body, reden", [
    # Een reeks die niet aansluit: een startOverride op niveau 1 (num 28: 3) die een alinea
    # op niveau 2 als eerste gebruikt, waarna niveau 1 op de gewone num van I. naar III. springt.
    ("".join([genummerd("A", "JuHIRoman", "4", "1"), genummerd("x", "JuHA", "28", "2"),
              genummerd("B", "JuHIRoman", "4", "1")]),
     "sluit niet aan"),
    # Het nummer staat al in de tekst én als nummering: welke geldt is niet te bewijzen.
    (genummerd(f"I.{NBSP}{NBSP}THE FACTS", "JuHIRoman", "4", "1"), "al in haar tekst"),
])
def test_wat_de_teller_niet_kan_bewijzen_weigert(body, reden):
    with pytest.raises(ConversionError, match=reden):
        hudoc_docx.omzetten(docx(p("CASE OF X v. Y") + body, numbering=NUMBERING_KOPPEN), "CASE OF X v. Y")
