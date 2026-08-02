"""EUR-Lex Formex (FMX) XML → Markdown.

Formex is het officiële structuurformaat van het EU-Publicatiebureau. Deze
parser dekt de gangbare elementen van wetgevingsteksten (titels, preambule met
overwegingen, hoofdstukken, artikelen, alinea's, lijsten, tabellen, voetnoten
en bijlagen). Onbekende containers worden transparant doorlopen, zodat ook
afwijkende of gedeeltelijke documenten leesbare uitvoer geven.

De renderstaat (`_Ctx`) wordt **expliciet doorgegeven** aan elke functie. In de
vorige opzet stond die in een module-level stack (`_CURRENT_CTX`), waardoor twee
gelijktijdige conversies elkaars voetnoten oppikten — meetbaar: document B
kreeg de voetnoten van A en miste die van zichzelf. Flask draait lokaal met
threads en meerdere documenten worden parallel geüpload, dus dat was echt
bereikbaar en geen theoretisch risico.
"""

from __future__ import annotations

from lxml import etree

from ..errors import ConversionError
from ..render import collapse_ws, tidy


def _local(tag) -> str:
    """De lokale elementnaam in hoofdletters (namespace weggelaten)."""
    if not isinstance(tag, str):
        return ""
    return tag.split("}")[-1].upper()


class _Ctx:
    """Renderstaat die door de walk wordt meegedragen."""

    __slots__ = ("blocks", "footnotes", "seen_note_ids", "pending")

    def __init__(self):
        self.blocks: list[str] = []
        self.footnotes: list[tuple[str, str]] = []
        self.seen_note_ids: set[str] = set()
        self.pending: str = ""  # nummerprefix dat op de volgende alinea wacht

    def add(self, text: str) -> None:
        text = text.strip("\n")
        if text.strip():
            self.blocks.append(text)

    def add_para(self, text: str) -> None:
        """Voeg een alinea toe en consumeer een wachtend nummer (bv. '1.')."""
        text = text.strip()
        if not text:
            return
        if self.pending:
            text = f"{self.pending}  {text}"
            self.pending = ""
        self.blocks.append(text)


# Elementen met louter bibliografische/technische metadata: overslaan.
_SKIP = {
    "BIB.INSTANCE", "BIB.DOC", "BIB.NOTICE", "BIB.DATA",
    "NCR", "NCR.NOTICE", "REFLEX", "OJ", "PUBLICATION.REF",
}


def convert_formex(xml_bytes: bytes) -> str:
    """Zet Formex-XML om naar Markdown."""
    ctx = _Ctx()
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)
    try:
        root = etree.fromstring(xml_bytes, parser=parser)
    except etree.XMLSyntaxError as e:
        raise ConversionError(f"Kon de XML niet parsen: {e}") from e
    if root is None:
        raise ConversionError("Kon de XML niet parsen.")

    _walk(root, ctx, level=0)
    body = "\n\n".join(ctx.blocks)

    if ctx.footnotes:
        notes = "\n".join(f"[^{nid}]: {txt}" for nid, txt in ctx.footnotes)
        body = f"{body}\n\n---\n\n{notes}"

    # Terugval: leverde de structuurparser vrijwel niets op, dump dan platte tekst.
    if len(body.strip()) < 40:
        text = " ".join(t.strip() for t in root.itertext() if t.strip())
        if text:
            body = text

    return tidy(body)


# --------------------------------------------------------------------------
# Blokniveau
# --------------------------------------------------------------------------

def _walk(el, ctx: _Ctx, level: int) -> None:
    tag = _local(el.tag)
    handler = _HANDLERS.get(tag)
    if handler is not None:
        handler(el, ctx, level)
    elif tag in _SKIP:
        return
    else:
        # Onbekende container: gewoon de kinderen doorlopen.
        for child in el:
            _walk(child, ctx, level)


