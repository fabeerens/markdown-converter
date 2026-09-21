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


def test_profile_and_identity_are_derived_only_for_legislation():
    assert kb_bundle.identiteit({"bwb": "BWBR0040940"}) == ("wetten-nl", "BWBR0040940")
    assert kb_bundle.identiteit({"celex": "32022R1925"}) == ("eurlex", "32022R1925")
    assert kb_bundle.identiteit({"celex": "02019R0881-20250204",
                                 "base_celex": "32019R0881"}) == ("eurlex", "32019R0881")
    assert kb_bundle.identiteit({"celex": "62019CJ0311"}) is None
    assert kb_bundle.identiteit({"celex": "52025PC0837"}) is None
    assert kb_bundle.identiteit({"ecli": "ECLI:NL:HR:2026:1"}) is None
    assert kb_bundle.identiteit({"bestandsnaam": "rapport.pdf"}) is None


def test_bundle_paths_hash_source_and_mark_edited_markdown():
    bron = b"PK\x03\x04exacte bronbytes"
    provenance = herkomst(bron=bron)
    token = kb_bundle.store(provenance)
    gebouwd = kb_bundle.build(token, "# Bewerkt", bewerkt_met_ai=False)
    assert gebouwd is not None
    stream, document_id = gebouwd
    digest = hashlib.sha256(bron).hexdigest()
    assert document_id == "32022R1925"

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

    document = Document(
        "# Arrest", "test",
        provenance=Herkomst(format="rechtspraak-xml", ecli="ECLI:NL:HR:2026:1"),
    )
    payload = _doc_payload(document)
    assert "bundle_token" not in payload
    result = client.post("/api/download", json={**payload, "filename": "arrest"})
    assert result.content_type.startswith("text/markdown")
    assert "arrest.md" in result.headers["Content-Disposition"]


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
