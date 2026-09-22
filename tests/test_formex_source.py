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