def _heading(el, ctx: _Ctx, level: int) -> None:
    """Document-<TITLE>: hoofdtitel (H1) plus optionele ondertitel."""
    ti = _first(el, "TI")
    sti = _first(el, "STI")
    title = _inline(ti, ctx) if ti is not None else _inline(el, ctx)
    if title.strip():
        ctx.add(f"# {title.strip()}")
    if sti is not None:
        sub = _inline(sti, ctx).strip()
        if sub:
            ctx.add(f"## {sub}")


def _division(el, ctx: _Ctx, level: int) -> None:
    """Hoofdstukken/afdelingen/titels; kopdiepte groeit met de nesting."""
    depth = min(level + 2, 6)
    for child in el:
        if _local(child.tag) == "TITLE":
            ti = _first(child, "TI")
            sti = _first(child, "STI")
            head = _inline(ti, ctx).strip() if ti is not None else _inline(child, ctx).strip()
            if sti is not None and _inline(sti, ctx).strip():
                sub = _inline(sti, ctx).strip()
                head = f"{head} — {sub}" if head else sub
            if head:
                ctx.add(f"{'#' * depth} {head}")
        else:
            _walk(child, ctx, level + 1)


def _article(el, ctx: _Ctx, level: int) -> None:
    ti = _first(el, "TI.ART")
    sti = _first(el, "STI.ART")
    head = _inline(ti, ctx).strip() if ti is not None else "Artikel"
    if sti is not None and _inline(sti, ctx).strip():
        head = f"{head} — {_inline(sti, ctx).strip()}"
    ctx.add(f"### {head}")
    for child in el:
        if _local(child.tag) in ("TI.ART", "STI.ART"):
            continue
        _walk(child, ctx, level)


def _parag(el, ctx: _Ctx, level: int) -> None:
    """Genummerde alinea: <NO.PARAG> plus één of meer <ALINEA>/lijsten.

    Het nummer wacht als prefix op de eerste tekstalinea, zodat geneste
    lijsten/tabellen er alsnog onder komen te staan.
    """
    no = _first(el, "NO.PARAG")
    if no is not None:
        ctx.pending = _inline(no, ctx).strip()
    for child in el:
        if _local(child.tag) == "NO.PARAG":
            continue
        _walk(child, ctx, level)
    # Had de genummerde alinea geen tekstuele aanhef, zet het nummer dan alleen.
    if ctx.pending:
        ctx.add(ctx.pending)
        ctx.pending = ""


def _alinea(el, ctx: _Ctx, level: int) -> None:
    # Een ALINEA kan P's en/of lijsten/tabellen door elkaar bevatten.
    if len(el) == 0:
        ctx.add_para(_inline(el, ctx))
        return
    for child in el:
        if _local(child.tag) == "P":
            ctx.add_para(_inline(child, ctx))
        else:
            _walk(child, ctx, level)


def _paragraph(el, ctx: _Ctx, level: int) -> None:
    ctx.add_para(_inline(el, ctx))


def _consid(el, ctx: _Ctx, level: int) -> None:
    """Een overweging: <NP><NO.P>(1)</NO.P><TXT>…</TXT></NP>, of platte tekst."""
    np = _first(el, "NP")
    target = np if np is not None else el
    no_p = _first(target, "NO.P")
    num = _inline(no_p, ctx).strip() if no_p is not None else ""
    txt_el = _first(target, "TXT")
    txt = _inline(txt_el, ctx).strip() if txt_el is not None else _inline(target, ctx).strip()
    line = f"{num} {txt}".strip()
    if line:
        ctx.add(line)


def _visa(el, ctx: _Ctx, level: int) -> None:
    txt = _inline(el, ctx).strip()
    if txt:
        ctx.add(txt)


def _list(el, ctx: _Ctx, level: int) -> None:
    """<LIST> wordt één compacte, correct geneste Markdown-opsomming."""
    lines = _list_lines(el, ctx, 0)
    if lines:
        ctx.add("\n".join(lines))


