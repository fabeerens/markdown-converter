"""Tests voor de Kamerstukken-bron (`mdconv/sources/kamerstuk.py`).

Geen netwerk: de ophaalstroom draait met een vervangen strenge route
(`officiele_bekendmakingen.fetch`, de ene omzetting van de XML sinds WP-77) en vervangen
netwerkfuncties voor de terugval.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mdconv.errors import ConversionError  # noqa: E402
from mdconv.sources import kamerstuk  # noqa: E402


# ---------------------------------------------------------------------------
# Invoer herkennen
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query, ident", [
    ("kst-36600-VII-1", "kst-36600-VII-1"),
    ("KST-36600-vii-1", "kst-36600-VII-1"),
    ("https://zoek.officielebekendmakingen.nl/kst-36600-VII-1.html", "kst-36600-VII-1"),
    ("https://zoek.officielebekendmakingen.nl/kst-36600-VII-1.xml", "kst-36600-VII-1"),
    ("36600-VII, nr. 1", "kst-36600-VII-1"),
    ("36 600 VII nr 1", "kst-36600-VII-1"),
    ("Kamerstukken II 2024/25, 36600-VII, nr. 1", "kst-36600-VII-1"),
    ("Kamerstuk 29279, nr. 1044", "kst-29279-1044"),
    ("21501-02 nr. 3174", "kst-21501-02-3174"),
    ("21501-02-3174", "kst-21501-02-3174"),
    ("36200, nr. 1", "kst-36200-1"),
    ("36836 D", "kst-36836-D"),
    ("Kamerstukken I 2023/24, 36200, A", "kst-36200-A"),
    ("36600-12", "kst-36600-12"),
    ("kst-1268678", "kst-1268678"),          # nieuw formaat: geen dossier in het id
    ("blg-1184123", "blg-1184123"),
    ("ah-1271549", "ah-1271549"),
    ("ah-tk-20242025-100", "ah-tk-20242025-100"),
    ("AH-TK-20242025-100", "ah-tk-20242025-100"),
    ("h-tk-20242025-20-3", "h-tk-20242025-20-3"),
])
def test_parse_reference_to_publication_id(query, ident):
    assert kamerstuk.parse_reference(query).ident == ident


def test_parse_reference_tweede_kamer_documents():
    assert kamerstuk.parse_reference("2024D40329").dnummer == "2024D40329"
    link = "https://www.tweedekamer.nl/kamerstukken/detail?id=2024Z12345&did=2024D40329"
    assert kamerstuk.parse_reference(link).dnummer == "2024D40329"
    guid = "6df1fc96-2547-4606-b267-00120d3696e7"
    assert kamerstuk.parse_reference(guid).guid == guid


@pytest.mark.parametrize("query", ["36600", "36600-VII", "36 600 VII"])
def test_dossier_without_stuknummer_explains_itself(query):
    with pytest.raises(ConversionError, match="stuknummer"):
        kamerstuk.parse_reference(query)


@pytest.mark.parametrize("query", ["", "blabla", "ECLI:NL:HR:2012:BQ9251"])
def test_unrecognised_input_is_a_dutch_error(query):
    with pytest.raises(ConversionError):
        kamerstuk.parse_reference(query)


def test_detect_source_only_claims_unambiguous_kamerstuk_forms():
    from mdconv.sources import detect_source

    assert detect_source("kst-36600-VII-1") == "kamerstuk"
    assert detect_source("https://zoek.officielebekendmakingen.nl/ah-tk-20242025-100.html") == "kamerstuk"
    assert detect_source("https://www.tweedekamer.nl/kamerstukken/detail?did=2024D1") == "kamerstuk"
    # Bestaande bronnen blijven ongemoeid, ook die met cijfergroepen als een id.
    assert detect_source("01999L0001-20040501") is None
    assert detect_source("32016R0679") is None
    assert detect_source("001-210077") == "hudoc"
    assert detect_source("ECLI:NL:HR:2012:BQ9251") == "rechtspraak"
    # Een kale dossiernotatie hoort bij het eigen tabblad, niet bij auto-detectie.
    assert detect_source("36600-VII, nr. 1") is None


def test_tk_open_data_row_to_publication_id():
    row = {"Kamerstukdossier": [{"Nummer": 36800, "Toevoeging": "VIII"}], "Volgnummer": 182}
    assert kamerstuk._ident_from_tk_row(row) == "kst-36800-VIII-182"
    row = {"Kamerstukdossier": [{"Nummer": 32627, "Toevoeging": None}], "Volgnummer": 73}
    assert kamerstuk._ident_from_tk_row(row) == "kst-32627-73"
    # Kamervragen: aanhangselnummer 24|25|01244 → vergaderjaar 2024-2025, nr 1244.
    row = {"Kamerstukdossier": [], "Volgnummer": -1, "Aanhangselnummer": "242501244", "Kamer": 2}
    assert kamerstuk._ident_from_tk_row(row) == "ah-tk-20242025-1244"
    # Geen dossier, geen aanhangsel: geen officiële publicatie af te leiden.
    assert kamerstuk._ident_from_tk_row({"Kamerstukdossier": [], "Volgnummer": -1}) is None


# ---------------------------------------------------------------------------
# Ophaalstroom (zonder netwerk)
# ---------------------------------------------------------------------------

from mdconv.herkomst import Herkomst  # noqa: E402
from mdconv.sources import officiele_bekendmakingen as ob  # noqa: E402

HERKOMST = Herkomst(format="op-xml", document_id="kst-36180-nr-133")


def _streng(monkeypatch, uitkomst):
    """De strenge route (`officiele_bekendmakingen.fetch`) zonder netwerk: geeft het drietal
    terug of gooit de meegegeven fout. Levert de lijst van opgevraagde id's."""
    gezien = []

    def fetch(ident):
        gezien.append(ident)
        if isinstance(uitkomst, Exception):
            raise uitkomst
        return uitkomst

    monkeypatch.setattr(ob, "fetch", fetch)
    return gezien


