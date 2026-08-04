"""Gedeelde parser voor de "RspDL"-alineaconventie van het Duitse `juris`-systeem.

Zowel rechtsprechung-im-internet.de (XML, federale gerechten) als OpenLegalData
(HTML, bredere dekking incl. deelstaten) leveren voor federale/juris-gebaseerde
uitspraken **dezelfde onderliggende structuur**: `<dl class="RspDL"><dt>…</dt>
<dd>…</dd></dl>`-paren, met het randnummer in `<dt>` (als `<a name="rd_N">N</a>`)
en de bijbehorende alinea of een `<table>` (bv. het handtekeningenblok) in
`<dd>`. Deze module bevat de walker éénmalig, zodat beide bronmodules hem
kunnen hergebruiken in plaats van hem te dupliceren.

Werkt op zowel `lxml.etree`- als `lxml.html`-elementen: beide bieden dezelfde
`.tag`/`.text`/`.tail`/iteratie-interface, dus deze functies maken geen
onderscheid tussen XML- en HTML-afkomstige bomen.
"""

from __future__ import annotations

from .. import render


def ln(tag) -> str:
    """Lokale naam van een (mogelijk namespace-gekwalificeerde) tag."""
    return tag.split("}")[-1] if isinstance(tag, str) else ""


def first(el, name: str):
    if el is None:
        return None
    for child in el:
        if ln(child.tag) == name:
            return child
    return None


def text_of(el) -> str:
    return render.collapse_ws("".join(el.itertext())) if el is not None else ""


def walk_dl_section(section) -> list[str]:
    """Elke `<dl>` binnen `section` is één genummerde alinea (`<dt>`) met inhoud (`<dd>`)."""
    blocks: list[str] = []
    for dl in section.iter("dl"):
        dt, dd = first(dl, "dt"), first(dl, "dd")
        if dd is None:
            continue
        content = render_dd(dd)
        if not content:
            continue
        num = text_of(dt)
        blocks.append(f"{num}. {content}" if num else content)
    return blocks


def render_dd(dd) -> str:
    parts = []
    for child in dd:
        tag = ln(child.tag)
        if tag == "p":
            text = inline(child)
            if text:
                parts.append(text)
        elif tag == "table":
            table_md = table_to_markdown(child)
            if table_md:
                parts.append(table_md)
    return "\n\n".join(parts)


def inline(el) -> str:
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        tag = ln(child.tag)
        if tag == "em":
            inner = inline(child)
            parts.append(f"*{inner}*" if inner.strip() else inner)
        elif tag == "br":
            parts.append(" ")
        else:
            parts.append(inline(child))
        if child.tail:
            parts.append(child.tail)
    return render.collapse_ws("".join(parts))


def table_to_markdown(table) -> str:
    rows = []
    for tr in table.iter("tr"):
        cells = [text_of(td).replace("|", "\\|") for td in tr if ln(td.tag) == "td"]
        if any(c.strip() for c in cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    for r in rows[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def has_rspdl_structure(html_or_xml: str) -> bool:
    """Snelle detectie: bevat dit de RspDL-conventie (dan is deze walker van
    toepassing), of is het een ander bronformaat (bv. een deelstaatportaal met
    zijn eigen HTML-conventie)?"""
    return 'class="RspDL"' in html_or_xml or "class='RspDL'" in html_or_xml
