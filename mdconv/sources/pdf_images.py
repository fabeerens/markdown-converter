"""Losse afbeeldingen uit een PDF extraheren — `pdfimages`/`pdfinfo` (poppler-utils).

Haalt ingesloten rasterafbeeldingen (grafieken, screenshots) uit een PDF.
Systeembinaries, niet via pip te installeren — zie CLAUDE.md voor de
installatie-instructies (Homebrew lokaal, `apt-get` in Docker).

**Volledige pagina's als scan worden bewust overgeslagen.** Een gescande
pagina staat in de PDF vaak als één grote ingesloten afbeelding die (bijna)
de hele pagina beslaat; die als "geëxtraheerde afbeelding" behandelen zou de
paginatekst dupliceren als los bijlagebestand. `_is_full_page()` vergelijkt
de **fysieke afmeting** van elke afbeelding (breedte/hoogte in pixels
gedeeld door de eigen ppi uit `pdfimages -list`) met de paginaomvang uit
`pdfinfo`; beslaat een afbeelding op beide assen minstens `_FULL_PAGE_COVERAGE`
van de pagina, dan is het vrijwel zeker de hele pagina, geen losse figuur.

**Waarom niet alles als JPEG.** `pdfimages -j` levert alleen écht al-JPEG-
gecodeerde afbeeldingen (foto's) als `.jpg`; een afbeelding die in de PDF als
rauwe pixmap staat (typisch voor met vectorgrafiek/PNG gegenereerde
grafieken en screenshots) komt er als een enorm ongecomprimeerd `.ppm`/`.pbm`
uit. Die worden hier met Pillow herschreven naar PNG — klein en lossless,
passend bij het soort bron (grafiek/screenshot, geen foto).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..errors import ConversionError

_TIMEOUT = 180
# Rasteren van veel pagina's op hoge dpi (wiskunde-modus) duurt langer dan het
# lichte `pdfimages -list`/`pdfinfo`-werk hierboven.
_RENDER_TIMEOUT = 600

# Beslaat een ingesloten afbeelding op zowel breedte als hoogte minstens dit
# aandeel van de fysieke paginaomvang, dan tellen we 'm als "hele pagina".
_FULL_PAGE_COVERAGE = 0.85

# Kolommen van `pdfimages -list` (16 velden — "object" en "ID" zijn twee
# aparte kolommen, niet één "object ID"):
#   page num type width height color comp bpc enc interp object ID x-ppi y-ppi size ratio
_LIST_LINE_RE = re.compile(
    r"^\s*(?P<page>\d+)\s+(?P<num>\d+)\s+\S+\s+(?P<width>\d+)\s+(?P<height>\d+)"
    r"\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+(?P<xppi>[\d.]+)\s+(?P<yppi>[\d.]+)"
)
#   "Page size:       595.276 x 841.89 pts (A4)"       (single-page output)
#   "Page    2 size:  595.276 x 841.89 pts (A4)"        (with -f/-l page range)
_PAGE_SIZE_RE = re.compile(r"^Page\s*\d*\s+size:\s+([\d.]+)\s+x\s+([\d.]+)\s+pts")


@dataclass(frozen=True, slots=True)
class ExtractedImage:
    """Eén losse, behouden afbeelding uit een PDF."""

    page: int            # 1-gebaseerd paginanummer
    index_on_page: int   # 1-gebaseerd, voor de bestandsnaam bij meerdere per pagina
    data: bytes
    ext: str              # "jpg" of "png"


def available() -> bool:
    return shutil.which("pdfimages") is not None and shutil.which("pdfinfo") is not None


def _run(args: list[str], *, timeout: int = _TIMEOUT) -> str:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as e:
        raise ConversionError("Een poppler-bewerking duurde te lang.") from e
    except OSError as e:
        raise ConversionError(f"Kon pdfimages/pdfinfo/pdftoppm niet uitvoeren: {e}") from e
    return result.stdout


def _parse_list(listing: str) -> list[dict]:
    rows = []
    for line in listing.splitlines():
        m = _LIST_LINE_RE.match(line)
        if m:
            rows.append({
                "page": int(m["page"]),
                "num": int(m["num"]),
                "width": int(m["width"]),
                "height": int(m["height"]),
                "xppi": float(m["xppi"]),
                "yppi": float(m["yppi"]),
            })
    return rows


def _page_size_inches(pdf_path: Path, page: int) -> tuple[float, float] | None:
    out = _run(["pdfinfo", "-f", str(page), "-l", str(page), str(pdf_path)])
    for line in out.splitlines():
        m = _PAGE_SIZE_RE.match(line)
        if m:
            return float(m.group(1)) / 72, float(m.group(2)) / 72
    return None


def _is_full_page(row: dict, page_size: tuple[float, float] | None) -> bool:
    if not page_size or not row["xppi"] or not row["yppi"]:
        return False
    page_w_in, page_h_in = page_size
    img_w_in = row["width"] / row["xppi"]
    img_h_in = row["height"] / row["yppi"]
    return (
        page_w_in > 0 and page_h_in > 0
        and img_w_in / page_w_in >= _FULL_PAGE_COVERAGE
        and img_h_in / page_h_in >= _FULL_PAGE_COVERAGE
    )


def _to_png(raw_path: Path) -> bytes:
    from io import BytesIO

    from PIL import Image

    with Image.open(raw_path) as im:
        buf = BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()


def extract_images(pdf_bytes: bytes) -> list[ExtractedImage]:
    """Losse afbeeldingen uit `pdf_bytes`, hele-pagina-scans uitgesloten."""
    if not available():
        raise ConversionError(
            "Afbeeldingen extraheren vereist poppler-utils (pdfimages/pdfinfo), dat niet "
            "op dit systeem is geïnstalleerd. Zie CLAUDE.md voor installatie-instructies."
        )

    with tempfile.TemporaryDirectory(prefix="mdconv-pdfimg-") as tmp:
        tmp_path = Path(tmp)
        pdf_path = tmp_path / "in.pdf"
        pdf_path.write_bytes(pdf_bytes)

        rows = _parse_list(_run(["pdfimages", "-list", str(pdf_path)]))
        if not rows:
            return []

        page_sizes: dict[int, tuple[float, float] | None] = {}
        for row in rows:
            if row["page"] not in page_sizes:
                page_sizes[row["page"]] = _page_size_inches(pdf_path, row["page"])

        # `pdfimages -j` schrijft de bestanden in dezelfde volgorde als
        # `-list` ze rapporteert (verschijningsvolgorde in de PDF), dus rows[i]
        # hoort bij het i-de bestand in die gesorteerde lijst — niet bij de
        # "num"-kolom zelf (die telt per PDF-objectverwijzing, niet per
        # uitvoerbestand).
        out_prefix = tmp_path / "img"
        _run(["pdfimages", "-j", str(pdf_path), str(out_prefix)])
        extracted_files = sorted(tmp_path.glob("img-*"))
        if len(extracted_files) != len(rows):
            raise ConversionError(
                f"Onverwacht aantal geëxtraheerde afbeeldingen ({len(extracted_files)} "
                f"i.p.v. {len(rows)}) — kan afbeeldingen niet betrouwbaar aan pagina's koppelen."
            )

        page_counts: dict[int, int] = {}
        images: list[ExtractedImage] = []
        for row, file_path in zip(rows, extracted_files):
            if _is_full_page(row, page_sizes.get(row["page"])):
                continue
            page_counts[row["page"]] = page_counts.get(row["page"], 0) + 1
            if file_path.suffix.lower() == ".jpg":
                data, ext = file_path.read_bytes(), "jpg"
            else:
                data, ext = _to_png(file_path), "png"
            images.append(ExtractedImage(
                page=row["page"],
                index_on_page=page_counts[row["page"]],
                data=data,
                ext=ext,
            ))
        return images


# --------------------------------------------------------------------------
# Pagina's naar afbeeldingen rasteren (wiskunde-modus / OCR)
# --------------------------------------------------------------------------
#
# Aparte functie van `extract_images`: die haalt de ingesloten rasters eruit;
# dit rendert elke *pagina* als PNG, zodat een vision-model de opgemaakte
# formules kan lezen (zie `mdconv/ocr.py`). Deelt wel de poppler-conventies
# van deze module (`_run`, `ConversionError`, tempdir).

_PAGES_RE = re.compile(r"^Pages:\s+(\d+)", re.M)


def render_available() -> bool:
    return shutil.which("pdftoppm") is not None and shutil.which("pdfinfo") is not None


def page_count(pdf_bytes: bytes) -> int:
    """Aantal pagina's in de PDF, via `pdfinfo`. 0 als het niet te bepalen is."""
    with tempfile.TemporaryDirectory(prefix="mdconv-pdfinfo-") as tmp:
        pdf_path = Path(tmp) / "in.pdf"
        pdf_path.write_bytes(pdf_bytes)
        m = _PAGES_RE.search(_run(["pdfinfo", str(pdf_path)]))
        return int(m.group(1)) if m else 0