def test_fetch_takes_the_strict_route_and_keeps_its_provenance(monkeypatch):
    gezien = _streng(monkeypatch, ("# Dossiertitel\n", "Officiële Bekendmakingen • kst-36180-133", HERKOMST))
    got = kamerstuk.fetch("36180, nr. 133")
    assert gezien == ["kst-36180-133"]
    assert got.markdown == "# Dossiertitel\n" and got.herkomst is HERKOMST and got.warnings == ()
    assert got.source == "Officiële Bekendmakingen • kst-36180-133"
    assert got.ident == got.name == "kst-36180-133" and got.bijlagen == []


def test_a_pasted_id_or_link_goes_through_the_strict_recognition(monkeypatch):
    gezien = _streng(monkeypatch, ("# T\n", "Bron", HERKOMST))
    kamerstuk.fetch("https://zoek.officielebekendmakingen.nl/kst-36600-VII-1.html")
    kamerstuk.fetch("KST-36600-VII-1")
    # De kennisbankroute vraagt het id klein geschreven op, zoals zij altijd deed.
    assert gezien == ["kst-36600-vii-1", "kst-36600-vii-1"]


def test_publication_without_xml_falls_back_to_the_pdf_of_the_search_record(monkeypatch):
    from mdconv.sources import files, sru

    rec = sru.Record(ident="ah-1271549", title="Antwoord op vragen", soort="Aanhangsel van de Handelingen",
                     vergaderjaar="2026-2027", creator="Tweede Kamer der Staten-Generaal", date="2026-10-01",
                     indiener="A. Podt", page_url="https://zoek.officielebekendmakingen.nl/ah-1271549.html",
                     files={"pdf": "https://repository.overheid.nl/x.pdf"})
    monkeypatch.setattr(kamerstuk, "_bijlagen", lambda ident: [])
    monkeypatch.setattr(sru, "by_identifier", lambda ident: rec)
    monkeypatch.setattr(kamerstuk, "_download", lambda url: b"%PDF")
    monkeypatch.setattr(files, "convert", lambda data, name: ("Pdf-tekst", "pdf-inspector"))
    got = kamerstuk.fetch("ah-1271549")
    assert got.markdown.startswith("# Antwoord op vragen")
    assert "- **Indiener:** A. Podt" in got.markdown and "geen gestructureerde XML" in got.markdown
    assert got.markdown.rstrip().endswith("Pdf-tekst")
    assert got.source.endswith("— PDF (pdf-inspector)") and got.ident == "ah-1271549"
    # Een terugval is een losse download: geen herkomst, en de gebruiker leest waarom.
    assert got.herkomst is None and "geen kennisbankbundel" in got.warnings[0]


