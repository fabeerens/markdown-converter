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


def formex_zip(*, doc: bytes = DOC, act: bytes = ACT, doc_naam: str = "L_test.doc.xml",
               extra: dict[str, bytes] | None = None) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(doc_naam, doc)
        archive.writestr("handeling.xml", act)
        for naam, data in (extra or {}).items():
            archive.writestr(naam, data)
    return stream.getvalue()


def test_modern_doc_fmx_manifest_and_its_publication_toc_are_supported():
    doc = DOC.replace(b"<PUBLICATION.REF>", b'<PUBLICATION.REF FILE="L_test.toc.fmx.xml">')
    toc = b'<PUBLICATION><OJ><VOLUME><ITEM.PUB DOC.INSTANCE="L_test.doc.fmx.xml"/></VOLUME></OJ></PUBLICATION>'
    data = formex_zip(
        doc=doc,
        doc_naam="L_test.doc.fmx.xml",
        extra={"L_test.toc.fmx.xml": toc},
    )

    markdown, eenheden, onbekend, _ = formex_xml.omzetten(data)

    assert "### Artikel\u00a01" in markdown
    ankers = [eenheid.anker for eenheid in eenheden]
    assert "art-1" in ankers
    assert "art-1-1" in ankers
    assert onbekend == {}


def test_modern_publication_toc_must_point_back_to_the_document_manifest():
    doc = DOC.replace(b"<PUBLICATION.REF>", b'<PUBLICATION.REF FILE="L_test.toc.fmx.xml">')
    toc = b'<PUBLICATION><OJ><VOLUME><ITEM.PUB DOC.INSTANCE="ander.doc.fmx.xml"/></VOLUME></OJ></PUBLICATION>'
    data = formex_zip(
        doc=doc,
        doc_naam="L_test.doc.fmx.xml",
        extra={"L_test.toc.fmx.xml": toc},
    )

    with pytest.raises(ConversionError, match="verwijst niet terug"):
        formex_xml.omzetten(data)


def test_quoted_alinea_is_preserved_as_an_inline_leaf():
    act = ACT.replace(
        b"Deze verordening stelt regels vast.",
        b"Deze verordening wijzigt: <QUOT.S><ALINEA>geciteerde bladregel.</ALINEA></QUOT.S>",
    )

    markdown, _, onbekend, _ = formex_xml.omzetten(formex_zip(act=act))

    assert "Deze verordening wijzigt: geciteerde bladregel." in markdown
    assert onbekend == {}


def test_quoted_paragraph_is_preserved_without_creating_its_own_unit():
    act = ACT.replace(
        b"Deze verordening stelt regels vast.",
        b"Deze verordening wijzigt: <QUOT.S><PARAG><NO.PARAG>5.</NO.PARAG>"
        b"<ALINEA>geciteerd lid.</ALINEA></PARAG></QUOT.S>",
    )

    markdown, eenheden, onbekend, _ = formex_xml.omzetten(formex_zip(act=act))

    assert "Deze verordening wijzigt: 5. geciteerd lid." in markdown
    assert [eenheid.anker for eenheid in eenheden].count("art-1-5") == 0
    assert onbekend == {}


def test_paragraph_identifier_disambiguates_a_duplicate_printed_number():
    act = ACT.replace(
        b'<PARAG><NO.PARAG>1.</NO.PARAG><ALINEA>',
        b'<PARAG IDENTIFIER="001.010"><NO.PARAG>11.</NO.PARAG><ALINEA>',
    )

    _, eenheden, _, _ = formex_xml.omzetten(formex_zip(act=act))

    assert any(eenheid.anker == "art-1-10" and eenheid.tekst.startswith("11.")
               for eenheid in eenheden)


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


def _cons_act(*, met_considerans: bool = False, met_noot: bool = True,
              fam_comp: bytes = b"", markeringen: tuple[str, ...] = ()) -> bytes:
    """Een geconsolideerde handeling zoals de Cellar hem levert: een lege PREAMBLE.

    `fam_comp` is het blok met de vindplaats en de wijzigende handelingen;
    `markeringen` zijn CELEX-nummers waarvoor een passage een `CLG.MDFO` krijgt.
    """
    body = ACT.removeprefix(b"<ACT>").removesuffix(b"</ACT>")
    begin = body.index(b"<ENACTING.TERMS>")
    voor = body[:body.index(b"<PREAMBLE>")]
    preambule = (body[body.index(b"<PREAMBLE>"):begin] if met_considerans
                 else b"<PREAMBLE><PREAMBLE.INIT/><PREAMBLE.FINAL/></PREAMBLE>")
    bepalingen = body[begin:]
    if met_noot:
        # Een eigen noot in de wettekst: haar nummering begint opnieuw bij (1),
        # los van de noten van de considerans.
        bepalingen = bepalingen.replace(
            b"Deze verordening stelt regels vast.",
            b'Deze verordening stelt regels vast.<NOTE NOTE.ID="E0900" TYPE="FOOTNOTE">'
            b"<P>Een noot van de wettekst.</P></NOTE>",
        )
    if markeringen:
        pis = b"".join(
            f'<?CLG.MDFO ID="O{n}" IDREF="C{n}" ACTION="REPLACED" LEVEL="STRUCTURE" COMMAND="EXPLICIT" '
            f'ACTIVE.DOC="{celex}" ACTIVE.LOC="AR:1;PT:1" MOD.LEVEL="1"?><?CLG.MDFC ID="C{n}" IDREF="O{n}"?>'.encode()
            for n, celex in enumerate(markeringen, 1)
        )
        bepalingen = bepalingen.replace(b"<ITEM><NP><NO.P>b)", pis + b"<ITEM><NP><NO.P>b)")
    return (
        b'<CONS.ACT><INFO.CONSLEG CONSLEG.REF="2019R0881" START.DATE="20250204" '
        b'END.DATE="99999999" PROD.SEQ="001.001.0"/><CONS.DOC>'
        b"<BIB.INSTANCE><LG.DOC>NL</LG.DOC></BIB.INSTANCE>" + fam_comp + voor + preambule + bepalingen
        + b"</CONS.DOC></CONS.ACT>"
    )


def _mod_act(*, nummer: str = "37", jaar: str = "2025", celex: str | None = "32025R0037",
             leg_val: str | None = "REG", soort: str = "MOD") -> bytes:
    """Eén `MOD.ACT` zoals de Cellar hem in `GR.MOD.ACT` zet."""
    leg = f' LEG.VAL="{leg_val}"' if leg_val else ""
    noceles = f"<NO.CELEX>{celex}</NO.CELEX>" if celex else ""
    return (
        f'<MOD.ACT TYPE="{soort}"{leg} EXISTS="YES"><BIB.DATA><BIB.INSTANCE.CONS><DOCUMENT.REF.CONS>'
        f'<COLL>L</COLL><NO.DOC FORMAT="YN" TYPE="OJ"><NO.CURRENT>{nummer}</NO.CURRENT><YEAR>{jaar}</YEAR>'
        f'<COM>EU</COM></NO.DOC><LG.OJ>NL</LG.OJ><PAGE.FIRST>1</PAGE.FIRST></DOCUMENT.REF.CONS>'
        f'<DATE ISO="20250115">20250115</DATE></BIB.INSTANCE.CONS>{noceles}</BIB.DATA></MOD.ACT>'
    ).encode()


def _fam_comp(*wijzigingen: bytes, celex: str = "32019R0881", no_oj: str | None = "151",
              pagina: str = "15", datum: str = "20190607") -> bytes:
    """`FAM.COMP` met de vindplaats van de basishandeling en haar wijzigende handelingen."""
    oj = f"<NO.OJ>{no_oj}</NO.OJ>" if no_oj else ""
    return (
        f'<FAM.COMP LEG.VAL="REG"><BIB.DATA><BIB.INSTANCE.CONS><DOCUMENT.REF.CONS><COLL>L</COLL>{oj}'
        f'<YEAR>2019</YEAR><LG.OJ>NL</LG.OJ><PAGE.FIRST>{pagina}</PAGE.FIRST></DOCUMENT.REF.CONS>'
        f'<DATE ISO="{datum}">{datum}</DATE><NO.DOC FORMAT="YN" TYPE="OJ"><NO.CURRENT>881</NO.CURRENT>'
        f'<YEAR>2019</YEAR><COM>EU</COM></NO.DOC></BIB.INSTANCE.CONS><NO.CELEX>{celex}</NO.CELEX></BIB.DATA>'
        f'<GR.MOD.ACT>'.encode() + b"".join(wijzigingen) + b"</GR.MOD.ACT></FAM.COMP>"
    )


def test_consolidated_formex_reads_the_base_citation_and_the_amending_acts_from_the_source(monkeypatch):
    data = formex_zip(act=_cons_act(fam_comp=_fam_comp(_mod_act()), markeringen=("32025R0037",)))
    _cellar(monkeypatch, {"02019R0881-20250204": data})

    document = from_link("02019R0881-20250204", "NL")

    # Dezelfde vorm als bij een handeling uit het Publicatieblad.
    assert document.provenance.oj_reference == "PB L 151 van 7.6.2019, blz. 15"
    assert document.provenance.amendments == ({"celex": "32025R0037", "shown": True},)
    assert not any("GR.MOD.ACT" in m or "CLG.MDFO" in m or "FAM.COMP" in m
                   for m in document.provenance.waarschuwingen)

    # Het zijbestand in de vorm die `extract_meta.py` leest (`side["amendments"][i]["celex"]`).
    from mdconv.herkomst import als_zijbestand
    import json
    zij = json.loads(als_zijbestand(document.provenance.as_json(), bewerkt_met_ai=False))
    assert [a["celex"] for a in zij["amendments"]] == ["32025R0037"]
    assert zij["amendments"][0].get("shown") is True
    assert zij["oj_reference"] == "PB L 151 van 7.6.2019, blz. 15"
    assert "corrections" not in zij     # niet gelezen, dus niet als "geen" beweerd


def test_consolidated_formex_without_fam_comp_says_so_instead_of_inventing_a_citation(monkeypatch):
    _cellar(monkeypatch, {"02019R0881-20250204": formex_zip(act=_cons_act())})
    document = from_link("02019R0881-20250204", "NL")
    assert document.provenance.oj_reference is None
    assert document.provenance.amendments == ()
    assert any("vindplaats" in m and "oj_reference is leeg" in m for m in document.provenance.waarschuwingen)


def test_incomplete_citation_is_not_written_as_a_partial_one():
    zonder_nummer = formex_zip(act=_cons_act(fam_comp=_fam_comp(no_oj=None)))
    _, _, _, extra = formex_xml.omzetten(zonder_nummer)
    assert extra["metadata"]["oj_reference"] is None
    assert "NO.OJ" in extra["metadata"]["waarschuwingen"][0]


def test_citation_of_another_act_than_the_consolidation_is_refused():
    with pytest.raises(ConversionError, match="hoort bij 32016R0679"):
        formex_xml.omzetten(formex_zip(act=_cons_act(fam_comp=_fam_comp(celex="32016R0679"))))


def test_amendment_celex_letter_comes_from_leg_val_when_the_source_gives_no_celex():
    fam = _fam_comp(_mod_act(celex=None, leg_val="DIR", nummer="7"),
                    _mod_act(celex=None, leg_val="REG", nummer="1234", jaar="2021"),
                    _mod_act(celex=None, leg_val="DEC", nummer="12", jaar="2019"))
    _, _, _, extra = formex_xml.omzetten(formex_zip(act=_cons_act(fam_comp=fam)))
    assert [a["celex"] for a in extra["metadata"]["amendments"]] == [
        "32025L0007", "32021R1234", "32019D0012"]


def test_amendment_whose_kind_and_celex_cannot_be_determined_is_reported_not_guessed():
    fam = _fam_comp(_mod_act(celex=None, leg_val="ONBEKEND"), _mod_act(celex=None, leg_val=None))
    _, _, _, extra = formex_xml.omzetten(formex_zip(act=_cons_act(fam_comp=fam)))
    assert extra["metadata"]["amendments"] == []
    meldingen = extra["metadata"]["waarschuwingen"]
    assert sum("niet af te leiden" in m for m in meldingen) == 2


def test_amendment_celex_that_contradicts_number_or_kind_is_refused():
    # NO.CELEX zegt R, LEG.VAL zegt DIR: twee bronnen die het oneens zijn.
    tegen_soort = _fam_comp(_mod_act(celex="32025R0037", leg_val="DIR"))
    with pytest.raises(ConversionError, match="spreekt 32025L0037"):
        formex_xml.omzetten(formex_zip(act=_cons_act(fam_comp=tegen_soort)))
    tegen_nummer = _fam_comp(_mod_act(celex="32025R0038"))
    with pytest.raises(ConversionError, match="spreekt NO.DOC 37/2025 tegen"):
        formex_xml.omzetten(formex_zip(act=_cons_act(fam_comp=tegen_nummer)))
    geen_celex = _fam_comp(_mod_act(celex="2025/37"))
    with pytest.raises(ConversionError, match="geen CELEX-nummer"):
        formex_xml.omzetten(formex_zip(act=_cons_act(fam_comp=geen_celex)))


