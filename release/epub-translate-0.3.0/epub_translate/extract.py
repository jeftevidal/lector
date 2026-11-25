"""Extract (SPEC §4.2): blocos → placeholders opacos ⟦n⟧ para tags inline.

Camada de DECISÃO: produz Segments sem tocar em nada (GR-ISOLATION).
Provado byte-fiel no Spike B (20/20 arquivos do piloto).
"""

from __future__ import annotations

import re

from lxml import etree

from .glossary import CLOSE, Glossary, OPEN
from .model import Segment, SKIPPED
from .xmlio import XmlDoc, inner_html, local, strip_ns_decl, unescape

TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)((?:[^>'\"]|'[^']*'|\"[^\"]*\")*?)(/?)>")
PLACEHOLDER_RE = re.compile(rf"{OPEN}/?\d+/?{CLOSE}")
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"


class ExtractError(Exception):
    pass


def protect(html: str) -> tuple[str, dict[str, str]]:
    """innerHTML → (texto com placeholders, mapa placeholder→tag original)."""
    mapping: dict[str, str] = {}
    counter = 0
    stack: list[int] = []
    out: list[str] = []
    pos = 0
    for m in TAG_RE.finditer(html):
        out.append(html[pos:m.start()])
        closing, _name, _attrs, selfclose = m.groups()
        if selfclose:
            counter += 1
            ph = f"{OPEN}{counter}/{CLOSE}"
        elif closing:
            if not stack:
                raise ExtractError(f"tag de fechamento sem abertura em: {html[:80]!r}")
            ph = f"{OPEN}/{stack.pop()}{CLOSE}"
        else:
            counter += 1
            stack.append(counter)
            ph = f"{OPEN}{counter}{CLOSE}"
        mapping[ph] = m.group(0)
        out.append(ph)
        pos = m.end()
    out.append(html[pos:])
    if stack:
        raise ExtractError(f"tags sem fechamento ({stack}) em: {html[:80]!r}")
    return "".join(out), mapping


def to_model_text(protected: str) -> str:
    """Fora dos placeholders o texto vai 'natural' pro modelo (entidades desfeitas)."""
    if OPEN not in protected:
        return unescape(protected)
    parts = PLACEHOLDER_RE.split(protected)
    marks = PLACEHOLDER_RE.findall(protected)
    out: list[str] = []
    for i, part in enumerate(parts):
        out.append(unescape(part))
        if i < len(marks):
            out.append(marks[i])
    return "".join(out)


def is_unit(el, block_tags: set[str]) -> bool:
    name = local(el)
    if name is None:
        return False
    if name in block_tags:
        return not any(local(d) in block_tags for d in el.iterdescendants())
    if name == "div" and el.text and el.text.strip():
        return not any(local(d) in block_tags for d in el.iterdescendants())
    return False


def _excluded(el, dont_descend: set[str]) -> bool:
    if el.get("translate") == "no":
        return True
    for a in el.iterancestors():
        if local(a) in dont_descend or a.get("translate") == "no":
            return True
    return False


def _has_translatable_text(model_text: str) -> bool:
    """Bloco só com número/pontuação/placeholder não vai pro modelo."""
    stripped = PLACEHOLDER_RE.sub("", model_text) if OPEN in model_text else model_text
    if re.fullmatch(r"\s*(https?://|www[.])\S+\s*", stripped):
        return False  # URL pura: não gasta cota
    return bool(re.search(r"[A-Za-zÀ-ÿ]", stripped))


def extract_doc(name: str, doc: XmlDoc, glossary: Glossary) -> list[Segment]:
    """Segments de um documento XHTML: blocos + atributos traduzíveis."""
    block_tags = set(glossary.block_tags)
    dont_descend = set(glossary.dont_descend)
    tree = doc.root.getroottree()
    segments: list[Segment] = []
    ordinal = 0

    for el in doc.root.iter():
        if not isinstance(el.tag, str):
            continue
        xpath = tree.getpath(el)
        # atributos traduzíveis (alt/title), inclusive em elementos não-bloco
        for attr in glossary.translatable_attrs:
            val = el.get(attr)
            if not val or not val.strip():
                continue
            if val.strip().lower() in [v.lower() for v in glossary.ignore_attr_values.get(attr, [])]:
                continue
            if _excluded(el, dont_descend):
                continue
            if not re.search(r"[A-Za-zÀ-ÿ]", val):
                continue
            ordinal += 1
            segments.append(Segment(
                seg_id=f"{name}#{xpath}#@{attr}",
                file=name, xpath=xpath, kind="attr",
                attr_name=attr, source_text=val,
            ))

        if not is_unit(el, block_tags) or _excluded(el, dont_descend):
            continue
        html = strip_ns_decl(inner_html(el))
        protected, mapping = protect(html)
        model_text = to_model_text(protected)
        ordinal += 1
        seg = Segment(
            seg_id=f"{name}#{xpath}#{ordinal}",
            file=name, xpath=xpath, kind="block",
            source_html=html, source_text=model_text, placeholders=mapping,
        )
        if not _has_translatable_text(model_text):
            seg.status = SKIPPED
        segments.append(seg)
    return segments


def extract_ncx(name: str, doc: XmlDoc) -> list[Segment]:
    """Rótulos de navegação do toc.ncx (navLabel/text + docTitle/text)."""
    segments: list[Segment] = []
    tree = doc.root.getroottree()
    for el in doc.root.iter(f"{{{NCX_NS}}}text"):
        if not el.text or not el.text.strip():
            continue
        xpath = tree.getpath(el)
        segments.append(Segment(
            seg_id=f"{name}#{xpath}#ncx",
            file=name, xpath=xpath, kind="ncx-label",
            source_text=el.text,
        ))
    return segments
