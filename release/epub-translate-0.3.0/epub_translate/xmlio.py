"""Leitura/serialização XML byte-fiel.

Provado no Spike B (20/20 arquivos do livro-piloto byte-idênticos):
- o parser XML normaliza CRLF→LF; na escrita restauramos CRLF se a fonte usava;
- o serializador do lxml escapa & < > em texto — esc()/unescape() espelham isso;
- o prólogo (xml decl, doctype, comentários antes da raiz) é capturado como
  bytes crus e re-prefixado na saída, porque tostring(element) o descarta.
"""

from __future__ import annotations

import re

from lxml import etree

XHTML_NS = "http://www.w3.org/1999/xhtml"

# entidades HTML nomeadas fora do XML básico (ex.: &nbsp;) num arquivo SEM DTD
# interno quebram o parser; convertemos para referência numérica (mesmo render).
_XML_BUILTIN = {"amp", "lt", "gt", "quot", "apos"}
_NAMED_ENTITY_RE = re.compile(r"&([a-zA-Z][a-zA-Z0-9]*);")


def _numeric_entities(raw: bytes) -> tuple[bytes, list[str]]:
    """Converte entidades nomeadas não-XML em numéricas. Retorna (bytes, avisos)."""
    import html.entities as he

    converted: list[str] = []

    def sub(m: re.Match[bytes]) -> bytes:
        name = m.group(1).decode("ascii")
        if name in _XML_BUILTIN:
            return m.group(0)
        cp = he.name2codepoint.get(name)
        if cp is None:
            return m.group(0)  # desconhecida: deixa o parser reclamar
        converted.append(name)
        return f"&#{cp};".encode("ascii")

    pattern = re.compile(rb"&([a-zA-Z][a-zA-Z0-9]*);")
    out = pattern.sub(sub, raw)
    return out, sorted(set(converted))


class XmlDoc:
    """Um arquivo XML/XHTML parseado com tudo que é preciso para reescrevê-lo fiel."""

    def __init__(self, raw: bytes):
        self.raw = raw
        self.crlf = b"\r\n" in raw
        self.entity_warnings: list[str] = []

        body = raw
        parser = etree.XMLParser(resolve_entities=False, strip_cdata=False)
        try:
            self.root = etree.fromstring(body, parser)
        except etree.XMLSyntaxError as e:
            if "Entity" in str(e):
                body, self.entity_warnings = _numeric_entities(raw)
                self.root = etree.fromstring(body, parser)
            else:
                raise

        self.prologue = body[: _root_start(body)]

    def serialize(self) -> bytes:
        out = etree.tostring(self.root, encoding="unicode")
        if self.crlf:
            out = out.replace("\r\n", "\n").replace("\n", "\r\n")
        return self.prologue + out.encode("utf-8")


def _root_start(raw: bytes) -> int:
    """Offset do elemento-raiz: pula xml decl, doctype, comentários e espaço."""
    i = 0
    n = len(raw)
    while i < n:
        m = re.compile(rb"\S").search(raw, i)
        if m is None:
            break
        i = m.start()
        if raw.startswith(b"<?", i):
            i = raw.index(b"?>", i) + 2
        elif raw.startswith(b"<!--", i):
            i = raw.index(b"-->", i) + 3
        elif raw.startswith(b"<!", i):  # DOCTYPE (pode ter subset interno [..])
            depth = 0
            j = i
            while j < n:
                c = raw[j : j + 1]
                if c == b"[":
                    depth += 1
                elif c == b"]":
                    depth -= 1
                elif c == b">" and depth <= 0:
                    break
                j += 1
            i = j + 1
        else:
            return i
    return i


def local(el) -> str | None:
    """Nome local da tag (sem namespace); None p/ comentários/PIs."""
    return etree.QName(el).localname if isinstance(el.tag, str) else None


def esc(s: str) -> str:
    """Escape de TEXTO idêntico ao do serializador do lxml."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def unescape(s: str) -> str:
    """Inverso exato de esc() — a entrada vem sempre de serialização lxml."""
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def inner_html(el) -> str:
    """innerHTML do elemento, com texto escapado na convenção do lxml."""
    parts = [esc(el.text or "")]
    for child in el:
        parts.append(etree.tostring(child, encoding="unicode", with_tail=True))
    return "".join(parts)


def strip_ns_decl(s: str) -> str:
    return s.replace(f' xmlns="{XHTML_NS}"', "")


def set_inner_html(el, html: str) -> None:
    ns = etree.QName(el).namespace if isinstance(el.tag, str) else None
    nsdecl = f' xmlns="{ns}"' if ns else ""
    wrapper = etree.fromstring(f"<w{nsdecl}>{html}</w>")
    for child in list(el):
        el.remove(child)
    el.text = wrapper.text
    for child in wrapper:
        el.append(child)
