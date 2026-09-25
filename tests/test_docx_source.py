"""Word-bestanden als document voor de kennisbank (`mdconv/sources/docx.py`).

De fixture is een met de hand gebouwd DOCX, zoals `test_hudoc_docx.py` dat doet. Waar
het HUDOC-bestand zijn eigen stijlnamen heeft (`JuPara`), kent een willekeurig Word-
bestand alleen `Heading N`, `Title` en `Normal` - en stijlen die daarop teruggaan.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from mdconv import kb_bundle
from mdconv.errors import ConversionError
from mdconv.sources import docx, files, from_file

NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

STIJLEN = f"""<?xml version="1.0" encoding="UTF-8"?><w:styles {NS}>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Kop1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Kop2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Tekst"><w:name w:val="Body Text"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="MijnStijl"><w:name w:val="Mijn stijl"/><w:basedOn w:val="Tekst"/></w:style>
<w:style w:type="paragraph" w:styleId="Wees"><w:name w:val="Wees"/></w:style>
<w:style w:type="paragraph" w:styleId="Lus"><w:name w:val="Lus"/><w:basedOn w:val="Lus"/></w:style>
<w:style w:type="paragraph" w:styleId="Inhoud1"><w:name w:val="toc 1"/><w:basedOn w:val="Normal"/></w:style>
</w:styles>"""

NUMBERING = f"""<?xml version="1.0" encoding="UTF-8"?><w:numbering {NS}>
<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl></w:abstractNum>
<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/></w:lvl></w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num></w:numbering>"""


def p(tekst="", stijl=None, ppr="", inhoud=None):
    st = f'<w:pStyle w:val="{stijl}"/>' if stijl else ""
    pr = f"<w:pPr>{st}{ppr}</w:pPr>" if (st or ppr) else ""
    run = inhoud if inhoud is not None else f'<w:r><w:t xml:space="preserve">{tekst}</w:t></w:r>'
    return f"<w:p>{pr}{run}</w:p>"


def bouw(body, *, noten="", stijlen=STIJLEN):
    onderdelen = {
        "word/document.xml": f'<?xml version="1.0" encoding="UTF-8"?><w:document {NS}><w:body>{body}</w:body></w:document>',
        "word/styles.xml": stijlen,
        "word/numbering.xml": NUMBERING,
        "[Content_Types].xml": "<Types/>",
    }
    if noten:
        onderdelen["word/footnotes.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8"?><w:footnotes {NS}>'
            '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
            f"{noten}</w:footnotes>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for naam, inhoud in onderdelen.items():
            zf.writestr(naam, inhoud)
    return buf.getvalue()


def cel(tekst, extra=""):
    return f"<w:tc><w:tcPr>{extra}</w:tcPr>{p(tekst)}</w:tc>"


TABEL = ("<w:tbl><w:tr>" + cel("Grondslag") + cel("Voorbeeld") + "</w:tr>"
         "<w:tr>" + cel("Toestemming") + cel("Nieuwsbrief") + "</w:tr></w:tbl>")
NOOT = '<w:r><w:footnoteReference w:id="2"/></w:r>'
NOTEN = ('<w:footnote w:id="2"><w:p><w:r><w:footnoteRef/></w:r>'
         '<w:r><w:t xml:space="preserve"> Zie artikel 7 van de verordening.</w:t></w:r></w:p></w:footnote>')

BODY = "".join([
    p("Richtsnoer over toestemming", "Title"),
    p("Inleiding", "Kop1"),
    p("Toestemming moet vrij en specifiek zijn.", "Tekst"),
    p(inhoud='<w:r><w:t>Zij volgt uit de wet.</w:t></w:r>' + NOOT + '<w:r><w:t xml:space="preserve"> Dat blijft zo.</w:t></w:r>',
      stijl="MijnStijl"),
    p("Grondslagen", "Kop2"),
    p("Eerste punt", ppr='<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'),
    p("Kop via outline", ppr='<w:outlineLvl w:val="2"/>'),
    TABEL,
])


def test_headings_notes_tables_and_bullets_come_from_the_word_structure():
    markdown, meta = docx.convert(bouw(BODY, noten=NOTEN))
    regels = markdown.split("\n")
    # De titel is de enige `#`; `Heading 1` is `##`, `Heading 2` `###`, en een outlineLvl van 2 is `####`.
    assert regels[0] == "# Richtsnoer over toestemming"
    assert "## Inleiding" in regels and "### Grondslagen" in regels
    assert "#### Kop via outline" in regels
    # Een stijl die via basedOn op Normal teruggaat is een gewone alinea.
    assert "Toestemming moet vrij en specifiek zijn." in regels
    # De noot is native, en de definitie staat onderaan.
    assert "Zij volgt uit de wet.[^1] Dat blijft zo." in regels
    assert "[^1]: Zie artikel 7 van de verordening." in regels
    assert "- Eerste punt" in regels
    assert "| Grondslag | Voorbeeld |" in regels and "| Toestemming | Nieuwsbrief |" in regels
    assert meta["koppen"] == 4 and meta["noten"] == 1 and meta["tabellen"] == 1 and meta["opsommingen"] == 1


@pytest.mark.parametrize("body, reden", [
    (p("Tekst", "Wees"), "stijlen zonder eigen behandeling: Wees"),
    (p("Tekst", "Lus"), "verwijst via basedOn naar zichzelf"),
    # WP-20: een decimale nummering wordt nagerekend (`docx.Teller`); een num die numbering.xml
    # niet kent blijft een weigering.
    (p("Punt", ppr='<w:numPr><w:ilvl w:val="0"/><w:numId w:val="3"/></w:numPr>'), "niet kent"),
    (p(inhoud='<w:r><w:drawing/></w:r>'), "afbeelding, object of tekstvak"),
    ("<w:tbl><w:tr>" + cel("A", '<w:gridSpan w:val="2"/>') + "</w:tr></w:tbl>", "gridSpan of vMerge"),
    ("<w:tbl><w:tr><w:tc>" + TABEL + p("x") + "</w:tc></w:tr></w:tbl>", "tabel in een tabelcel"),
    (p(inhoud='<w:ins><w:r><w:t>nieuw</w:t></w:r></w:ins>'), "bijgehouden wijziging"),
    (p(inhoud='<w:r><w:endnoteReference w:id="1"/></w:r>'), "eindnoot"),
    ("<w:sdt/>", "zonder eigen behandeling in de body \\(w:sdt\\)"),
])
def test_what_the_map_does_not_know_is_refused_with_the_reason(body, reden):
    with pytest.raises(ConversionError, match=reden):
        docx.convert(bouw(body))


def test_a_file_that_is_not_a_docx_is_refused():
    with pytest.raises(ConversionError, match="geen leesbare DOCX"):
        docx.convert(b"dit is geen zip")


def test_self_check_refuses_lost_text(monkeypatch):
    oorspronkelijk = docx.Lezer.tekst

    def slordig(self, para, **kw):
        t = oorspronkelijk(self, para, **kw)
        return t.replace("vrij", "") if "vrij" in t else t

    monkeypatch.setattr(docx.Lezer, "tekst", slordig)
    with pytest.raises(ConversionError, match="mist of verdubbelt tekst"):
        docx.convert(bouw(BODY, noten=NOTEN))


def test_docx_with_document_id_becomes_a_kb_bundle_with_the_original_bytes():
    bron = bouw(BODY, noten=NOTEN)
    doc = from_file(bron, "richtsnoer.docx", document_id="x-test-richtsnoer-toestemming")
    zij = doc.provenance.as_json()
    assert zij["format"] == "docx" and zij["extra"]["docx"]["noten"] == 1
    assert kb_bundle.identiteit(zij) == ("documenten", "x-test-richtsnoer-toestemming",
                                         "x-test-richtsnoer-toestemming")
    stream, _ = kb_bundle.build(kb_bundle.store(zij), doc.markdown, bewerkt_met_ai=False)
    digest = hashlib.sha256(bron).hexdigest()
    with zipfile.ZipFile(stream) as archive:
        assert archive.read(f"raw/source-evidence/x-test-richtsnoer-toestemming/{digest}.docx") == bron
        fetch = json.loads(archive.read("raw/source-evidence/x-test-richtsnoer-toestemming/fetch.json"))
        assert fetch["source_format"] == "docx"


def test_refusal_with_document_id_is_final_but_without_it_falls_back_visibly(monkeypatch):
    kapot = bouw(p("Tekst", "Wees"))
    with pytest.raises(ConversionError, match="stijlen zonder eigen behandeling"):
        from_file(kapot, "x.docx", document_id="x-test-kapot")
    # Zonder documentnummer blijft de losse markdown-download werken, met de reden in de
    # waarschuwing en zonder kennisbankbundel.
    monkeypatch.setattr(files, "convert", lambda data, filename="": ("Tekst\n", "MarkItDown"))
    doc = from_file(kapot, "x.docx")
    assert doc.provenance is None
    assert "geen kennisbankbundel" in doc.warnings[0] and "Wees" in doc.warnings[0]


def test_table_header_row_is_honoured_and_absent_header_stays_empty():
    kop = "<w:tr><w:trPr><w:tblHeader/></w:trPr>" + cel("Grondslag") + cel("Voorbeeld") + "</w:tr>"
    rij = "<w:tr>" + cel("Toestemming") + cel("Nieuwsbrief") + "</w:tr>"
    md, _ = docx.convert(bouw(f"<w:tbl>{kop}{rij}</w:tbl>"))
    assert md.splitlines()[:3] == ["| Grondslag | Voorbeeld |", "| --- | --- |", "| Toestemming | Nieuwsbrief |"]
    # Markeert de bron geen kopregel, dan is er ook geen: lege kop, en het profiel vlagt hem.
    md, _ = docx.convert(bouw(f"<w:tbl>{rij}{rij}</w:tbl>"))
    assert md.splitlines()[0] == "|  |  |"
    # Een kopregel die niet vooraan staat of een tweede kopregel past niet in één Markdown-koprij.
    with pytest.raises(ConversionError, match="niet vooraan"):
        docx.convert(bouw(f"<w:tbl>{rij}{kop}</w:tbl>"))
    with pytest.raises(ConversionError, match="meer dan één kopregel"):
        docx.convert(bouw(f"<w:tbl>{kop}{kop}</w:tbl>"))
