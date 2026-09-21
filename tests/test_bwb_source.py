"""De officiële BWB-XML-route en haar begrensde HTML-terugval."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from mdconv.errors import ConversionError
from mdconv.sources import from_link, wetten


def manifest(*, begin="2020-01-01", eind="9999-12-31", sha512="0" * 128):
    return f"""<manifest><expression label="expr_1"><metadata>
<datum_inwerkingtreding>{begin}</datum_inwerkingtreding><einddatum>{eind}</einddatum>
</metadata><manifestation label="xml"><metadata><hashcode>{sha512}</hashcode></metadata>
<item label="BWBR0000001.xml"/></manifestation></expression></manifest>""".encode()


def toestand(*, bwb="BWBR0000001", begin="2020-01-01", status="goed",
             artikel_inwerking="2020-01-01"):
    return f"""<toestand bwb-id="{bwb}" inwerkingtreding="{begin}"><wetgeving>
<citeertitel>Testwet</citeertitel><wet-besluit><wettekst>
<artikel status="{status}" inwerking="{artikel_inwerking}"><kop><label>Artikel</label>
<nr>1</nr><titel>Reikwijdte</titel></kop><lid><lidnr>1</lidnr>
<al>Deze wet geldt.</al></lid></artikel></wettekst></wet-besluit>
</wetgeving></toestand>""".encode()


def response(data, url):
    return SimpleNamespace(status_code=200, content=data, url=url)


def test_bwb_xml_precedes_html_and_manifest_hash_is_only_a_note(monkeypatch):
    xml = toestand()
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return response(manifest(sha512="f" * 128), url) if url.endswith("manifest.xml") else response(xml, url)

    monkeypatch.setattr(wetten.net, "documents", lambda: SimpleNamespace(get=get))
    document = from_link("BWBR0000001/2020-02-01")

    assert len(calls) == 2
    assert calls[0].endswith("/BWBR0000001/manifest.xml")
    assert "# Testwet" in document.markdown
    assert document.provenance.format == "bwb-xml"
    assert document.provenance.geldend_van == "2020-01-01"
    assert document.provenance.extra["xml_sha512_wijkt_af_van_manifest"] is True
    assert document.provenance.extra["xml_sha512_gemeten"] == hashlib.sha512(xml).hexdigest()
    assert any("SHA-512" in melding for melding in document.provenance.waarschuwingen)


def test_expired_article_is_rendered_and_recorded(monkeypatch):
    xml = toestand(status="vervallen", artikel_inwerking="2021-03-04")

    def get(url, **kwargs):
        return response(manifest(), url) if url.endswith("manifest.xml") else response(xml, url)

    monkeypatch.setattr(wetten.net, "documents", lambda: SimpleNamespace(get=get))
    document = from_link("BWBR0000001/2022-01-01")

    assert "[Vervallen per 04-03-2021]" in document.markdown
    assert document.provenance.expired == {"art-1": "2021-03-04"}


def test_not_yet_effective_paragraph_keeps_text_without_new_anchor_unit():
    xml = toestand(status="nogniet", artikel_inwerking="")
    markdown, eenheden, _, _ = wetten.bwb_xml.omzetten(xml)

    assert "1. Deze wet geldt." in markdown
    assert not markdown.split("1. Deze wet geldt.", 1)[0].endswith("- ")
    assert [e.anker for e in eenheden] == ["art-1"]


def test_not_yet_effective_article_is_marked_under_its_heading_and_keeps_its_text():
    """Tekst van een artikel dat nog niet geldt mag nooit als geldend recht lezen."""
    xml = toestand(status="nogniet", artikel_inwerking="")
    markdown = wetten.bwb_xml.omzetten(xml)[0]
    kop, rest = markdown.split("Reikwijdte", 1)[1].split("\n", 1)
    assert rest.lstrip("\n").startswith("[Nog niet in werking getreden.]\n\n1. Deze wet geldt.")


def test_a_goed_article_gets_no_not_yet_in_force_marker():
    assert "Nog niet in werking" not in wetten.bwb_xml.omzetten(toestand())[0]


def test_not_yet_effective_status_on_anything_but_an_article_is_refused():
    """Alleen bij een artikel weet de omzetter hoe hij dit toont; elders is het een weigering."""
    xml = toestand().replace(b"<artikel ", b'<hoofdstuk status="nogniet" inwerking="2020-01-01"><kop><label>Hoofdstuk</label><nr>1</nr><titel>Eerste</titel></kop><artikel ', 1).replace(
        b"</artikel>", b"</artikel></hoofdstuk>", 1)
    with pytest.raises(ConversionError, match="nogniet"):
        wetten.bwb_xml.omzetten(xml)


def test_withdrawn_regulation_uses_last_version(monkeypatch):
    xml = toestand()

    def get(url, **kwargs):
        return response(manifest(eind="2021-12-31"), url) if url.endswith("manifest.xml") else response(xml, url)

    monkeypatch.setattr(wetten.net, "documents", lambda: SimpleNamespace(get=get))
    document = from_link("BWBR0000001/2022-01-01")

    assert document.provenance.ingetrokken_op == "2021-12-31"
    assert "ingetrokken per 2021-12-31" in document.source


def test_identity_and_selected_start_date_are_hard_requirements(monkeypatch):
    verkeerd = toestand(bwb="BWBR9999999")

    def get(url, **kwargs):
        return response(manifest(), url) if url.endswith("manifest.xml") else response(verkeerd, url)

    monkeypatch.setattr(wetten.net, "documents", lambda: SimpleNamespace(get=get))
    with pytest.raises(ConversionError, match="BWBR9999999"):
        from_link("BWBR0000001/2020-02-01")


def test_unavailable_xml_falls_back_to_html_with_warning(monkeypatch):
    html = """<html><head><meta name="dcterms:title" content="Testwet"></head><body>
<div id="regeling"><h1>Testwet</h1><div class="wetgeving"><p>""" + (
        "Juridische tekst. " * 10
    ) + "</p></div></div></body></html>"
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        if url.endswith("manifest.xml"):
            return SimpleNamespace(status_code=404, content=b"", url=url)
        return SimpleNamespace(status_code=200, text=html, url="https://wetten.overheid.nl/BWBR0000001/2020-01-01")

    monkeypatch.setattr(wetten.net, "documents", lambda: SimpleNamespace(get=get))
    monkeypatch.setattr(wetten.net, "decoded_text", lambda r: r.text)
    document = from_link("BWBR0000001")

    assert len(calls) == 2
    assert document.provenance.format == "wetten-nl"
    assert any("HTML-route" in melding for melding in document.provenance.waarschuwingen)
