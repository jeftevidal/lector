"""QA gates (SPEC §4.6): rodados ANTES do repack. BLOCK impede empacotar."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO

from lxml import etree

from .glossary import OPEN
from .model import QAFinding

STRAY_AMP_RE = re.compile(rb"&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")
TAG_RE = re.compile(rb"<[^>]+>")


def tag_count(raw: bytes) -> int:
    """Conta marcações tratando <x/> como abrir+fechar: reserializar <a id=\"p1\"></a> como <a id=\"p1\"/> não é perda de tag."""
    return sum(2 if tag.endswith(b"/>") else 1 for tag in TAG_RE.findall(raw))
IMG_SRC_RE = re.compile(rb'src="([^"]+)"')
# palavras funcionais EN — smoke detector de resíduo, não prova
EN_RESIDUE_RE = re.compile(rb"\b(the|and|with|from|which|because|should|would)\b", re.I)


def run_gates(
    original: dict[str, bytes],
    translated: dict[str, bytes],
    content_docs: list[str],
    skip_files: list[str] | None = None,
    residue_threshold: float = 0.02,
) -> list[QAFinding]:
    """Compara original × traduzido, arquivo a arquivo. Retorna achados."""
    findings: list[QAFinding] = []
    skip = set(skip_files or [])

    if set(original) != set(translated):
        only_o = set(original) - set(translated)
        only_t = set(translated) - set(original)
        findings.append(QAFinding(
            "BLOCK", "conjunto-de-arquivos", "(zip)",
            f"só no original: {sorted(only_o)[:5]}; só na tradução: {sorted(only_t)[:5]}",
        ))

    for name in content_docs:
        orig = original.get(name)
        trad = translated.get(name)
        if orig is None or trad is None:
            continue

        m = STRAY_AMP_RE.search(trad)
        if m:
            ctx = trad[max(0, m.start() - 30): m.start() + 30]
            findings.append(QAFinding("BLOCK", "amp-solto", name, f"...{ctx!r}..."))

        try:
            etree.fromstring(trad, etree.XMLParser(resolve_entities=False))
        except etree.XMLSyntaxError as e:
            findings.append(QAFinding("BLOCK", "xml-bem-formado", name, str(e)[:200]))
            continue  # os demais checks pressupõem XML são

        n_orig, n_trad = tag_count(orig), tag_count(trad)
        if n_orig != n_trad:
            findings.append(QAFinding(
                "BLOCK", "paridade-de-tags", name, f"original={n_orig} traduzido={n_trad}",
            ))

        src_o = set(IMG_SRC_RE.findall(orig))
        src_t = set(IMG_SRC_RE.findall(trad))
        if src_o != src_t:
            findings.append(QAFinding(
                "BLOCK", "src-de-imagens", name, f"diferença: {sorted(src_o ^ src_t)[:5]}",
            ))

        if OPEN.encode("utf-8") in trad:
            findings.append(QAFinding("BLOCK", "placeholder-residual", name, "⟦ encontrado na saída"))

        base = name.rsplit("/", 1)[-1]
        if base not in skip:
            text = TAG_RE.sub(b" ", trad)
            words = max(1, len(text.split()))
            hits = len(EN_RESIDUE_RE.findall(text))
            ratio = hits / words
            if ratio > residue_threshold:
                findings.append(QAFinding(
                    "WARN", "residuo-idioma-fonte", name,
                    f"{ratio*100:.1f}% de palavras funcionais EN ({hits} hits)",
                ))
    return findings


def check_packed(epub_bytes: bytes) -> list[QAFinding]:
    """Gates do container: mimetype STORED e 1ª entrada."""
    findings: list[QAFinding] = []
    zf = zipfile.ZipFile(BytesIO(epub_bytes))
    infos = zf.infolist()
    if not infos or infos[0].filename != "mimetype":
        findings.append(QAFinding("BLOCK", "mimetype-primeiro", "(zip)",
                                  f"1ª entrada é {infos[0].filename if infos else '(vazio)'}"))
    elif infos[0].compress_type != zipfile.ZIP_STORED:
        findings.append(QAFinding("BLOCK", "mimetype-stored", "(zip)", "mimetype não é STORED"))
    return findings


def has_block(findings: list[QAFinding]) -> bool:
    return any(f.severity == "BLOCK" for f in findings)
