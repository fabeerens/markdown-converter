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


def test_container_heading_without_label_and_number_is_only_its_title():
    """Een kop met alleen een `<titel>` is die titel, zonder leesteken ervoor.

    De Wet bescherming persoonsgegevens BES (BWBR0028067) heeft een hoofdstuk met
    enkel `<kop><titel>Slotbepalingen</titel></kop>`; `kopregel()` schreef daar
    `## . Slotbepalingen`, en de kennisbank struikelde over een kop die met een
    leesteken begint (kb WP-04, 23 september 2026)."""
    xml = toestand().replace(
        b"<artikel ",
        b'<hoofdstuk status="goed" inwerking="2020-01-01"><kop><titel>Slotbepalingen</titel></kop><artikel ', 1
    ).replace(b"</artikel>", b"</artikel></hoofdstuk>", 1)
    markdown = wetten.bwb_xml.omzetten(xml)[0]
    assert "\n## Slotbepalingen\n" in markdown
    assert ". Slotbepalingen" not in markdown
    # De gewone vorm blijft zoals ze was: label, nummer, punt, titel.
    assert "Artikel 1. Reikwijdte" in markdown


def _artikel(nr):
    return (f'<artikel status="goed" inwerking="2020-01-01"><kop><label>Artikel</label>'
            f'<nr>{nr}</nr></kop><lid><lidnr>1</lidnr><al>Tekst van artikel {nr}.</al></lid></artikel>')


def _paragraaf_1(nr):
    return (f'<paragraaf><kop><label>§</label><nr>1</nr><titel>Eerste paragraaf</titel></kop>'
            f'{_artikel(nr)}</paragraaf>')


def test_a_heading_with_the_number_before_the_label_follows_the_source_order():
    """`<nr>Vierde</nr><label>titel</label>` wordt `Vierde titel`, zoals de bron het zet.

    Het Wetboek van Koophandel telt zijn paragrafen per titel opnieuw en zet het
    rangtelwoord vóór het label: `Eerste Boek`, `Vierde titel`, `Vijfde afdeeling`.
    `kopregel()` schreef altijd eerst het label (`titel Vierde.`), een vorm die het
    profiel van de kennisbank niet kent; titel en boek kregen daar geen anker, en de
    twee keer `§ 1` werd twee keer `par-1` (kb WP-09, klasse G). Een kop met het label
    vóór het nummer, zoals `<label>Vijfde titel</label><nr>A</nr>`, verandert niet."""
    xml = f"""<toestand bwb-id="BWBR0000001" inwerkingtreding="2020-01-01"><wetgeving>
<citeertitel>Testwetboek</citeertitel><wet-besluit><wettekst>
<boek><kop><nr>Tweede</nr><label>Boek</label><titel>Het tweede boek</titel></kop>
<titeldeel><kop><nr>Vierde</nr><label>titel</label><titel>De vierde titel</titel></kop>
{_paragraaf_1("341")}
<afdeling><kop><nr>Vijfde</nr><label>afdeeling</label><titel>De vijfde afdeling</titel></kop>
{_artikel("360")}</afdeling></titeldeel>
<titeldeel><kop><nr>Vijfde</nr><label>titel</label><titel>De vijfde titel</titel></kop>
{_paragraaf_1("378")}</titeldeel>
<titeldeel><kop><label>Vijfde titel</label><nr>A</nr><titel>De ingevoegde titel</titel></kop>
{_artikel("400")}</titeldeel>
</boek></wettekst></wet-besluit></wetgeving></toestand>""".encode()

    markdown, eenheden, onbekend, extra = wetten.bwb_xml.omzetten(xml)

    assert onbekend == {}
    assert "\n## Tweede Boek. Het tweede boek\n" in markdown
    assert "\n### Vierde titel. De vierde titel\n" in markdown
    assert "\n#### Vijfde afdeeling. De vijfde afdeling\n" in markdown
    assert "\n### Vijfde titel. De vijfde titel\n" in markdown
    assert markdown.count("\n#### § 1. Eerste paragraaf\n") == 2
    for omgedraaid in ("Boek Tweede", "titel Vierde", "titel Vijfde", "afdeeling Vijfde"):
        assert omgedraaid not in markdown
    # Label vóór nummer blijft label vóór nummer.
    assert "\n### Vijfde titel A. De ingevoegde titel\n" in markdown
    # De eenheden dragen dezelfde kopregel; de ankers komen uit het nummer en veranderen niet.
    teksten = {e.anker: e.tekst for e in eenheden}
    assert teksten["tit-vierde"] == "Vierde titel. De vierde titel"
    assert teksten["tit-a"] == "Vijfde titel A. De ingevoegde titel"
    # Gevolgd, en gemeld.
    assert extra["omgedraaide_koppen"] == 4
    assert extra["waarschuwingen"] == [
        "De BWB-XML zet 4 keer het nummer vóór het label in een kop ('Tweede Boek', "
        "'Vierde titel', 'Vijfde afdeeling', 'Vijfde titel'); de omzetter volgt die volgorde."]


