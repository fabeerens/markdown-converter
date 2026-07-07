"""Convert EUR-Lex Formex (FMX) XML into Markdown.

Formex is the official structured XML format used by the EU Publications
Office. This converter handles the common structural elements found in legal
acts (titles, preamble/recitals, chapters, articles, paragraphs, lists,
tables, footnotes and annexes). Unknown containers are transparently
recursed into, so partial/variant documents still yield readable output.
"""

from __future__ import annotations

from lxml import etree


def _local(tag) -> str:
    """Return the upper-cased local name of an element tag."""
    if not isinstance(tag, str):
        return ""
    return tag.split("}")[-1].upper()


class _Ctx:
    """Mutable rendering state carried through the walk."""

    def __init__(self):
        self.blocks: list[str] = []
        self.footnotes: list[tuple[str, str]] = []
        self._seen_note_ids: set[str] = set()
        self.pending: str = ""  # a number prefix waiting to attach to the next paragraph

    def add(self, text: str):
        text = text.strip("\n")
        if text.strip():
            self.blocks.append(text)

    def add_para(self, text: str):
        """Add a paragraph, consuming any pending number prefix (e.g. '1.')."""
        text = text.strip()
        if not text:
            return
        if self.pending:
            text = f"{self.pending}  {text}"
            self.pending = ""
        self.blocks.append(text)


# Elements that carry only bibliographic / technical metadata we skip.
_SKIP = {
    "BIB.INSTANCE", "BIB.DOC", "BIB.NOTICE", "BIB.DATA",
    "NCR", "NCR.NOTICE", "REFLEX", "OJ", "PUBLICATION.REF",
}


# The footnote handler needs access to the active context; keep a small stack.
_CURRENT_CTX: list["_Ctx"] = []


def convert_formex(xml_bytes: bytes) -> str:
    """Convert Formex XML bytes into a Markdown string."""
    ctx = _Ctx()
    _CURRENT_CTX.append(ctx)
    try:
        parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)
        root = etree.fromstring(xml_bytes, parser=parser)
        if root is None:
            raise ValueError("Kon de XML niet parsen.")

        _walk(root, ctx, level=0)
        body = "\n\n".join(ctx.blocks)

        if ctx.footnotes:
            notes = "\n".join(f"[^{nid}]: {txt}" for nid, txt in ctx.footnotes)
            body = f"{body}\n\n---\n\n{notes}"

        # Fallback: if structured parsing found almost nothing, dump plain text.
        if len(body.strip()) < 40:
            text = " ".join(t.strip() for t in root.itertext() if t.strip())
            if text:
                body = text

        return _tidy(body)
    finally:
        _CURRENT_CTX.pop()


# --------------------------------------------------------------------------
# Block-level walk
# --------------------------------------------------------------------------

def _walk(el, ctx: _Ctx, level: int):
    tag = _local(el.tag)
    handler = _HANDLERS.get(tag)
    if handler is not None:
        handler(el, ctx, level)
    elif tag in _SKIP:
        return
    else:
        # Generic container: recurse into children.
        for child in el:
            _walk(child, ctx, level)


def _walk_children(el, ctx: _Ctx, level: int):
    for child in el:
        _walk(child, ctx, level)


def _heading(el, ctx: _Ctx, level: int):
    """Document <TITLE>: main title (H1) plus optional subtitle."""
    ti = _first(el, "TI")
    sti = _first(el, "STI")
    title = _inline(ti) if ti is not None else _inline(el)
    if title.strip():
        ctx.add(f"# {title.strip()}")
    if sti is not None:
        sub = _inline(sti).strip()
        if sub:
            ctx.add(f"## {sub}")


def _division(el, ctx: _Ctx, level: int):
    """Chapters / sections / titles. Heading depth grows with nesting."""
    depth = min(level + 2, 6)
    for child in el:
        if _local(child.tag) == "TITLE":
            ti = _first(child, "TI")
            sti = _first(child, "STI")
            head = _inline(ti).strip() if ti is not None else _inline(child).strip()
            if sti is not None and _inline(sti).strip():
                head = f"{head} — {_inline(sti).strip()}" if head else _inline(sti).strip()
            if head:
                ctx.add(f"{'#' * depth} {head}")
        else:
            _walk(child, ctx, level + 1)


def _article(el, ctx: _Ctx, level: int):
    ti = _first(el, "TI.ART")
    sti = _first(el, "STI.ART")
    head = _inline(ti).strip() if ti is not None else "Artikel"
    if sti is not None and _inline(sti).strip():
        head = f"{head} — {_inline(sti).strip()}"
    ctx.add(f"### {head}")
    for child in el:
        if _local(child.tag) in ("TI.ART", "STI.ART"):
            continue
        _walk(child, ctx, level)


def _parag(el, ctx: _Ctx, level: int):
    """Numbered paragraph: <NO.PARAG> + one or more <ALINEA>/lists.

    The number is queued as a pending prefix so it attaches to the first
    text paragraph, while nested lists/tables still render underneath it.
    """
    no = _first(el, "NO.PARAG")
    if no is not None:
        ctx.pending = _inline(no).strip()
    for child in el:
        if _local(child.tag) == "NO.PARAG":
            continue
        _walk(child, ctx, level)
    # If a numbered paragraph had no plain-text lead-in, emit the number alone.
    if ctx.pending:
        ctx.add(ctx.pending)
        ctx.pending = ""


