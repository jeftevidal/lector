"""Reinsert (SPEC §4.4): restaura placeholders → tags e devolve ao DOM.

Validação de placeholders é cidadã de primeira classe: qualquer marcador
perdido/inventado ⇒ segmento vira ERROR e o bloco mantém o original.
"""

from __future__ import annotations

from lxml import etree

from .extract import PLACEHOLDER_RE
from .model import ERROR, Segment, TRANSLATED, UNCERTAIN
from .xmlio import XmlDoc, esc, set_inner_html


class PlaceholderError(Exception):
    pass


def validate_placeholders(source_text: str, target_text: str) -> None:
    """Todo ⟦n⟧ da entrada aparece na saída — nem a mais, nem a menos."""
    src = sorted(PLACEHOLDER_RE.findall(source_text))
    tgt = sorted(PLACEHOLDER_RE.findall(target_text))
    if src != tgt:
        missing = [p for p in src if p not in tgt]
        extra = [p for p in tgt if p not in src]
        raise PlaceholderError(
            f"placeholders divergem — faltando: {missing or '-'} | sobrando: {extra or '-'}"
        )


def restore(target_text: str, mapping: dict[str, str]) -> str:
    """Texto do modelo (com placeholders) → innerHTML com as tags originais."""
    parts = PLACEHOLDER_RE.split(target_text)
    marks = PLACEHOLDER_RE.findall(target_text)
    out: list[str] = []
    for i, part in enumerate(parts):
        out.append(esc(part))
        if i < len(marks):
            if marks[i] not in mapping:
                raise PlaceholderError(f"placeholder inventado pelo modelo: {marks[i]}")
            out.append(mapping[marks[i]])
    return "".join(out)


def apply_segment(doc: XmlDoc, seg: Segment) -> bool:
    """Aplica a tradução de um segmento no DOM. False = manteve original (erro)."""
    if seg.status not in (TRANSLATED, UNCERTAIN, "cached"):
        return False
    els = doc.root.getroottree().xpath(seg.xpath)
    if not els:
        seg.status = ERROR
        seg.note = f"xpath não encontrado: {seg.xpath}"
        return False
    el = els[0]

    if seg.kind == "attr":
        el.set(seg.attr_name, seg.target_text)
        return True
    if seg.kind == "ncx-label":
        el.text = seg.target_text
        return True

    try:
        validate_placeholders(seg.source_text, seg.target_text)
        html = restore(seg.target_text, seg.placeholders)
        set_inner_html(el, html)
        return True
    except (PlaceholderError, etree.XMLSyntaxError) as e:
        seg.status = ERROR
        seg.note = str(e)
        return False