def test_amendment_without_a_mark_in_the_text_gets_no_shown_field():
    # `shown: false` zou beweren dat de wijziging is overschreven; dat is voor
    # Formex niet gemeten. De melding zegt het, en het veld ontbreekt.
    _, _, _, extra = formex_xml.omzetten(formex_zip(act=_cons_act(fam_comp=_fam_comp(_mod_act()))))
    assert extra["metadata"]["amendments"] == [{"celex": "32025R0037"}]
    assert any("32025R0037" in m and "`shown` niet vastgesteld" in m
               for m in extra["metadata"]["waarschuwingen"])


def test_marks_that_name_an_act_outside_gr_mod_act_are_reported():
    data = formex_zip(act=_cons_act(fam_comp=_fam_comp(_mod_act()), markeringen=("32025R0037", "32024R1183")))
    _, _, _, extra = formex_xml.omzetten(data)
    assert extra["metadata"]["amendments"] == [{"celex": "32025R0037", "shown": True}]
    assert any("32024R1183" in m and "onvolledig" in m for m in extra["metadata"]["waarschuwingen"])


def test_mod_act_that_is_not_a_modification_is_left_out_with_a_reason_and_duplicates_count_once():
    fam = _fam_comp(_mod_act(), _mod_act(), _mod_act(celex="32025R0099", nummer="99", soort="COR"))
    _, _, _, extra = formex_xml.omzetten(formex_zip(act=_cons_act(fam_comp=fam, markeringen=("32025R0037",))))
    meta = extra["metadata"]
    assert [a["celex"] for a in meta["amendments"]] == ["32025R0037"]
    assert any("32025R0099" in m and "TYPE='COR'" in m for m in meta["waarschuwingen"])
    assert any("meer dan eens" in m for m in meta["waarschuwingen"])


def test_wijzigingsmarkeringen_are_counted_in_every_part_of_the_zip():
    # Een handeling die alleen een bijlage wijzigt heeft haar markering in dat onderdeel.
    zonder = b"<CONS.ANNEX><P>Tekst.</P></CONS.ANNEX>"
    met = (b'<CONS.ANNEX><?CLG.MDFO ID="O1" IDREF="C1" ACTIVE.DOC="32025R0037"?><P>Tekst.</P>'
           b'<?CLG.MDFC ID="C1" IDREF="O1"?></CONS.ANNEX>')
    data = formex_zip(act=_cons_act(markeringen=("32025R0037",)),
                      extra={"bijlage_1.xml": zonder, "bijlage_2.xml": met})
    assert formex_xml._wijzigingsmarkeringen(data) == {"32025R0037": 2}


def test_provenance_without_amendments_writes_an_empty_list():
    from mdconv.herkomst import Herkomst
    assert Herkomst(format="formex").as_json()["amendments"] == []


def _basis_act(*, jaar: int = 2019, nummer: int = 881, met_overwegingen: bool = True) -> bytes:
    """De basishandeling: BIB.INSTANCE/NO.DOC draagt haar identiteit, de PREAMBLE de considerans."""
    overwegingen = (
        b"<GR.CONSID><GR.CONSID.INIT>Overwegende hetgeen volgt:</GR.CONSID.INIT>"
        b"<CONSID><NP><NO.P>(1)</NO.P><TXT>Een eerste overweging van de basishandeling"
        b'<NOTE NOTE.ID="E0001" TYPE="FOOTNOTE"><P>PB C 227 van 28.6.2018, blz. 86.</P></NOTE>.</TXT></NP></CONSID>'
        b"<CONSID><NP><NO.P>(2)</NO.P><TXT>Een tweede overweging.</TXT></NP></CONSID></GR.CONSID>"
    ) if met_overwegingen else b""
    return (
        b"<ACT><BIB.INSTANCE><PAGE.FIRST>1</PAGE.FIRST>"
        + f'<NO.DOC FORMAT="YN" TYPE="OJ"><NO.CURRENT>{nummer}</NO.CURRENT><YEAR>{jaar}</YEAR>'
          "<COM>EU</COM></NO.DOC>".encode()
        + b"</BIB.INSTANCE><TITLE><P>Verordening (EU) 2019/881</P></TITLE>"
        b"<PREAMBLE><PREAMBLE.INIT>HET EUROPEES PARLEMENT EN DE RAAD,</PREAMBLE.INIT>"
        b"<GR.VISA><VISA>Gezien het Verdrag,</VISA></GR.VISA>" + overwegingen
        + b"<PREAMBLE.FINAL>HEBBEN DE VOLGENDE VERORDENING VASTGESTELD:</PREAMBLE.FINAL></PREAMBLE>"
        b"<ENACTING.TERMS><ARTICLE IDENTIFIER=\"001\"><TI.ART>Artikel 1</TI.ART><ALINEA><P>Niet gebruikt.</P>"
        b"</ALINEA></ARTICLE></ENACTING.TERMS></ACT>"
    )


def _cellar(monkeypatch, antwoorden: dict[str, bytes]):
    """Een Cellar die per CELEX-nummer antwoordt; al het andere is een 404."""
    from mdconv.sources import eurlex

    aanvragen: list[str] = []

    def get(url, **kwargs):
        aanvragen.append(url)
        data = antwoorden.get(url.rsplit("/", 1)[-1])
        if data is None:
            return SimpleNamespace(status_code=404, content=b"", url=url)
        return SimpleNamespace(status_code=200, content=data, url=url)

    monkeypatch.setattr(eurlex.net, "documents", lambda: SimpleNamespace(get=get))
    return aanvragen


def test_consolidated_formex_metadata_is_literal_and_missing_recitals_are_reported(monkeypatch):
    data = formex_zip(act=_cons_act())
    aanvragen = _cellar(monkeypatch, {"02019R0881-20250204": data})

    document = from_link("02019R0881-20250204", "NL")

    assert aanvragen[-1].endswith("/32019R0881")     # de basishandeling is gevraagd
    assert document.provenance.format == "clg"
    assert document.provenance.base_celex == "32019R0881"
    assert document.provenance.consolidation_date == "2025-02-04"
    assert document.provenance.version == "001.001.0"
    # De basishandeling was niet op te halen: dat mag nooit stil zijn.
    assert document.provenance.recitals_from is None
    assert "32019R0881" in document.provenance.recitals_reason
    assert "HTTP 404" in document.provenance.recitals_reason
    assert any("geen considerans" in melding for melding in document.provenance.waarschuwingen)
    assert "(1) " not in document.markdown.split("\n\n---\n\n")[0][:80]


def test_consolidated_formex_gets_the_preamble_of_the_base_act(monkeypatch):
    data = formex_zip(act=_cons_act())
    basis = formex_zip(act=_basis_act())
    aanvragen = _cellar(monkeypatch, {"02019R0881-20250204": data, "32019R0881": basis})

    document = from_link("02019R0881-20250204", "NL")

    nbsp = "\u00a0"
    regels = [r for r in document.markdown.split("\n") if r]
    volgorde = [
        "HET EUROPEES PARLEMENT EN DE RAAD,",
        "Overwegende hetgeen volgt:",
        "(1) Een eerste overweging van de basishandeling (1).",
        "(2) Een tweede overweging.",
        f"(1){nbsp}{nbsp}PB C 227 van 28.6.2018, blz. 86.",
        "HEBBEN DE VOLGENDE VERORDENING VASTGESTELD:",
        f"### Artikel{nbsp}1",
    ]
    posities = [regels.index(zin) for zin in volgorde]
    assert posities == sorted(posities), "aanhef, overwegingen, noten van de considerans, formule, bepalingen"
    # De noten van de wettekst beginnen opnieuw bij (1) en staan ná de bepalingen.
    assert regels.count(f"(1){nbsp}{nbsp}Een noot van de wettekst.") == 1
    assert regels.index(f"(1){nbsp}{nbsp}Een noot van de wettekst.") > posities[-1]

    assert document.provenance.recitals_from == "32019R0881"
    assert document.provenance.recitals_reason is None
    assert any("oorspronkelijke handeling" in melding and "32019R0881" in melding
               for melding in document.provenance.waarschuwingen)
    assert not any("geen considerans" in melding for melding in document.provenance.waarschuwingen)
    assert [u.rsplit("/", 1)[-1] for u in aanvragen] == ["02019R0881-20250204", "32019R0881"]

    bronnen = document.provenance.extra["source_structure"]["sources"]
    assert [(b["identifier"], b["role"]) for b in bronnen] == [
        ("02019R0881-20250204", "document"), ("32019R0881", "preamble")]
    assert base64.b64decode(bronnen[0]["original_base64"]) == data
    assert base64.b64decode(bronnen[1]["original_base64"]) == basis


def test_base_act_that_is_another_act_is_refused(monkeypatch):
    _cellar(monkeypatch, {"02019R0881-20250204": formex_zip(act=_cons_act()),
                          "32019R0881": formex_zip(act=_basis_act(nummer=999))})
    with pytest.raises(ConversionError, match="werd gevraagd"):
        from_link("02019R0881-20250204", "NL")


def test_base_act_without_recitals_keeps_the_consolidated_text_with_a_reason(monkeypatch):
    _cellar(monkeypatch, {"02019R0881-20250204": formex_zip(act=_cons_act()),
                          "32019R0881": formex_zip(act=_basis_act(met_overwegingen=False))})
    document = from_link("02019R0881-20250204", "NL")
    assert document.provenance.recitals_from is None
    assert "geen overwegingen" in document.provenance.recitals_reason
    assert "HEBBEN DE VOLGENDE" not in document.markdown


def test_a_base_act_is_refused_when_the_consolidated_text_has_its_own_recitals():
    cons = formex_zip(act=_cons_act(met_considerans=True))
    with pytest.raises(ConversionError, match="al een considerans"):
        formex_xml.omzetten(cons, formex_zip(act=_basis_act()))


def test_inserted_recitals_are_covered_by_the_text_preservation_check(monkeypatch):
    monkeypatch.setattr(formex_xml.FormexOmzetter, "overweging", lambda self, el: None)
    with pytest.raises(ConversionError, match="woordmultiset|structuurcontrole"):
        formex_xml.omzetten(formex_zip(act=_cons_act()), formex_zip(act=_basis_act()))


# De considerans van het Data Privacy Framework (2023/1795) en van het
# adequaatheidsbesluit voor het VK (2021/1772): overwegingen in geneste groepen
# (`DIV.CONSID`) met een genummerde kop in vet of cursief, en in 2017/2116 een
# kop zonder nummer. De overwegingen nummeren over de groepen heen door.
OVERWEGINGEN_IN_GROEPEN = (
    b'<GR.CONSID><GR.CONSID.INIT>Overwegende hetgeen volgt:</GR.CONSID.INIT>'
    b'<DIV.CONSID><TITLE><TI><NP><NO.P>1.</NO.P><TXT><HT TYPE="BOLD">INLEIDING</HT></TXT></NP></TI></TITLE>'
    b'<CONSID><NP><NO.P>(1)</NO.P><TXT>Eerste overweging.</TXT></NP></CONSID></DIV.CONSID>'
    b'<DIV.CONSID><TITLE><TI><NP><NO.P>2.</NO.P><TXT><HT TYPE="BOLD">BEOORDELING</HT></TXT></NP></TI></TITLE>'
    b'<DIV.CONSID><TITLE><TI><NP><NO.P>2.1</NO.P><TXT><HT TYPE="ITALIC">Toepassingsgebied</HT></TXT></NP></TI>'
    b'</TITLE><CONSID><NP><NO.P>(2)</NO.P><TXT>Tweede overweging.</TXT></NP></CONSID></DIV.CONSID>'
    b'<DIV.CONSID><TITLE><TI><P>Referentiestelsel</P></TI></TITLE>'
    b'<CONSID><NP><NO.P>(3)</NO.P><TXT>Derde overweging.</TXT></NP></CONSID></DIV.CONSID>'
    b'</DIV.CONSID></GR.CONSID>'
)


def met_overwegingen_in_groepen(groepen: bytes = OVERWEGINGEN_IN_GROEPEN) -> bytes:
    voor, rest = ACT.split(b"<GR.CONSID>", 1)
    return formex_zip(act=voor + groepen + rest.split(b"</GR.CONSID>", 1)[1])


def test_overwegingen_in_een_groep_blijven_overwegingen_en_hun_kop_wordt_een_alinea():
    """Tot 23 september 2026 weigerde `DIV.CONSID` 9 van de 347 documenten in de meetlat."""
    markdown, eenheden, _, _ = formex_xml.omzetten(met_overwegingen_in_groepen())
    assert ("Overwegende hetgeen volgt:\n\n1.   INLEIDING\n\n(1) Eerste overweging.\n\n"
            "2.   BEOORDELING\n\n2.1   Toepassingsgebied\n\n(2) Tweede overweging."
            "\n\nReferentiestelsel\n\n(3) Derde overweging.\n") in markdown
    assert "**" not in markdown and "*Toepassingsgebied*" not in markdown
    assert [e.anker for e in eenheden if e.soort == "overweging"] == ["rec-1", "rec-2", "rec-3"]
    assert {e.soort for e in eenheden} == {"overweging", "divisie", "artikel", "lid", "onderdeel"}