def test_pdf_only_ids_skip_the_xml_attempt(monkeypatch):
    from mdconv.sources import sru

    _streng(monkeypatch, RuntimeError("geen XML voor een blg-id"))
    monkeypatch.setattr(kamerstuk, "_bijlagen", lambda ident: [])
    monkeypatch.setattr(sru, "by_identifier", lambda ident: None)
    with pytest.raises(ConversionError, match="niet gevonden"):
        kamerstuk.fetch("blg-1184123")


def test_a_refusal_of_the_strict_route_falls_back_with_its_reason(monkeypatch):
    """De XML bestaat, maar de strenge route kent haar vocabulaire niet (een Kamervraag, een
    element zonder behandeling). De losse download valt terug op de PDF, met de reden erbij;
    de kennisbank krijgt dit nooit (geen herkomst)."""
    from mdconv.sources import files, sru

    _streng(monkeypatch, ConversionError("Onverwacht element in het Kamerstuk (sup)."))
    rec = sru.Record(ident="kst-36180-133", title="Brief", files={"pdf": "https://repository.overheid.nl/x.pdf"})
    monkeypatch.setattr(kamerstuk, "_bijlagen", lambda ident: [])
    monkeypatch.setattr(sru, "by_identifier", lambda ident: rec)
    monkeypatch.setattr(kamerstuk, "_download", lambda url: b"%PDF")
    monkeypatch.setattr(files, "convert", lambda data, name: ("Pdf-tekst", "pdf-inspector"))
    got = kamerstuk.fetch("kst-36180-133")
    assert got.herkomst is None
    assert got.warnings[0].startswith("Onverwacht element in het Kamerstuk (sup). De tekst is omgezet uit de PDF")
    assert "geen kennisbankbundel" in got.warnings[0] and f"*{got.warnings[0]}*" in got.markdown


def test_a_refusal_without_a_pdf_stays_a_refusal(monkeypatch):
    from mdconv.sources import sru

    _streng(monkeypatch, ConversionError("Onverwacht element in het Kamerstuk (sup)."))
    monkeypatch.setattr(kamerstuk, "_bijlagen", lambda ident: [])
    monkeypatch.setattr(sru, "by_identifier", lambda ident: None)
    with pytest.raises(ConversionError, match=r"\(sup\)\. Er is ook geen PDF"):
        kamerstuk.fetch("kst-36180-133")
    # Hapert de zoekdienst tijdens de terugval, dan blijft de reden van de weigering voorop.
    monkeypatch.setattr(sru, "by_identifier", lambda ident: (_ for _ in ()).throw(ConversionError("zoekdienst down")))
    with pytest.raises(ConversionError, match=r"\(sup\)\. De terugval op de PDF lukte ook niet: zoekdienst down"):
        kamerstuk.fetch("kst-36180-133")


def test_bijlagen_come_from_the_search_service_and_a_bijlage_points_back(monkeypatch):
    from mdconv.sources import sru

    blg = sru.Record(ident="blg-1", title="Beslisnota", page_url="https://x/blg-1.html")
    monkeypatch.setattr(sru, "attachments_of", lambda ident: [blg] if ident == "kst-1-1" else [])
    items = kamerstuk._bijlagen("kst-1-1")
    assert items == [{"query": "blg-1", "titel": "Beslisnota", "rol": "Bijlage", "open_url": "https://x/blg-1.html"}]
    monkeypatch.setattr(sru, "by_identifier", lambda ident: sru.Record(ident=ident, hoofddocument="kst-32813-1407"))
    back = kamerstuk._bijlagen("blg-9")
    assert back[0]["query"] == "kst-32813-1407" and back[0]["rol"] == "Hoofddocument"


def test_a_failing_search_service_never_fails_the_conversion(monkeypatch):
    from mdconv.sources import sru

    def boom(ident):
        raise RuntimeError("down")

    monkeypatch.setattr(sru, "attachments_of", boom)
    assert kamerstuk._bijlagen("kst-1-1") == []


