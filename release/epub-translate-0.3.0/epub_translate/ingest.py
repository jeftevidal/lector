"""Ingest (SPEC §4.1): abre o EPUB, valida, resolve OPF/spine/nav.

Camada de DECISÃO: só lê — nunca escreve nada (GR-ISOLATION).
"""

from __future__ import annotations

import os
import posixpath
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"


MAX_UNCOMPRESSED = 2 * 1024 ** 3  # teto de memória contra zip bomb


class IngestError(Exception):
    pass


@dataclass
class Book:
    path: Path
    files: dict[str, bytes]              # nome-no-zip → bytes (ordem original)
    opf_path: str
    epub_version: str                    # "2.0" | "3.0"...
    spine: list[str] = field(default_factory=list)   # docs XHTML na ordem de leitura
    ncx_path: str | None = None
    nav_path: str | None = None          # EPUB 3 nav doc
    language: str = ""
    guide_refs: dict[str, str] = field(default_factory=dict)
    spine_types: dict[str, str] = field(default_factory=dict)

    @property
    def content_docs(self) -> list[str]:
        """Documentos traduzíveis: spine + nav doc (se fora do spine)."""
        docs = list(self.spine)
        if self.nav_path and self.nav_path not in docs:
            docs.append(self.nav_path)
        return docs


PREFIX = chr(92) * 2 + "?" + chr(92)  # \?\ (prefixo de caminho longo do Windows)


def _long_path(path: Path) -> Path:
    """Windows: caminhos >= 255 caracteres exigem o prefixo de caminho longo (após resolve)."""
    if os.name != "nt":
        return path
    full = str(path.resolve())
    if len(full) >= 255 and not full.startswith(PREFIX):
        unc = chr(92) * 2
        full = PREFIX + "UNC" + chr(92) + full[2:] if full.startswith(unc) else PREFIX + full
    return Path(full)


def _parse_opf(raw: bytes):
    """Parse estrito; se houver prefixo não declarado (ex.: dc: sem xmlns), recupera."""
    try:
        return etree.fromstring(raw)
    except etree.XMLSyntaxError:
        root = etree.fromstring(raw, etree.XMLParser(recover=True))
        if root is None:
            raise IngestError("OPF ilegível") from None
        return root


def load_book(path: str | Path) -> Book:
    path = Path(path)
    open_path = _long_path(path)
    if not open_path.is_file():
        raise IngestError(f"arquivo não existe: {path}")
    try:
        zf = zipfile.ZipFile(open_path)
    except zipfile.BadZipFile as e:
        raise IngestError(f"não é um zip válido: {e}") from e

    if sum(info.file_size for info in zf.infolist()) > MAX_UNCOMPRESSED:
        raise IngestError("EPUB descompactado grande demais")
    names = zf.namelist()
    try:
        files = {n: zf.read(n) for n in names}
    except (RuntimeError, NotImplementedError, zipfile.BadZipFile, EOFError) as e:
        raise IngestError(f"EPUB protegido por senha ou corrompido: {e}") from e

    mimetype = files.get("mimetype", b"").strip()
    if mimetype != b"application/epub+zip":
        raise IngestError(f"mimetype inválido: {mimetype!r} (esperado application/epub+zip)")

    container = files.get("META-INF/container.xml")
    if container is None:
        raise IngestError("META-INF/container.xml ausente")
    croot = etree.fromstring(container)
    rootfile = croot.find(f".//{{{CONTAINER_NS}}}rootfile")
    if rootfile is None:
        raise IngestError("rootfile não declarado no container.xml")
    opf_path = rootfile.get("full-path")
    if opf_path not in files:
        raise IngestError(f"OPF declarado não existe no zip: {opf_path}")

    opf = _parse_opf(files[opf_path])
    version = opf.get("version", "2.0")
    opf_dir = posixpath.dirname(opf_path)

    def resolve(href: str) -> str:
        return posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href

    manifest: dict[str, tuple[str, str, str]] = {}  # id → (href, media-type, properties)
    for item in opf.findall(f".//{{{OPF_NS}}}manifest/{{{OPF_NS}}}item"):
        manifest[item.get("id")] = (
            resolve(item.get("href", "")),
            item.get("media-type", ""),
            item.get("properties", "") or "",
        )

    spine_el = opf.find(f".//{{{OPF_NS}}}spine")
    spine: list[str] = []
    spine_types: dict[str, str] = {}
    if spine_el is not None:
        for ref in spine_el.findall(f"{{{OPF_NS}}}itemref"):
            entry = manifest.get(ref.get("idref"))
            if entry and entry[0] in files:
                spine.append(entry[0])
                spine_types[entry[0]] = ref.get("properties", "") or ""

    ncx_path = None
    toc_id = spine_el.get("toc") if spine_el is not None else None
    if toc_id and toc_id in manifest:
        ncx_path = manifest[toc_id][0]
    if ncx_path is None:
        for href, mt, _props in manifest.values():
            if mt == "application/x-dtbncx+xml" and href in files:
                ncx_path = href
                break

    nav_path = None
    guide_refs: dict[str, str] = {}
    guide = opf.find(f"{{{OPF_NS}}}guide")
    if guide is not None:
        for reference in guide.findall(f"{{{OPF_NS}}}reference"):
            href = reference.get("href", "").split("#", 1)[0]
            guide_refs[resolve(href)] = (reference.get("type", "") + " " + reference.get("title", "")).lower()
    for href, _mt, props in manifest.values():
        if "nav" in props.split() and href in files:
            nav_path = href
            break

    lang_el = opf.find(f".//{{{DC_NS}}}language")
    language = (lang_el.text or "").strip() if lang_el is not None else ""

    return Book(
        path=path, files=files, opf_path=opf_path, epub_version=version,
        spine=spine, ncx_path=ncx_path, nav_path=nav_path, language=language,
        guide_refs=guide_refs, spine_types=spine_types,
    )