def test_a_heading_with_the_label_first_is_not_reported():
    markdown, _, _, extra = wetten.bwb_xml.omzetten(toestand())
    assert "Artikel 1. Reikwijdte" in markdown
    assert extra["omgedraaide_koppen"] == 0
    assert extra["waarschuwingen"] == []


def test_the_reversed_heading_warning_travels_with_the_provenance(monkeypatch):
    """De melding van de omzetter komt in `herkomst.waarschuwingen`, en zo in `ophaal.json`."""
    xml = toestand().replace(
        b"<artikel ",
        b'<titeldeel><kop><nr>Vierde</nr><label>titel</label><titel>De vierde titel</titel></kop><artikel ', 1
    ).replace(b"</artikel>", b"</artikel></titeldeel>", 1)

    def get(url, **kwargs):
        return response(manifest(), url) if url.endswith("manifest.xml") else response(xml, url)

    monkeypatch.setattr(wetten.net, "documents", lambda: SimpleNamespace(get=get))
    document = from_link("BWBR0000001/2020-02-01")

    assert "Vierde titel. De vierde titel" in document.markdown
    assert ("De BWB-XML zet 1 keer het nummer vóór het label in een kop ('Vierde titel'); "
            "de omzetter volgt die volgorde.") in document.provenance.waarschuwingen


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


def regeling(*, bwb="BWBR0000001", begin="2020-01-01"):
    """Een ministeriële regeling: <regeling> met <regeling-tekst>/<regeling-sluiting>
    in plaats van de <wet-besluit>-vorm met <wettekst>/<wetsluiting>."""
    return f"""<toestand bwb-id="{bwb}" inwerkingtreding="{begin}"><wetgeving>
<citeertitel>Testregeling</citeertitel><regeling><aanhef><al>Gelet op artikel 1.</al></aanhef>
<regeling-tekst><artikel status="goed" inwerking="{begin}"><kop><label>Artikel</label>
<nr>1</nr><titel>Reikwijdte</titel></kop><lid><lidnr>1</lidnr>
<al>Deze regeling geldt.</al></lid></artikel></regeling-tekst>
<regeling-sluiting><al>Aldus vastgesteld.</al></regeling-sluiting>
</regeling></wetgeving></toestand>""".encode()


def test_regeling_route_is_converted_like_wet_besluit(monkeypatch):
    xml = regeling()

    def get(url, **kwargs):
        return response(manifest(), url) if url.endswith("manifest.xml") else response(xml, url)

    monkeypatch.setattr(wetten.net, "documents", lambda: SimpleNamespace(get=get))
    document = from_link("BWBR0000001/2020-02-01")

    assert "# Testregeling" in document.markdown
    assert "Reikwijdte" in document.markdown
    assert "Deze regeling geldt." in document.markdown
    assert "Aldus vastgesteld." in document.markdown
    assert document.provenance.koppen_bron == 1


def test_unknown_top_level_element_under_wetgeving_is_refused_not_flattened():
    """Vroeger viel <regeling> hier stil in plat(); nu geldt dat voor elk
    onbekend hoofdelement direct onder <wetgeving>, niet alleen <regeling>."""
    xml = toestand().replace(b"<wet-besluit>", b"<circulaire>").replace(b"</wet-besluit>", b"</circulaire>")

    with pytest.raises(ConversionError, match="circulaire"):
        wetten.bwb_xml.omzetten(xml)


def test_zero_headings_with_source_articles_is_refused(monkeypatch):
    """Goedkope grendel: als de XML <artikel>-elementen bevat maar de omzetting
    nul structuurkoppen geeft, is dat een teken dat er ergens stil is platgeslagen."""
    xml = toestand()
    echte_omzetten = wetten.bwb_xml.omzetten

    def lege_omzetting(data):
        markdown, eenheden, onbekend, extra = echte_omzetten(data)
        return markdown, [], onbekend, extra

    monkeypatch.setattr(wetten.bwb_xml, "omzetten", lege_omzetting)

    def get(url, **kwargs):
        return response(manifest(), url) if url.endswith("manifest.xml") else response(xml, url)

    monkeypatch.setattr(wetten.net, "documents", lambda: SimpleNamespace(get=get))
    with pytest.raises(ConversionError, match="structuurkop"):
        from_link("BWBR0000001/2020-02-01")