def test_fetch_missing_publication_suggests_the_d_number(monkeypatch):
    from mdconv.sources import sru

    _streng(monkeypatch, ob.GeenXml("kst-99999-1 heeft geen officiële XML (404)."))
    monkeypatch.setattr(kamerstuk, "_bijlagen", lambda ident: [])
    monkeypatch.setattr(sru, "by_identifier", lambda ident: None)
    with pytest.raises(ConversionError, match="D-nummer"):
        kamerstuk.fetch("kst-99999-1")


def test_d_number_resolves_to_the_publication(monkeypatch):
    doc = kamerstuk._TkDocument("guid", "2026D35388", "Brief regering", "kst-36800-VIII-182", "x")
    monkeypatch.setattr(kamerstuk, "_lookup_tk_document", lambda ref: doc)
    gezien = _streng(monkeypatch, ("# T\n", "Bron", HERKOMST))
    got = kamerstuk.fetch("2026D35388")
    assert gezien == ["kst-36800-VIII-182"] and got.herkomst is HERKOMST


def test_d_number_without_publication_falls_back_to_the_original_file_with_a_note(monkeypatch):
    from mdconv.sources import files

    doc = kamerstuk._TkDocument("guid", "2024D40329", "Brief regering", None,
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    monkeypatch.setattr(kamerstuk, "_lookup_tk_document", lambda ref: doc)
    monkeypatch.setattr(kamerstuk, "_bijlagen", lambda ident: [])
    monkeypatch.setattr(kamerstuk, "_file_from_tk", lambda d: (b"bytes", "2024D40329.docx"))
    monkeypatch.setattr(files, "convert", lambda data, name: ("Brieftekst", "MarkItDown"))
    got = kamerstuk.fetch("2024D40329")
    assert got.markdown.startswith("*Dit document is geen gepubliceerd kamerstuk;")
    assert "DOCX" in got.markdown and got.markdown.rstrip().endswith("Brieftekst")
    assert got.source == "Tweede Kamer open data • 2024D40329 (MarkItDown)"
    assert got.herkomst is None and got.warnings[0].endswith("er is geen kennisbankbundel.")


@pytest.fixture
def client():
    from mdconv import create_app

    return create_app(ai_enabled=False).test_client()


def test_endpoint_returns_markdown_attachment_token_and_name_with_bijlagen_in_the_text(client, monkeypatch):
    from mdconv.sources.common import Fetched

    monkeypatch.setattr(kamerstuk, "fetch", lambda q: Fetched(
        "# Titel\n", "Bron (kst-1-1)", [("p.png", b"IMG")],
        [{"query": "blg-1", "titel": "B", "rol": "Bijlage", "open_url": None}],
        ident="kst-1-1", name="kst-1-1"))
    r = client.post("/api/convert/overheid", json={"query": "kst-1-1"})
    body = r.get_json()
    assert r.status_code == 200
    assert body["markdown"].startswith("# Titel\n") and body["kind"] == "document"
    # De bijlagen staan als linklijst in de Markdown zelf; er is geen apart paneel meer.
    assert "## Bijlagen en gerelateerde documenten\n\n- [B](blg-1) — Bijlage" in body["markdown"]
    assert "bijlagen" not in body
    assert body["attachment_count"] == 1 and body["attachments_token"]
    assert body["ident"] == "kst-1-1" and body["name"] == "kst-1-1"


def test_endpoint_validates_input_in_dutch(client):
    r = client.post("/api/convert/overheid", json={"query": "  "})
    assert r.status_code == 400 and "kamerstuk" in r.get_json()["error"]
    r = client.post("/api/convert/overheid", json={"query": "blabla"})
    assert r.status_code == 400 and "Geen kamerstuk herkend" in r.get_json()["error"]


def test_link_endpoint_routes_a_pasted_publication_id_to_the_same_source(client, monkeypatch):
    from mdconv.sources.common import Fetched

    monkeypatch.setattr(kamerstuk, "fetch", lambda q: Fetched("# T\n", "Bron"))
    r = client.post("/api/convert/link", json={"query": "kst-36600-VII-1"})
    assert r.status_code == 200 and r.get_json()["markdown"] == "# T\n"
