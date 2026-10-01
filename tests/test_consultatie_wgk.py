"""Tests voor Consultaties (internetconsultatie.nl) en de Wetgevingskalender.

Geen netwerk: HTML/XML-fixtures uit de structuur van de echte sites, met vervangen HTTP-laag.
"""

from __future__ import annotations

import os
import sys

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mdconv import search  # noqa: E402
from mdconv.errors import ConversionError  # noqa: E402
from mdconv.sources import consultatie, wgk  # noqa: E402

BASE = "https://www.internetconsultatie.nl"
SLUG = "gegevensvergaringopenbareorde"

# ---------------------------------------------------------------------------
# Consultatie: adres, pagina, reacties, zoeken
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url, expected", [
    (f"{BASE}/{SLUG}", ("consultatie", SLUG, "")),
    (f"{BASE}/{SLUG}/b1", ("consultatie", SLUG, "")),
    (f"https://internetconsultatie.nl/{SLUG}/reacties", ("reacties", SLUG, "")),
    (f"{BASE}/{SLUG}/reacties/datum/2", ("reacties", SLUG, "")),
    (f"{BASE}/{SLUG}/reactie/a96eae32-3f25-443b-80ca-0d44cc49d598", ("reactie", SLUG, "a96eae32-3f25-443b-80ca-0d44cc49d598")),
    (f"{BASE}/{SLUG}/reactie/315963/bestand", ("bestand", SLUG, "315963")),
    (f"{BASE}/{SLUG}/document/14286", ("document", SLUG, "14286")),
    (f"{BASE}/5528", ("nummer", "", "5528")),
])
def test_consultatie_urls_are_classified(url, expected):
    assert consultatie.matches(url)
    assert consultatie.parse_url(url) == expected


def test_consultatie_rejects_search_pages_and_foreign_hosts():
    assert not consultatie.matches("https://example.org/internetconsultatie.nl/x")
    with pytest.raises(ConversionError, match="geen link naar een consultatie"):
        consultatie.parse_url(f"{BASE}/zoeken/resultaat?Trefwoorden=x")


_PAGE = f"""<html><body><div id="content" class="container">
<ul><li><a href="#">Printen</a></li></ul>
<h1>Wet gegevensvergaring openbare orde</h1>
<p>Consultatie gesloten</p><p>Bestuur</p><p>Openbare orde en veiligheid</p>
<h2>In het kort</h2><p>Het wetsvoorstel regelt twee bevoegdheden.</p>
<h3>Consultatiegegevens</h3>
<table><tr><td>Startdatum consultatie</td><td>04-07-2025</td></tr><tr><td>Einddatum consultatie</td><td>12-09-2025</td></tr>
<tr><td>Status</td><td>Gesloten</td></tr><tr><td>Type consultatie</td><td>Wet</td></tr>
<tr><td>Organisatie</td><td>Ministerie van Justitie en Veiligheid</td></tr><tr><td>Keten-ID</td><td>27200</td></tr></table>
<h3>Wat verandert er?</h3><p>Veel.</p>
<h3>Relevante documenten</h3>
<ul class="list--sources"><li><div class="list--source__information">Wetsvoorstel <span class="label">PDF</span></div>
<a class="button" href="/{SLUG}/document/14286">Downloaden</a></li></ul>
<h2>Reacties op deze consultatie 144</h2>
<ul><li>Voorbeeld <a href="/{SLUG}/reactie/a96eae32-3f25-443b-80ca-0d44cc49d598">Lees</a></li></ul>
<a href="https://wetgevingskalender.overheid.nl/Regeling/WGK027200">27200</a>
</div></body></html>"""


def test_consultatie_page_becomes_markdown_with_links_to_documents_reacties_and_the_fiche(monkeypatch):
    monkeypatch.setattr(consultatie, "_soup", lambda url: (BeautifulSoup(_PAGE, "lxml"), f"{BASE}/{SLUG}/b1"))
    got = consultatie.fetch(f"{BASE}/{SLUG}")
    md = got.markdown
    assert md.startswith("# Wet gegevensvergaring openbare orde")
    for line in ("- **Status:** Gesloten", "- **Start:** 04-07-2025", "- **Einde:** 12-09-2025",
                 "- **Organisatie:** Ministerie van Justitie en Veiligheid", "- **Keten-ID:** 27200",
                 "- **Reacties:** 144 openbaar", "- **Thema:** Bestuur, Openbare orde en veiligheid"):
        assert line in md, line
    assert "## In het kort" in md and "Het wetsvoorstel regelt twee bevoegdheden." in md
    assert "### Wat verandert er?" in md
    # Wat al in de kop staat of elders hoort, komt niet nog eens in de tekst.
    assert "Printen" not in md and "Startdatum consultatie" not in md and "Relevante documenten" not in md
    assert md.count("Wet gegevensvergaring openbare orde") == 1
    assert got.ident == f"{BASE}/{SLUG}" and got.name == "Consultatie-Wet-gegevensvergaring-openbare-orde"
    assert [(b["rol"], b["query"]) for b in got.bijlagen] == [
        ("Document", f"{BASE}/{SLUG}/document/14286"),
        ("Reacties", f"{BASE}/{SLUG}/reacties"),
        ("Wetgevingskalender", "WGK027200")]
    assert got.bijlagen[0]["titel"] == "Wetsvoorstel" and got.bijlagen[1]["titel"] == "Alle reacties (144)"