def test_bijlage_divisie_preserves_its_title_table_and_footnote():
    xml = b"""<toestand bwb-id="BWBR0000001" inwerkingtreding="2020-01-01"><wetgeving>
<citeertitel>Testwet</citeertitel><bijlage><kop><label>Bijlage</label><nr>1</nr></kop>
<divisie><kop><titel>Bijlage bij artikel 1</titel></kop>
<table><tgroup cols="1"><colspec colname="c1"/><tbody><row><entry colname="c1">
<al>Waarde<sup>1</sup></al></entry></row></tbody></tgroup></table>
<al><sup>1</sup>De tabelnoot.</al></divisie></bijlage></wetgeving></toestand>"""

    markdown, _, onbekend, _ = wetten.bwb_xml.omzetten(xml)

    assert "Bijlage bij artikel 1" in markdown
    assert "Waarde[^annex-1-1]" in markdown
    assert "[^annex-1-1]: De tabelnoot." in markdown
    assert onbekend == {}


def test_tussenkop_between_two_variants_of_an_article_stays_an_italic_paragraph():
    """Artikel 8:36c Awb staat twee keer in de toestand (digitaal procederen en op papier);
    een `<tussenkop kopopmaak="cur">` scheidt ze. Geen kop en geen eenheid: dat zou een
    tweede `art-8-36c` geven."""
    xml = b"""<toestand bwb-id="BWBR0000001" inwerkingtreding="2020-01-01"><wetgeving>
<citeertitel>Testwet</citeertitel><wet-besluit><wettekst>
<artikel><kop><label>Artikel</label><nr>8:36c</nr></kop>
<lid><lidnr>1</lidnr><al>Digitale variant.</al></lid>
<al><redactie type="extra">Voor overige gevallen luidt het artikel als volgt:</redactie></al>
<tussenkop kopopmaak="cur">Artikel 8:36c.</tussenkop>
<lid><lidnr>1</lidnr><al>Papieren variant.</al></lid>
</artikel></wettekst></wet-besluit></wetgeving></toestand>"""

    markdown, eenheden, onbekend, _ = wetten.bwb_xml.omzetten(xml)

    assert onbekend == {}
    assert "\n\n*Artikel 8:36c.*\n\n" in markdown
    assert markdown.index("Digitale variant.") < markdown.index("*Artikel 8:36c.*") < markdown.index("Papieren variant.")
    assert sum("Artikel 8:36c" in regel for regel in markdown.splitlines() if regel.startswith("#")) == 1
    assert [e.anker for e in eenheden if e.soort == "artikel"] == ["art-8-36c"]


def test_articles_inside_a_divisie_of_a_bijlage_get_headings_and_anchors():
    """Bijlage 2 Awb (Bevoegdheidsregeling) deelt twaalf artikelen in vier divisies in;
    de divisiekop blijft een alinea, het artikel krijgt kop en citeeranker."""
    xml = b"""<toestand bwb-id="BWBR0000001" inwerkingtreding="2020-01-01"><wetgeving>
<citeertitel>Testwet</citeertitel><bijlage><kop><label>Bijlage</label><nr>2</nr>
<titel>Bevoegdheidsregeling</titel></kop>
<divisie><kop><label>Hoofdstuk</label><nr>1</nr><titel>Van beroep uitgezonderde besluiten</titel></kop>
<artikel><kop><label>Artikel</label><nr>1</nr><titel>Geen beroep</titel></kop>
<al>Tegen een besluit kan geen beroep worden ingesteld.</al>
<lijst><li><li.nr>a.</li.nr><al>artikel 38;</al></li></lijst></artikel></divisie>
</bijlage></wetgeving></toestand>"""

    markdown, eenheden, onbekend, _ = wetten.bwb_xml.omzetten(xml)

    assert onbekend == {}
    assert "\n\nHoofdstuk 1. Van beroep uitgezonderde besluiten\n\n" in markdown
    assert "### Artikel 1. Geen beroep" in markdown
    ankers = [e.anker for e in eenheden]
    assert "annex-2-art-1" in ankers
    assert "annex-2-art-1-a" in ankers


def test_html_fallback_labels_a_resolved_url_without_a_version_date(monkeypatch):
    html = """<html><head><meta name="dcterms:title" content="Testwet"></head><body>
<div id="regeling"><h1>Testwet</h1><div class="wetgeving"><p>""" + (
        "Juridische tekst. " * 10
    ) + "</p></div></div></body></html>"

    def get(url, **kwargs):
        if url.endswith("manifest.xml"):
            return SimpleNamespace(status_code=404, content=b"", url=url)
        return SimpleNamespace(status_code=200, text=html, url="https://wetten.overheid.nl/BWBR0000001")

    monkeypatch.setattr(wetten.net, "documents", lambda: SimpleNamespace(get=get))
    monkeypatch.setattr(wetten.net, "decoded_text", lambda r: r.text)

    document = from_link("BWBR0000001")

    assert "BWBR0000001" in document.source


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
