"""Postproc (SPEC §4.5): idioma nos metadados e nos documentos.

A navegação (toc.ncx/nav) NÃO é remendada aqui: os rótulos são extraídos como
segmentos e traduzidos pelo mesmo motor (fix na origem, não a jusante).
"""

from __future__ import annotations

from lxml import etree

from .ingest import DC_NS
from .xmlio import XmlDoc


def set_doc_language(doc: XmlDoc, lang: str) -> None:
    """xml:lang / lang no elemento raiz de um XHTML."""
    root = doc.root
    if root.get("{http://www.w3.org/XML/1998/namespace}lang"):
        root.set("{http://www.w3.org/XML/1998/namespace}lang", lang)
    if root.get("lang"):
        root.set("lang", lang)


def set_opf_language(doc: XmlDoc, lang: str) -> None:
    """dc:language no OPF."""
    for el in doc.root.iter(f"{{{DC_NS}}}language"):
        el.text = lang
