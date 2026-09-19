"""Capture source HTML and table coordinates before a renderer mutates them.

This is provenance, not a signature or an independent verification claim.
The consuming kb rebuilds the grid from original_html with its own parser.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar

_documents = ContextVar("source_structure_documents", default=None)


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_structure(html, container, *, selector, anchor=None):
    tables = []
    for index, table in enumerate(container.find_all("table")):
        rows = [r for r in table.find_all("tr") if r.find_parent("table") is table]
        cells = []
        for row_index, row in enumerate(rows):
            for cell_index, cell in enumerate(row.find_all(["td", "th"], recursive=False)):
                cells.append({"source_row": row_index, "source_cell": cell_index,
                              "rowspan": str(cell.get("rowspan", "1")),
                              "colspan": str(cell.get("colspan", "1")),
                              "text": cell.get_text(" ", strip=True)})
        tables.append({"source_table": index, "row_count": len(rows), "cells": cells})
    return {"schema_version": 1, "format": "html-source-structure",
            "source_sha256": sha256(html), "original_html": html,
            "selection": {"selector": selector, "anchor": anchor}, "tables": tables}


@contextmanager
def capture_source_documents():
    """Each conversion owns its list, including concurrent and nested calls."""
    documents = []
    token = _documents.set(documents)
    try:
        yield documents
    finally:
        _documents.reset(token)


def record_html(html, *, source_url, identifier=None, language=None, role="document"):
    documents = _documents.get()
    if documents is None:
        return
    from bs4 import BeautifulSoup
    evidence = source_structure(html, BeautifulSoup(html, "lxml"), selector="document")
    evidence.update(source_url=source_url, identifier=identifier, language=language, role=role)
    documents.append(evidence)


def bind_structure(provenance, markdown, documents):
    from .version import _fingerprint
    extra = dict(provenance.extra)
    extra["source_structure"] = {"schema_version": 1, "markdown_sha256": sha256(markdown),
                                 "converter_fingerprint": _fingerprint(), "sources": documents}
    return provenance.met(extra=extra)
