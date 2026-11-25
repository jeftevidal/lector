"""Orquestração do pipeline. Decisão × efeito separados (GR-ISOLATION):

- plan()  — parseia e extrai TUDO sem efeito colateral; é o --dry-run impresso.
- apply() — o único caminho com efeito: chama o motor, grava cache, monta bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
import re

from lxml import etree

from .cache import TMCache
from .extract import extract_doc, extract_ncx
from .glossary import Glossary
from .ingest import Book
from .model import CACHED, PENDING, QAFinding, Segment, SKIPPED, TRANSLATED, UNCERTAIN
from .postproc import set_doc_language, set_opf_language
from .qa import check_packed, has_block, run_gates
from .repack import pack
from .report import RunStats
from .verify import check_translation, review_uncertain
from .translate import BATCH_MAX_SEGMENTS, BATCH_MAX_WORDS, Engine, translate_all
from .xmlio import XmlDoc


@dataclass
class Plan:
    book: Book
    glossary: Glossary
    docs: dict[str, XmlDoc] = field(default_factory=dict)      # docs XHTML traduzíveis
    ncx_doc: XmlDoc | None = None
    opf_doc: XmlDoc | None = None
    segments: list[Segment] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def pending(self) -> list[Segment]:
        return [s for s in self.segments if s.status == PENDING]

    def estimate_calls(self, batch_segments: int = BATCH_MAX_SEGMENTS,
                       batch_words: int = BATCH_MAX_WORDS) -> int:
        from .translate import make_batches
        seen: set[str] = set()  # repetidos vão ao modelo uma vez só
        unique = [s for s in self.pending if not (s.source_text in seen or seen.add(s.source_text))]
        return len(make_batches(unique, batch_segments, batch_words))


def build_plan(book: Book, glossary: Glossary, only: str | None = None) -> Plan:
    plan = Plan(book=book, glossary=glossary)
    skip = set(glossary.skip_files)
    non_narrative = re.compile(r"(?<![a-z])(?:cover|title[-_ ]?page|halftitle|half[-_ ]title|copyright|imprint|dedication|epigraph|colophon|ack(?:nowledg\w*)?|glossary|bibliography|index|about[-_ ]author)(?![a-z])", re.I)

    for name in book.content_docs:
        base = name.rsplit("/", 1)[-1]
        semantic = book.guide_refs.get(name, "") + " " + book.spine_types.get(name, "")
        auxiliary = name in book.spine and (bool(non_narrative.search(semantic)) or bool(non_narrative.search(base)))
        if base in skip or name in skip or (glossary.main_content_only and auxiliary and name != book.nav_path):
            plan.skipped_files.append(name)
            continue
        if only and base != only and name != only:
            continue
        try:
            doc = XmlDoc(book.files[name])
        except Exception as e:  # XHTML podre na entrada: pula com WARN (SPEC §10)
            plan.warnings.append(f"{name}: não parseou ({str(e)[:120]}) — pulado")
            continue
        if glossary.main_content_only and name in book.spine:
            epub_type = " ".join(
                el.get("{http://www.idpf.org/2007/ops}type", "")
                for el in doc.root.iter() if isinstance(el.tag, str)
            )
            if non_narrative.search(epub_type):
                plan.skipped_files.append(name)
                continue
        if doc.entity_warnings:
            plan.warnings.append(
                f"{name}: entidades nomeadas convertidas p/ numéricas: {doc.entity_warnings}"
            )
        plan.docs[name] = doc
        plan.segments.extend(extract_doc(name, doc, glossary))

    if book.ncx_path and book.ncx_path in book.files and not only:
        plan.ncx_doc = XmlDoc(book.files[book.ncx_path])
        plan.segments.extend(extract_ncx(book.ncx_path, plan.ncx_doc))

    if book.opf_path in book.files:
        try:
            plan.opf_doc = XmlDoc(book.files[book.opf_path])
        except etree.XMLSyntaxError as e:
            plan.warnings.append(f"{book.opf_path}: OPF fora do padrão XML ({str(e)[:120]}) — mantido sem alterar")

    return plan


@dataclass
class RunResult:
    files: dict[str, bytes]
    findings: list[QAFinding]
    stats: RunStats
    epub_bytes: bytes | None = None    # None = QA BLOCK segurou o empacotamento
    cancelled: bool = False


def apply_plan(
    plan: Plan,
    engine: Engine,
    tm: TMCache | None,
    log=lambda msg: None,
    progress: Callable[[dict], None] | None = None,
    reuse_cache: bool = False,
    is_cancelled: Callable[[], bool] | None = None,
    review: bool = False,
    workers: int = 1,
    only_files: set[str] | None = None,
) -> RunResult:
    from .reinsert import apply_segment

    book, glossary = plan.book, plan.glossary

    # 1. ÚNICO ponto semântico + cache (efeito)
    if progress:
        progress({"event": "translation_started", "total_segments": len(plan.pending)})
    counts = translate_all(plan.segments, engine, glossary, tm, log=log,
                           progress=progress, reuse_cache=reuse_cache, is_cancelled=is_cancelled,
                           workers=workers, only_files=only_files)
    if is_cancelled and is_cancelled():
        stats = RunStats(input_path=str(book.path), target_lang=glossary.target_lang,
                         engine=engine.name, segments_total=len(plan.segments), counts=counts)
        return RunResult(files=dict(book.files), findings=[], stats=stats, epub_bytes=None, cancelled=True)

    # 1b. verificação por script (glossário, números) e revisão opcional dos incertos pelo modelo
    verify_findings = check_translation(plan.segments, glossary)
    if review and any(s.status == UNCERTAIN for s in plan.segments):
        review_uncertain(plan.segments, engine, glossary, tm, progress, is_cancelled)
        verify_findings = check_translation(plan.segments, glossary)
        if is_cancelled and is_cancelled():
            stats = RunStats(input_path=str(book.path), target_lang=glossary.target_lang,
                             engine=engine.name, segments_total=len(plan.segments), counts=counts)
            return RunResult(files=dict(book.files), findings=[], stats=stats, epub_bytes=None, cancelled=True)

    # 2. reinserção (memória, determinístico)
    for seg in plan.segments:
        if seg.status not in (TRANSLATED, UNCERTAIN, CACHED):
            continue
        doc = plan.docs.get(seg.file)
        if doc is None and plan.ncx_doc is not None and seg.file == book.ncx_path:
            doc = plan.ncx_doc
        if doc is not None:
            apply_segment(doc, seg)

    # 3. postproc: idioma
    for doc in plan.docs.values():
        set_doc_language(doc, glossary.target_lang)
    if plan.opf_doc is not None:
        set_opf_language(plan.opf_doc, glossary.target_lang)

    # 4. serializar por cima do conjunto original
    out_files = dict(book.files)
    for name, doc in plan.docs.items():
        out_files[name] = doc.serialize()
    if plan.ncx_doc is not None:
        out_files[book.ncx_path] = plan.ncx_doc.serialize()
    if plan.opf_doc is not None:
        out_files[book.opf_path] = plan.opf_doc.serialize()

    # 5. QA gates (original × traduzido)
    qa_docs = list(plan.docs)
    if plan.ncx_doc is not None:
        qa_docs.append(book.ncx_path)
    findings = run_gates(book.files, out_files, qa_docs, glossary.skip_files)
    findings.extend(verify_findings)
    if progress:
        progress({"event": "qa_completed", "blocks": sum(f.severity == "BLOCK" for f in findings),
                  "warnings": sum(f.severity == "WARN" for f in findings)})

    stats = RunStats(
        input_path=str(book.path),
        target_lang=glossary.target_lang,
        engine=engine.name,
        files_translated=len(plan.docs),
        segments_total=len(plan.segments),
        words_total=sum(s.word_count for s in plan.segments),
        counts=counts,
        cache_hits=counts.get(CACHED, 0),
        engine_calls=getattr(engine, "calls", 0),
        engine_seconds=getattr(engine, "total_seconds", 0.0),
    )

    # 6. repack só sem BLOCK (RNF1)
    epub_bytes = None
    if not has_block(findings):
        epub_bytes = pack(out_files)
        findings.extend(check_packed(epub_bytes))
        if has_block(findings):
            epub_bytes = None
    stats.packed = epub_bytes is not None
    if progress:
        progress({"event": "pipeline_finished", "packed": stats.packed, "counts": counts})

    return RunResult(files=out_files, findings=findings, stats=stats, epub_bytes=epub_bytes)