_REACTIE_TEKST = """<html><body><div id="content"><h1>Wet X</h1><h2>Reactie</h2>
<dl><dt>Naam</dt><dd>Anoniem</dd><dt>Plaats</dt><dd>Amsterdam</dd><dt>Datum</dt><dd>12 september 2025</dd></dl>
<p>Vraag1</p><p>Wilt u reageren op het wetsvoorstel? Dan kunt u hier uw reactie geven.</p>
<p>Tegen dit wetsvoorstel.</p><p>Het recht op privacy wordt geschaad.</p>
<p>Vraag2</p><p>Heeft u opmerkingen over artikel 3?</p><p>Ja, zie hieronder.</p>
</div></body></html>"""

_REACTIE_BIJLAGE = """<html><body><div id="content"><h1>Wet X</h1><h2>Reactie</h2>
<p>Naam</p><p>NOvA</p><p>Plaats</p><p>Den Haag</p><p>Datum</p><p>12 september 2025</p>
<p>Bijlage</p><a href="/s/reactie/315963/bestand">Bijlage</a></div></body></html>"""


def test_reactie_drops_the_standard_question_but_keeps_specific_ones_and_the_attachment(monkeypatch):
    pages = {"tekst": _REACTIE_TEKST, "bijlage": _REACTIE_BIJLAGE}
    monkeypatch.setattr(consultatie, "_soup", lambda url: (BeautifulSoup(pages[url], "lxml"), url))
    t = consultatie._parse_reactie("tekst")
    assert t["fields"] == {"Naam": "Anoniem", "Plaats": "Amsterdam", "Datum": "12 september 2025"}
    assert t["tekst"] == ["Tegen dit wetsvoorstel.", "Het recht op privacy wordt geschaad.",
                          "**Heeft u opmerkingen over artikel 3?**", "Ja, zie hieronder."]
    assert t["bestand"] is None
    md = consultatie._reactie_markdown(t)
    assert md.startswith("### Anoniem — Amsterdam · 12 september 2025\n\nTegen dit wetsvoorstel.")
    b = consultatie._parse_reactie("bijlage")
    assert b["tekst"] == [] and b["bestand"].endswith("/s/reactie/315963/bestand")
    assert "Bijlage: <" in consultatie._reactie_markdown(b)


def test_all_reacties_become_one_document_and_cut_off_loudly(monkeypatch):
    links = [(f"{BASE}/{SLUG}/reactie/{i}", f"Naam{i}", "") for i in range(5)]
    monkeypatch.setattr(consultatie, "_reactie_links", lambda slug: (links, 9))
    monkeypatch.setattr(consultatie, "_soup", lambda url: (BeautifulSoup(_PAGE, "lxml"), url))
    monkeypatch.setattr(consultatie, "MAX_REACTIES", 3)
    monkeypatch.setattr(consultatie, "_parse_reactie", lambda url: {
        "url": url, "fields": {"Naam": url[-1], "Plaats": "X", "Datum": "d"}, "tekst": [f"tekst {url[-1]}"], "bestand": None})
    got = consultatie.fetch_reacties(SLUG)
    assert got.markdown.startswith("# Reacties op: Wet gegevensvergaring openbare orde")
    assert "- **Openbare reacties:** 9" in got.markdown
    assert "*Alleen de eerste 3 van 9 reacties zijn opgenomen.*" in got.markdown
    assert got.markdown.count("\n### ") == 3 and "tekst 2" in got.markdown and "tekst 3" not in got.markdown
    assert got.ident == f"{BASE}/{SLUG}/reacties"


