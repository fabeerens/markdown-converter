"""De opdrachtregel `mdconv.kb_fetch` zet dezelfde bundel neer als de browserdownload.

Twee dingen worden hier vastgehouden. Ten eerste dat de map die het script schrijft
precies de vorm heeft die de kennisbank verwacht (`raw/<profiel>/<pad_id>.md`,
`.source.json`, `raw/source-evidence/<pad_id>/fetch.json` + bronbytes) en dat
`ophaal.json` de vraag aan de geland identiteit koppelt, ook wanneer die twee
verschillen (een geconsolideerde CELEX landt als haar basishandeling). Ten tweede
dat één mislukte ophaal de rest niet stopt maar wel de afloopcode 1 geeft.

Geen netwerk: `sources.from_link` wordt vervangen, zoals `AGENTS.md` voorschrijft.
"""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from mdconv import kb_fetch
from mdconv.errors import ConversionError
from mdconv.herkomst import Herkomst
from mdconv.source_structure import bind_structure
from mdconv.sources import Document


def _bewijs(bron: bytes, identifier: str, role: str = "document") -> dict:
    return {
        "schema_version": 1, "format": "binary-source", "source_format": "formex",
        "media_type": "application/zip;mtype=fmx4",
        "source_sha256": hashlib.sha256(bron).hexdigest(),
        "original_base64": base64.b64encode(bron).decode("ascii"),
        "source_url": f"https://example.test/{identifier}",
        "identifier": identifier, "language": "nl", "role": role,
    }


def _document(celex: str, bron: bytes, *, base_celex: str | None = None,
              markdown: str = "# Handeling") -> Document:
    herkomst = bind_structure(
        Herkomst(format="formex", celex=celex, base_celex=base_celex, language="nl",
                 requested_url=celex, source_url=f"https://example.test/{celex}",
                 waarschuwingen=("EUR-Lex is via de officiële Formex-manifestatie opgehaald.",)),
        markdown, [_bewijs(bron, celex)],
    )
    return Document(markdown=markdown, source=f"EUR-Lex • CELEX:{celex}", provenance=herkomst)


def test_de_bundel_landt_uitgepakt_en_ophaal_json_koppelt_vraag_aan_identiteit(tmp_path, monkeypatch):
    bron_a, bron_b = b"PK\x03\x04handeling a", b"PK\x03\x04geconsolideerd b"
    antwoorden = {
        "32022R1925": _document("32022R1925", bron_a),
        # Een geconsolideerde vraag landt als haar basishandeling (kb_bundle.identiteit).
        "02015R0848-20251106": _document("02015R0848-20251106", bron_b, base_celex="32015R0848"),
    }

    def nep_from_link(vraag, lang="NL"):
        if vraag not in antwoorden:
            raise ConversionError(f"Geen geldig CELEX-nummer: {vraag}")
        return antwoorden[vraag]

    monkeypatch.setattr(kb_fetch.sources, "from_link", nep_from_link)
    lijst = tmp_path / "set.txt"
    lijst.write_text("# query\tprofiel\tcategorie\treden\n"
                     "32022R1925\teurlex\tmodern\tproef\n"
                     "\n"
                     "02015R0848-20251106\teurlex\tgeconsolideerd\tproef  # commentaar\n"
                     "99999X9999\teurlex\tkapot\tbestaat niet\n", encoding="utf-8")
    uit = tmp_path / "holdout"

    code = kb_fetch.main(["--lijst", str(lijst), "--uit", str(uit)])

    assert code == 1  # één van de drie faalt, de andere twee zijn wel geschreven
    digest_a = hashlib.sha256(bron_a).hexdigest()
    assert (uit / "raw/eurlex/32022R1925.md").read_text(encoding="utf-8") == "# Handeling"
    assert (uit / "raw/eurlex/32022R1925.source.json").exists()
    assert (uit / f"raw/source-evidence/32022R1925/{digest_a}.fmx4.zip").read_bytes() == bron_a
    fetch = json.loads((uit / "raw/source-evidence/32022R1925/fetch.json").read_text(encoding="utf-8"))
    assert fetch["sha256"] == digest_a
    # De geconsolideerde vraag staat onder de basishandeling op schijf ...
    assert (uit / "raw/eurlex/32015R0848.md").exists()
    assert not (uit / "raw/eurlex/02015R0848-20251106.md").exists()

    ophaal = json.loads((uit / "ophaal.json").read_text(encoding="utf-8"))
    assert set(ophaal) == {"32022R1925", "02015R0848-20251106", "99999X9999"}
    assert ophaal["32022R1925"]["status"] == "ok"
    assert ophaal["32022R1925"]["pad_id"] == "32022R1925"
    assert ophaal["32022R1925"]["profiel"] == "eurlex"
    assert ophaal["32022R1925"]["sha256"] == digest_a
    assert ophaal["32022R1925"]["source_format"] == "formex"
    # ... en ophaal.json is de koppeling van vraag naar identiteit.
    assert ophaal["02015R0848-20251106"]["pad_id"] == "32015R0848"
    assert ophaal["99999X9999"]["status"] == "geweigerd"
    assert "Geen geldig CELEX-nummer" in ophaal["99999X9999"]["melding"]
    assert ophaal["99999X9999"]["pad_id"] is None