@pytest.mark.parametrize("oud, nieuw, context", [
    # Een alinea direct in een groep: niet gemeten, dus niet geraden waar ze hoort.
    (b"</DIV.CONSID></GR.CONSID>", b"<P>Losse alinea.</P></DIV.CONSID></GR.CONSID>", "overwegingen:P"),
    # Een opschrift onder de kop: geen van de 330 koppen in de meetlat heeft er een.
    (b"<TI><P>Referentiestelsel</P></TI>", b"<TI><P>Referentiestelsel</P></TI><STI>Opschrift</STI>",
     "overwegingenkop:STI"),
])
def test_wat_in_een_groep_overwegingen_niet_gemeten_is_weigert(oud, nieuw, context):
    groepen = OVERWEGINGEN_IN_GROEPEN.replace(oud, nieuw, 1)
    with pytest.raises(ConversionError, match=context):
        formex_xml.omzetten(met_overwegingen_in_groepen(groepen))


def test_emphasis_inside_a_word_does_not_split_the_word():
    """`cyberbeveiliging<HT TYPE="BOLD">s</HT>certificering` is één woord in de bron."""
    act = ACT.replace(
        b"Deze verordening stelt regels vast.",
        b'Deze verordening stelt cyberbeveiliging<HT TYPE="BOLD">s</HT>regels vast en <HT TYPE="BOLD">dit</HT> blijft vet.',
    )
    markdown = formex_xml.omzetten(formex_zip(act=act))[0]
    assert "cyberbeveiligingsregels" in markdown
    assert "**dit**" in markdown           # opmaak die niet aan een woord vastzit blijft staan
    assert "**s**" not in markdown


def test_manifest_and_zip_must_name_exactly_the_same_parts():
    with pytest.raises(ConversionError, match="buiten het documentmanifest"):
        formex_xml.omzetten(formex_zip(extra={"stil-vergeten.xml": b"<ANNEX/>"}))


INGESLOTEN_BIJLAGE = b"""<ANNEX NNC="YES">
<BIB.INSTANCE><NO.SEQ>0001.0001</NO.SEQ></BIB.INSTANCE>
<TITLE><TI><P><QUOT.START CODE="201C" ID="QS0001" REF.END="QE0001"/>Bijlage XIV</P></TI></TITLE>
<CONTENTS><P>De lijst met codes van AI-systemen.</P>
<GR.SEQ LEVEL="1"><TITLE><TI><NP><NO.P>1.</NO.P><TXT><HT TYPE="NORMAL">Inleiding</HT></TXT></NP></TI></TITLE>
<P><TBL COLS="2" NO.SEQ="0001"><CORPUS><ROW TYPE="HEADER"><CELL COL="1" TYPE="HEADER">Code</CELL>
<CELL COL="2" TYPE="HEADER"><IE/></CELL></ROW><ROW><CELL COL="1">AIP 0102</CELL>
<CELL COL="2">Machines en veiligheidscomponenten.</CELL></ROW></CORPUS></TBL></P></GR.SEQ>
<P>Slotzin van de bijlage.<QUOT.END CODE="201D" ID="QE0001" REF.START="QS0001"/>.</P>
</CONTENTS></ANNEX>"""


def _wijzigingshandeling(*, inclusies: bytes = b'<INCLUSIONS><INCL.ELEMENT FILEREF="L_test.003601.fmx.xml" TYPE="FORMEX.DOC"/></INCLUSIONS>',
                         aanroep: bytes = b'<P><QUOT.S LEVEL="1"><INCL.ELEMENT FILEREF="L_test.003601.fmx.xml" TYPE="FORMEX.DOC"/></QUOT.S></P>') -> bytes:
    """Een wijzigingshandeling die een bijlage in een andere handeling invoegt (32026R1744, punt 43)."""
    return (b"""<ACT NNC="YES">
<BIB.INSTANCE><PAGE.FIRST>1</PAGE.FIRST>""" + inclusies + b"""</BIB.INSTANCE>
<TITLE><P><HT TYPE="UC">Verordening (EU) 2026/1744</HT></P></TITLE>
<PREAMBLE><GR.CONSID><CONSID><NP><NO.P>(1)</NO.P><TXT>Vereenvoudiging is nodig.</TXT></NP></CONSID></GR.CONSID></PREAMBLE>
<ENACTING.TERMS><ARTICLE IDENTIFIER="001"><TI.ART>Artikel 1</TI.ART><STI.ART><P>Wijzigingen</P></STI.ART>
<ALINEA>Verordening (EU) 2024/1689 wordt als volgt gewijzigd:<LIST TYPE="ARAB"><ITEM><NP><NO.P>43)</NO.P>
<TXT>de volgende bijlage wordt toegevoegd:</TXT>""" + aanroep + b"""</NP></ITEM></LIST></ALINEA>
</ARTICLE></ENACTING.TERMS><FINAL><P>Gedaan te Brussel.</P></FINAL></ACT>""")


def test_an_annex_the_act_includes_is_rendered_as_a_quoted_block_where_the_text_calls_it():
    """32026R1744 (Digitale omnibus AI) draagt bijlage XIV als los zipbestand dat niet het
    manifest maar de handeling zelf aanwijst (BIB.INSTANCE/INCLUSIONS); punt 43 roept het aan
    binnen een QUOT.S. Het is tekst van een andere handeling: wel brontekst, geen structuur."""
    data = formex_zip(act=_wijzigingshandeling(), extra={"L_test.003601.fmx.xml": INGESLOTEN_BIJLAGE})

    markdown, eenheden, onbekend, _ = formex_xml.omzetten(data)

    assert onbekend == {}
    assert markdown.index("43) de volgende bijlage") < markdown.index("\u201cBijlage XIV") < markdown.index("AIP 0102")
    assert "Slotzin van de bijlage.\u201d." in markdown
    assert "## " not in markdown.split("\u201cBijlage XIV", 1)[1]        # geen eigen bijlagekop
    assert not [e for e in eenheden if e.soort in ("bijlage", "bijlagedeel")]
    assert [e.anker for e in eenheden if e.soort == "artikel"] == ["art-1"]
    assert sum(regel.startswith("### ") for regel in markdown.splitlines()) == 1


def test_an_included_file_the_text_never_calls_is_refused():
    data = formex_zip(act=_wijzigingshandeling(aanroep=b"<P>Zie bijlage.</P>"),
                      extra={"L_test.003601.fmx.xml": INGESLOTEN_BIJLAGE})
    with pytest.raises(ConversionError, match="nergens in de tekst aangeroepen"):
        formex_xml.omzetten(data)


def test_an_inclusion_missing_from_the_zip_is_refused():
    with pytest.raises(ConversionError, match="ontbrekende inclusies"):
        formex_xml.omzetten(formex_zip(act=_wijzigingshandeling()))


def test_an_inclusion_with_text_beside_it_is_still_refused():
    """Alleen de kale vorm `<P><QUOT.S><INCL.ELEMENT/></QUOT.S></P>` is gemeten."""
    aanroep = b'<P>Tekst ervoor <QUOT.S LEVEL="1"><INCL.ELEMENT FILEREF="L_test.003601.fmx.xml" TYPE="FORMEX.DOC"/></QUOT.S></P>'
    data = formex_zip(act=_wijzigingshandeling(aanroep=aanroep),
                      extra={"L_test.003601.fmx.xml": INGESLOTEN_BIJLAGE})
    with pytest.raises(ConversionError, match="INCL.ELEMENT"):
        formex_xml.omzetten(data)


def test_eli_link_in_a_note_keeps_its_visible_text_and_no_markup():
    """02024R1689-20260727: het Publicatieblad zet sinds 2026 `<LINK URI=…>` achter elke
    REF.DOC.OJ in een noot; de zichtbare tekst is de URI zelf."""
    act = ACT.replace(
        b"<P>Deze verordening stelt regels vast.</P>",
        b'<P>Deze verordening stelt regels vast.<NOTE NOTE.ID="E0001" NUMBERING="ARAB"><P>PB L 316 van '
        b'14.11.2012, blz. 12, ELI: <LINK URI="http://data.europa.eu/eli/reg/2012/1025/oj">'
        b"http://data.europa.eu/eli/reg/2012/1025/oj</LINK>.</P></NOTE></P>",
    )

    markdown, _, onbekend, _ = formex_xml.omzetten(formex_zip(act=act))

    assert onbekend == {}
    assert "ELI: http://data.europa.eu/eli/reg/2012/1025/oj." in markdown
    assert "URI=" not in markdown and "](http" not in markdown


def test_articles_quoted_inside_an_amendment_run_inline_and_do_not_count_as_own_articles():
    """32026R1744 citeert zeven hele artikelen binnen QUOT.S; kop, opschrift en leden lopen
    inline door zoals geciteerde leden al deden, en de structuurcontrole telt ze niet mee."""
    citaat = (b'<P><QUOT.S LEVEL="1"><ARTICLE IDENTIFIER="004"><TI.ART>Artikel 4</TI.ART>'
              b"<STI.ART><P>AI-geletterdheid</P></STI.ART><PARAG><NO.PARAG>1.</NO.PARAG>"
              b"<ALINEA>Aanbieders nemen maatregelen.</ALINEA></PARAG></ARTICLE></QUOT.S></P>")
    data = formex_zip(act=_wijzigingshandeling(inclusies=b"", aanroep=citaat))

    markdown, eenheden, onbekend, _ = formex_xml.omzetten(data)

    assert onbekend == {}
    assert "Artikel 4 AI-geletterdheid 1. Aanbieders nemen maatregelen." in markdown
    assert sum(regel.startswith("### ") for regel in markdown.splitlines()) == 1
    assert [e.anker for e in eenheden if e.soort == "artikel"] == ["art-1"]
    assert not [e for e in eenheden if e.soort == "lid"]


def test_een_geciteerde_afdeling_met_artikelen_loopt_inline_zonder_kop_of_eenheden():
    """eIDAS 2 (32024R1183) voegt zes hele afdelingen in, van AFDELING 1 (artikel 5 bis tot en
    met 5 septies) tot HOOFDSTUK IV BIS; dat weigerde als `inline:DIVISION`. Een `##`-kop of een
    eigen alinea per lid zou de kennisbank lezen als structuur van déze handeling."""
    citaat = (b'<P><QUOT.S LEVEL="1"><DIVISION><TITLE><TI><P><HT TYPE="ITALIC">'
              b'<QUOT.START CODE="201C" ID="Q1" REF.END="E1"/>AFDELING 1</HT></P></TI>'
              b'<STI><P><HT TYPE="BOLD">EUROPESE PORTEMONNEE</HT></P></STI></TITLE>'
              b'<ARTICLE IDENTIFIER="005A"><TI.ART>Artikel 5 bis</TI.ART><STI.ART><P>Portemonnees</P></STI.ART>'
              b'<PARAG IDENTIFIER="005A.001"><NO.PARAG>1.</NO.PARAG><ALINEA>De lidstaten verstrekken er een.'
              b'</ALINEA></PARAG><PARAG IDENTIFIER="005A.002"><NO.PARAG>2.</NO.PARAG><ALINEA>Zij doen dat gratis.'
              b'<QUOT.END CODE="201D" ID="E1" REF.START="Q1"/></ALINEA></PARAG></ARTICLE></DIVISION></QUOT.S></P>')
    data = formex_zip(act=_wijzigingshandeling(inclusies=b"", aanroep=citaat))

    markdown, eenheden, onbekend, _ = formex_xml.omzetten(data)

    assert onbekend == {}
    assert ("\n“AFDELING 1 EUROPESE PORTEMONNEE Artikel 5 bis Portemonnees 1. De lidstaten "
            "verstrekken er een. 2. Zij doen dat gratis.”\n") in markdown
    assert not [regel for regel in markdown.splitlines() if regel.startswith("## ")]
    assert sum(regel.startswith("### ") for regel in markdown.splitlines()) == 1
    assert [e.anker for e in eenheden if e.soort in ("divisie", "artikel", "lid")] == ["art-1"]


def test_een_geciteerde_definitielijst_loopt_inline_met_nummer_term_en_definitie_los():
    """De interoperabiliteitsverordening (32019R0817) voegt zo drie definities aan artikel 4 van
    een andere verordening toe; PREFIX, TERM en DEFINITION staan in de bron tegen elkaar aan."""
    citaat = (b'<P><QUOT.S LEVEL="1"><DLIST SEPARATOR=":"><DLIST.ITEM><PREFIX>'
              b'<QUOT.START CODE="201C" ID="Q1" REF.END="E1"/>12)</PREFIX><TERM>VIS-gegevens</TERM>'
              b'<DEFINITION>alle gegevens in het VIS;</DEFINITION></DLIST.ITEM><DLIST.ITEM><PREFIX>13)</PREFIX>'
              b'<TERM>identiteitsgegevens</TERM><DEFINITION>de gegevens van artikel 9.'
              b'<QUOT.END CODE="201D" ID="E1" REF.START="Q1"/></DEFINITION></DLIST.ITEM></DLIST></QUOT.S></P>')
    data = formex_zip(act=_wijzigingshandeling(inclusies=b"", aanroep=citaat))

    markdown, eenheden, onbekend, _ = formex_xml.omzetten(data)

    assert onbekend == {}
    assert ("\n“12) VIS-gegevens alle gegevens in het VIS; 13) identiteitsgegevens "
            "de gegevens van artikel 9.”\n") in markdown
    assert not [e for e in eenheden if e.soort == "onderdeel" and e.anker.endswith(("-12", "-13"))]


