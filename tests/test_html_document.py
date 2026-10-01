"""HTML-pagina's en PDF's als document voor de kennisbank (profiel `documenten`).

De route mag mislukken: een pagina zonder eenduidige inhoud wordt geweigerd, en een
PDF zonder tekstlaag ook. Netwerk komt er niet aan te pas.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from mdconv import kb_bundle
from mdconv.errors import ConversionError
from mdconv.sources import from_file, html_document, files

PAGINA = """<html><head><title>x</title><script>alert(1)</script></head><body>
<nav><table><tr><td>menu</td><td>menu</td></tr></table></nav>
<main>
<h1>Richtsnoer over toestemming</h1>
<p>Toestemming moet vrij en specifiek zijn, zodat de betrokkene werkelijk kan kiezen.</p>
<table><thead><tr><th>Grondslag</th><th>Voorbeeld</th></tr></thead>
<tbody><tr><td>Toestemming</td><td>Nieuwsbrief</td></tr></tbody></table>
<img src="x.png" alt="schema">
</main>
<footer>colofon</footer></body></html>"""


def test_container_choice_is_main_and_does_not_fall_back_to_body():
    markdown, waarschuwingen, extra = html_document.convert(PAGINA)
    assert "Richtsnoer over toestemming" in markdown
    # De zijbalk en de voettekst horen er niet in: de ladder koos <main>.
    assert "menu" not in markdown and "colofon" not in markdown
    assert extra["selectie"] == "main"
    assert waarschuwingen == ("1 afbeelding(en) niet overgenomen",)
    assert "| Grondslag | Voorbeeld |" in markdown


def test_page_without_unambiguous_container_is_refused_with_reason():
    with pytest.raises(ConversionError) as fout:
        html_document.convert("<html><body><p>Alleen een body met tekst genoeg om te tellen.</p></body></html>")
    assert "niet geraden" in str(fout.value)
    # Twee <article>'s zijn een lijst van berichten, geen document.
    twee = ("<html><body><article><p>Eerste bericht met genoeg woorden.</p></article>"
            "<article><p>Tweede bericht met genoeg woorden.</p></article></body></html>")
    with pytest.raises(ConversionError) as fout:
        html_document.convert(twee)
    assert "2× article" in str(fout.value)


def test_evidence_is_the_container_and_keeps_original_table_shape():
    # Samengevoegde cellen worden bij het omzetten uitgeschreven; het bewijs moet de
    # oorspronkelijke vorm dragen, zodat de kennisbank de rowspan zelf kan herberekenen.
    pagina = ("<html><body><main><p>Een tabel met een samengevoegde cel in de bron van dit stuk.</p>"
              "<table><tr><th>A</th><th>B</th></tr>"
              "<tr><td rowspan='2'>x</td><td>1</td></tr><tr><td>2</td></tr></table></main></body></html>")
    from mdconv.source_structure import capture_source_documents
    with capture_source_documents() as documenten:
        html_document.convert(pagina, source_url="https://example.test/p")
    assert len(documenten) == 1
    assert "rowspan" in documenten[0]["original_html"]
    assert "menu" not in documenten[0]["original_html"]
    assert documenten[0]["tables"][0]["row_count"] == 3


def test_html_file_with_document_id_becomes_a_kb_bundle():
    doc = from_file(PAGINA.encode("utf-8"), "richtsnoer.html", document_id="edpb-guidelines-05-2020")
    assert doc.provenance is not None
    assert doc.provenance.document_id == "edpb-guidelines-05-2020"
    assert kb_bundle.identiteit(doc.provenance.as_json()) == (
        "documenten", "edpb-guidelines-05-2020", "edpb-guidelines-05-2020")
    token = kb_bundle.store(doc.provenance.as_json())
    stream, pad_id = kb_bundle.build(token, doc.markdown, bewerkt_met_ai=False)
    with zipfile.ZipFile(stream) as archive:
        namen = set(archive.namelist())
        assert "raw/documenten/edpb-guidelines-05-2020.md" in namen
        assert "raw/documenten/edpb-guidelines-05-2020.source.json" in namen
        fetch = json.loads(archive.read("raw/source-evidence/edpb-guidelines-05-2020/fetch.json"))
        assert fetch["source_format"] == "html"
        bron = [n for n in namen if n.endswith(".html")]
        assert len(bron) == 1
        assert hashlib.sha256(archive.read(bron[0])).hexdigest() == fetch["sha256"]


def test_file_without_document_id_keeps_plain_download():
    # De identiteit komt nooit uit de bestandsnaam: `edpb-guidelines-05-2020.html` zegt
    # nog niet dat dit het richtsnoer is.
    doc = from_file(PAGINA.encode("utf-8"), "edpb-guidelines-05-2020.html")
    assert kb_bundle.identiteit(doc.provenance.as_json()) is None


@pytest.mark.parametrize("slug", ["Rapport 1", "rapport_1", "-rapport", "rapport-", "a--b", ""])
def test_invalid_document_id_is_refused_not_repaired(slug):
    with pytest.raises(ConversionError):
        from_file(PAGINA.encode("utf-8"), "x.html", document_id=slug)
    assert kb_bundle.identiteit({"document_id": slug}) is None


def _pdf_met_type(monkeypatch, pdf_type, markdown="x"):
    import pdf_inspector

    class Resultaat:
        pass

    r = Resultaat()
    r.pdf_type, r.markdown = pdf_type, markdown
    monkeypatch.setattr(pdf_inspector, "process_pdf_bytes", lambda data: r)


def test_scanned_pdf_is_refused_instead_of_falling_back_to_markitdown(monkeypatch):
    _pdf_met_type(monkeypatch, "scanned")
    with pytest.raises(ConversionError) as fout:
        files.convert(b"%PDF-1.4 nep", "scan.pdf")
    assert "geen tekstlaag" in str(fout.value) and "OCR" in str(fout.value)


def test_pdf_with_document_id_keeps_bytes_and_measures_quality(monkeypatch):
    tekst = ("# Besluit\n\nDit is een besluit met een B etaling en de afbe- \nkeningen die erbij horen.\n"
             * 3)
    _pdf_met_type(monkeypatch, "text_based", tekst)
    bytes_ = b"%PDF-1.4 nep"
    doc = from_file(bytes_, "besluit.pdf", document_id="x-ap-besluit-2021-belastingdienst-toeslagen")
    zij = doc.provenance.as_json()
    assert zij["extra"]["kwaliteit"]["source_quality"] in {"fair", "poor"}
    assert zij["extra"]["kwaliteit"]["signalen"]["opengebroken_woorden"] == 3
    token = kb_bundle.store(zij)
    stream, _ = kb_bundle.build(token, doc.markdown, bewerkt_met_ai=False)
    with zipfile.ZipFile(stream) as archive:
        digest = hashlib.sha256(bytes_).hexdigest()
        assert archive.read(
            f"raw/source-evidence/x-ap-besluit-2021-belastingdienst-toeslagen/{digest}.pdf") == bytes_
        fetch = json.loads(archive.read(
            "raw/source-evidence/x-ap-besluit-2021-belastingdienst-toeslagen/fetch.json"))
        # Een PDF heeft geen boom; de kennisbank krijgt er geen bewijsformaat bij.
        assert fetch["source_format"] == "pdf"


def test_pdf_quality_counts_the_same_three_signals_as_the_kb():
    schoon = files.pdf_kwaliteit("Een gewone alinea zonder schade.\n" * 50)
    assert schoon["source_quality"] == "good"
    rommel = files.pdf_kwaliteit("Deze regel heeft de B etaling en de afbe- keningen.\n" * 10)
    assert rommel["source_quality"] == "poor"
    assert rommel["signalen"]["score_per_1000_regels"] >= 20
    assert files.pdf_kwaliteit("## 3 / 12\n\ntekst\n")["source_quality"] == "fair"