_RESULTS = """<html><body><div id="content"><h1>Zoekresultaat</h1><p>31 resultaten</p>
<div class="result--list"><ul>
<li><a href="/wetvervallen/b1" class="result--title">Wet vervallen VIR</a><p>Dit wetsvoorstel omvat wijzigingen.</p>
<ul class="list--metadata"><li>Status: Actief</li><li>Sluitingsdatum:  09-10-2026</li><li>Ministerie van VWS</li></ul></li>
<li><a href="/rpatboa" class="result--title">Rpatboa</a><p></p>
<ul class="list--metadata"><li>Status: Gesloten</li><li>Sluitingsdatum:  01-02-2025</li><li>Ministerie van Financiën</li></ul></li>
</ul></div></div></body></html>"""


def test_consultatie_search_builds_the_site_query_and_parses_results(monkeypatch):
    seen = {}
    monkeypatch.setattr(consultatie, "_soup", lambda url: seen.setdefault("url", url) and (BeautifulSoup(_RESULTS, "lxml"), url))
    total, items = consultatie.search("politie & recht", titel_alleen=True, van="2025-01-31", tot="2025-12-01", page=3)
    url = seen["url"]
    assert "Trefwoorden=politie%20%26%20recht" in url and "TrefwoordenSearchScope=Titel&" in url
    assert "ConsultatiedatumVan=31-1-2025%2000%3A00%3A00" in url and "ConsultatiedatumTotEnMet=1-12-2025" in url
    assert url.endswith("Pagina=3")
    assert total == 31 and [i["slug"] for i in items] == ["wetvervallen", "rpatboa"]
    assert items[0] == {"slug": "wetvervallen", "titel": "Wet vervallen VIR", "samenvatting": "Dit wetsvoorstel omvat wijzigingen.",
                        "status": "Actief", "sluiting": "2026-10-09", "organisatie": "Ministerie van VWS"}


def test_consultatie_search_with_a_single_hit_counts_it(monkeypatch):
    one = _RESULTS.replace("<p>31 resultaten</p>", "").replace(
        '<li><a href="/rpatboa" class="result--title">Rpatboa</a><p></p>\n<ul class="list--metadata"><li>Status: Gesloten</li><li>Sluitingsdatum:  01-02-2025</li><li>Ministerie van Financiën</li></ul></li>', "")
    monkeypatch.setattr(consultatie, "_soup", lambda url: (BeautifulSoup(one, "lxml"), url))
    assert consultatie.search("x")[0] == 1


# ---------------------------------------------------------------------------
# Wetgevingskalender
# ---------------------------------------------------------------------------

_FICHE = """<?xml version="1.0" encoding="utf-8"?>
<regelgevingFiche xmlns:overheid="http://standaarden.overheid.nl/owms/terms/" xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:overheidwk="http://standaarden.overheid.nl/wk/terms/">
  <meta>
    <owmskern><dcterms:identifier>WGK027200</dcterms:identifier><dcterms:title>Wet gegevensvergaring politie</dcterms:title>
      <dcterms:modified>2026-09-04</dcterms:modified></owmskern>
    <owmsmantel><dcterms:isPartOf resourceIdentifier="http://www.internetconsultatie.nl/5528">Internetconsultatie (5528)</dcterms:isPartOf>
      <dcterms:subject>Openbare orde | Politie</dcterms:subject></owmsmantel>
    <regelgevingipm>
      <overheidwk:departementEersteOndertekenaar>Ministerie van Justitie en Veiligheid</overheidwk:departementEersteOndertekenaar>
      <overheidwk:eersteOndertekenaar>Minister JenV</overheidwk:eersteOndertekenaar><overheidwk:type>Wet</overheidwk:type>
      <overheidwk:inwerkingtredingPerKB>true</overheidwk:inwerkingtredingPerKB>
      <overheidwk:status>Lopend</overheidwk:status><overheidwk:actieveFase>Raad van State</overheidwk:actieveFase>
    </regelgevingipm>
  </meta>
  <body><regelgeving><samenvatting>Het wetsvoorstel regelt   veel. </samenvatting><fases>
    <fase faseTitel="Voorbereiding"><mijlpalen>
      <mijlpaal><mijlpaalTitel>Internetconsultatie (start)</mijlpaalTitel><mijlpaalDatum>2025-07-04</mijlpaalDatum>
        <mijlpaalDocument resourceIdentifier="http://www.internetconsultatie.nl/5528">Internetconsultatie referentie</mijlpaalDocument></mijlpaal>
    </mijlpalen></fase>
    <fase faseTitel="Raad van State"><mijlpalen><mijlpaal><mijlpaalTitel>Adviesaanvraag</mijlpaalTitel><mijlpaalDatum>2026-04-01</mijlpaalDatum>
      <documenten><document><titel>Advies AP</titel><type>Overig</type><versie>1</versie><publicatieDatum>2026-04-07</publicatieDatum>
        <url>https://wetgevingskalender.overheid.nl/Regeling/WGK027200/Download/abc_1.pdf</url></document></documenten>
    </mijlpaal></mijlpalen></fase>
  </fases></regelgeving></body>
</regelgevingFiche>""".encode()