def test_een_tweede_ophaal_laat_geen_weeszip_achter_en_houdt_de_rest_van_ophaal_json(tmp_path, monkeypatch):
    """De zip-container krijgt bij elke download een nieuw tijdstip en dus een nieuwe
    hash; twee zips in één bewijsmap laten `structure_gate` niet kiezen welke de bron
    is. Een vervangen id mag ook niet de koppeling van de andere documenten wissen."""
    eerste, tweede = b"PK\x03\x04eerste download", b"PK\x03\x04tweede download"
    uit = tmp_path / "holdout"

    monkeypatch.setattr(kb_fetch.sources, "from_link", lambda v, lang="NL": _document(v, eerste))
    assert kb_fetch.main(["--uit", str(uit), "32022R1925", "32022R2065"]) == 0
    bewijsmap = uit / "raw/source-evidence/32022R1925"
    (bewijsmap / "processing").mkdir()  # werkartefacten van een eerdere plaatsing blijven staan
    (bewijsmap / "processing" / "evidence.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(kb_fetch.sources, "from_link", lambda v, lang="NL": _document(v, tweede))
    assert kb_fetch.main(["--uit", str(uit), "32022R1925"]) == 0

    zips = sorted(p.name for p in bewijsmap.glob("*.fmx4.zip"))
    assert zips == [hashlib.sha256(tweede).hexdigest() + ".fmx4.zip"]
    assert (bewijsmap / "processing" / "evidence.json").exists()
    ophaal = json.loads((uit / "ophaal.json").read_text(encoding="utf-8"))
    assert set(ophaal) == {"32022R1925", "32022R2065"}
    assert ophaal["32022R1925"]["sha256"] == hashlib.sha256(tweede).hexdigest()
    assert ophaal["32022R2065"]["sha256"] == hashlib.sha256(eerste).hexdigest()


def test_een_document_zonder_kennisbankidentiteit_is_een_fout_geen_stille_overslag(tmp_path, monkeypatch):
    monkeypatch.setattr(kb_fetch.sources, "from_link",
                        lambda v, lang="NL": Document("# Notitie", "test",
                                                      provenance=Herkomst(format="pasted", bestandsnaam="n.md")))
    uit = tmp_path / "uit"
    assert kb_fetch.main(["--uit", str(uit), "iets"]) == 1
    ophaal = json.loads((uit / "ophaal.json").read_text(encoding="utf-8"))
    assert ophaal["iets"]["status"] == "fout"
    assert "kennisbankidentiteit" in ophaal["iets"]["melding"]
    assert not (uit / "raw").exists()


def test_lees_lijst_weigert_een_dubbele_vraag(tmp_path):
    lijst = tmp_path / "set.txt"
    lijst.write_text("32022R1925\teurlex\n32022R1925\teurlex\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="twee keer"):
        kb_fetch.lees_lijst(lijst)