def test_geciteerde_punten_zonder_witruimte_ertussen_blijven_gescheiden():
    """32025R0038 citeert een bijlageonderdeel met losse NP's tegen elkaar aan; transparant gaf
    dat `cyberbeveiliging3.2.` en weigerde de woordcontrole op woorden aan elkaar."""
    citaat = (b'<P><QUOT.S LEVEL="1"><GR.SEQ LEVEL="1"><TITLE><TI><P>'
              b'<QUOT.START CODE="201C" ID="Q1" REF.END="E1"/>Specifieke doelstelling 3</P></TI></TITLE>'
              b'<NP><NO.P>3.1.</NO.P><TXT>Het aantal voorzieningen voor cyberbeveiliging</TXT></NP>'
              b'<NP><NO.P>3.2.</NO.P><TXT>Het aantal gebruikers'
              b'<QUOT.END CODE="201D" ID="E1" REF.START="Q1"/></TXT></NP></GR.SEQ></QUOT.S>;</P>')
    data = formex_zip(act=_wijzigingshandeling(inclusies=b"", aanroep=citaat))

    markdown = formex_xml.omzetten(data)[0]

    assert ("\n“Specifieke doelstelling 3 3.1. Het aantal voorzieningen voor cyberbeveiliging "
            "3.2. Het aantal gebruikers”") in markdown


GECITEERDE_TABEL = (
    b'<QUOT.S LEVEL="1"><TBL COLS="2" NO.SEQ="0001"><CORPUS><ROW TYPE="HEADER">'
    b'<CELL COL="1" TYPE="HEADER">Stof</CELL><CELL COL="2" TYPE="HEADER">Grenswaarde</CELL></ROW>'
    b'<ROW><CELL COL="1"><QUOT.START CODE="201E" ID="Q1" REF.END="E1"/>Fenol'
    b'<NOTE NOTE.ID="E0001"><P>Eerste noot.</P></NOTE></CELL>'
    b'<CELL COL="2">5 mg/l<QUOT.END CODE="201D" ID="E1" REF.START="Q1"/></CELL></ROW></CORPUS></TBL></QUOT.S>'
)


def test_een_geciteerde_tabel_komt_als_blok_waar_de_tekst_haar_citeert():
    """De PIC-verordening (32014R0167) voegt zo zes vermeldingen toe aan een tabel van een andere
    verordening: `<P><QUOT.S><TBL>` naast de TXT van een onderdeel; dat weigerde als `inline:TBL`.
    Een tabel bestaat alleen als blok; de alinea breekt daar, zonder eigen eenheden, en de noot
    in de tabel krijgt haar nummer vóór die in de tekst erna."""
    act = ACT.replace(
        b"<TXT>eerste onderdeel;</TXT>",
        b"<TXT>de volgende vermelding wordt toegevoegd:</TXT><P>" + GECITEERDE_TABEL + b"</P>",
    ).replace(b"<TXT>tweede onderdeel.</TXT>",
              b'<TXT>tweede onderdeel<NOTE NOTE.ID="E0002"><P>Tweede noot.</P></NOTE>.</TXT>')

    markdown, eenheden, onbekend, _ = formex_xml.omzetten(formex_zip(act=act))

    assert onbekend == {}
    assert ("\na) de volgende vermelding wordt toegevoegd:\n\n|  |  |\n| --- | --- |\n"
            "| Stof | Grenswaarde |\n| “Fenol (1) | 5 mg/l” |\n\nb) tweede onderdeel (2).\n") in markdown
    assert markdown.index("(1)  Eerste noot.") < markdown.index("(2)  Tweede noot.")
    assert [e.anker for e in eenheden if e.soort == "onderdeel"] == ["art-1-1-a", "art-1-1-b"]


def test_een_geciteerde_alinea_voor_een_geciteerde_tabel_blijft_een_eigen_alinea():
    """32022R0469 vervangt een overweging door een alinea met een tabel; 32017L0774 zet de
    geciteerde tabel direct in een ALINEA, na de P die haar aankondigt."""
    citaat = GECITEERDE_TABEL.replace(
        b'<QUOT.S LEVEL="1">',
        b'<QUOT.S LEVEL="1"><P><QUOT.START CODE="201C" ID="Q0" REF.END="E0"/>De percentages bedragen:</P>')
    act = ACT.replace(b"</ARTICLE>", b"<ALINEA><P>Overweging 217 wordt vervangen door:</P>" + citaat
                      + b"</ALINEA></ARTICLE>", 1)

    markdown = formex_xml.omzetten(formex_zip(act=act))[0]

    assert ("\nOverweging 217 wordt vervangen door:\n\n“De percentages bedragen:\n\n|  |  |\n"
            "| --- | --- |\n| Stof | Grenswaarde |\n") in markdown


def test_een_geciteerde_tabel_waar_alleen_tekst_kan_staan_wordt_geweigerd():
    """In de TXT van een onderdeel wordt de regel zonder `schrijf()` geschreven; een tabel kan
    daar niet, en dan zou ze nergens staan."""
    act = ACT.replace(b"<TXT>eerste onderdeel;</TXT>", b"<TXT>eerste onderdeel: " + GECITEERDE_TABEL + b"</TXT>")
    with pytest.raises(ConversionError, match="alleen tekst kan staan"):
        formex_xml.omzetten(formex_zip(act=act))


def test_een_lid_dat_met_een_geciteerde_tabel_begint_wordt_geweigerd():
    """Het lidnummer hoort op de eerste regel tekst; die vorm is niet gemeten."""
    act = ACT.replace(b"<ALINEA><P>Deze verordening stelt regels vast.</P>",
                      b"<ALINEA>" + GECITEERDE_TABEL + b"</ALINEA><ALINEA><P>Deze verordening stelt regels vast.</P>")
    with pytest.raises(ConversionError, match="lid dat met een geciteerde tabel begint"):
        formex_xml.omzetten(formex_zip(act=act))


def test_bijlageonderdelen_in_een_tabelcel_worden_tekst_van_die_cel():
    """De lijst van goedgekeurde werkzame stoffen (32011R0704) zet `DEEL A` en `DEEL B` als GR.SEQ
    in één cel; dat weigerde als `inline:GR.SEQ`. Een cel draagt geen structuur."""
    cel = (b'<CELL COL="2"><GR.SEQ LEVEL="1"><TITLE><TI><P>DEEL A</P></TI></TITLE>'
           b'<P>Alleen als herbicide.</P></GR.SEQ><GR.SEQ LEVEL="1"><TITLE><TI><P>DEEL B</P></TI></TITLE>'
           b'<P>Let op:</P><LIST TYPE="ARAB"><ITEM><NP><NO.P>1.</NO.P><TXT>het grondwater.</TXT></NP></ITEM>'
           b'</LIST></GR.SEQ></CELL>')
    act = ACT.replace(b"</ARTICLE>", b'<ALINEA><TBL COLS="2"><CORPUS><ROW><CELL COL="1">Azimsulfuron</CELL>'
                      + cel + b"</ROW></CORPUS></TBL></ALINEA></ARTICLE>", 1)

    markdown = formex_xml.omzetten(formex_zip(act=act))[0]

    assert "| Azimsulfuron | DEEL A Alleen als herbicide. DEEL B Let op: 1. het grondwater. |" in markdown


def test_een_afdeling_inline_buiten_een_citaat_blijft_een_weigering():
    """Buiten een citaat is een DIVISION een eenheid van de handeling zelf; die mag niet stil
    in een alinea opgaan."""
    act = ACT.replace(b"<P>Deze verordening stelt regels vast.</P>",
                      b"<P>Deze verordening stelt regels vast. <DIVISION><TITLE><TI>HOOFDSTUK II</TI>"
                      b"</TITLE></DIVISION></P>")
    with pytest.raises(ConversionError, match=r"inline:DIVISION"):
        formex_xml.omzetten(formex_zip(act=act))


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


def test_italic_in_a_heading_is_typography_and_does_not_reach_the_heading_line():
    """Het Publicatieblad zet `HOOFDSTUK II` in een geconsolideerde tekst cursief.

    De planner herkent een kop aan zijn kale vorm; `## *HOOFDSTUK II*` kreeg geen anker.
    """
    act = ACT.replace(b"<TI>HOOFDSTUK I</TI>", b'<TI><P><HT TYPE="ITALIC">HOOFDSTUK I</HT></P></TI>').replace(
        b"<STI>Algemene bepalingen</STI>",
        b'<STI><P><HT TYPE="BOLD"><HT TYPE="ITALIC">Algemene bepalingen</HT></HT></P></STI>')
    markdown = formex_xml.omzetten(formex_zip(act=act))[0]
    assert "\n## HOOFDSTUK I\n" in markdown
    assert "\n**Algemene bepalingen**\n" in markdown


def test_a_numbered_point_inside_a_quoted_amendment_keeps_a_space_after_its_number():
    """`“67)Verordening` stond aaneen: NO.P en TXT van een NP in QUOT.S liepen zonder spatie in elkaar.

    De woorden waren gelijk, dus geen enkele controle zag het; de tekst las wel slecht.
    """
    act = ACT.replace(
        b"</ARTICLE>",
        b'<ALINEA><P>Aan de bijlage wordt het volgende punt toegevoegd:</P><QUOT.S LEVEL="1">'
        b'<LIST TYPE="ARAB"><ITEM><NP><NO.P><QUOT.START CODE="201C" ID="Q1" REF.END="E1"/>67)</NO.P>'
        b'<TXT>Verordening (EU) 2022/1925<QUOT.END CODE="201D" ID="E1" REF.START="Q1"/></TXT></NP></ITEM>'
        b'</LIST></QUOT.S></ALINEA></ARTICLE>', 1)
    markdown = formex_xml.omzetten(formex_zip(act=act))[0]
    assert "“67) Verordening (EU) 2022/1925”" in markdown
    assert "67)Verordening" not in markdown


def test_the_title_of_a_table_is_written_as_a_paragraph_above_it():
    """`TBL/TITLE` viel weg; de woordcontrole weigerde daardoor 12 documenten op "CONCORDANTIETABEL"."""
    act = ACT.replace(
        b"</ARTICLE>",
        b'<ALINEA><TBL COLS="2" NO.SEQ="0001"><TITLE><TI><P><HT TYPE="BOLD">CONCORDANTIETABEL</HT></P>'
        b'</TI></TITLE><CORPUS><ROW TYPE="HEADER"><CELL COL="1" TYPE="HEADER">Richtlijn 2007/64/EG</CELL>'
        b'<CELL COL="2" TYPE="HEADER">Deze richtlijn</CELL></ROW><ROW><CELL COL="1">Artikel 1</CELL>'
        b'<CELL COL="2">Artikel 2</CELL></ROW></CORPUS></TBL></ALINEA></ARTICLE>', 1)
    markdown = formex_xml.omzetten(formex_zip(act=act))[0]
    assert "\n\nCONCORDANTIETABEL\n\n|  |  |\n" in markdown
    assert "**CONCORDANTIETABEL**" not in markdown


def test_quotation_marks_and_emphasis_inside_a_table_cell_do_not_get_spaces():
    """`“ smart home ” -apparaat`: in een cel kreeg elk inline element spaties om zich heen."""
    act = ACT.replace(
        b"</ARTICLE>",
        b'<ALINEA><TBL COLS="2" NO.SEQ="0001"><CORPUS><ROW TYPE="HEADER"><CELL COL="1">Naam</CELL>'
        b'<CELL COL="2">Omschrijving</CELL></ROW><ROW><CELL COL="1">Assistent</CELL><CELL COL="2">een knop of een '
        b'<QUOT.START CODE="201C" ID="Q1" REF.END="E1"/>smart home<QUOT.END CODE="201D" ID="E1" REF.START="Q1"/>-apparaat, '
        b'<HT TYPE="BOLD">vet</HT> en dan door.</CELL></ROW></CORPUS></TBL></ALINEA></ARTICLE>', 1)
    markdown = formex_xml.omzetten(formex_zip(act=act))[0]
    assert "een knop of een “smart home”-apparaat, **vet** en dan door." in markdown
    assert "“ smart" not in markdown


def test_een_adresblok_in_een_bijlage_wordt_een_alinea_per_regel():
    """De brieven in de bijlagen van het Data Privacy Framework (2023/1795): het adres onder de datum."""
    brief = (b'<P><DATE ISO="20230706">6 juli 2023</DATE></P><ADDR.S><P>De heer Didier Reynders</P>'
             b'<P>Wetstraat 200</P><P>1049 Brussel</P></ADDR.S><P>Geachte commissaris Reynders,</P>')
    markdown, eenheden, _, _ = formex_xml.omzetten(_met_bijlagen(_bijlage(b"BIJLAGE II", brief)))
    assert ("\n\n6 juli 2023\n\nDe heer Didier Reynders\n\nWetstraat 200\n\n1049 Brussel\n\n"
            "Geachte commissaris Reynders,\n") in markdown
    assert [e.soort for e in eenheden if e.anker.startswith("annex")] == ["bijlage"]


