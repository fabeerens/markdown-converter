"""Waar staat de kennisbank? Voor de twee tests die een gouden voorbeeld van de kb lezen.

Tot kb WP-55 bouwden ze `~/Documents/kb/golden/...` en sloegen ze met `skipif` stil over als dat
ontbrak: op een andere machine "slaagden" ze zonder iets te bewijzen (gemeten: 2 tests, 625 groen
plus 2 overgeslagen met `HOME` op een lege map). Dezelfde volgorde als `MDCONV_ROOT` in
`kb/tools/selectie/trek.py`: `KB_ROOT`, dan `~/Documents/kb`, dan de map naast de converter.
"""

from __future__ import annotations

import os
import pathlib

import pytest

CONVERTER = pathlib.Path(__file__).resolve().parent.parent


def kb_wortel() -> pathlib.Path:
    """De kb-map, of een overgeslagen test met de reden als er geen is.

    Een gezette `KB_ROOT` die naar een ontbrekende map wijst, is een fout met het pad: wie de
    variabele zet, bedoelt die map, en een stille terugval verbergt een typefout.
    """
    gezet = os.environ.get("KB_ROOT", "").strip()
    if gezet:
        pad = pathlib.Path(gezet).expanduser()
        if not pad.is_dir():
            pytest.fail(f"KB_ROOT wijst naar een map die er niet is: {pad}", pytrace=False)
        return pad
    for pad in (pathlib.Path.home() / "Documents" / "kb", CONVERTER.parent / "kb"):
        if pad.is_dir():
            return pad
    pytest.skip("geen kennisbank gevonden: zet KB_ROOT (de kb is privé, dus in CI slaat dit over)")


def kb_golden(*delen: str, bestand: str) -> pathlib.Path:
    """`<kb>/golden/<delen>`, met `bestand` erin. Is de kb er wel maar het voorbeeld niet, dan is de
    kb te oud of de map verkeerd: dat faalt met het pad, want overslaan bewijst niets."""
    map_ = kb_wortel() / "golden" / pathlib.Path(*delen)
    if not (map_ / bestand).exists():
        pytest.fail(f"het gouden voorbeeld van de kennisbank ontbreekt: {map_ / bestand}", pytrace=False)
    return map_
