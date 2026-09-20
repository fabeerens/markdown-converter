"""De downloadroute voor officiële EUR-Lex-Formex-manifestaties."""

from __future__ import annotations

import base64
import io
from types import SimpleNamespace
import zipfile

import pytest

from mdconv.errors import ConversionError
from mdconv.sources import formex_xml, from_link
from mdconv.sources.xml_gedeeld import tabel_markdown


DOC = b"""<FMX>
<PUBLICATION.REF><COLL>L</COLL><NO.OJ>265</NO.OJ><LG.OJ>NL</LG.OJ>
<DATE ISO="20221012"/></PUBLICATION.REF>
<REF.PHYS TYPE="DOC.XML" FILE="handeling.xml"/>
</FMX>"""

ACT = b"""<ACT>
<BIB.INSTANCE><PAGE.FIRST>1</PAGE.FIRST></BIB.INSTANCE>
<TITLE><P><HT TYPE="UC">Verordening (EU) 2022/1925</HT></P></TITLE>
<PREAMBLE><GR.CONSID><CONSID><NP><NO.P>(1)</NO.P>
<TXT>Digitale diensten vragen duidelijke regels.</TXT></NP></CONSID></GR.CONSID></PREAMBLE>
<ENACTING.TERMS><DIVISION><TITLE><TI>HOOFDSTUK I</TI><STI>Algemene bepalingen</STI></TITLE>
<ARTICLE IDENTIFIER="1"><TI.ART>Artikel 1</TI.ART><STI.ART>Onderwerp</STI.ART>
<PARAG><NO.PARAG>1.</NO.PARAG><ALINEA><P>Deze verordening stelt regels vast.</P>
<LIST TYPE="ALPHA"><ITEM><NP><NO.P>a)</NO.P><TXT>eerste onderdeel;</TXT></NP></ITEM>
<ITEM><NP><NO.P>b)</NO.P><TXT>tweede onderdeel.</TXT></NP></ITEM></LIST>
</ALINEA></PARAG></ARTICLE></DIVISION></ENACTING.TERMS>
<FINAL><P>Gedaan te Brussel.</P></FINAL>
</ACT>"""


def formex_zip(*, doc: bytes = DOC, act: bytes = ACT, extra: dict[str, bytes] | None = None) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("L_test.doc.xml", doc)
        archive.writestr("handeling.xml", act)
        for naam, data in (extra or {}).items():
            archive.writestr(naam, data)
    return stream.getvalue()


def test_eurlex_uses_formex_before_html_and_preserves_the_source(monkeypatch):
    data = formex_zip()
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs["headers"]))
        return SimpleNamespace(status_code=200, content=data, url=url)

    from mdconv.sources import eurlex
    monkeypatch.setattr(eurlex.net, "documents", lambda: SimpleNamespace(get=get))

    document = from_link("32022R1925", "NL")

    assert len(calls) == 1
    assert calls[0][1] == {
        "Accept": "application/zip;mtype=fmx4",
        "Accept-Language": "nld",
    }
    assert "### Artikel\u00a01" in document.markdown
    assert "1.\u00a0\u00a0\u00a0Deze verordening stelt regels vast." in document.markdown
    assert "(1) Digitale diensten vragen duidelijke regels." in document.markdown
    assert document.provenance.format == "formex"
    assert document.provenance.oj_reference == "PB L 265 van 12.10.2022, blz. 1"
    assert document.provenance.language == "nl"
    assert "Formex" in document.provenance.waarschuwingen[0]
    bron = document.provenance.extra["source_structure"]["sources"][0]
    assert bron["source_format"] == "formex"
    assert base64.b64decode(bron["original_base64"]) == data


