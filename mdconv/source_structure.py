"""Capture source bytes and, for HTML, table coordinates before rendering.

This is provenance, not a signature or an independent verification claim.
The consuming kb rebuilds the structure from the original bytes with its own parser.
"""
from __future__ import annotations

import base64
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar

_documents = ContextVar("source_structure_documents", default=None)


def sha256(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


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


def record_source(data, *, media_type, source_format, source_url,
                  identifier=None, language=None, role="document"):
    documents = _documents.get()
    if documents is None:
        return
    if source_format == "html":
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        from bs4 import BeautifulSoup
        evidence = source_structure(data, BeautifulSoup(data, "lxml"), selector="document")
    else:
        raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        evidence = {
            "schema_version": 1,
            "format": "binary-source",
            "source_format": source_format,
            "media_type": media_type,
            "source_sha256": sha256(raw),
            # JSON houdt de bytes exact vast. Opdracht 2 verhuist deze payload
            # naar een server-side bundeltoken; tot die tijd blijft hij alleen
            # in de afgeschermde herkomst en gaat hij niet door de browser.
            "original_base64": base64.b64encode(raw).decode("ascii"),
        }
    evidence.update(source_url=source_url, identifier=identifier, language=language, role=role)
    documents.append(evidence)


def record_html(html, *, source_url, identifier=None, language=None, role="document"):
    """Compatibele HTML-ingang; de bestaande capturevorm blijft bytegelijk."""
    record_source(html, media_type="text/html", source_format="html",
                  source_url=source_url, identifier=identifier,
                  language=language, role=role)


def bind_structure(provenance, markdown, documents):
    from .version import _fingerprint
    extra = dict(provenance.extra)
    extra["source_structure"] = {"schema_version": 1, "markdown_sha256": sha256(markdown),
                                 "converter_fingerprint": _fingerprint(), "sources": documents}
    return provenance.met(extra=extra)