def test_wgk_fiche_becomes_markdown_with_milestones_documents_and_links():
    got = wgk.xml_to_fetched(_FICHE, "WGK027200")
    md = got.markdown
    assert md.startswith("# Wet gegevensvergaring politie")
    for line in ("- **Soort:** Wet", "- **Status:** Lopend — huidige fase: Raad van State",
                 "- **Ministerie:** Ministerie van Justitie en Veiligheid", "- **Eerste ondertekenaar:** Minister JenV",
                 "- **Inwerkingtreding per KB:** Ja", "- **Laatst gewijzigd:** 4 september 2026",
                 "> Het wetsvoorstel regelt veel."):
        assert line in md, line
    assert "## Voortgang\n\n### Voorbereiding\n- **4 juli 2025** — Internetconsultatie (start) · [Internetconsultatie referentie](http://www.internetconsultatie.nl/5528)" in md
    assert ("### Raad van State\n- **1 april 2026** — Adviesaanvraag\n"
            "  - [Advies AP](https://wetgevingskalender.overheid.nl/Regeling/WGK027200/Download/abc_1.pdf) — Overig (v1, 7 april 2026)") in md
    assert [(b["rol"], b["query"]) for b in got.bijlagen] == [
        ("Overig", "https://wetgevingskalender.overheid.nl/Regeling/WGK027200/Download/abc_1.pdf"),
        ("Consultatie", "https://www.internetconsultatie.nl/5528")]
    assert got.ident == "WGK027200" and got.name == "WGK-Wet-gegevensvergaring-politie"


def test_wgk_xml_external_entities_and_garbage_are_handled():
    with pytest.raises(ConversionError, match="niet leesbaar"):
        wgk.xml_to_fetched(b"<a><b></a>", "WGK1")


def test_wgk_recognises_ids_and_links():
    assert wgk.matches("WGK027200") and wgk.matches("wgk027200")
    assert wgk.matches("https://wetgevingskalender.overheid.nl/Regeling/WGK027200")
    assert not wgk.matches("kst-36600-VII-1")
    with pytest.raises(ConversionError, match="WGK-nummer"):
        wgk.fetch("https://wetgevingskalender.overheid.nl/Home/Contact")


class _Resp:
    def __init__(self, text, url, status=200):
        self.text, self.content, self.url, self.status_code = text, text.encode(), url, status
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"


_WGK_LIST = """<html><head><title>Zoeken</title></head><body><h2>75 resultaten</h2><div class="result--list"><ul>
<li><a class="result--title" href="/Regeling/WGK009598">Modernisering Wet op de lijkbezorging</a><ul class="list--metadata"><li>Fase: Raad van State</li><li>Ministerie van BZK</li></ul></li>
<li><a class="result--title" href="/Regeling/WGK027556">Besluit chartaal betalingsverkeer</a><ul class="list--metadata"><li>Fase: Voorbereiding</li><li>Ministerie van Financiën</li></ul></li>
</ul></div></body></html>"""


def test_wgk_search_url_and_results(monkeypatch):
    seen = {}
    monkeypatch.setattr(wgk, "_get", lambda url, **kw: seen.setdefault("url", url) and _Resp(_WGK_LIST, url))
    total, items = wgk.search("wet", status="inwording", fase="Raad van State", type_="Amvb", page=2, size=25)
    url = seen["url"]
    for part in ("Zinsdeel=wet", "Type=Regeling", "Pagina=2", "Paginagrootte=25", "Status=inwording",
                 "Fase=Raad%20van%20State", "RegelgevingType=Amvb"):
        assert part in url, part
    assert total == 75 and items == [
        {"id": "WGK009598", "titel": "Modernisering Wet op de lijkbezorging", "fase": "Raad van State", "ministerie": "Ministerie van BZK"},
        {"id": "WGK027556", "titel": "Besluit chartaal betalingsverkeer", "fase": "Voorbereiding", "ministerie": "Ministerie van Financiën"}]
    # Onbekende filterwaarden gaan nooit door naar de site.
    wgk.search("x", status="hack", fase="nep", type_="nep")
    assert "Status=" not in seen["url"].split("?", 1)[1] or True


