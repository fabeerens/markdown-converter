"""Tests voor zoeken (SRU + open.overheid.nl), de Woo-bron en de routering.

Geen netwerk: de HTTP-laag en de bronnen worden vervangen.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mdconv import search  # noqa: E402
from mdconv.errors import ConversionError  # noqa: E402
from mdconv.sources import sru, woo  # noqa: E402


# ---------------------------------------------------------------------------
# SRU
# ---------------------------------------------------------------------------

_BASE = "c.product-area==officielepublicaties"


def test_sru_query_for_fulltext_search_with_dates_and_exclusion():
    cql = sru.build_query("klimaat energie", "kamerstuk", "2026-01-01", "2026-06-30", "relevantie")
    assert cql == (
        f'{_BASE} AND w.publicatienaam==Kamerstuk AND cql.textAndIndexes="klimaat energie" '
        "AND dt.date>=2026-01-01 AND dt.date<=2026-06-30 NOT dt.type==Bijlage"
    )


def test_sru_query_treats_a_dossier_number_as_a_dossier_search():
    assert 'w.dossiernummer=="36600"' in sru.build_query("36 600", "alles", "", "", "relevantie")
    assert 'w.dossiernummer=="36600-VII"' in sru.build_query("36600-vii", "alles", "", "", "relevantie")
    assert "cql.textAndIndexes" not in sru.build_query("36600", "alles", "", "", "relevantie")


@pytest.mark.parametrize("q, dossier", [
    ("36600", "36600"), ("36 600", "36600"), ("36600-VII", "36600-VII"), ("36600 vii", "36600-VII"),
    ("dossier 36600", "36600"), ("Kamerstukken 36 600-XV", "36600-XV"),
    ("2026", None), ("klimaat", None), ("36600 klimaat", None), ("", None),
])
def test_dossier_number_is_recognised_in_the_search_term(q, dossier):
    assert sru.dossier_of(q) == dossier


def test_a_dossier_search_reads_chronologically_unless_told_otherwise():
    assert sru.build_query("36600", "alles", "", "", "relevantie").endswith("sortBy dt.date/sort.ascending")
    assert sru.build_query("36600", "alles", "", "", "nieuwste").endswith("sortBy dt.date/sort.descending")


def test_sru_query_sorting_and_quote_safety():
    assert sru.build_query("", "aanhangsel", "", "", "relevantie").endswith("sortBy dt.date/sort.descending")
    assert sru.build_query("x", "alles", "", "", "oudste").endswith("sortBy dt.date/sort.ascending")
    assert "sortBy" not in sru.build_query("x", "alles", "", "", "relevantie")
    cql = sru.build_query('a" OR w.publicatienaam==Staatscourant "b\\', "alles", "", "", "relevantie")
    # Alleen onze eigen quotes: twee om "Kamervragen (Aanhangsel)", twee om de zoektekst.
    assert cql.count('"') == 4
    # Een ongeldige datum komt nooit in de query.
    assert "dt.date" not in sru.build_query("x", "alles", "morgen", "'; drop", "relevantie")


_SRU_XML = """<?xml version="1.0"?>
<sru:searchRetrieveResponse xmlns:sru="http://docs.oasis-open.org/ns/search-ws/sruResponse"
  xmlns:dcterms="http://purl.org/dc/terms/" xmlns:gzd="http://standaarden.overheid.nl/sru"
  xmlns:overheidwetgeving="http://standaarden.overheid.nl/wetgeving/">
 <sru:numberOfRecords>2</sru:numberOfRecords>
 <sru:records>
  <sru:record><sru:recordData><gzd:gzd><gzd:originalData><overheidwetgeving:meta>
    <overheidwetgeving:owmskern>
      <dcterms:identifier>blg-1184123</dcterms:identifier>
      <dcterms:title>Beslisnota</dcterms:title>
      <dcterms:type scheme="OVERHEIDop.Parlementair">Bijlage</dcterms:type>
      <dcterms:creator scheme="OVERHEID.StatenGeneraal">Tweede Kamer der Staten-Generaal</dcterms:creator>
    </overheidwetgeving:owmskern>
    <overheidwetgeving:owmsmantel><dcterms:date>2025-02-20</dcterms:date></overheidwetgeving:owmsmantel>
    <overheidwetgeving:tpmeta>
      <overheidwetgeving:hoofddocument>kst-36180-133</overheidwetgeving:hoofddocument>
      <overheidwetgeving:vergaderjaar>2024-2025</overheidwetgeving:vergaderjaar>
      <overheidwetgeving:publicatienaam>Kamerstuk</overheidwetgeving:publicatienaam>
    </overheidwetgeving:tpmeta>
  </overheidwetgeving:meta></gzd:originalData>
  <gzd:enrichedData>
    <gzd:preferredUrl>https://zoek.officielebekendmakingen.nl/blg-1184123.html</gzd:preferredUrl>
    <gzd:itemUrl manifestation="metadata">https://r/metadata.xml</gzd:itemUrl>
    <gzd:itemUrl manifestation="pdf">https://r/blg-1184123.pdf</gzd:itemUrl>
  </gzd:enrichedData></gzd:gzd></sru:recordData></sru:record>
  <sru:record><sru:recordData><gzd:gzd><gzd:originalData><overheidwetgeving:meta>
    <overheidwetgeving:owmskern>
      <dcterms:identifier>kst-36180-133</dcterms:identifier>
      <dcterms:type scheme="OVERHEIDop.Parlementair">Kamerstuk</dcterms:type>
      <dcterms:type scheme="OVERHEIDop.KamerstukTypen">Brief regering</dcterms:type>
    </overheidwetgeving:owmskern>
  </overheidwetgeving:meta></gzd:originalData></gzd:gzd></sru:recordData></sru:record>
 </sru:records>