def test_een_adresblok_in_een_tabelcel_staat_met_spaties_tussen_zijn_regels():
    """De Europese lijst van scheepsrecyclinginrichtingen (2020/1675): 44 cellen met een ADDR.S."""
    act = ACT.replace(
        b"</ARTICLE>",
        b'<ALINEA><TBL COLS="2"><CORPUS><ROW TYPE="HEADER"><CELL COL="1">Inrichting</CELL>'
        b'<CELL COL="2">Methode</CELL></ROW><ROW><CELL COL="1"><P>NV Galloo Recycling Gent</P><ADDR.S>'
        b'<P>Scheepzatestraat 9</P><P>9000 Gent</P></ADDR.S><P>Tel. +32 92512521</P></CELL>'
        b'<CELL COL="2">Langszij</CELL></ROW></CORPUS></TBL></ALINEA></ARTICLE>', 1)
    markdown = formex_xml.omzetten(formex_zip(act=act))[0]
    assert "| NV Galloo Recycling Gent Scheepzatestraat 9 9000 Gent Tel. +32 92512521 | Langszij |" in markdown


@pytest.mark.parametrize("adres, context", [
    (b"<P>Het adres is <ADDR.S><P>Wetstraat 200</P></ADDR.S> te Brussel.</P>", "inline:ADDR.S"),
    (b"<ADDR.S>Wetstraat 200<P>1049 Brussel</P></ADDR.S>", "inhoud:ADDR.S"),
])
def test_een_adresblok_midden_in_een_zin_of_met_losse_tekst_weigert(adres, context):
    with pytest.raises(ConversionError, match=context):
        formex_xml.omzetten(_met_bijlagen(_bijlage(b"BIJLAGE II", adres)))


# ------------------------------------------------------------------ algemene documenten (GENERAL)

VERKLARING = (
    b'<GENERAL><BIB.INSTANCE><PAGE.FIRST>11</PAGE.FIRST></BIB.INSTANCE><TITLE><TI>'
    b'<P><HT TYPE="UC">Gezamenlijke verklaring</HT></P><P>over het Galileo interinstitutioneel panel</P>'
    b'</TI></TITLE><CONTENTS><NP><NO.P>1.</NO.P><TXT>Het panel volgt de volgende zaken:</TXT><P>'
    b'<LIST TYPE="alpha"><ITEM><NP><NO.P>a)</NO.P><TXT>de aanbestedingen;</TXT></NP></ITEM></LIST></P></NP>'
    b'<P>De Commissie houdt rekening met de standpunten.</P></CONTENTS></GENERAL>'
)


def test_a_declaration_after_the_act_is_written_after_it_without_units():
    """Rome II (32007R0864), de geoblockingverordening (32018R0302) en 32008R0683 (Galileo) dragen
    een verklaring als GENERAL achter de handeling; dat weigerde het hele document."""
    markdown, eenheden, onbekend, extra = formex_xml.omzetten(_met_bijlagen(VERKLARING))
    verklaring = markdown.split("\n---\n")[-1]
    assert verklaring.startswith("\nGEZAMENLIJKE VERKLARING\n\nover het Galileo interinstitutioneel panel\n")
    assert "\n1.\u00a0\u00a0\u00a0Het panel volgt de volgende zaken:\n\na) de aanbestedingen;\n" in verklaring
    assert verklaring.rstrip().endswith("De Commissie houdt rekening met de standpunten.")
    alleen_de_handeling = formex_xml.omzetten(formex_zip())[1]
    assert [e.anker for e in eenheden] == [e.anker for e in alleen_de_handeling]
    assert extra["metadata"]["oj_reference"] == "PB L 265 van 12.10.2022, blz. 1"
    assert onbekend == {}


def test_a_general_document_on_its_own_carries_the_citation_and_its_notes():
    """32006D0857 (samenvatting van de AstraZeneca-beschikking) is als geheel een GENERAL, met een PROLOG."""
    algemeen = (
        '<GENERAL><BIB.INSTANCE><PAGE.FIRST>24</PAGE.FIRST></BIB.INSTANCE><TITLE><TI>'
        '<P><HT TYPE="UC">Beschikking van de Commissie</HT></P><P>(Zaak COMP/A.37.507/F3 — AstraZeneca)'
        '<NOTE NOTE.ID="E0001"><P>Advies van het Adviescomité.</P></NOTE></P></TI></TITLE>'
        '<PROLOG><P>Op 15 juni 2005 heeft de Commissie een beschikking gegeven.</P></PROLOG>'
        '<CONTENTS><GR.SEQ LEVEL="1"><TITLE><TI><NP><NO.P>1.</NO.P><TXT>GELDBOETEN</TXT></NP></TI></TITLE>'
        '<P>De boete bedraagt 60 000 000 EUR.</P></GR.SEQ></CONTENTS></GENERAL>'
    ).encode()
    markdown, eenheden, onbekend, extra = formex_xml.omzetten(formex_zip(act=algemeen))
    assert markdown == (
        "---\n\nBESCHIKKING VAN DE COMMISSIE\n\n(Zaak COMP/A.37.507/F3 — AstraZeneca) (1)\n\n"
        "Op 15 juni 2005 heeft de Commissie een beschikking gegeven.\n\n1.\u00a0\u00a0\u00a0GELDBOETEN\n\n"
        "De boete bedraagt 60 000 000 EUR.\n\n(1)\u00a0\u00a0Advies van het Adviescomité.\n")
    assert eenheden == []
    assert extra["metadata"]["oj_reference"] == "PB L 265 van 12.10.2022, blz. 24"
    assert onbekend == {}


def test_a_paragraph_that_starts_with_a_list_writes_its_number_once_with_hard_spaces():
    """Artikel 6, lid 3 van Rome II (32007R0864): een lid dat meteen met a) begint werd `3.   3.`.

    Het nummer staat op een eigen regel met de drie harde spaties waaraan het profiel een
    kaal lidnummer herkent; het lidanker hoort bij die regel, de onderdelen eronder."""
    act = ACT.replace(b"<ALINEA><P>Deze verordening stelt regels vast.</P>", b"<ALINEA>", 1)
    markdown, eenheden, onbekend, _ = formex_xml.omzetten(formex_zip(act=act))
    assert "\n\n1.\u00a0\u00a0\u00a0\n\na) eerste onderdeel;\n\nb) tweede onderdeel.\n\n" in markdown
    assert "1.\u00a0\u00a0\u00a01." not in markdown
    assert [(e.anker, e.tekst) for e in eenheden if e.anker.startswith("art-1-1")] == [
        ("art-1-1", "1."), ("art-1-1-a", "a) eerste onderdeel;"), ("art-1-1-b", "b) tweede onderdeel.")]
    assert onbekend == {}


def test_a_general_document_with_an_unmeasured_part_is_refused():
    onbekend_deel = VERKLARING.replace(b"</CONTENTS>", b"</CONTENTS><FINAL><P>Gedaan te Brussel.</P></FINAL>")
    with pytest.raises(ConversionError, match="algemeen:FINAL"):
        formex_xml.omzetten(_met_bijlagen(onbekend_deel))


# De vorm van artikel 4 AVG en artikel 3 LED: een definitielijst waarvan een
# deel van de punten zelf een opsomming draagt. Tot 22 september 2026 ging
# DEFINITION altijd door inline(), zodat `16) “term” a) … b) …` één alinea werd.
# De definities krijgen een eigen artikel: een artikel met genummerde leden
# *en* een definitielijst eronder zou twee keer `art-N-1` opleveren, en dat
# weigert de structuurcontrole terecht.
DEFINITIES = (
    b'</ARTICLE><ARTICLE IDENTIFIER="2"><TI.ART>Artikel 2</TI.ART><STI.ART>Definities</STI.ART>'
    b'<ALINEA><P>Voor de toepassing van deze verordening wordt verstaan onder:</P><DLIST>'
    b'<DLIST.ITEM><PREFIX>1)</PREFIX>'
    b'<TERM><QUOT.START CODE="201E" ID="Q1" REF.END="E1"/>persoonsgegevens'
    b'<QUOT.END CODE="201D" ID="E1" REF.START="Q1"/></TERM>'
    b'<DEFINITION>alle informatie over een natuurlijke persoon;</DEFINITION></DLIST.ITEM>'
    b'<DLIST.ITEM><PREFIX>16)</PREFIX>'
    b'<TERM><QUOT.START CODE="201E" ID="Q2" REF.END="E2"/>hoofdvestiging'
    b'<QUOT.END CODE="201D" ID="E2" REF.START="Q2"/></TERM>'
    b'<DEFINITION><LIST TYPE="alpha">'
    b'<ITEM><NP><NO.P>a)</NO.P><TXT>de plaats van de centrale administratie;</TXT></NP></ITEM>'
    b'<ITEM><NP><NO.P>b)</NO.P><TXT>de plaats van de voornaamste activiteiten;</TXT></NP></ITEM>'
    b'</LIST></DEFINITION></DLIST.ITEM>'
    b'<DLIST.ITEM><PREFIX>22)</PREFIX>'
    b'<TERM><QUOT.START CODE="201E" ID="Q3" REF.END="E3"/>betrokken autoriteit'
    b'<QUOT.END CODE="201D" ID="E3" REF.START="Q3"/></TERM>'
    b'<DEFINITION><P>een autoriteit die betrokken is omdat:</P><LIST TYPE="alpha">'
    b'<ITEM><NP><NO.P>a)</NO.P><TXT>de verwerker daar is gevestigd;</TXT></NP></ITEM>'
    b'<ITEM><NP><NO.P>b)</NO.P><TXT>een klacht is ingediend;</TXT></NP></ITEM>'
    b'</LIST></DEFINITION></DLIST.ITEM>'
    b'<DLIST.ITEM><PREFIX>30)</PREFIX>'
    b'<TERM><QUOT.START CODE="201E" ID="Q4" REF.END="E4"/>geciteerde term'
    b'<QUOT.END CODE="201D" ID="E4" REF.START="Q4"/></TERM>'
    b'<DEFINITION>wat in een andere handeling staat: <QUOT.S LEVEL="1">'
    b'<LIST TYPE="alpha"><ITEM><NP><NO.P>a)</NO.P><TXT>geciteerd onderdeel;</TXT></NP></ITEM>'
    b'</LIST></QUOT.S></DEFINITION></DLIST.ITEM>'
    b'</DLIST></ALINEA></ARTICLE>'
)


def met_definities() -> bytes:
    return formex_zip(act=ACT.replace(b"</ARTICLE>", DEFINITIES, 1))


def test_a_definition_carrying_a_list_stays_a_list():
    """Artikel 4 AVG punt 16: `DEFINITION > LIST` geeft een kopregel plus twee
    onderdelen, met het punt als ankerouder — niet één samengevoegde alinea.

    Zo stond het in de HTML-route-versie (`art-4-16`, `art-4-16-a`, `-b`), en
    zo vindt het bronbewijs van de kennisbank de regel terug: `bind()` eist de
    hele regel, niet een deelreeks."""
    markdown, eenheden, onbekend, _ = formex_xml.omzetten(met_definities())
    regels = [r for r in markdown.splitlines() if r.strip()]

    assert "16) “hoofdvestiging”" in regels
    assert "a) de plaats van de centrale administratie;" in regels
    assert "b) de plaats van de voornaamste activiteiten;" in regels
    ankers = [e.anker for e in eenheden if e.anker.startswith("art-2-")]
    assert ankers == ["art-2-1", "art-2-16", "art-2-16-a", "art-2-16-b",
                      "art-2-22", "art-2-22-a", "art-2-22-b", "art-2-30"]
    assert not onbekend


def test_a_definition_keeps_its_own_lead_in_on_the_heading_line():
    """Artikel 4 AVG punt 22: `DEFINITION > [P, LIST]`. De `P` hoort bij de
    kopregel; kwam ze als eigen alinea, dan bindt het bronbewijs niet, want
    `nummering()` zet haar aan de kant van de kennisbank óók op die regel."""
    regels = [r for r in formex_xml.omzetten(met_definities())[0].splitlines() if r.strip()]

    # Als hele regel, niet als deelreeks: vóór de reparatie stonden de
    # onderdelen a) en b) achter de dubbele punt op diezelfde regel, en dan
    # slaagt een `in markdown` nog steeds.
    assert "22) “betrokken autoriteit” een autoriteit die betrokken is omdat:" in regels
    assert "a) de verwerker daar is gevestigd;" in regels


def test_a_plain_definition_stays_one_paragraph():
    """Zonder structureel kind verandert er niets: dat is wat de zes andere
    Formex-bronnen byte-identiek houdt."""
    regels = [r for r in formex_xml.omzetten(met_definities())[0].splitlines() if r.strip()]

    assert "1) “persoonsgegevens” alle informatie over een natuurlijke persoon;" in regels