def _list_lines(el, ctx: _Ctx, depth: int) -> list[str]:
    indent = "  " * depth
    lines: list[str] = []
    for item in el:
        if _local(item.tag) != "ITEM":
            continue
        np = _first(item, "NP")
        marker = ""
        text = ""
        if np is not None:
            no = _first(np, "NO.P")
            txt = _first(np, "TXT")
            marker = _inline(no, ctx).strip() if no is not None else ""
            text = _inline(txt, ctx).strip() if txt is not None else _inline(np, ctx).strip()
        else:
            text = _inline(item, ctx).strip()
        line = f"{marker} {text}".strip()
        if line:
            lines.append(f"{indent}- {line}")
        # Extra alinea's en geneste lijsten binnen het item.
        for sub in item:
            stag = _local(sub.tag)
            if stag == "LIST":
                lines.extend(_list_lines(sub, ctx, depth + 1))
            elif stag in ("P", "ALINEA") and sub is not np:
                cont = _inline(sub, ctx).strip()
                if cont:
                    lines.append(f"{indent}  {cont}")
    return lines


def _table(el, ctx: _Ctx, level: int) -> None:
    rows = []
    for row in el.iter():
        if _local(row.tag) != "ROW":
            continue
        cells = [_inline(c, ctx).strip().replace("\n", " ").replace("|", "\\|")
                 for c in row if _local(c.tag) == "CELL"]
        if cells:
            rows.append(cells)
    if not rows:
        return
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    md = ["| " + " | ".join(rows[0]) + " |",
          "| " + " | ".join(["---"] * width) + " |"]
    for r in rows[1:]:
        md.append("| " + " | ".join(r) + " |")
    ctx.add("\n".join(md))


def _annex(el, ctx: _Ctx, level: int) -> None:
    ti = _first(el, "TITLE")
    if ti is not None:
        head = _inline(ti, ctx).strip()
        if head:
            ctx.add(f"## {head}")
    for child in el:
        if _local(child.tag) == "TITLE":
            continue
        _walk(child, ctx, level)


def _signature(el, ctx: _Ctx, level: int) -> None:
    txt = _inline(el, ctx).strip()
    if txt:
        ctx.add(txt)


_HANDLERS = {
    "TITLE": _heading,
    "DIVISION": _division,
    "ARTICLE": _article,
    "PARAG": _parag,
    "ALINEA": _alinea,
    "P": _paragraph,
    "CONSID": _consid,
    "VISA": _visa,
    "LIST": _list,
    "TBL": _table,
    "TABLE": _table,
    "ANNEX": _annex,
    "SIGNATURE": _signature,
    "PREAMBLE.INIT": _paragraph,
    "PREAMBLE.FINAL": _paragraph,
    "GR.CONSID.INIT": _paragraph,
    "GR.VISA.INIT": _paragraph,
    "INTRO": _paragraph,
}


# --------------------------------------------------------------------------
# Inline
# --------------------------------------------------------------------------

def _first(el, name: str):
    if el is None:
        return None
    for child in el:
        if _local(child.tag) == name:
            return child
    return None


def _inline(el, ctx: _Ctx) -> str:
    if el is None:
        return ""
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        parts.append(_render_inline(child, ctx))
        if child.tail:
            parts.append(child.tail)
    return collapse_ws("".join(parts))


def _render_inline(el, ctx: _Ctx) -> str:
    tag = _local(el.tag)
    if tag == "HT":
        style = (el.get("TYPE") or "").upper()
        inner = _inline(el, ctx)
        if style in ("BOLD", "STRONG"):
            return f"**{inner}**" if inner.strip() else inner
        if style in ("ITALIC", "EMPHASIS"):
            return f"*{inner}*" if inner.strip() else inner
        return inner
    if tag in ("QUOT.START", "QUOT.END"):
        return '"'
    if tag == "NOTE":
        return _note(el, ctx)
    if tag == "BR":
        return " "
    return _inline(el, ctx)


def _note(el, ctx: _Ctx) -> str:
    """Voetnoot → Markdown-verwijzing, met de noot verzameld in de context."""
    nid = el.get("NOTE.ID") or el.get("ID") or f"n{len(ctx.footnotes) + 1}"
    text = " ".join(t.strip() for t in el.itertext() if t.strip())
    if nid not in ctx.seen_note_ids:
        ctx.seen_note_ids.add(nid)
        ctx.footnotes.append((nid, text))
    return f"[^{nid}]"