def test_non_zip_formex_response_falls_back_to_html_with_a_warning(monkeypatch):
    html = "<html><body><h1>Verordening</h1><p>" + "Juridische tekst. " * 20 + "</p></body></html>"
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs["headers"]["Accept"])
        if "fmx4" in kwargs["headers"]["Accept"]:
            return SimpleNamespace(status_code=200, content=b"geen zip", url=url)
        return SimpleNamespace(status_code=200, text=html, url=url)

    from mdconv.sources import eurlex
    monkeypatch.setattr(eurlex.net, "documents", lambda: SimpleNamespace(get=get))
    monkeypatch.setattr(eurlex.net, "decoded_text", lambda response: response.text)

    document = from_link("32022R0868", "NL")

    assert calls == ["application/zip;mtype=fmx4", "application/xhtml+xml, text/html;q=0.9"]
    assert document.provenance.format == "eurlex-html"
    assert any("geen zip" in melding for melding in document.provenance.waarschuwingen)
    assert any("HTML-route" in melding for melding in document.provenance.waarschuwingen)


def test_consolidated_formex_metadata_is_literal_and_missing_recitals_are_reported(monkeypatch):
    body = ACT.removeprefix(b"<ACT>").removesuffix(b"</ACT>")
    begin = body.index(b"<ENACTING.TERMS>")
    zonder_considerans = body[:body.index(b"<PREAMBLE>")] + body[begin:]
    cons = (
        b'<CONS.ACT><INFO.CONSLEG CONSLEG.REF="2019R0881" START.DATE="20250204" '
        b'END.DATE="99999999" PROD.SEQ="001.001.0"/><CONS.DOC>'
        b'<BIB.INSTANCE><LG.DOC>NL</LG.DOC></BIB.INSTANCE>' + zonder_considerans
        + b'</CONS.DOC></CONS.ACT>'
    )
    data = formex_zip(act=cons)
    from mdconv.sources import eurlex
    monkeypatch.setattr(
        eurlex.net,
        "documents",
        lambda: SimpleNamespace(get=lambda *a, **k: SimpleNamespace(
            status_code=200, content=data, url="https://example.test/clg"
        )),
    )

    document = from_link("02019R0881-20250204", "NL")

    assert document.provenance.format == "clg"
    assert document.provenance.base_celex == "32019R0881"
    assert document.provenance.consolidation_date == "2025-02-04"
    assert document.provenance.version == "001.001.0"
    assert any("geen considerans" in melding for melding in document.provenance.waarschuwingen)


def test_manifest_and_zip_must_name_exactly_the_same_parts():
    with pytest.raises(ConversionError, match="buiten de .doc.xml"):
        formex_xml.omzetten(formex_zip(extra={"stil-vergeten.xml": b"<ANNEX/>"}))


def test_unknown_text_element_is_refused_instead_of_counted():
    act = ACT.replace(b"</FINAL>", b"<MYSTERY>onbehandelde tekst</MYSTERY></FINAL>")
    with pytest.raises(ConversionError, match="MYSTERY"):
        formex_xml.omzetten(formex_zip(act=act))


def test_structure_control_refuses_an_article_that_does_not_reach_markdown(monkeypatch):
    monkeypatch.setattr(formex_xml.FormexOmzetter, "artikel", lambda self, el: None)
    with pytest.raises(ConversionError, match="woordmultiset|structuurcontrole"):
        formex_xml.omzetten(formex_zip())


def test_table_grid_refuses_overlap_gap_and_invalid_span():
    with pytest.raises(ConversionError, match="overlappende"):
        tabel_markdown([[{"tekst": "A", "kol": 0}, {"tekst": "B", "kol": 0}]], 0)
    with pytest.raises(ConversionError, match="ontbrekende cellen"):
        tabel_markdown([[{"tekst": "A", "kol": 0}, {"tekst": "C", "kol": 2}]], 0)
    with pytest.raises(ConversionError, match="ongeldige"):
        tabel_markdown([[{"tekst": "A", "kol": 0, "rowspan": 2}]], 0)


def test_nested_content_table_is_refused():
    tabel = b"<TBL><CORPUS><ROW><CELL>A<TBL><CORPUS><ROW><CELL>B</CELL></ROW></CORPUS></TBL></CELL></ROW></CORPUS></TBL>"
    act = ACT.replace(b"<P>Deze verordening stelt regels vast.</P>", tabel)
    with pytest.raises(ConversionError, match="Geneste inhoudstabel"):
        formex_xml.omzetten(formex_zip(act=act))
