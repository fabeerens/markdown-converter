"""Gedeelde bouwstenen voor de BWB- en de Formex-omzetter.

- `Uitvoer` verzamelt Markdownblokken, structuureenheden (anker + eigen tekst),
  voetnoten en elementen zonder eigen behandeling.
- `tabel_markdown` zet een tabel met rij- en kolomoverspanning om naar een
  rechthoekige Markdowntabel. Een overspannen cel wordt herhaald op elke plek
  die hij in de bron beslaat: dat is wat de bron zegt, niet een gok. Een cel
  die de bron leeg laat blijft leeg.
- `nummer_anker` maakt van een bronnummer een ankersegment volgens
  md-clean-core/references/conventions.md §4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..errors import ConversionError


@dataclass
class Eenheid:
    anker: str
    soort: str          # hoofdstuk, artikel, lid, onderdeel, overweging, bijlage, ...
    tekst: str          # eigen tekst: de kop, of de aanhef vóór eventuele kinderen

ROMEINS = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
LATIJN = ("bis", "ter", "quater", "quinquies", "sexies", "septies", "octies", "novies", "nonies", "decies")


def romeins_naar_arabisch(s: str) -> int | None:
    s = s.lower()
    if not s or any(c not in ROMEINS for c in s):
        return None
    totaal, vorige = 0, 0
    for c in reversed(s):
        w = ROMEINS[c]
        totaal = totaal - w if w < vorige else totaal + w
        vorige = max(vorige, w)
    return totaal


def nummer_anker(nr: str, *, romeins_omrekenen: bool = False) -> str:
    """'49 bis' -> '49bis', '6a.19' -> '6a-19', '3:4' -> '3-4', 'c)' -> 'c', 'IV' -> '4'."""
    s = nr.strip().lower()
    s = s.replace("°", "").replace("º", "")
    s = re.sub(r"^[(\[]|[.)\]]+$", "", s).strip()
    s = s.replace("*", "-ster")
    s = re.sub(r"\s+(" + "|".join(LATIJN) + r")\b", r"\1", s)
    if romeins_omrekenen:
        m = re.fullmatch(r"([ivxlcdm]+)([a-z]?)", s)
        if m and romeins_naar_arabisch(m.group(1)):
            s = f"{romeins_naar_arabisch(m.group(1))}{m.group(2)}"
    s = re.sub(r"[.:]", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    return s.strip("-")


def ws(tekst: str) -> str:
    return re.sub(r"\s+", " ", tekst).strip()


@dataclass
class Uitvoer:
    blokken: list[str] = field(default_factory=list)
    eenheden: list[Eenheid] = field(default_factory=list)
    noten: list[tuple[str, str]] = field(default_factory=list)  # (label, tekst)
    onbekend: dict[str, int] = field(default_factory=dict)

    def blok(self, tekst: str) -> None:
        if tekst.strip():
            self.blokken.append(tekst.rstrip())

    def eenheid(self, anker: str, soort: str, tekst: str) -> None:
        self.eenheden.append(Eenheid(anker, soort, ws(tekst)))

    def markeer_onbekend(self, tag: str) -> None:
        # In de meetfase was dit alleen een telling. Voor een downloadbare bron
        # is dat te laat: onbekende inhoud kan dan al uit de Markdown ontbreken.
        raise ConversionError(
            f"XML-element zonder eigen behandeling ({tag}); omzetting geweigerd."
        )

    def markdown(self) -> str:
        md = "\n\n".join(self.blokken)
        if self.noten:
            md += "\n\n" + "\n".join(f"[^{label}]: {tekst}" for label, tekst in self.noten)
        return md.strip() + "\n"


def tabel_markdown(rijen: list[list[dict]], kop_rijen: int) -> tuple[str, int]:
    """Rijen van cellen {tekst, kol (0-based of None), colspan, rowspan} -> Markdown.

    Geeft ook het aantal cellen terug dat door overspanning is herhaald.
    """
    if not rijen:
        raise ConversionError("Tabel zonder rijen; omzetting geweigerd.")
    raster: dict[tuple[int, int], str] = {}
    herhaald = 0
    breedte = 0
    for r, rij in enumerate(rijen):
        k = 0
        for cel in rij:
            kol = cel.get("kol")
            if kol is None:
                while (r, k) in raster:
                    k += 1
                kol = k
            rowspan = cel.get("rowspan", 1)
            colspan = cel.get("colspan", 1)
            if (not isinstance(kol, int) or not isinstance(rowspan, int)
                    or not isinstance(colspan, int) or kol < 0
                    or rowspan < 1 or colspan < 1 or r + rowspan > len(rijen)
                    or kol + colspan > 1000 or rowspan * colspan > 1_000_000):
                raise ConversionError(
                    "Tabel heeft een ongeldige of grensoverschrijdende rowspan/colspan; "
                    "controleer de oorspronkelijke bron."
                )
            for dr in range(rowspan):
                for dk in range(colspan):
                    positie = (r + dr, kol + dk)
                    if positie in raster:
                        raise ConversionError(
                            "Tabel bevat overlappende samengevoegde cellen; omzetting geweigerd."
                        )
                    if (dr, dk) != (0, 0):
                        herhaald += 1
                    raster[positie] = cel["tekst"]
            k = kol + colspan
            breedte = max(breedte, k)
    hoogte = max((r for r, _ in raster), default=-1) + 1
    if breedte > 1000 or breedte * hoogte > 1_000_000:
        raise ConversionError("Tabelraster is te groot om betrouwbaar om te zetten.")
    if any((r, k) not in raster for r in range(hoogte) for k in range(breedte)):
        raise ConversionError(
            "Tabel heeft ontbrekende cellen buiten een bewezen rowspan; omzetting geweigerd."
        )

    def regel(r: int) -> str:
        cellen = [raster.get((r, k), "").replace("|", "\\|") for k in range(breedte)]
        return "| " + " | ".join(cellen) + " |"

    kop = max(kop_rijen, 1)
    uit = [regel(r) for r in range(min(kop, hoogte))]
    if kop_rijen == 0:
        # Markdown eist een kopregel; zonder kop in de bron een lege kop.
        uit = ["| " + " | ".join([""] * breedte) + " |"]
        start = 0
    else:
        start = kop
    uit.append("| " + " | ".join(["---"] * breedte) + " |")
    uit += [regel(r) for r in range(start, hoogte)]
    return "\n".join(uit), herhaald