def test_a_quoted_list_inside_a_definition_stays_inline():
    """Een opsomming binnen `QUOT.S` citeert een andere handeling; daar mag de
    planner geen onderdelen van maken. Alleen een LIST/DLIST/TBL die rechtstreeks
    onder DEFINITION hangt, splitst."""
    markdown, eenheden, _, _ = formex_xml.omzetten(met_definities())
    ankers = [e.anker for e in eenheden]

    assert "30) “geciteerde term” wat in een andere handeling staat: a) geciteerd onderdeel;" in markdown
    # Het punt zelf is wél een eenheid — tot 22 september 2026 kreeg een DLIST
    # nooit een basis mee, en had geen van de definitiepunten er een.
    assert "art-2-30" in ankers
    assert not [a for a in ankers if a.startswith("art-2-30-")]


# --- een herhaalde markering binnen één opsomming (AVG artikel 13, lid 1) -------------

DUBBELE_D = (
    b'<LIST TYPE="alpha"><ITEM><NP><NO.P>a)</NO.P><TXT>eerste onderdeel;</TXT></NP></ITEM>'
    b'<ITEM><NP><NO.P>d)</NO.P><TXT>de gerechtvaardigde belangen;</TXT></NP></ITEM>'
    b'<ITEM><NP><NO.P>d)</NO.P><TXT>in voorkomend geval, de ontvangers;</TXT></NP></ITEM>'
    b'<ITEM><NP><NO.P>e)</NO.P><TXT>laatste onderdeel.</TXT></NP></ITEM></LIST>'
)


def met_dubbele_d(tweede_d: bytes = b"") -> bytes:
    """De opsomming van het testartikel vervangen door a), d), d), e); `tweede_d`
    is optionele inhoud achter de TXT van het eerste d) (bijvoorbeeld een geneste
    opsomming)."""
    act = ACT.replace(
        b'<LIST TYPE="ALPHA"><ITEM><NP><NO.P>a)</NO.P><TXT>eerste onderdeel;</TXT></NP></ITEM>\n'
        b'<ITEM><NP><NO.P>b)</NO.P><TXT>tweede onderdeel.</TXT></NP></ITEM></LIST>',
        DUBBELE_D.replace(b"belangen;</TXT>", b"belangen;</TXT>" + tweede_d, 1),
    )
    assert act != ACT
    return formex_zip(act=act)


def test_a_repeated_list_marker_gets_an_ordinal_anchor_and_a_warning_instead_of_a_refusal():
    """De Nederlandse Formex van de AVG (32016R0679, `L_2016119NL.01000101.xml`,
    gemeten 23 september 2026) nummert in artikel 13, lid 1 de onderdelen
    a), b), c), d), d), e) waar het Publicatieblad a) t/m f) heeft; de Engelse
    manifestatie heeft wél (a)–(f). Een `ITEM`/`NP` draagt geen IDENTIFIER, dus
    de oplossing van artikel 73 van 2024/1689 gaat hier niet. De tekst komt wel
    volledig aan, dus de omzetter weigert niet: het tweede d) krijgt zijn
    volgnummer als anker, de markering en de tekst blijven zoals de bron ze
    heeft, en de herkomst zegt het."""
    markdown, eenheden, _, extra = formex_xml.omzetten(met_dubbele_d())
    ankers = [e.anker for e in eenheden]

    assert ankers.count("art-1-1-d") == 1
    assert "art-1-1-d-2" in ankers
    assert "art-1-1-e" in ankers
    assert "d) de gerechtvaardigde belangen;" in markdown
    assert "d) in voorkomend geval, de ontvangers;" in markdown
    assert "e) de gerechtvaardigde" not in markdown and "e) in voorkomend" not in markdown
    meldingen = extra["metadata"]["waarschuwingen"]
    assert len(meldingen) == 1
    assert "artikel 1, lid 1" in meldingen[0]
    assert "d) 2 keer" in meldingen[0]
    assert "art-1-1-d-2" in meldingen[0]
    assert "ongewijzigd" in meldingen[0]


def test_the_repeated_marker_warning_reaches_the_provenance(monkeypatch):
    data = met_dubbele_d()
    from mdconv.sources import eurlex
    monkeypatch.setattr(
        eurlex.net, "documents",
        lambda: SimpleNamespace(get=lambda url, **kw: SimpleNamespace(status_code=200, content=data, url=url)),
    )

    document = from_link("32022R1925", "NL")

    assert document.provenance.format == "formex"
    assert any("art-1-1-d-2" in m and "artikel 1, lid 1" in m
               for m in document.provenance.waarschuwingen)


def test_a_repeated_marker_whose_ordinal_collides_with_a_nested_point_is_still_refused():
    """Het volgnummer is een anker als elk ander: draagt het eerste d) een geneste
    opsomming `1.`, `2.`, dan bestaat `art-1-1-d-2` al en blijft de zelfcontrole
    fail-closed, met een melding die zegt wat het dubbele anker betekent."""
    genest = (b'<LIST TYPE="arabic"><ITEM><NP><NO.P>1.</NO.P><TXT>eerste punt;</TXT></NP></ITEM>'
              b'<ITEM><NP><NO.P>2.</NO.P><TXT>tweede punt;</TXT></NP></ITEM></LIST>')

    with pytest.raises(ConversionError, match=r"structuurcontrole.*art-1-1-d-2.*nummert op één niveau"):
        formex_xml.omzetten(met_dubbele_d(tweede_d=genest))


# ------------------------------------------------------------------ ankers in bijlagen
# De ankers in `eenheden` zijn voor de zelfcontrole, niet voor het profiel. Ze
# moeten de boom van de bron volgen: een deel dat weer bij punt 1 begint, is een
# eigen niveau. Tot 23 september 2026 weigerde de zelfcontrole daardoor 13 van de
# 347 documenten in de meetlat op dubbele ankers (MiCA, de SCC's, de
# zorgvuldigheidsrichtlijn, de klokkenluidersrichtlijn, de Chips Act, ...).

def _met_bijlagen(*bijlagen: bytes) -> bytes:
    namen = [f"bijlage{i}.xml" for i in range(1, len(bijlagen) + 1)]
    doc = DOC.replace(b"</FMX>", b"".join(
        b'<REF.PHYS TYPE="DOC.XML" FILE="' + n.encode() + b'"/>' for n in namen) + b"</FMX>")
    return formex_zip(doc=doc, extra=dict(zip(namen, bijlagen)))


def _bijlage(titel: bytes, inhoud: bytes) -> bytes:
    return b"<ANNEX><TITLE><TI><P>" + titel + b"</P></TI></TITLE><CONTENTS>" + inhoud + b"</CONTENTS></ANNEX>"


def _deel(titel: bytes, *punten: bytes) -> bytes:
    return (b"<GR.SEQ><TITLE><TI><P>" + titel + b"</P></TI></TITLE>" + b"".join(
        b"<NP><NO.P>" + nr + b"</NO.P><TXT>Punt " + nr + b" van " + titel + b".</TXT></NP>"
        for nr in punten) + b"</GR.SEQ>")


def _ankers(data: bytes, soort: str) -> list[str]:
    return [e.anker for e in formex_xml.omzetten(data)[1] if e.soort == soort]


def test_annex_parts_that_restart_their_numbering_are_their_own_level():
    """MiCA (2023/1114) bijlage I: een ongenummerde titel met daaronder `Deel A:` tot `Deel I:`, elk vanaf 1."""
    inhoud = (b"<GR.SEQ><TITLE><TI><P>OPENBAAR TE MAKEN ELEMENTEN</P></TI></TITLE>"
              + _deel(b"Deel A: Informatie over de aanbieder", b"1.", b"2.")
              + _deel(b"Deel B: Informatie over de uitgever", b"1.", b"2.") + b"</GR.SEQ>")
    assert _ankers(_met_bijlagen(_bijlage(b"BIJLAGE I", inhoud)), "punt") == [
        "annex-1-a-1", "annex-1-a-2", "annex-1-b-1", "annex-1-b-2"]


def test_unnumbered_sibling_parts_get_their_position_and_decimal_numbers_stay_whole():
    """De SCC's (2021/914): cursief `Bepaling 8`, twee ongenummerde modules, en daarin `8.1.` en `8.2.`."""
    module = lambda naam: (b"<GR.SEQ><TITLE><TI><P>" + naam + b"</P></TI></TITLE>"
                           + _deel(b"8.1.Doelbinding", b"a)") + _deel(b"8.2.Transparantie", b"a)") + b"</GR.SEQ>")
    inhoud = (b'<GR.SEQ><TITLE><TI><P><HT TYPE="ITALIC">Bepaling 8</HT></P></TI></TITLE>'
              + module(b"MODULE EEN") + module(b"MODULE TWEE") + b"</GR.SEQ>")
    assert _ankers(_met_bijlagen(_bijlage(b"BIJLAGE I", inhoud)), "bijlagedeel") == [
        "annex-1-8", "annex-1-8-s1-8-1", "annex-1-8-s1-8-2", "annex-1-8-s2-8-1", "annex-1-8-s2-8-2"]


def test_an_unnumbered_annex_does_not_take_the_anchor_of_annex_one():
    """De SCC's: `BIJLAGE` (de bepalingen), dan een aanhangsel met `BIJLAGE I`."""
    data = _met_bijlagen(_bijlage(b"BIJLAGE", _deel(b"A.", b"1.")), _bijlage(b"BIJLAGE I", _deel(b"A.", b"1.")))
    assert _ankers(data, "bijlage") == ["annex-o1", "annex-1"]


def test_a_second_series_of_points_in_one_part_gets_al2():
    """SCC-bijlage I.A nummert de exporteurs en daarna de importeurs elk vanaf 1. (patronen.md: `al<k>`)."""
    inhoud = _deel(b"A.", b"1.", b"2.", b"1.", b"2.")
    assert _ankers(_met_bijlagen(_bijlage(b"BIJLAGE I", inhoud)), "punt") == [
        "annex-1-a-1", "annex-1-a-2", "annex-1-a-al2-1", "annex-1-a-al2-2"]


def test_a_definition_list_after_a_list_in_the_same_paragraph_is_the_second_series():
    """Artikel 28 bis, lid 2 van de geconsolideerde AVMD: LIST a)–b), daarna een DLIST a)–c)."""
    dlist = (b'<ALINEA><P>In dit lid wordt verstaan onder:</P><DLIST><DLIST.ITEM><PREFIX>a)</PREFIX>'
             b'<TERM>moederonderneming</TERM><DEFINITION>een onderneming met zeggenschap;</DEFINITION>'
             b'</DLIST.ITEM></DLIST></ALINEA></PARAG>')
    act = ACT.replace(b"</ALINEA></PARAG>", b"</ALINEA>" + dlist, 1)
    assert _ankers(formex_zip(act=act), "onderdeel") == ["art-1-1-a", "art-1-1-b", "art-1-1-al2-a"]


def test_a_chapter_bis_is_another_chapter_than_the_one_before_it():
    """02018R1862 (SIS) kent `HOOFDSTUK IX` en `HOOFDSTUK IX bis`; alleen `IX` lezen gaf twee keer hfd-9."""
    tweede = (b'<DIVISION><TITLE><TI>HOOFDSTUK I bis</TI><STI>Nog meer bepalingen</STI></TITLE>'
              b'<ARTICLE IDENTIFIER="2"><TI.ART>Artikel 2</TI.ART><ALINEA><P>Tweede artikel.</P></ALINEA>'
              b'</ARTICLE></DIVISION></ENACTING.TERMS>')
    act = ACT.replace(b"</ENACTING.TERMS>", tweede, 1)
    assert _ankers(formex_zip(act=act), "divisie") == ["hfd-1", "hfd-1bis"]


def test_artikelen_van_een_bijlage_dragen_de_bijlage_in_hun_anker():
    """Het besluit over AnaEE-ERIC (32022D0289) heeft de statuten als bijlage, met elf eigen
    artikelen; dat weigerde als `inhoud:ARTICLE`. Het profiel ankert ze `annex-<n>-art-<k>`."""
    artikelen = (b'<ARTICLE IDENTIFIER="001"><TI.ART>Artikel 1</TI.ART><STI.ART>Naam</STI.ART>'
                 b'<ALINEA>Er wordt een ERIC opgericht.</ALINEA></ARTICLE>'
                 b'<ARTICLE IDENTIFIER="002"><TI.ART>Artikel 2</TI.ART><STI.ART>Taken</STI.ART>'
                 b'<PARAG IDENTIFIER="002.001"><NO.PARAG>1.</NO.PARAG><ALINEA>De hoofdtaak is onderzoek.</ALINEA>'
                 b'</PARAG></ARTICLE>')

    data = _met_bijlagen(_bijlage(b"BIJLAGE", artikelen))
    markdown, eenheden, _, _ = formex_xml.omzetten(data)

    assert "\n### Artikel 2\n\nTaken\n\n1.   De hoofdtaak is onderzoek.\n" in markdown
    assert [e.anker for e in eenheden if e.soort in ("artikel", "lid")] == [
        "art-1", "art-1-1", "annex-o1-art-1", "annex-o1-art-2", "annex-o1-art-2-1"]