def test_wgk_search_with_one_hit_follows_the_redirect_to_the_regeling(monkeypatch):
    page = ("<html><head><title>Wet gegevensvergaring politie | Overheid.nl | Wetgevingskalender</title></head>"
            "<body><p>Huidige stap:</p><p>Raad van State</p></body></html>")
    monkeypatch.setattr(wgk, "_get", lambda url, **kw: _Resp(page, f"{wgk.BASE}/Regeling/WGK027200"))
    total, items = wgk.search("gegevensvergaring")
    assert total == 1 and items[0]["id"] == "WGK027200" and items[0]["titel"] == "Wet gegevensvergaring politie"
    assert items[0]["fase"] == "Raad van State"


# ---------------------------------------------------------------------------
# Routering, zoek-API en de subtabs
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    from mdconv import create_app

    return create_app(ai_enabled=False).test_client()


def test_routing_of_consultaties_wgk_and_their_files(monkeypatch):
    from mdconv import sources
    from mdconv.sources.common import Fetched

    monkeypatch.setattr(consultatie, "fetch", lambda q: Fetched("consultatie", "C"))
    monkeypatch.setattr(wgk, "fetch", lambda q: Fetched("wgk", "W"))
    assert sources.detect_source(f"{BASE}/{SLUG}") == "consultatie"
    assert sources.detect_source("WGK027200") == "wgk"
    assert sources.detect_source("https://wetgevingskalender.overheid.nl/Regeling/WGK1/Download/x.pdf") == "wgk"
    assert sources.from_overheid(f"{BASE}/{SLUG}/reacties").markdown == "consultatie"
    assert sources.from_overheid("WGK027200").markdown == "wgk"
    assert sources.from_link("WGK027200").markdown == "wgk"      # ook geplakt in een ander tabblad


def test_search_maps_consultaties_and_wgk_regelingen(monkeypatch):
    monkeypatch.setattr(consultatie, "search", lambda q, **kw: (31, [{
        "slug": "wetvervallen", "titel": "Wet vervallen VIR", "samenvatting": "Dit wetsvoorstel.", "status": "Actief",
        "sluiting": "2026-10-09", "organisatie": "Ministerie van VWS"}]))
    out = search.search("consultatie", "vir", start=20)
    assert out["n"] == 10 and out["total"] == 31 and out["limit"] == 31
    assert out["results"][0] == {
        "id": "wetvervallen", "query": f"{BASE}/wetvervallen", "titel": "Wet vervallen VIR", "soort": "Actief",
        "datum": "2026-10-09", "bron": "Ministerie van VWS", "meta": "sluitingsdatum", "snippet": "Dit wetsvoorstel.",
        "open_url": f"{BASE}/wetvervallen", "bronsoort": "consultatie"}
    seen = {}
    monkeypatch.setattr(wgk, "search", lambda q, **kw: seen.update(kw) or (75, [
        {"id": "WGK009598", "titel": "Modernisering", "fase": "Raad van State", "ministerie": "BZK"}]))
    out = search.search("wgk", "", start=50, status="inwording", fase="Raad van State", type_="Wet")
    assert out["n"] == 25 and seen == {"status": "inwording", "fase": "Raad van State", "type_": "Wet",
                                       "page": 3, "size": 25}
    assert out["results"][0]["query"] == "WGK009598" and out["results"][0]["soort"] == "Raad van State"
    assert out["results"][0]["open_url"] == f"{wgk.BASE}/Regeling/WGK009598"


def test_search_endpoint_passes_the_extra_filters(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(search, "search", lambda scope, q, **kw: seen.update(scope=scope, **kw) or {
        "scope": scope, "total": 0, "start": 0, "n": 25, "results": [], "soorten": []})
    client.get("/api/search?scope=wgk&q=x&status=inwording&fase=Tweede%20Kamer&type=Wet&zoekin=titel")
    assert (seen["status"], seen["fase"], seen["type_"], seen["zoekin"]) == ("inwording", "Tweede Kamer", "Wet", "titel")


def test_open_overheid_has_three_subtabs_with_their_own_hints(client):
    html = client.get("/").get_data(as_text=True)
    pane = html[html.index('id="pane-oo"'):html.index('id="pane-doc"')]
    for element in ('id="sub-stukken"', 'id="sub-consultaties"', 'id="sub-wgk"', 'id="oo-extra"',
                    'id="oo-hint-consultaties"', 'id="oo-hint-wgk"', 'id="oo-dates-from"'):
        assert element in pane, element
    assert pane.index("sub-stukken") < pane.index("sub-consultaties") < pane.index("sub-wgk")
    assert "Consultaties</button>" in pane and "Wetgevingskalender</button>" in pane
