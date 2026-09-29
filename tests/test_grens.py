"""De grens tussen converter en kennisbank: geen import uit de kb (kb WP-55).

`AGENTS.md`, regel 1: de converter en de kennisbank delen geen code en controleren elkaar op
structuur. Twee onafhankelijke implementaties die het oneens zijn, wijzen de fout aan; een
gedeelde module maakt dat bewijs waardeloos. Dit is het spiegelbeeld van
`md-clean-core/tests/test_grens.py` in de kb (WP-53), die op `import mdconv` faalt.

De test leest elk `.py`-bestand in de converter (app, `mdconv/`, `meetlat/`, `tests/`) met `ast`,
zodat commentaar en docstrings die `md-clean-core` noemen niet tellen, en faalt op:

- een absolute import van een kb-module: de scripts van `md-clean-core/scripts/` (`structure_gate`,
  `source_evidence`, `place_file`, ...) en `tools/` (`meetlat_kb`, `bronlezing`, ...);
- een `sys.path`-wijziging die naar de kb of naar `md-clean-*` wijst.

Wat de kb wel van de converter mag kennen, is het bestandsformaat: `mdconv/kb_fetch.py` en
`kb_bundle.py` schrijven `raw/<profiel>/` en `raw/source-evidence/<pad_id>/` in een map die de
gebruiker met `--uit` aanwijst. Dat zijn paden in data, geen code.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAPPEN = ("mdconv", "meetlat", "tests")
LOSSE_BESTANDEN = ("app.py",)

# De modulenamen van de kb (stand kb WP-54). `mdconv.table_structure` is de converter zelf, en
# valt hier niet onder: alleen een absolute import waarvan de eerste naam een kb-module is.
KB_MODULES = frozenset("""
apply_identifier_proposals apply_structure build_index bwbxml_source check_anchors describe_headings
docx_source extract_references footnote_checks formex_hvj_source formex_source hudoc_source
label_headings lint noot_stroom op_xml_source place_file preclean propose_identifiers
prune_reference_errors rebuild_legal_document rechtspraak_source source_checks source_evidence
source_footnotes source_format source_hierarchy source_tables structure_gate table_checks
verify_integrity wordlist yamlmini
meetlat_kb bronlezing check_skill_sync verwijsskill nummering_ronde eurlex_html table_structure
""".split())
KB_PADEN = ("md-clean", "md_clean", "documents/kb", "/kb/", "../kb", "kb_root")


def overtredingen(bestanden) -> list[str]:
    """`pad:regel: reden` voor elke kb-import of kb-pad in `sys.path`, gesorteerd."""
    uit = []
    for pad in bestanden:
        tekst = pad.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(tekst, filename=str(pad))):
            namen = []
            if isinstance(node, ast.Import):
                namen = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                namen = [node.module]
            for naam in namen:
                if naam.split(".")[0] in KB_MODULES:
                    uit.append(f"{pad.name}:{node.lineno}: import van kb-module {naam}")
            if isinstance(node, ast.Call) and ast.unparse(node.func).startswith("sys.path."):
                bron = ast.unparse(node).lower()
                if any(k in bron for k in KB_PADEN):
                    uit.append(f"{pad.name}:{node.lineno}: sys.path wijst naar de kb: {ast.unparse(node)}")
    return sorted(uit)


def converterbestanden() -> list[pathlib.Path]:
    bestanden = [ROOT / n for n in LOSSE_BESTANDEN]
    for map_ in MAPPEN:
        bestanden += [p for p in sorted((ROOT / map_).rglob("*.py")) if "__pycache__" not in p.parts]
    return bestanden


def test_de_converter_importeert_niets_uit_de_kb():
    bestanden = converterbestanden()
    assert len(bestanden) > 50, "de scan vond bijna niets: klopt ROOT nog?"
    assert overtredingen(bestanden) == []


def test_een_kb_import_valt_op(tmp_path):
    """Negatief: een ingevoegde import in een tijdelijke kopie laat de controle aanslaan, en
    commentaar, een docstring, `mdconv.table_structure` en een relatieve import doen dat niet."""
    slecht = tmp_path / "slecht.py"
    slecht.write_text(
        "import sys\n"
        "from structure_gate import attention\n"
        "def f():\n    import source_evidence\n"
        "sys.path.insert(0, '/Users/iemand/Documents/kb/md-clean-core/scripts')\n",
        encoding="utf-8")
    schoon = tmp_path / "schoon.py"
    schoon.write_text(
        '"""Noemt md-clean-core/scripts/structure_gate.py alleen in een docstring."""\n'
        "# import structure_gate: commentaar telt niet\n"
        "from mdconv import table_structure\n"
        "from . import lint\n"
        "import sys\nsys.path.insert(0, '/tmp/ergens')\n",
        encoding="utf-8")
    assert [r.split(": ")[0] for r in overtredingen([slecht])] == ["slecht.py:2", "slecht.py:4", "slecht.py:5"]
    assert overtredingen([schoon]) == []