def test_an_annex_that_quotes_a_block_of_another_act_renders_it_without_units():
    """16 wijzigingshandelingen in de meetlat vervangen een bijlage elders door een geciteerd blok
    (QUOT.S met een tabel of onderdelen als kind van CONTENTS); dat weigerde als onbekende inhoud."""
    citaat = (b'<P>Bijlage II bij Richtlijn 2006/1/EG wordt vervangen door:</P><QUOT.S LEVEL="1">'
              b'<TBL COLS="2"><CORPUS><ROW><CELL COL="1"><QUOT.START CODE="201C" ID="Q1" REF.END="E1"/>Stof</CELL>'
              b'<CELL COL="2">Grenswaarde<QUOT.END CODE="201D" ID="E1" REF.START="Q1"/></CELL></ROW></CORPUS></TBL>'
              b'<NP><NO.P>1.</NO.P><TXT>Geciteerd punt.</TXT></NP></QUOT.S>')
    markdown, eenheden, _, _ = formex_xml.omzetten(_met_bijlagen(_bijlage(b"BIJLAGE", citaat)))
    assert "| “Stof | Grenswaarde” |" in markdown
    assert "1.   Geciteerd punt." in markdown
    assert [e.soort for e in eenheden if e.anker.startswith("annex")] == ["bijlage"]


# ------------------------------------------------------------------ afbeeldingen (TIFF)

def _met_afbeelding(act: bytes, *, bestand: bool = True, soort: bytes = b"TIFF") -> bytes:
    act = act.replace(b"<PAGE.FIRST>1</PAGE.FIRST>", b'<PAGE.FIRST>1</PAGE.FIRST><INCLUSIONS>'
                      b'<INCL.ELEMENT FILEREF="L_test.beeld.tif" TYPE="' + soort + b'"/></INCLUSIONS>', 1)
    return formex_zip(act=act, extra={"L_test.beeld.tif": b"II*\x00"} if bestand else None)


def test_an_image_used_as_a_list_marker_is_left_out_with_a_warning():
    """e-evidence (2023/1543) zet 200 aankruisvakjes als TIFF in NO.P; dat weigerde het hele document."""
    act = ACT.replace(b"<NO.P>b)</NO.P>", b'<NO.P><INCL.ELEMENT FILEREF="L_test.beeld.tif" TYPE="TIFF"/></NO.P>')
    markdown, _, _, extra = formex_xml.omzetten(_met_afbeelding(act))
    assert "\ntweede onderdeel.\n" in markdown
    assert extra["metadata"]["afbeeldingen_weggelaten"] == [
        {"fileref": "L_test.beeld.tif", "format": "TIFF", "tekst_overgenomen": False}]
    assert any("1 afbeelding uit de Formex-bron niet overgenomen" in w
               for w in extra["metadata"]["waarschuwingen"])


def test_the_text_an_image_carries_is_written_as_paragraphs():
    """Brussel I bis (1215/2012): de certificaten zijn TIFF's met hun tekst in IMG.CNT."""
    bijlage = (b'<ANNEX><BIB.INSTANCE><INCLUSIONS><INCL.ELEMENT FILEREF="L_test.beeld.tif" TYPE="TIFF"/>'
               b'</INCLUSIONS></BIB.INSTANCE><TITLE><TI><P>BIJLAGE I</P></TI></TITLE><CONTENTS>'
               b'<INCL.ELEMENT CONTENT="FORM" FILEREF="L_test.beeld.tif" TYPE="TIFF"><IMG.CNT>'
               b'<P>CERTIFICAAT BETREFFENDE EEN BESLISSING</P><P>1.1. Naam:</P></IMG.CNT></INCL.ELEMENT>'
               b'</CONTENTS></ANNEX>')
    doc = DOC.replace(b"</FMX>", b'<REF.PHYS TYPE="DOC.XML" FILE="bijlage.xml"/></FMX>')
    data = formex_zip(doc=doc, extra={"bijlage.xml": bijlage, "L_test.beeld.tif": b"II*\x00"})
    markdown, _, _, extra = formex_xml.omzetten(data)
    assert "\nCERTIFICAAT BETREFFENDE EEN BESLISSING\n\n1.1. Naam:\n" in markdown
    assert extra["metadata"]["afbeeldingen_weggelaten"][0]["tekst_overgenomen"] is True


def _brief(slot: bytes) -> bytes:
    """Een bijlage die een brief is, met een handtekening als TIFF, naar het model van 2023/1795."""
    bijlage = (b'<ANNEX><BIB.INSTANCE><INCLUSIONS><INCL.ELEMENT FILEREF="L_test.beeld.tif" TYPE="TIFF"/>'
               b'</INCLUSIONS></BIB.INSTANCE><TITLE><TI><P>BIJLAGE II</P></TI></TITLE><CONTENTS>'
               b'<P>Wij kijken ernaar uit.</P>' + slot + b'</CONTENTS></ANNEX>')
    doc = DOC.replace(b"</FMX>", b'<REF.PHYS TYPE="DOC.XML" FILE="bijlage.xml"/></FMX>')
    return formex_zip(doc=doc, extra={"bijlage.xml": bijlage, "L_test.beeld.tif": b"II*\x00"})


SLOTFORMULE = (b'<FINAL><SIGNATURE><SIGNATORY><P>Hoogachtend,</P>'
               b'<P><INCL.ELEMENT CONTENT="SIGNATURE" FILEREF="L_test.beeld.tif" TYPE="TIFF"/></P>'
               b'<P>Gina M. <HT TYPE="UC">Raimondo</HT></P></SIGNATORY></SIGNATURE></FINAL>')


def test_de_slotformule_van_een_brief_in_een_bijlage_wordt_alineas_zonder_de_handtekening():
    """Zes bijlagen van het Data Privacy Framework (2023/1795) sluiten af met een FINAL; dat weigerde."""
    markdown, _, _, extra = formex_xml.omzetten(_brief(SLOTFORMULE))
    assert "\n\nWij kijken ernaar uit.\n\nHoogachtend,\n\nGina M. RAIMONDO" in markdown
    assert extra["metadata"]["afbeeldingen_weggelaten"] == [
        {"fileref": "L_test.beeld.tif", "format": "TIFF", "tekst_overgenomen": False}]


@pytest.mark.parametrize("oud, nieuw, context", [
    # Plaats en datum onder de ondertekening staan alleen in de FINAL van de handeling.
    (b"<SIGNATORY>", b'<PL.DATE><P>Brussel, <DATE ISO="20230710">10 juli 2023</DATE></P></PL.DATE><SIGNATORY>',
     "inhoud:PL.DATE"),
    (b"<FINAL>", b"<FINAL>Losse tekst", "inhoud:FINAL"),
])
def test_een_slotformule_in_een_bijlage_die_niet_gemeten_is_weigert(oud, nieuw, context):
    with pytest.raises(ConversionError, match=context):
        formex_xml.omzetten(_brief(SLOTFORMULE.replace(oud, nieuw, 1)))


@pytest.mark.parametrize("bestand, soort, reden", [
    (False, b"TIFF", "ontbrekende afbeeldingen"),
    (True, b"EPS", "onbekende type 'EPS'"),
])
def test_a_missing_image_or_another_image_type_is_still_refused(bestand, soort, reden):
    act = ACT.replace(b"<NO.P>b)</NO.P>", b'<NO.P><INCL.ELEMENT FILEREF="L_test.beeld.tif" TYPE="' + soort + b'"/></NO.P>')
    with pytest.raises(ConversionError, match=reden):
        formex_xml.omzetten(_met_afbeelding(act, bestand=bestand, soort=soort))


def test_a_repeated_chapter_number_gets_a_sequence_number_and_a_warning():
    """De Nederlandse DORA (32022R2554) noemt hoofdstuk VII `HOOFDSTUK III`: dezelfde regel als de dubbele d) in de AVG."""
    tweede = (b'<DIVISION><TITLE><TI>HOOFDSTUK I</TI><STI>Bevoegde autoriteiten</STI></TITLE>'
              b'<DIVISION><TITLE><TI>AFDELING 1</TI><STI>Toezicht</STI></TITLE>'
              b'<ARTICLE IDENTIFIER="2"><TI.ART>Artikel 2</TI.ART><ALINEA><P>Tweede artikel.</P></ALINEA>'
              b'</ARTICLE></DIVISION></DIVISION></ENACTING.TERMS>')
    act = ACT.replace(b"</ENACTING.TERMS>", tweede, 1)
    _, eenheden, _, extra = formex_xml.omzetten(formex_zip(act=act))
    assert [e.anker for e in eenheden if e.soort == "divisie"] == ["hfd-1", "hfd-1-2", "afd-1-2-1"]
    assert any("de kop HOOFDSTUK I 2 keer" in w for w in extra["metadata"]["waarschuwingen"])


def test_a_repeated_article_number_is_still_refused():
    tweede = (b'<ARTICLE IDENTIFIER="2"><TI.ART>Artikel 1</TI.ART><ALINEA><P>Nog een artikel 1.</P></ALINEA>'
              b'</ARTICLE></DIVISION></ENACTING.TERMS>')
    act = ACT.replace(b"</DIVISION></ENACTING.TERMS>", tweede, 1)
    with pytest.raises(ConversionError, match="dubbele structurele ankers: art-1"):
        formex_xml.omzetten(formex_zip(act=act))


# ------------------------------------------------------------------ annotaties
#
# Een ANNOTATION is in Formex een noot die geen voetnoot is: een NB, een
# opmerking, een technische noot. Tot 23 september 2026 weigerde elke annotatie
# het hele document (20 van de 347 in de meetlat noemden haar in hun weigering,
# waaronder het Europees wetboek voor elektronische communicatie). De tekst komt
# nu als gewone alinea's op de plek waar de bron haar zet, zonder kop en zonder
# eenheden.

def test_an_annotation_in_an_annex_is_its_title_and_text_as_plain_paragraphs():
    """32018L1972, bijlage X: `Noot 1` en `Noot 2` onder de tabellen, als GR.ANNOTATION met TITLE."""
    noot = (b"<GR.ANNOTATION><TITLE><TI><P>Noot 1</P></TI></TITLE><ANNOTATION><P>De parameters maken "
            b"een analyse mogelijk.</P></ANNOTATION></GR.ANNOTATION>")
    markdown, eenheden, _, _ = formex_xml.omzetten(
        _met_bijlagen(_bijlage(b"BIJLAGE X", _deel(b"A.", b"1.") + noot)))
    assert "\n\n1.   Punt 1. van A..\n\nNoot 1\n\nDe parameters maken een analyse mogelijk.\n" in markdown
    assert "# Noot" not in markdown
    assert not any("noot" in e.anker for e in eenheden)


def test_an_annotation_among_the_table_notes_comes_directly_under_the_table():
    """32023L0544: `Aantekeningen bij de tabel:` staat in TBL/GR.NOTES vóór de genummerde tabelnoten.

    Die noten gaan zoals altijd naar het notenblok achter de bijlage; de annotatie
    is geen noot met een nummer maar tekst onder de tabel.
    """
    tabel = (b'<TBL COLS="2"><CORPUS><ROW TYPE="HEADER"><CELL COL="1" TYPE="HEADER">Toepassing</CELL>'
             b'<CELL COL="2" TYPE="HEADER">Vrijstelling</CELL></ROW><ROW><CELL COL="1">Lood in aluminium'
             b'<NOTE NOTE.REF="E0001" NUMBERING="ARAB" TYPE="TABLE"/></CELL><CELL COL="2">X</CELL></ROW>'
             b'</CORPUS><GR.NOTES><GR.ANNOTATION><ANNOTATION><P>Aantekeningen bij de tabel:</P></ANNOTATION>'
             b'</GR.ANNOTATION><NOTE NOTE.ID="E0001" NUMBERING="ARAB" TYPE="TABLE"><P>Wordt opnieuw bekeken '
             b'in 2024.</P></NOTE></GR.NOTES></TBL>')
    markdown = formex_xml.omzetten(_met_bijlagen(_bijlage(b"BIJLAGE II", tabel)))[0]
    assert ("| Lood in aluminium (1) | X |\n\nAantekeningen bij de tabel:\n\n"
            "(1)  Wordt opnieuw bekeken in 2024.\n") in markdown


def test_an_annotation_wrapped_in_a_paragraph_is_written_where_it_stands_without_a_unit():
    """32024D2627: `Opmerking:` als NP in P/GR.ANNOTATION/ANNOTATION onder een onderdeel van de bijlage.

    Het `Opmerking:` in NO.P is geen markering van een onderdeel; een anker
    `annex-1-a-opmerking` zou een structuur beweren die de bron niet heeft.
    """
    lijst = (b'<LIST TYPE="alpha"><ITEM><NP><NO.P>a)</NO.P><TXT>Monitoring:</TXT><P>Geen.</P>'
             b'<P><GR.ANNOTATION><ANNOTATION><NP><NO.P><HT TYPE="ITALIC">Opmerking:</HT></NO.P>'
             b'<TXT><HT TYPE="ITALIC">links kunnen wijzigen.</HT></TXT></NP></ANNOTATION></GR.ANNOTATION></P>'
             b'</NP></ITEM></LIST>')
    markdown, eenheden, _, _ = formex_xml.omzetten(_met_bijlagen(_bijlage(b"BIJLAGE", lijst)))
    assert "\n\na) Monitoring:\n\nGeen.\n\n*Opmerking:* *links kunnen wijzigen.*\n" in markdown
    assert [e.anker for e in eenheden if e.anker.startswith("annex")] == ["annex-o1", "annex-o1-a"]