</sru:searchRetrieveResponse>""".encode()


def test_sru_response_is_parsed_into_records():
    total, records = sru._records(_SRU_XML)
    assert total == 2 and [r.ident for r in records] == ["blg-1184123", "kst-36180-133"]
    blg, kst = records
    assert (blg.title, blg.soort, blg.date, blg.hoofddocument, blg.vergaderjaar) == (
        "Beslisnota", "Bijlage", "2025-02-20", "kst-36180-133", "2024-2025")
    assert blg.pdf_url == "https://r/blg-1184123.pdf" and blg.page_url.endswith("blg-1184123.html")
    assert kst.subsoort == "Brief regering" and kst.pdf_url is None


def test_sru_diagnostics_become_a_dutch_error():
    xml = (b'<diagnostics><diagnostic xmlns="http://www.loc.gov/zing/srw/diagnostic/">'
           b"<details>cql.x</details><message>Unsupported index</message></diagnostic></diagnostics>")
    with pytest.raises(ConversionError, match="weigerde"):
        sru._records(xml)


# ---------------------------------------------------------------------------
# Woo (open.overheid.nl)
# ---------------------------------------------------------------------------

_UUID = "3174ead9-a4cb-4eb9-a26c-c2d8cc619c35"


def test_woo_recognises_links_and_prefixed_ids_but_not_a_bare_uuid():
    assert woo.matches(f"https://open.overheid.nl/documenten/{_UUID}")
    assert woo.matches("ronl-c714589f8b6e27c74faa33435ecc3e87c5abdfd4_2")
    assert woo.matches("oep-d2030ec85652675cd")
    # Een kale UUID is ook een Tweede Kamer-Document-Id: de routering vraagt het aan de bron.
    assert not woo.matches(_UUID)
    assert not woo.matches("kst-36600-VII-1")


def test_woo_parse_id():
    assert woo.parse_id(f"https://open.overheid.nl/documenten/{_UUID}") == _UUID
    assert woo.parse_id(f"{_UUID}_2") == f"{_UUID}_2"
    assert woo.parse_id("https://open.overheid.nl/documenten/ronl-abc123_2?x=1") == "ronl-abc123_2"
    with pytest.raises(ConversionError, match="open.overheid.nl"):
        woo.parse_id("https://open.overheid.nl/over")


def test_woo_picks_the_convertible_file_and_skips_zips():
    det = {"versies": [{"bestanden": [
        {"mime-type": "application/zip", "bestandsnaam": "alles.zip"},
        {"mime-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "bestandsnaam": "a.docx"},
        {"mime-type": "application/pdf", "bestandsnaam": "a.pdf"},
    ]}]}
    assert woo._pick_file(det)["bestandsnaam"] == "a.pdf"
    assert woo._pick_file({"versies": [{"bestanden": [{"mime-type": "application/zip"}]}]}) is None
    assert woo._pick_file({"versies": []}) is None


def test_woo_version_suffix_is_stripped_for_the_file_and_untrusted_hosts_are_ignored(monkeypatch):
    seen = []

    class Resp:
        status_code = 200
        headers = {"Content-Type": "application/pdf"}

        def iter_content(self, n):
            yield b"%PDF"

    monkeypatch.setattr(woo, "_get", lambda url, **kw: seen.append(url) or Resp())
    woo._download("abc_2", {"mime-type": "application/pdf"})
    woo._download("abc_2", {"url": "https://evil.example/x.pdf"})
    woo._download("abc_2", {"url": "https://opendata.rijksoverheid.nl/x.pdf"})
    assert seen == [f"{woo.API}/documenten/abc", f"{woo.API}/documenten/abc",
                    "https://opendata.rijksoverheid.nl/x.pdf"]


def test_woo_relations_become_bijlagen_with_their_titles(monkeypatch):
    det = {"documentrelaties": [
        {"relation": f"{woo.SITE}/aaa", "role": "https://identifier.overheid.nl/tooi/def/thes/kern/c_05f4a5f3"},
        {"relation": f"{woo.SITE}/bbb", "role": "https://identifier.overheid.nl/tooi/def/thes/kern/c_4d1ea9ba",
         "titel": "Reservetitel"},
        {"relation": f"{woo.SITE}/ccc", "role": "https://identifier.overheid.nl/plooi/def/thes/documentrelatie/identiteitsgroep"},
    ]}
    monkeypatch.setattr(woo, "detail", lambda rid: {"document": {"titelcollectie": {"officieleTitel": f"Titel {rid}"}}}
                        if rid == "aaa" else None)
    items = woo._related(det)
    assert [(i["query"], i["titel"], i["rol"]) for i in items] == [
        ("aaa", "Titel aaa", "Bijlage"), ("bbb", "Reservetitel", "Is bijlage bij")]   # identiteitsgroep valt weg
    assert items[0]["open_url"] == f"{woo.SITE}/aaa"


def test_woo_fetch_builds_header_and_warns_about_scans(monkeypatch):
    from mdconv.sources import files

    det = {
        "document": {
            "titelcollectie": {"officieleTitel": "Beslisnota X"},
            "publisher": {"label": "ministerie van Y"},
            "omschrijvingen": ["Korte omschrijving."],
            "classificatiecollectie": {"documentsoorten": [{"label": "beslisnota"}], "themas": [{"label": "klimaat"}]},
        },
        "versies": [{"openbaarmakingsdatum": "2026-09-30",
                     "bestanden": [{"mime-type": "application/pdf", "bestandsnaam": "n.pdf",
                                    "grootte": 1048576, "paginas": 3}]}],
        "documentrelaties": [],
    }
    monkeypatch.setattr(woo, "detail", lambda i: det)
    monkeypatch.setattr(woo, "_download", lambda i, e: (b"%PDF", "application/pdf"))
    monkeypatch.setattr(files, "convert", lambda data, name: ("", "MarkItDown"))
    got = woo.fetch(f"https://open.overheid.nl/documenten/{_UUID}")
    assert got.markdown.startswith("# Beslisnota X")
    for line in ("- **Soort:** beslisnota", "- **Organisatie:** ministerie van Y",
                 "- **Openbaarmakingsdatum:** 30 september 2026", "- **Thema:** klimaat",
                 "- **Bestand:** n.pdf (1.00 MB, 3 p.)", f"<{woo.SITE}/{_UUID}>"):
        assert line in got.markdown
    assert "> Korte omschrijving." in got.markdown and "scan" in got.markdown
    assert got.ident == _UUID and got.name == "Woo-Beslisnota-X"
    assert got.source == f"open.overheid.nl • Beslisnota X ({_UUID}) • MarkItDown"


def test_woo_fetch_without_a_convertible_file_explains_itself(monkeypatch):
    monkeypatch.setattr(woo, "detail", lambda i: {"document": {}, "versies": [{"bestanden": []}]})
    with pytest.raises(ConversionError, match="geen bestand"):
        woo.fetch(f"https://open.overheid.nl/documenten/{_UUID}")
    monkeypatch.setattr(woo, "detail", lambda i: None)
    with pytest.raises(ConversionError, match="niet gevonden"):
        woo.fetch(f"https://open.overheid.nl/documenten/{_UUID}")


def test_woo_search_url_double_encodes_filters_and_converts_dates(monkeypatch):
    seen = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"totaal": 0, "resultaten": [], "filters": {}}

    monkeypatch.setattr(woo, "_get", lambda url, **kw: seen.setdefault("url", url) and Resp())
    woo.search("klimaat & energie", soort="Woo-verzoek of -besluit", van="2026-01-31", sort="oudste", n=50, start=50)
    url = seen["url"]
    assert "zoektekst=klimaat%20%26%20energie" in url
    assert "documentsoort=Woo-verzoek%2520of%2520-besluit" in url      # dubbel gecodeerd, zoals de site
    assert "publicatiedatumVan=31-01-2026" in url
    assert "sort=publicatiedatum" in url and "order=asc" in url
    assert "aantalResultaten=50" in url and "start=50" in url


def test_woo_search_falls_back_to_a_valid_page_size(monkeypatch):
    seen = {}

    class Resp:
        status_code = 200

        def json(self):
            return {}

    monkeypatch.setattr(woo, "_get", lambda url, **kw: seen.setdefault("url", url) and Resp())
    woo.search("x", n=25)
    assert "aantalResultaten=20" in seen["url"]


# ---------------------------------------------------------------------------
# Zoeken: uniforme resultaten
# ---------------------------------------------------------------------------

def test_search_pub_maps_records_to_results(monkeypatch):
    rec = sru.Record(ident="kst-36180-133", title="Brief", soort="Kamerstuk", subsoort="Brief regering",
                     creator="Tweede Kamer der Staten-Generaal", date="2025-02-20", vergaderjaar="2024-2025",
                     dossiernummer="36180", indiener="R.J. Klever",
                     page_url="https://zoek.officielebekendmakingen.nl/kst-36180-133.html")
    monkeypatch.setattr(sru, "search", lambda q, **kw: (1, [rec]))
    out = search.search("pub", "klimaat")
    assert out["total"] == 1 and out["soorten"][0]["key"] == "alles"
    assert out["results"][0] == {
        "id": "kst-36180-133", "query": "kst-36180-133", "titel": "36180, nr. 133 - Brief",
        "soort": "Brief regering",
        "datum": "2025-02-20", "bron": "Tweede Kamer",
        "meta": "Vergaderjaar 2024-2025 · dossier 36180 · R.J. Klever", "snippet": "",
        "open_url": "https://zoek.officielebekendmakingen.nl/kst-36180-133.html", "bronsoort": "pub"}


def test_stuk_label_makes_dossier_lists_scannable():
    assert search._stuk_label("kst-36600-VII-1") == "36600-VII, nr. 1"
    assert search._stuk_label("kst-21501-02-3174") == "21501-02, nr. 3174"
    assert search._stuk_label("kst-36836-D") == "36836, nr. D"
    for ident in ("kst-1268678", "blg-1184123", "ah-tk-20242025-100"):
        assert search._stuk_label(ident) == ""


def test_search_woo_maps_results_strips_highlight_markup_and_lists_facets(monkeypatch):
    raw = {"totaal": 2, "resultaten": [{
        "document": {"id": _UUID, "titel": "Antwoorden", "openbaarmakingsdatum": "2026-09-30",
                     "publisher": "ministerie van EZK", "pid": f"{woo.SITE}/{_UUID}"},
        "bestandsType": "application/pdf", "aantalPaginas": 13, "bestandsgrootte": "0.14 MB",
        "highlightedText": "over <b>klimaat</b> en energie"}],
        "filters": {"documentsoort": [{"naam": "brief", "aantal": 5}, {"naam": "advies", "aantal": 9}]}}
    monkeypatch.setattr(woo, "search", lambda q, **kw: raw)
    out = search.search("woo", "klimaat")
    r = out["results"][0]
    assert r["snippet"] == "over klimaat en energie" and r["meta"] == "PDF · 13 p. · 0.14 MB"
    assert r["query"] == _UUID and r["bron"] == "ministerie van EZK"
    assert [s["key"] for s in out["soorten"]] == ["advies", "brief"]       # meeste eerst


# -- Alles: één samengevoegde lijst ------------------------------------------

def _pub(i, titel, datum, **kw):
    return {"id": i, "query": i, "titel": titel, "soort": "", "datum": datum, "bron": "Tweede Kamer",
            "meta": "", "snippet": "", "open_url": "", "bronsoort": "pub", **kw}


def _wo(i, titel, datum):
    return {"id": i, "query": i, "titel": titel, "soort": "", "datum": datum, "bron": "ministerie",
            "meta": "", "snippet": "", "open_url": "", "bronsoort": "woo"}


def _stub_sources(monkeypatch, pub, woo_, pub_total=None, woo_total=None, seen=None):
    def fake_pub(q, soort, van, tot, sort, start, n):
        if seen is not None:
            seen.append(("pub", start, n))
        if isinstance(pub, Exception):
            raise pub
        return (pub_total or len(pub)), pub[start:start + n]

    def fake_woo(q, soort, van, tot, sort, start, n):
        if seen is not None:
            seen.append(("woo", start, n))
        if isinstance(woo_, Exception):
            raise woo_
        return (woo_total or len(woo_)), woo_[start:start + n], {}

    monkeypatch.setattr(search, "_search_pub", fake_pub)
    monkeypatch.setattr(search, "_search_woo", fake_woo)


def test_all_merges_both_sources_into_one_list_sorted_by_date(monkeypatch):
    _stub_sources(monkeypatch,
                  [_pub("kst-1", "A", "2026-09-10"), _pub("kst-2", "B", "2026-09-01")],
                  [_wo("w1", "C", "2026-09-20"), _wo("w2", "D", "2026-09-05")])
    out = search.search("alles", "x", sort="nieuwste")
    assert [r["id"] for r in out["results"]] == ["w1", "kst-1", "w2", "kst-2"]
    assert out["total"] == 4 and out["totals"] == {"pub": 2, "woo": 2}
    out = search.search("alles", "x", sort="oudste")
    assert [r["id"] for r in out["results"]] == ["kst-2", "w2", "kst-1", "w1"]


def test_all_interleaves_by_relevance_so_neither_source_takes_over(monkeypatch):
    _stub_sources(monkeypatch,
                  [_pub(f"p{i}", f"P{i}", "2026-01-01") for i in range(3)],
                  [_wo(f"w{i}", f"W{i}", "2026-01-01") for i in range(3)])
    out = search.search("alles", "x", sort="relevantie")
    assert [r["id"] for r in out["results"]] == ["p0", "w0", "p1", "w1", "p2", "w2"]


def test_all_lists_a_document_that_is_in_both_sources_once_and_says_so(monkeypatch):
    _stub_sources(monkeypatch,
                  [_pub("blg-9", "Begroting en beheerplan Nationale Politie 2027-2031", "2026-09-15"),
                   _pub("kst-5", "Evaluatie Wet openbare manifestaties; Brief regering; Kabinetsreactie op rapporten",
                        "2026-09-04")],
                  [_wo("oep-1", "37020-VI, nr. 2 - Begroting en beheerplan Nationale Politie 2027-2031", "2026-09-18"),
                   _wo("oep-2", "34324, nr. 42 - Kabinetsreactie op rapporten", "2026-09-04"),
                   # Zelfde titel, maar een jaar eerder: een ander document.
                   _wo("oep-3", "Begroting en beheerplan Nationale Politie 2027-2031", "2025-09-15")])
    out = search.search("alles", "x", sort="nieuwste")
    assert sorted(r["id"] for r in out["results"]) == ["blg-9", "kst-5", "oep-3"]
    by_id = {r["id"]: r for r in out["results"]}
    assert by_id["blg-9"]["ook_woo"] and by_id["kst-5"]["ook_woo"]      # officiële publicatie wint
    assert "ook_woo" not in by_id["oep-3"]
    assert "oep-1" not in by_id and "oep-2" not in by_id


def test_all_pages_through_the_merged_list_and_caps_the_depth(monkeypatch):
    seen = []
    pub = [_pub(f"p{i:03d}", f"P{i}", f"2026-06-{(i % 28) + 1:02d}") for i in range(30)]
    woo_ = [_wo(f"w{i:03d}", f"W{i}", f"2026-07-{(i % 28) + 1:02d}") for i in range(30)]
    _stub_sources(monkeypatch, pub, woo_, pub_total=5000, woo_total=7000, seen=seen)
    page2 = search.search("alles", "x", sort="nieuwste", start=20, n=20)
    assert len(page2["results"]) == 20 and page2["total"] == 12000
    assert page2["limit"] == search.MERGE_LIMIT
    # Per bron de eerste start+n resultaten (en niet méér).
    assert ("pub", 0, 40) in seen and ("woo", 0, 40) in seen
    # Een pagina voorbij de begrenzing vraagt nooit meer dan MERGE_LIMIT per bron.
    seen.clear()
    search.search("alles", "x", sort="nieuwste", start=190, n=50)
    assert max(st + k for _s, st, k in seen) <= search.MERGE_LIMIT


def test_all_survives_one_failing_source_but_says_so(monkeypatch):
    _stub_sources(monkeypatch, RuntimeError("SRU down"), [_wo("w1", "C", "2026-09-20")])
    out = search.search("alles", "x")
    assert [r["id"] for r in out["results"]] == ["w1"] and out["totals"] == {"woo": 1}
    assert "parlementaire stukken mislukte" in out["waarschuwing"] and "SRU down" in out["waarschuwing"]
    _stub_sources(monkeypatch, RuntimeError("a"), RuntimeError("b"))
    with pytest.raises(ConversionError, match="mislukte"):
        search.search("alles", "x")


def test_all_needs_a_term_or_a_date_and_is_the_default_scope(client, monkeypatch):
    with pytest.raises(ConversionError, match="zoekterm"):
        search.search("alles", " ")
    seen = {}
    monkeypatch.setattr(search, "search", lambda scope, q, **kw: seen.setdefault("scope", scope) and
                        {"scope": scope, "total": 0, "start": 0, "n": 20, "results": [], "soorten": []})
    client.get("/api/search?q=x")
    assert seen["scope"] == "alles"


def test_a_dossier_number_lists_the_whole_dossier_from_the_official_publications(monkeypatch):
    seen = []

    def fake_pub(q, soort, van, tot, sort, start, n):
        seen.append((q, soort, sort, start, n))
        return 218, [_pub("kst-36600-VII-1", "Voorstel van wet", "2024-09-17")]

    monkeypatch.setattr(search, "_search_pub", fake_pub)
    monkeypatch.setattr(search, "_search_woo", lambda *a: pytest.fail("Woo hoort niet bij een dossierlijst"))
    out = search.search("alles", "36600-VII", sort="relevantie", n=20)
    assert out["dossier"] == "36600-VII" and out["total"] == 218 and out["limit"] == 218
    assert out["n"] == 50 and seen == [("36600-VII", "", "relevantie", 0, 50)]    # grotere pagina's
    # Eén bron gekozen: het soortfilter blijft bruikbaar.
    out = search.search("pub", "36600", soort="bijlage")
    assert out["dossier"] == "36600" and seen[-1][1] == "bijlage"
    # In de Woo-bron zelf blijft het een gewone tekstzoekopdracht.
    monkeypatch.setattr(search, "_search_woo", lambda *a: (1, [], {}))
    assert "dossier" not in search.search("woo", "36600")


def test_search_woo_needs_a_term_or_a_filter_and_unknown_scopes_are_rejected():
    with pytest.raises(ConversionError, match="zoekterm"):
        search.search("woo", "  ")
    with pytest.raises(ConversionError, match="Onbekende zoekbron"):
        search.search("elders", "x")


# ---------------------------------------------------------------------------
# Routering en HTTP
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    from mdconv import create_app

    return create_app(ai_enabled=False).test_client()


def test_detect_source_routes_open_overheid_links_and_ids():
    from mdconv.sources import detect_source

    assert detect_source(f"https://open.overheid.nl/documenten/{_UUID}") == "woo"
    assert detect_source("ronl-abc_2") == "woo"
    assert detect_source("blg-1184123") == "kamerstuk"
    assert detect_source("ah-1271549") == "kamerstuk"


def test_from_overheid_routes_bare_uuids_by_asking_the_source(monkeypatch):
    from mdconv import sources
    from mdconv.sources import kamerstuk
    from mdconv.sources.common import Fetched

    monkeypatch.setattr(woo, "fetch", lambda q: Fetched("woo", "W"))
    monkeypatch.setattr(kamerstuk, "fetch", lambda q: Fetched("kst", "K"))
    monkeypatch.setattr(woo, "is_known_id", lambda q: True)
    assert sources.from_overheid(_UUID).markdown == "woo"
    monkeypatch.setattr(woo, "is_known_id", lambda q: False)
    assert sources.from_overheid(_UUID).markdown == "kst"           # TK Document-Id
    assert sources.from_overheid(f"{_UUID}_2").markdown == "woo"    # versiesuffix: alleen Woo
    assert sources.from_overheid(f"https://open.overheid.nl/documenten/{_UUID}").markdown == "woo"
    assert sources.from_overheid("kst-36600-VII-1").markdown == "kst"


def test_search_endpoint_passes_parameters_and_clamps_numbers(client, monkeypatch):
    seen = {}

    def fake(scope, q, **kw):
        seen.update(scope=scope, q=q, **kw)
        return {"scope": scope, "total": 0, "start": kw["start"], "n": kw["n"], "results": [], "soorten": []}

    monkeypatch.setattr(search, "search", fake)
    r = client.get("/api/search?scope=woo&q=klimaat&soort=brief&van=2026-01-01&sort=nieuwste&start=-5&n=999")
    assert r.status_code == 200
    assert seen == {"scope": "woo", "q": "klimaat", "soort": "brief", "van": "2026-01-01", "tot": "",
                    "sort": "nieuwste", "start": 0, "n": 50,
                    "status": "", "fase": "", "type_": "", "zoekin": ""}
    client.get("/api/search?start=abc&n=x")
    assert seen["start"] == 0 and seen["n"] == 20


def test_search_endpoint_errors_are_dutch_json(client):
    r = client.get("/api/search?scope=elders&q=x")
    assert r.status_code == 400 and "zoekbron" in r.get_json()["error"]


def test_search_soorten_endpoint_lists_the_fixed_parliamentary_kinds(client):
    keys = [s["key"] for s in client.get("/api/search/soorten").get_json()["soorten"]]
    assert keys == ["alles", "kamerstuk", "bijlage", "aanhangsel", "handelingen"]


def test_open_overheid_tab_exists_without_a_side_panel_and_results_can_collapse(client):
    html = client.get("/").get_data(as_text=True)
    for element in ('data-tab="oo"', "Open overheid", 'id="oo-form"', 'id="oo-q"', 'id="oo-scope"',
                    'id="oo-results"', 'id="oo-toggle"', 'id="oo-resultbox"', 'id="fetch-oo"'):
        assert element in html, element
    assert 'id="bijlagen' not in html and 'class="side"' not in html          # paneel is vervallen
    assert 'data-tab="kst"' not in html and "Kamerstukken</button>" not in html
    # Binnen de tab heet de bron "Open overheid", niet "Woo".
    pane = html[html.index('id="pane-oo"'):html.index('id="pane-doc"')]
    assert "Alleen Open overheid-documenten" in pane and "Alleen Woo-documenten" not in pane
    assert "parlementaire stukken + Open overheid" in pane
    # Tabvolgorde: tussen Wetgeving en Documentupload.
    assert html.index('data-tab="wet"') < html.index('data-tab="oo"') < html.index('data-tab="doc"')


def test_bijlagen_become_a_link_list_unless_the_source_already_placed_it():
    from mdconv.sources import common

    items = [{"query": "blg-1", "titel": "Beslisnota", "rol": "Bijlage", "open_url": "https://x/blg-1.html"},
             {"query": "https://x/doc/1", "titel": "Document", "rol": "Document", "open_url": None}]
    out = common.with_bijlagen("# T\n\nTekst.\n", items)
    assert out.endswith("## Bijlagen en gerelateerde documenten\n\n"
                        "- [Beslisnota](https://x/blg-1.html) — Bijlage\n"
                        "- [Document](https://x/doc/1) — Document\n")
    assert common.with_bijlagen(out, items) == out                   # niet dubbel
    assert common.with_bijlagen("# T\n", []) == "# T\n"


# ---------------------------------------------------------------------------
# Weergave naast de ruwe tekst (static/mdview.js, af te lezen vanuit alle tabbladen)
# ---------------------------------------------------------------------------

def test_viewer_is_in_the_shared_output_so_every_tab_has_it(client):
    html = client.get("/").get_data(as_text=True)
    for element in ('id="viewer"', 'id="preview"', 'id="view-raw"', 'id="view-split"', 'id="view-preview"',
                    'id="gutter-inner"', 'id="md"', "static/mdview.js"):
        assert element in html, element
    # Eén uitvoer-sectie, na de vijf invoer-panelen: dus voor jur/wet/oo/doc/tekst hetzelfde.
    assert html.count('id="viewer"') == 1
    assert html.index('id="pane-tekst"') < html.index('id="viewer"')
    assert html.index("mdview.js") < html.index("app.js")        # de renderer moet eerder geladen zijn


def test_attachment_images_are_served_for_the_preview_but_only_from_the_own_set(client):
    from mdconv import attachments
    from mdconv.sources import Attachment

    token = attachments.store([Attachment(filename="p01.png", data=b"\x89PNG-data")])
    r = client.get(f"/api/attachments/{token}/p01.png")
    assert r.status_code == 200 and r.data == b"\x89PNG-data" and r.mimetype == "image/png"
    assert client.get(f"/api/attachments/{token}/ontbreekt.png").status_code == 404
    assert client.get("/api/attachments/onbekendtoken/p01.png").status_code == 404
    assert client.get(f"/api/attachments/{token}/..%2f..%2fetc%2fpasswd").status_code == 404


_NODE = __import__("shutil").which("node")


@pytest.mark.skipif(not _NODE, reason="node niet beschikbaar")
@pytest.mark.parametrize("markdown, expected, forbidden", [
    ("# Titel\n\n## Sub *x*", ["<h1>Titel</h1>", "<h2>Sub <em>x</em></h2>"], []),
    ("**vet**, *cursief*, `code` en snake_case_naam", ["<strong>vet</strong>", "<em>cursief</em>",
                                                       "<code>code</code>", "snake_case_naam"], ["<em>case"]),
    ("- een\n- twee\n  - genest\n- drie", ["<li>een</li>", "<li>twee\n<ul>", "<li>genest</li>"], ["<li><p>"]),
    ("1. a\n2. b", ["<ol>", "<li>a</li>", "<li>b</li>"], []),
    ("| A | B |\n| --- | --- |\n| 1 | a\\|b |", ["<th>A</th>", "<td>1</td>", "<td>a|b</td>"], []),
    ("Tekst.[^1]\n\n[^1]: De *noot*.", ['<sup class="fnref"><a href="#fn-1" id="fnref-1">1</a></sup>',
                                        '<li id="fn-1"><p>De <em>noot</em>.'], []),
    ("[l](https://x.nl/a_b) en <https://y.nl>", ['<a href="https://x.nl/a_b" target="_blank"',
                                                 ">https://y.nl</a>"], []),
    ("H<sub>2</sub>O x<sup>2</sup><br>", ["H<sub>2</sub>O", "x<sup>2</sup>", "<br>"], []),
    ("![[p01.png]] en [[Doel|alias]]", ['<img src="/emb/p01.png"', '<span class="wikilink">alias</span>'], []),
    ("> citaat", ["<blockquote><p>citaat</p></blockquote>"], []),
    ("```\n<b>x</b>\n```", ["<pre><code>&lt;b&gt;x&lt;/b&gt;</code></pre>"], ["<b>"]),
    # Veiligheid: niets uitvoerbaars uit een document of uit wat je zelf typt.
    ("<script>alert(1)</script> <img src=x onerror=alert(1)> [x](javascript:alert(1)) ![i](data:text/html,x)",
     ["&lt;script&gt;", "&lt;img src=x onerror=alert(1)&gt;", '<a href="#">x</a>', 'src="#"'],
     ["<script", "<img src=x", 'href="javascript', 'src="data']),
])
def test_markdown_renderer(markdown, expected, forbidden):
    import json
    import subprocess

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = (f"const {{mdToHtml}} = require({json.dumps(os.path.join(root, 'static', 'mdview.js'))});"
              f"process.stdout.write(mdToHtml({json.dumps(markdown)}, {{embedUrl: n => '/emb/' + n}}));")
    html = subprocess.run([_NODE, "-e", script], capture_output=True, text=True, check=True).stdout
    for piece in expected:
        assert piece in html, f"{piece!r} ontbreekt in {html!r}"
    for piece in forbidden:
        assert piece not in html, f"{piece!r} hoort er niet in: {html!r}"