def _alinea(el, ctx: _Ctx, level: int):
    # An ALINEA may contain P's and/or lists/tables mixed together.
    if len(el) == 0:
        ctx.add_para(_inline(el))
        return
    for child in el:
        if _local(child.tag) == "P":
            ctx.add_para(_inline(child))
        else:
            _walk(child, ctx, level)


def _paragraph(el, ctx: _Ctx, level: int):
    ctx.add_para(_inline(el))


def _consid(el, ctx: _Ctx, level: int):
    """A recital: <NP><NO.P>(1)</NO.P><TXT>…</TXT></NP> or plain text."""
    np = _first(el, "NP")
    target = np if np is not None else el
    num = _inline(_first(target, "NO.P")).strip() if _first(target, "NO.P") is not None else ""
    txt_el = _first(target, "TXT")
    txt = _inline(txt_el).strip() if txt_el is not None else _inline(target).strip()
    line = f"{num} {txt}".strip()
    if line:
        ctx.add(line)


def _visa(el, ctx: _Ctx, level: int):
    txt = _inline(el).strip()
    if txt:
        ctx.add(txt)


def _list(el, ctx: _Ctx, level: int):
    """Render <LIST> as one compact, correctly-nested Markdown bullet list."""
    lines = _list_lines(el, 0)
    if lines:
        ctx.add("\n".join(lines))


def _list_lines(el, depth: int) -> list[str]:
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
            marker = _inline(no).strip() if no is not None else ""
            text = _inline(txt).strip() if txt is not None else _inline(np).strip()
        else:
            text = _inline(item).strip()
        line = f"{marker} {text}".strip()
        if line:
            lines.append(f"{indent}- {line}")
        # Extra paragraphs and nested lists inside the item.
        for sub in item:
            stag = _local(sub.tag)
            if stag == "LIST":
                lines.extend(_list_lines(sub, depth + 1))
            elif stag in ("P", "ALINEA") and sub is not np:
                cont = _inline(sub).strip()
                if cont:
                    lines.append(f"{indent}  {cont}")
    return lines


def _table(el, ctx: _Ctx, level: int):
    rows = []
    for row in el.iter():
        if _local(row.tag) != "ROW":
            continue
        cells = [_inline(c).strip().replace("\n", " ").replace("|", "\\|")
                 for c in row if _local(c.tag) == "CELL"]
        if cells:
            rows.append(cells)
    if not rows:
        return
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header = rows[0]
    md = ["| " + " | ".join(header) + " |",
          "| " + " | ".join(["---"] * width) + " |"]
    for r in rows[1:]:
        md.append("| " + " | ".join(r) + " |")
    ctx.add("\n".join(md))


def _annex(el, ctx: _Ctx, level: int):
    ti = _first(el, "TITLE")
    if ti is not None:
        head = _inline(ti).strip()
        if head:
            ctx.add(f"## {head}")
    for child in el:
        if _local(child.tag) == "TITLE":
            continue
        _walk(child, ctx, level)


def _signature(el, ctx: _Ctx, level: int):
    txt = _inline(el).strip()
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
# Inline rendering
# --------------------------------------------------------------------------

def _first(el, name: str):
    if el is None:
        return None
    for child in el:
        if _local(child.tag) == name:
            return child
    return None


def _inline(el) -> str:
    if el is None:
        return ""
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        parts.append(_render_inline(child))
        if child.tail:
            parts.append(child.tail)
    return _collapse_ws("".join(parts))


def _render_inline(el) -> str:
    tag = _local(el.tag)
    if tag == "HT":
        style = (el.get("TYPE") or "").upper()
        inner = _inline(el)
        if style in ("BOLD", "STRONG"):
            return f"**{inner}**" if inner.strip() else inner
        if style in ("ITALIC", "EMPHASIS"):
            return f"*{inner}*" if inner.strip() else inner
        return inner
    if tag in ("QUOT.START", "QUOT.END"):
        return '"'
    if tag == "NOTE":
        return _note(el)
    if tag == "BR":
        return " "
    # Default: render the element's textual content.
    return _inline(el)


def _note(el) -> str:
    """Turn a footnote into a Markdown footnote reference + collected note."""
    ctx = _CURRENT_CTX[-1] if _CURRENT_CTX else None
    nid = el.get("NOTE.ID") or el.get("ID") or f"n{len(ctx.footnotes) + 1 if ctx else 0}"
    text = " ".join(t.strip() for t in el.itertext() if t.strip())
    if ctx is not None and nid not in ctx._seen_note_ids:
        ctx._seen_note_ids.add(nid)
        ctx.footnotes.append((nid, text))
    return f"[^{nid}]"


# --------------------------------------------------------------------------
# Text utilities
# --------------------------------------------------------------------------

def _collapse_ws(s: str) -> str:
    return " ".join(s.split())


def _tidy(md: str) -> str:
    lines = md.split("\n")
    out = []
    blank = 0
    for line in lines:
        if line.strip() == "":
            blank += 1
            if blank <= 2:
                out.append("")
        else:
            blank = 0
            out.append(line.rstrip())
    return "\n".join(out).strip() + "\n"
