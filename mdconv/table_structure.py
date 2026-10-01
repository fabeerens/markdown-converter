"""Expand HTML table spans before Markdown can discard their relationships."""
from __future__ import annotations

from copy import deepcopy

from .errors import ConversionError


def normalize_data_tables(container):
    """Replace each table row by its occupied grid cells, preserving empty cells.

    Only direct rows/cells of a table participate. Marker tables inside a data
    cell are therefore never mistaken for additional rows of the outer table.
    The separate kb verifier rebuilds occupancy from preserved source HTML.
    """
    tables = ([container] if container.name == "table" else []) + list(container.find_all("table"))
    for table in reversed(tables):
        if table.find("table") is not None:
            raise ConversionError("Geneste inhoudstabel vereist afzonderlijke broncontrole; omzetting geweigerd.")
        rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
        if not rows:
            continue
        header_rows = [row for row in rows if row.find_parent("thead") is not None]
        leading_headers = []
        for row in rows:
            header_cells = row.find_all(["td", "th"], recursive=False)
            if header_cells and all(cell.name == "th" and cell.get("scope") != "row" for cell in header_cells):
                leading_headers.append(row)
            else:
                break
        if max(len(header_rows), len(leading_headers)) > 1:
            raise ConversionError("Tabel met meerdere koplagen vereist broncontrole; één Markdown-koprij is onvoldoende.")
        if not any(cell.has_attr(key) for row in rows for cell in row.find_all(["td", "th"], recursive=False)
                   for key in ("rowspan", "colspan")):
            continue
        rendered, carry, width = [], {}, 0
        for number, row in enumerate(rows):
            group = row.parent
            cells = row.find_all(["td", "th"], recursive=False)
            occupied = dict(carry)
            next_carry = {column: (cell, life - 1) for column, (cell, life) in carry.items() if life > 1}
            column = 0
            for cell in cells:
                while column in occupied:
                    column += 1
                try:
                    raw_rs, raw_cs = str(cell.get("rowspan", "1")), str(cell.get("colspan", "1"))
                    if not raw_rs.isdigit() or not raw_cs.isdigit():
                        raise ValueError
                    rs, cs = int(raw_rs), int(raw_cs)
                    if rs == 0:
                        rs = sum(1 for later in rows[number:] if later.parent is group)
                    if rs < 1 or cs < 1 or cs > 1000 or rs > len(rows) - number:
                        raise ValueError
                    if any(later.parent is not group for later in rows[number:number + rs]):
                        raise ValueError
                except ValueError:
                    raise ConversionError("Tabel heeft een ongeldige of grensoverschrijdende rowspan/colspan; controleer de oorspronkelijke bron.") from None
                for target in range(column, column + cs):
                    if target in occupied:
                        raise ConversionError("Tabel bevat overlappende samengevoegde cellen; omzetting geweigerd.")
                    occupied[target] = (cell, rs)
                    if rs > 1:
                        next_carry[target] = (cell, rs - 1)
                column += cs
            width = max(width, max(occupied, default=-1) + 1)
            rendered.append(occupied)
            carry = next_carry
        if width > 1000 or width * len(rows) > 1000000:
            raise ConversionError("Tabelraster is te groot om betrouwbaar om te zetten.")
        for row, occupied in zip(rows, rendered):
            if set(occupied) != set(range(width)):
                raise ConversionError("Tabel heeft ontbrekende cellen buiten een bewezen rowspan; omzetting geweigerd.")
            row.clear()
            for column in range(width):
                clone = deepcopy(occupied[column][0])
                clone.attrs.pop("rowspan", None)
                clone.attrs.pop("colspan", None)
                row.append(clone)
