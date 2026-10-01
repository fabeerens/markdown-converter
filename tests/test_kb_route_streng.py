"""De drie besluiten van WP-77 (1 oktober 2026): wat de kennisbankroute streng houdt waar
de losse download mag terugvallen.

1. Eén Kamerstukroute: de XML gaat door `officiele_bekendmakingen.py`, met herkomst, en de
   invoerherkenning van `kamerstuk.py` (dossiernotatie, D-nummer) staat ervoor.
2. Een bron zonder herkomst (Woo, consultatie, een terugval) is in `kb_fetch` een
   weigering met reden, geen crash.
3. De glyph-waarschuwing staat in de tekst bij een losse download en in de metadata bij een
   document met `document_id`.

Geen netwerk: de XML en de metadata komen uit de fixture van `test_officiele_bekendmakingen`.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_officiele_bekendmakingen import BASIS, METADATA, XML, _Antwoord, _netwerk  # noqa: E402

from mdconv import sources  # noqa: E402
from mdconv.sources import detect_source, from_link, from_overheid  # noqa: E402
from mdconv.sources import kamerstuk  # noqa: E402


def _kamerstuk_online(monkeypatch):
    return _netwerk(monkeypatch, **{f"{BASIS}/kst-34851-4.xml": _Antwoord(200, XML),
                                    f"{BASIS}/kst-34851-4/metadata.xml": _Antwoord(200, METADATA)})


# ---------------------------------------------------------------------------
# Besluit 1
# ---------------------------------------------------------------------------

def test_dossiernotatie_and_id_give_the_same_markdown_and_the_same_provenance(monkeypatch):
    sessie = _kamerstuk_online(monkeypatch)
    via_id = from_link("kst-34851-4")
    via_notatie = from_link("Kamerstukken II 2017/18, 34851, nr. 4")
    assert via_notatie.markdown == via_id.markdown
    assert via_id.provenance is not None and via_id.provenance.document_id == "kst-34851-nr-4"
    assert via_notatie.provenance.document_id == "kst-34851-nr-4"
    assert via_id.provenance.extra["source_structure"]["markdown_sha256"] == \
        via_notatie.provenance.extra["source_structure"]["markdown_sha256"]
    # Beide vragen dezelfde twee bestanden op; niets anders.
    assert sessie.verzoeken == [f"{BASIS}/kst-34851-4.xml", f"{BASIS}/kst-34851-4/metadata.xml"] * 2


def test_the_open_overheid_tab_gets_the_same_document_as_from_link(monkeypatch):
    _kamerstuk_online(monkeypatch)
    tab = from_overheid("kst-34851-4")
    link = from_link("https://zoek.officielebekendmakingen.nl/kst-34851-4.html")
    assert tab.markdown == link.markdown and tab.provenance.document_id == "kst-34851-nr-4"
    assert tab.ident == tab.name == "kst-34851-4"
    # De bijlagenlijst van het tabblad komt niet in de tekst: de Markdown moet byte-gelijk blijven
    # aan wat kb_fetch bewaart. De bijlage-id's staan in de herkomst.
    assert "## Bijlagen en gerelateerde documenten" not in tab.markdown
    assert "bijlagen" in tab.provenance.extra


def test_detect_source_knows_both_recognitions_under_one_label():
    assert detect_source("kst-34851-4") == "kamerstuk"
    assert detect_source("https://zoek.officielebekendmakingen.nl/blg-1014762.pdf") == "kamerstuk"
    assert detect_source("https://www.tweedekamer.nl/kamerstukken/detail?did=2024D1") == "kamerstuk"
    assert detect_source("Kamerstukken II 2017/18, 34851, nr. 4") == "kamerstuk"
    assert detect_source("Kamerstuk 29279, nr. 1044") == "kamerstuk"
    # Een kale notatie blijft bij het tabblad: te verwarren met een zoekregel.
    assert detect_source("34851, nr. 4") is None
    assert detect_source("Kamerstukken II 2017/18, 34851") is None     # geen stuknummer


def test_without_xml_from_link_falls_back_for_the_download(monkeypatch):
    """Zonder bundel mag de losse download terugvallen (zoals `_terugval()` bij bestanden); dat
    de kennisbank dit nooit krijgt, staat onder besluit 2."""
    from mdconv.sources import files, sru

    _netwerk(monkeypatch)   # alles 404
    rec = sru.Record(ident="blg-1014762", title="Bijlage", files={"pdf": "https://repository.overheid.nl/x.pdf"})
    monkeypatch.setattr(kamerstuk, "_bijlagen", lambda ident: [])
    monkeypatch.setattr(sru, "by_identifier", lambda ident: rec)
    monkeypatch.setattr(kamerstuk, "_download", lambda url: b"%PDF")
    monkeypatch.setattr(files, "convert", lambda data, name: ("Pdf-tekst", "pdf-inspector"))
    doc = from_link("blg-1014762")
    assert doc.provenance is None and doc.markdown.rstrip().endswith("Pdf-tekst")
    assert "geen gestructureerde XML" in doc.warnings[0] and "geen kennisbankbundel" in doc.warnings[0]
