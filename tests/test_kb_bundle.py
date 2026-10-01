"""De browserdownload als direct uitpakbare invoer voor de kennisbank."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile

import pytest

from mdconv import kb_bundle
from mdconv.herkomst import Herkomst
from mdconv.source_structure import bind_structure
from mdconv.sources import Document


@pytest.fixture
def client():
    from mdconv import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def herkomst(*, document_id="32022R1925", bron=b"PK\x03\x04formex"):
    bewijs = {
        "schema_version": 1,
        "format": "binary-source",
        "source_format": "formex",
        "media_type": "application/zip;mtype=fmx4",
        "source_sha256": hashlib.sha256(bron).hexdigest(),
        "original_base64": base64.b64encode(bron).decode("ascii"),
        "source_url": f"https://example.test/{document_id}",
        "identifier": document_id,
        "language": "nl",
        "role": "document",
    }
    return bind_structure(
        Herkomst(format="formex", celex=document_id, language="nl",
                 requested_url=document_id, source_url=bewijs["source_url"]),
        "# Oorspronkelijk", [bewijs],
    ).as_json()


def test_profile_and_identity_are_derived_for_legislation_and_case_law():
    assert kb_bundle.identiteit({"bwb": "BWBR0040940"}) == ("wetten-nl", "BWBR0040940", "BWBR0040940")
    assert kb_bundle.identiteit({"celex": "32022R1925"}) == ("eurlex", "32022R1925", "32022R1925")
    assert kb_bundle.identiteit({"celex": "02019R0881-20250204",
                                 "base_celex": "32019R0881"}) == ("eurlex", "32019R0881", "32019R0881")
    # Een uitspraak wordt op haar ECLI geïdentificeerd; de padnaam is dezelfde
    # identiteit met koppeltekens, zoals raw/ en work/ in de kennisbank heten.
    assert kb_bundle.identiteit({"ecli": "ECLI:NL:HR:2026:1"}) == (
        "jurisprudentie", "ECLI:NL:HR:2026:1", "ECLI-NL-HR-2026-1")
    assert kb_bundle.identiteit({"ecli": "ECLI:EU:C:2020:559", "celex": "62018CJ0311"}) == (
        "jurisprudentie", "ECLI:EU:C:2020:559", "ECLI-EU-C-2020-559")
    # Noemt de bron zelf geen ECLI, dan is het CELEX-nummer uit sector 6 de identiteit
    # (SKILL.md: "anders het CELEX-nummer (Hof)"). Een ECLI wordt nooit opgebouwd.
    assert kb_bundle.identiteit({"celex": "61956CJ0002"}) == (
        "jurisprudentie", "61956CJ0002", "61956CJ0002")
    assert kb_bundle.identiteit({"celex": "52025PC0837"}) is None
    assert kb_bundle.identiteit({"bestandsnaam": "rapport.pdf"}) is None
    assert kb_bundle.identiteit({"ecli": "  "}) is None
    # Een document zonder officieel nummer heeft een slug (identifiers.md). Die komt van
    # de gebruiker of uit de bron, en wordt hier alleen gecontroleerd; een slug die de
    # regels niet haalt is geen bundel, geen gok.
    assert kb_bundle.identiteit({"document_id": "edpb-guidelines-05-2020"}) == (
        "documenten", "edpb-guidelines-05-2020", "edpb-guidelines-05-2020")
    assert kb_bundle.identiteit({"document_id": "Rapport 2024"}) is None
    assert kb_bundle.identiteit({"document_id": "rapport--2024"}) is None


def test_bundle_paths_hash_source_and_mark_edited_markdown():
    bron = b"PK\x03\x04exacte bronbytes"
    provenance = herkomst(bron=bron)
    token = kb_bundle.store(provenance)
    gebouwd = kb_bundle.build(token, "# Bewerkt", bewerkt_met_ai=False)
    assert gebouwd is not None
    stream, pad_id = gebouwd
    digest = hashlib.sha256(bron).hexdigest()
    assert pad_id == "32022R1925"

    with zipfile.ZipFile(stream) as archive:
        assert set(archive.namelist()) == {
            "raw/eurlex/32022R1925.md",
            "raw/eurlex/32022R1925.source.json",
            "raw/source-evidence/32022R1925/fetch.json",
            f"raw/source-evidence/32022R1925/{digest}.fmx4.zip",
        }
        assert archive.read(f"raw/source-evidence/32022R1925/{digest}.fmx4.zip") == bron
        zij = json.loads(archive.read("raw/eurlex/32022R1925.source.json"))
        assert zij["extra"]["source_structure"]["markdown_changed"] is True
        fetch = json.loads(archive.read("raw/source-evidence/32022R1925/fetch.json"))
        assert fetch["sha256"] == digest
        assert fetch["source_format"] == "formex"


def test_api_keeps_provenance_server_side_and_returns_kb_zip(client):
    from mdconv.api import _doc_payload

    provenance = herkomst()
    document = Document("# Oorspronkelijk", "test", provenance=Herkomst(**{
        key: tuple(value) if key in {"toestand_meldingen", "waarschuwingen"} else value
        for key, value in provenance.items()
    }))
    payload = _doc_payload(document)
    assert "bundle_token" in payload
    assert "provenance" not in payload

    result = client.post("/api/download", json={
        "markdown": "# Oorspronkelijk",
        "filename": "32022R1925",
        "bundle_token": payload["bundle_token"],
    })
    assert result.status_code == 200
    assert result.content_type == "application/zip"
    assert "32022R1925.zip" in result.headers["Content-Disposition"]


def test_document_without_kb_identity_keeps_plain_download(client):
    from mdconv.api import _doc_payload

    # Geplakte tekst en losse bestanden houden de platte download: er is geen
    # identiteit waaronder de kennisbank ze zou kunnen plaatsen.
    document = Document(
        "# Notitie", "test",
        provenance=Herkomst(format="pasted", bestandsnaam="notitie.md"),
    )
    payload = _doc_payload(document)
    assert "bundle_token" not in payload
    result = client.post("/api/download", json={**payload, "filename": "notitie"})
    assert result.content_type.startswith("text/markdown")
    assert "notitie.md" in result.headers["Content-Disposition"]


def test_case_law_bundle_uses_the_ecli_with_hyphens_as_filename(client):
    from mdconv.api import _doc_payload

    bron = b"<open-rechtspraak/>"
    bewijs = {
        "schema_version": 1, "format": "binary-source", "source_format": "rechtspraak-xml",
        "media_type": "application/xml", "source_sha256": hashlib.sha256(bron).hexdigest(),
        "original_base64": base64.b64encode(bron).decode("ascii"),
        "source_url": "https://data.rechtspraak.nl/uitspraken/content?id=ECLI:NL:HR:2026:1",
        "identifier": "ECLI:NL:HR:2026:1", "language": "nl", "role": "document",
    }
    provenance = bind_structure(
        Herkomst(format="rechtspraak-xml", ecli="ECLI:NL:HR:2026:1", language="nl",
                 source_url=bewijs["source_url"], requested_url=bewijs["source_url"]),
        "# Arrest", [bewijs],
    ).as_json()
    document = Document("# Arrest", "test", provenance=Herkomst(**{
        key: tuple(value) if key in {"toestand_meldingen", "waarschuwingen"} else value
        for key, value in provenance.items()
    }))
    payload = _doc_payload(document)
    assert "bundle_token" in payload

    result = client.post("/api/download", json={
        "markdown": "# Arrest", "filename": "arrest",
        "bundle_token": payload["bundle_token"],
    })
    assert result.status_code == 200
    assert result.content_type == "application/zip"
    assert "ECLI-NL-HR-2026-1.zip" in result.headers["Content-Disposition"]
    digest = hashlib.sha256(bron).hexdigest()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert set(archive.namelist()) == {
            "raw/jurisprudentie/ECLI-NL-HR-2026-1.md",
            "raw/jurisprudentie/ECLI-NL-HR-2026-1.source.json",
            "raw/source-evidence/ECLI-NL-HR-2026-1/fetch.json",
            f"raw/source-evidence/ECLI-NL-HR-2026-1/{digest}.xml",
        }
        fetch = json.loads(archive.read("raw/source-evidence/ECLI-NL-HR-2026-1/fetch.json"))
        assert fetch["source_format"] == "rechtspraak-xml"
        assert fetch["media_type"] == "application/xml"


def test_attachment_download_keeps_its_zip_type_and_name(client):
    from mdconv import attachments
    from mdconv.sources import Attachment

    token = attachments.store([Attachment(filename="p01.png", data=b"PNG")])
    result = client.post("/api/download", json={
        "markdown": "# Rapport", "filename": "rapport",
        "attachments_token": token,
    })
    assert result.content_type == "application/zip"
    assert "rapport.zip" in result.headers["Content-Disposition"]


def test_batch_download_embeds_kb_tree_and_plain_document(client):
    token = kb_bundle.store(herkomst())
    result = client.post("/api/download", json={
        "filename": "documenten",
        "documents": [
            {"markdown": "# Wet", "filename": "wet", "bundle_token": token},
            {"markdown": "# Rapport", "filename": "rapport"},
        ],
    })
    assert result.content_type == "application/zip"
    assert "documenten.zip" in result.headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert "raw/eurlex/32022R1925.md" in archive.namelist()
        assert "rapport.md" in archive.namelist()


def test_sidecar_keeps_the_source_hash_but_not_the_source_bytes():
    """De bytes staan al onder raw/source-evidence/; in het zijbestand waren ze een dubbele kopie."""
    bron = b"PK\x03\x04exacte bronbytes"
    provenance = herkomst(bron=bron)
    token = kb_bundle.store(provenance)
    stream, _ = kb_bundle.build(token, "# Oorspronkelijk", bewerkt_met_ai=False)
    with zipfile.ZipFile(stream) as archive:
        zij = json.loads(archive.read("raw/eurlex/32022R1925.source.json"))
        assert archive.read(f"raw/source-evidence/32022R1925/{hashlib.sha256(bron).hexdigest()}.fmx4.zip") == bron
    bronnen = zij["extra"]["source_structure"]["sources"]
    assert bronnen[0]["source_sha256"] == hashlib.sha256(bron).hexdigest()
    assert "original_base64" not in bronnen[0] and "original_html" not in bronnen[0]
    assert base64.b64encode(bron).decode() not in json.dumps(zij)


def bewijs(bron: bytes, *, identifier: str, role: str = "document",
           source_format: str = "formex", media_type: str = "application/zip;mtype=fmx4") -> dict:
    """Eén vastgelegde binaire bron, in de vorm die `record_source` oplevert."""
    return {
        "schema_version": 1, "format": "binary-source", "source_format": source_format,
        "media_type": media_type, "source_sha256": hashlib.sha256(bron).hexdigest(),
        "original_base64": base64.b64encode(bron).decode("ascii"),
        "source_url": f"https://example.test/{identifier}",
        "identifier": identifier, "language": "nl", "role": role,
    }


def bewijs_html(html: str, *, identifier: str, role: str) -> dict:
    """Eén vastgelegde HTML-bron, in de vorm die `record_html` oplevert.

    Zonder `source_format` en `media_type`, precies zoals `source_structure()`
    ze weglaat; de bundel valt daarop terug op `html` en `text/html`.
    """
    return {
        "schema_version": 1, "format": "html-source-structure",
        "source_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
        "original_html": html, "selection": {"selector": "document", "anchor": None},
        "tables": [], "source_url": f"https://example.test/{identifier}",
        "identifier": identifier, "language": "nl", "role": role,
    }


def geconsolideerde_herkomst(bronnen: list[dict]) -> dict:
    """De herkomst van een geconsolideerde handeling met haar basishandeling."""
    return bind_structure(
        Herkomst(format="formex", celex="02016R0679-20160504", base_celex="32016R0679",
                 language="nl", requested_url="https://example.test/avg",
                 source_url=bronnen[0]["source_url"]),
        "# Oorspronkelijk", bronnen,
    ).as_json()


def test_bundle_archives_the_preamble_source_next_to_the_document():
    """Elke gedeclareerde bron hoort in het archief, niet alleen de hoofdbron.

    Gemeten geval van 22 september 2026: de AVG (`02016R0679-20160504`) haalt haar
    considerans uit de basishandeling `32016R0679`. Het bronbewijs noemde beide
    zips en het archief bevatte er één; de basiszip was daarna nergens meer terug
    te vinden, en zonder haar mist de herbouwde Markdown 408 regels considerans.
    """
    document, basis = b"PK\x03\x04geconsolideerd", b"PK\x03\x04basishandeling"
    token = kb_bundle.store(geconsolideerde_herkomst([
        bewijs(document, identifier="02016R0679-20160504"),
        bewijs(basis, identifier="32016R0679", role="preamble"),
    ]))
    stream, pad_id = kb_bundle.build(token, "# Bewerkt", bewerkt_met_ai=False)
    assert pad_id == "32016R0679"
    hoofd = hashlib.sha256(document).hexdigest()
    preambule = hashlib.sha256(basis).hexdigest()

    with zipfile.ZipFile(stream) as archive:
        assert set(archive.namelist()) == {
            "raw/eurlex/32016R0679.md",
            "raw/eurlex/32016R0679.source.json",
            "raw/source-evidence/32016R0679/fetch.json",
            f"raw/source-evidence/32016R0679/{hoofd}.fmx4.zip",
            f"raw/source-evidence/32016R0679/{preambule}.fmx4.zip",
        }
        assert archive.read(f"raw/source-evidence/32016R0679/{preambule}.fmx4.zip") == basis
        fetch = json.loads(archive.read("raw/source-evidence/32016R0679/fetch.json"))

    # De bovenste sleutels blijven die van de hoofdbron: `tools/xml_meting/ophalen.py`
    # leest `resolved_url` daar, en harde regel 1 van de kennisbank verbiedt een bump.
    assert fetch["sha256"] == hoofd
    assert fetch["resolved_url"] == "https://example.test/02016R0679-20160504"
    assert fetch["source_format"] == "formex"
    # De rol staat per bron in de nieuwe, optionele sleutel.
    assert [(b["role"], b["file"], b["identifier"]) for b in fetch["sources"]] == [
        ("document", f"{hoofd}.fmx4.zip", "02016R0679-20160504"),
        ("preamble", f"{preambule}.fmx4.zip", "32016R0679"),
    ]
    assert all(b["media_type"] == "application/zip;mtype=fmx4" for b in fetch["sources"])


def test_bundle_archives_every_part_of_a_multipart_html_document():
    """Een meerdelige handeling verloor alles behalve haar laatste onderdeel.

    `_bronnen()` koos de láátste bron die geen preambule was; bij drie
    `document-part`-onderdelen bleef daarmee alleen het derde over.
    """
    delen = ["<html>deel 1</html>", "<html>deel 2</html>", "<html>deel 3</html>"]
    token = kb_bundle.store(geconsolideerde_herkomst(
        [bewijs_html(deel, identifier="02016R0679-20160504", role="document-part")
         for deel in delen]
    ))
    stream, _ = kb_bundle.build(token, "# Bewerkt", bewerkt_met_ai=False)
    digests = [hashlib.sha256(deel.encode("utf-8")).hexdigest() for deel in delen]

    with zipfile.ZipFile(stream) as archive:
        for deel, digest in zip(delen, digests):
            assert archive.read(f"raw/source-evidence/32016R0679/{digest}.html").decode() == deel
        fetch = json.loads(archive.read("raw/source-evidence/32016R0679/fetch.json"))

    assert [b["file"] for b in fetch["sources"]] == [f"{d}.html" for d in digests]
    assert {b["role"] for b in fetch["sources"]} == {"document-part"}
    assert {b["source_format"] for b in fetch["sources"]} == {"html"}
    assert {b["media_type"] for b in fetch["sources"]} == {"text/html"}
    # De hoofdbron blijft het laatste onderdeel, zoals vóór deze wijziging.
    assert fetch["sha256"] == digests[-1]


def test_bundle_refuses_when_a_declared_source_has_no_bytes():
    """Fail-closed: een half archief is erger dan geen download.

    Een bron die wel wordt gedeclareerd maar geen bytes draagt, werd eerder
    stilzwijgend overgeslagen zodra ze niet de hoofdbron was. Dat is precies hoe
    het bronbewijs onopgemerkt incompleet raakte.
    """
    zonder_bytes = bewijs(b"PK\x03\x04basishandeling", identifier="32016R0679", role="preamble")
    del zonder_bytes["original_base64"]
    token = kb_bundle.store(geconsolideerde_herkomst([
        bewijs(b"PK\x03\x04geconsolideerd", identifier="02016R0679-20160504"),
        zonder_bytes,
    ]))
    with pytest.raises(ValueError, match="De binaire bron ontbreekt"):
        kb_bundle.build(token, "# Bewerkt", bewerkt_met_ai=False)


def test_two_sources_with_identical_bytes_share_one_archived_file():
    """Dezelfde bytes hebben dezelfde hash, dus één bestand — maar twee regels.

    De rollen verschillen wel, en het bewijs moet blijven zeggen dat beide zijn
    gedeclareerd; anders lijkt er één bron te ontbreken.
    """
    zelfde = b"PK\x03\x04een en dezelfde zip"
    token = kb_bundle.store(geconsolideerde_herkomst([
        bewijs(zelfde, identifier="02016R0679-20160504"),
        bewijs(zelfde, identifier="32016R0679", role="preamble"),
    ]))
    stream, _ = kb_bundle.build(token, "# Bewerkt", bewerkt_met_ai=False)
    digest = hashlib.sha256(zelfde).hexdigest()

    with zipfile.ZipFile(stream) as archive:
        namen = archive.namelist()
        assert namen.count(f"raw/source-evidence/32016R0679/{digest}.fmx4.zip") == 1
        fetch = json.loads(archive.read("raw/source-evidence/32016R0679/fetch.json"))
    assert [b["role"] for b in fetch["sources"]] == ["document", "preamble"]
    assert {b["file"] for b in fetch["sources"]} == {f"{digest}.fmx4.zip"}