def render_pages(pdf_bytes: bytes, *, dpi: int = 200, max_pages: int = 100) -> list[bytes]:
    """Elke pagina van de PDF als PNG (bytes), op volgorde.

    Weigert een PDF met meer dan `max_pages` pagina's vóór het rasteren — de
    gebruiker kiest bewust voor deze trage modus, dus een expliciete grens is
    beter dan een halve conversie of een uitlopende rekening.
    """
    if not render_available():
        raise ConversionError(
            "Pagina's rasteren vereist poppler-utils (pdftoppm/pdfinfo), dat niet op dit "
            "systeem is geïnstalleerd. Zie CLAUDE.md voor installatie-instructies."
        )

    n = page_count(pdf_bytes)
    if n > max_pages:
        raise ConversionError(
            f"De PDF heeft {n} pagina's; de wiskunde-modus verwerkt er maximaal {max_pages}. "
            "Splits het document en probeer de delen apart."
        )

    with tempfile.TemporaryDirectory(prefix="mdconv-pdftoppm-") as tmp:
        tmp_path = Path(tmp)
        pdf_path = tmp_path / "in.pdf"
        pdf_path.write_bytes(pdf_bytes)
        _run(
            ["pdftoppm", "-png", "-r", str(dpi), str(pdf_path), str(tmp_path / "page")],
            timeout=_RENDER_TIMEOUT,
        )
        # `pdftoppm` schrijft `page-1.png`, `page-2.png`, … (bij ≥ 10 pagina's
        # nul-gevuld tot gelijke breedte). Sorteer op het paginanummer zelf,
        # niet lexicaal — anders komt `page-10` vóór `page-2`.
        pages = sorted(
            tmp_path.glob("page-*.png"),
            key=lambda p: int(p.stem.rsplit("-", 1)[-1]),
        )
        if not pages:
            raise ConversionError("Geen pagina's uit de PDF kunnen rasteren.")
        return [p.read_bytes() for p in pages]