def test_an_annotation_in_a_table_cell_is_a_block_of_that_cell():
    """32026L0706: `Noot:` en haar tekst als ANNOTATION in een CELL, na de eigen alinea van de cel."""
    tabel = (b'<TBL COLS="2"><CORPUS><ROW><CELL COL="1">Aanwijzing</CELL><CELL COL="2"><P>De aanwijzing in '
             b'massa.</P><ANNOTATION><P>Noot:</P><P>Herleiden mag.</P></ANNOTATION></CELL></ROW></CORPUS></TBL>')
    markdown = formex_xml.omzetten(_met_bijlagen(_bijlage(b"BIJLAGE I", tabel)))[0]
    assert "| Aanwijzing | De aanwijzing in massa. Noot: Herleiden mag. |" in markdown


def test_an_annotation_above_the_title_of_an_act_is_a_paragraph_before_it():
    """32015D0926: de tweede handeling in de zip is een ontwerpaanbeveling met `ONTWERP` boven haar titel."""
    act = ACT.replace(b"<TITLE>", b"<GR.ANNOTATION><ANNOTATION><P>ONTWERP</P></ANNOTATION></GR.ANNOTATION><TITLE>", 1)
    markdown = formex_xml.omzetten(formex_zip(act=act))[0]
    assert "---\n\nONTWERP\n\nVERORDENING (EU) 2022/1925\n" in markdown


def test_margin_text_stands_at_the_start_of_its_paragraph_and_heading():
    """32017L0433: de categorie van de militaire lijst in de kantlijn (MARGIN), vóór een alinea en een begripskop.

    MARGIN heeft geen staart, dus zonder scheiding plakte `ML1` aan het eerste woord.
    """
    inhoud = (b'<GR.SEQ><TITLE><TI><P>Lijst</P></TI></TITLE><P><MARGIN>ML1</MARGIN><HT TYPE="BOLD">Wapens met '
              b'gladde loop:</HT></P></GR.SEQ><GR.SEQ><TITLE><TI><P><MARGIN>ML 7, 22.</MARGIN>"Biopolymeren"</P>'
              b'</TI></TITLE><P>Biologische macromoleculen.</P></GR.SEQ>')
    markdown = formex_xml.omzetten(_met_bijlagen(_bijlage(b"BIJLAGE", inhoud)))[0]
    assert "\n\nML1 **Wapens met gladde loop:**\n\n" in markdown
    assert '\n\nML 7, 22. "Biopolymeren"\n\nBiologische macromoleculen.\n' in markdown


def test_an_asterisk_as_printed_marker_gets_a_hard_space_so_the_line_is_no_markdown_list():
    """`* tekst` is in Markdown een opsomming en het sterretje verdwijnt.

    32025D2554 gebruikt `*` als lijstteken (NO.P), de legenda onder de PRODCOM-lijst
    (32010R0860) als term van een DLIST. Het profiel zet zulke markeercellen met een
    harde spatie aan hun tekst, "zodat Markdown er geen lijst van maakt".
    """
    inhoud = (b'<LIST TYPE="OTHER"><ITEM><NP><NO.P>*</NO.P><TXT>[Link: plan bekendgemaakt]</TXT></NP></ITEM></LIST>'
              b'<P><DLIST SEPARATOR="=" TYPE="TBL"><DLIST.ITEM><TERM>*</TERM><DEFINITION>Rubriek die wijziging '
              b'bevat</DEFINITION></DLIST.ITEM><DLIST.ITEM><TERM>@</TERM><DEFINITION>Eenheid verschillend van de GN'
              b'</DEFINITION></DLIST.ITEM></DLIST></P>')
    markdown = formex_xml.omzetten(_met_bijlagen(_bijlage(b"BIJLAGE", inhoud)))[0]
    assert "\n\n* [Link: plan bekendgemaakt]\n\n* Rubriek die wijziging bevat\n\n" in markdown
    assert "\n\n@ Eenheid verschillend van de GN\n" in markdown
    assert "\n* " not in markdown


def _rijgroepen(start: bytes = b"1", eind: bytes = b"2") -> bytes:
    """De vorm van de PRODCOM-lijst (32010R0860): geneste BLK's met een TI.BLK over alle kolommen."""
    return (b'<TBL COLS="2"><CORPUS><ROW TYPE="HEADER"><CELL COL="1" TYPE="HEADER">PRODCOM</CELL>'
            b'<CELL COL="2" TYPE="HEADER">Beschrijving</CELL></ROW><BLK><TI.BLK COL.START="' + start
            + b'" COL.END="' + eind + b'"><HT TYPE="BOLD">NACE 07.10: Winning van ijzererts</HT></TI.BLK>'
            b'<BLK><TI.BLK COL.START="1" COL.END="2"><NP><NO.P><HT TYPE="BOLD">CPA 07.10.10:</HT></NO.P><TXT>'
            b'<HT TYPE="BOLD">IJzererts</HT></TXT></NP></TI.BLK><ROW><CELL COL="1">07.10.10.00</CELL>'
            b'<CELL COL="2">IJzererts en concentraten</CELL></ROW></BLK></BLK><BLK><ROW><CELL COL="1">08.11</CELL>'
            b'<CELL COL="2">Marmer</CELL></ROW></BLK></CORPUS></TBL>')


def test_the_title_of_a_group_of_table_rows_is_a_row_with_one_spanning_cell():
    """32010R0860: 1.727 rijgroepen met een titel (BLK/TI.BLK); dat weigerde als onbekend element.

    De titel is een cel over COL.START tot en met COL.END, dus staat hij, zoals elke
    samengevoegde cel, op elke bezette plek; de brontelling herhaalt hem even vaak.
    Een BLK zonder titel (32019R0089) groepeert alleen.
    """
    markdown, _, _, extra = formex_xml.omzetten(_met_bijlagen(_bijlage(b"BIJLAGE", _rijgroepen())))
    assert ("| PRODCOM | Beschrijving |\n"
            "| **NACE 07.10: Winning van ijzererts** | **NACE 07.10: Winning van ijzererts** |\n"
            "| **CPA 07.10.10:** **IJzererts** | **CPA 07.10.10:** **IJzererts** |\n"
            "| 07.10.10.00 | IJzererts en concentraten |\n"
            "| 08.11 | Marmer |\n") in markdown
    assert extra["herhaalde_cellen"] == 2


@pytest.mark.parametrize("start, eind, reden", [
    (b"2", b"2", "ontbrekende cellen"),
    (b"", b"2", "TI.BLK"),
])
def test_a_row_group_title_that_does_not_name_or_fill_its_columns_is_refused(start, eind, reden):
    with pytest.raises(ConversionError, match=reden):
        formex_xml.omzetten(_met_bijlagen(_bijlage(b"BIJLAGE", _rijgroepen(start, eind))))


def test_an_annotation_at_the_start_of_a_paragraph_of_an_article_is_still_refused():
    """Niet gemeten: geen annotatie in de meetlat staat in een lid. Het lidnummer eraan vastplakken
    zou de noot tot lidtekst maken, en het nummer los laten staan is een vorm die niemand heeft gezien."""
    act = ACT.replace(b"<ALINEA><P>Deze verordening stelt regels vast.</P>",
                      b"<ANNOTATION><P>Opmerking vooraf.</P></ANNOTATION><ALINEA><P>Deze verordening stelt regels vast.</P>")
    with pytest.raises(ConversionError, match="annotatie aan het begin van een lid"):
        formex_xml.omzetten(formex_zip(act=act))


def test_a_title_that_restarts_in_every_part_carries_its_part():
    """32018L1972 begint in DEEL I en DEEL II bij TITEL I; dat gaf twee keer `tit-1` en `hfd-1-1`."""
    def deel(nr: bytes, artikel: bytes) -> bytes:
        return (b"<DIVISION><TITLE><TI>DEEL " + nr + b"</TI></TITLE><DIVISION><TITLE><TI>TITEL I</TI></TITLE>"
                b"<DIVISION><TITLE><TI>HOOFDSTUK I</TI></TITLE><ARTICLE IDENTIFIER=\"" + artikel + b"\"><TI.ART>"
                b"Artikel " + artikel + b"</TI.ART><ALINEA><P>Tekst.</P></ALINEA></ARTICLE></DIVISION>"
                b"</DIVISION></DIVISION>")
    act = ACT.replace(b"</ENACTING.TERMS>", deel(b"I", b"2") + deel(b"II", b"3") + b"</ENACTING.TERMS>", 1)
    assert _ankers(formex_zip(act=act), "divisie") == [
        "hfd-1", "deel-1", "tit-1-1", "hfd-1-1-1", "deel-2", "tit-2-1", "hfd-2-1-1"]


def _tabel(titel: bytes, *cellen: bytes) -> bytes:
    rij = b"".join(b'<CELL COL="%d">' % i + c + b"</CELL>" for i, c in enumerate(cellen, 1))
    kop = b"<TITLE><TI><P>" + titel + b"</P></TI></TITLE>" if titel else b""
    return b'<TBL COLS="%d">' % len(cellen) + kop + b"<CORPUS><ROW>" + rij + b"</ROW></CORPUS></TBL>"


def test_a_group_of_tables_in_a_paragraph_gets_its_title_and_every_table_in_order():
    """Artikel 224 CRR (32013R0575): `VOLATILITEITSAANPASSINGEN` boven `Tabel 1` tot en met `Tabel 4`
    in één GR.TBL, midden in lid 1. Dat weigerde als onbekend element; de lidtekst erna moet blijven."""
    groep = (b'<GR.TBL><TITLE><TI><P><HT TYPE="BOLD">VOLATILITEITSAANPASSINGEN</HT></P></TI></TITLE>'
             + _tabel(b"Tabel 1", b"Categorie", b"0,707") + _tabel(b"Tabel 2", b"Goud", b"21,213")
             + b"</GR.TBL>")
    act = ACT.replace(b"<P>Deze verordening stelt regels vast.</P>",
                      b"<P>Deze verordening stelt regels vast:</P>" + groep + b"<P>Daarna gaat het lid door.</P>")
    markdown, eenheden, onbekend, _ = formex_xml.omzetten(formex_zip(act=act))
    volgorde = ["1.\u00a0\u00a0\u00a0Deze verordening stelt regels vast:", "\n\nVOLATILITEITSAANPASSINGEN\n\n",
                "\n\nTabel 1\n\n", "| Categorie | 0,707 |", "\n\nTabel 2\n\n", "| Goud | 21,213 |",
                "\n\nDaarna gaat het lid door.\n\n"]
    posities = [markdown.index(stuk) for stuk in volgorde]
    assert posities == sorted(posities)
    assert "**VOLATILITEITSAANPASSINGEN**" not in markdown
    assert [e.anker for e in eenheden if e.soort == "lid"] == ["art-1-1"]
    assert onbekend == {}


def test_a_group_of_tables_in_an_annex_keeps_the_notes_of_its_tables():
    """Bijlage II van de consumentenrichtlijn (32011L0083): twee tabellen in één GR.TBL, de tweede met een noot."""
    noot = b'<GR.NOTES><NOTE NOTE.ID="E0001"><P>PB L 364 van 9.12.2004, blz. 1.</P></NOTE></GR.NOTES>'
    tweede = _tabel(b"", b'Verordening (EG) nr. 2006/2004<NOTE NOTE.REF="E0001"/>', b"Deze richtlijn")
    tweede = tweede.replace(b"<CORPUS>", noot + b"<CORPUS>")
    groep = b"<GR.TBL>" + _tabel(b"Concordantietabel", b"Artikel 1", b"Artikel 3") + tweede + b"</GR.TBL>"
    markdown, _, _, _ = formex_xml.omzetten(_met_bijlagen(_bijlage(b"BIJLAGE II", groep)))
    assert "\n\nConcordantietabel\n\n" in markdown
    assert "| Artikel 1 | Artikel 3 |" in markdown
    assert "| Verordening (EG) nr. 2006/2004 (1) | Deze richtlijn |" in markdown
    assert markdown.rstrip().endswith("(1)\u00a0\u00a0PB L 364 van 9.12.2004, blz. 1.")


def test_a_group_of_tables_with_something_else_than_a_title_or_a_table_is_refused():
    groep = b"<GR.TBL>" + _tabel(b"", b"A", b"B") + b"<P>Een losse alinea in de groep.</P></GR.TBL>"
    with pytest.raises(ConversionError, match="tabelgroep:P"):
        formex_xml.omzetten(_met_bijlagen(_bijlage(b"BIJLAGE II", groep)))
