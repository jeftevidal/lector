"""Verificação pós-tradução: glossário e números por script (sem cota) + revisão opcional por modelo.

Só gera avisos (WARN): nunca bloqueia o empacotamento. Segmentos suspeitos viram UNCERTAIN
para aparecerem na tela de Revisão; a revisão por modelo roda só neles.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

from .cache import TMCache, cache_key
from .extract import PLACEHOLDER_RE
from .glossary import Glossary, OPEN
from .model import CACHED, PENDING, QAFinding, Segment, TRANSLATED, UNCERTAIN
from .translate import (Engine, QuotaExhaustedError, TransientEngineError, _validate_batch,
                        make_batches)

NUMBER_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)*")
QA_NOTE = "QA: "
REVIEWED_NOTE = "revisado pelo modelo"
REVIEW_LIMIT = 300


def _numbers(text: str) -> Counter:
    """Números com 2+ dígitos, sem separadores (1,420 e 1.420 contam igual)."""
    clean = PLACEHOLDER_RE.sub(" ", text) if OPEN in text else text
    digits = (re.sub(r"[.,]", "", m) for m in NUMBER_RE.findall(clean))
    return Counter(d for d in digits if len(d) >= 2)


def _has_term(term: str, text: str) -> bool:
    if term.lower() not in text.lower():
        return False
    return bool(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I))


def _issues(seg: Segment, glossary: Glossary) -> list[tuple[str, str]]:
    src, tgt = seg.source_text, seg.target_text
    found: list[tuple[str, str]] = []
    missing = _numbers(src) - _numbers(tgt)
    for number in missing:
        found.append(("numero-divergente", f'Número divergente: "{number}" ausente na tradução'))
    for term, wanted in glossary.translate_map.items():
        if term and wanted and _has_term(term, src) and wanted.lower() not in tgt.lower():
            found.append(("glossario-nao-aplicado", f"Termo do glossário não aplicado: {term} → {wanted}"))
    for term in [*glossary.keep_in_source, *glossary.never_translate]:
        if term and _has_term(term, src) and term.lower() not in tgt.lower():
            found.append(("termo-preservado-alterado", f'Termo que deveria ficar no original foi alterado: "{term}"'))
    return found


def check_translation(segments: list[Segment], glossary: Glossary) -> list[QAFinding]:
    """Avisos de glossário/números. Marca como UNCERTAIN o que divergiu (idempotente)."""
    findings: list[QAFinding] = []
    for index, seg in enumerate(segments, 1):
        if seg.status not in (TRANSLATED, UNCERTAIN, CACHED) or not seg.target_text:
            continue
        issues = _issues(seg, glossary)
        if not issues:
            if seg.status == UNCERTAIN and seg.note.startswith(QA_NOTE):
                seg.status, seg.note = TRANSLATED, ""
            continue
        for check, detail in issues:
            findings.append(QAFinding("WARN", check, seg.file, f"seg {index}: {detail}"))
        seg.status = UNCERTAIN
        if not seg.note.startswith(QA_NOTE):
            seg.note = QA_NOTE + issues[0][1]
    return findings


class _ReviewGlossary:
    """Mesmo glossário, com instruções de revisão e as traduções anteriores no prompt."""

    def __init__(self, base: Glossary, prior: dict[str, str]):
        self._base = base
        self._prior = prior

    def __getattr__(self, name: str):
        return getattr(self._base, name)

    def system_prompt(self) -> str:
        return (self._base.system_prompt()
                + "\n\nMODO REVISÃO: cada segmento já tem uma tradução anterior suspeita (JSON abaixo, por id). "
                  "Compare com o original, corrija erros, aplique o glossário e preserve números e placeholders. "
                  "Se a anterior já estiver correta, devolva-a igual.\nTraduções anteriores: "
                + json.dumps(self._prior, ensure_ascii=False))


def review_uncertain(segments: list[Segment], engine: Engine, glossary: Glossary, tm: TMCache | None,
                     progress: Callable[[dict], None] | None = None,
                     is_cancelled: Callable[[], bool] | None = None, limit: int = REVIEW_LIMIT) -> dict[str, int]:
    """Segunda passada do modelo só nos segmentos incertos. Melhor esforço: falhas não derrubam o job."""
    def emit(event: str, **data) -> None:
        if progress:
            progress({"event": event, "ts": datetime.now(timezone.utc).isoformat(), **data})

    targets = [s for s in segments if s.status == UNCERTAIN and s.target_text][:limit]
    if not targets:
        return {"sent": 0, "changed": 0}
    by_id = {s.seg_id: s for s in targets}
    prior = {s.seg_id: s.target_text for s in targets}
    clones = [replace(s, status=PENDING, target_text="") for s in targets]
    emit("review_started", count=len(targets))
    changed = 0
    batches = make_batches(clones, max(1, int(getattr(engine, "batch_segments", 60) * 0.75)),
                           max(1, int(getattr(engine, "batch_words", 2000) * 0.75)))
    for batch in batches:
        if is_cancelled and is_cancelled():
            break
        batch_prior = {s.seg_id: prior[s.seg_id] for s in batch.segments}
        try:
            results = engine.translate(batch.segments, _ReviewGlossary(glossary, batch_prior))
        except (QuotaExhaustedError, TransientEngineError) as exc:
            emit("review_skipped", message=str(exc)[:200])
            return {"sent": len(targets), "changed": changed}
        bad = _validate_batch(batch.segments, results)
        for result in results:
            seg = by_id.get(result.seg_id)
            if seg is None or result.seg_id in bad:
                continue
            if result.text != seg.target_text:
                changed += 1
                seg.target_text = result.text
            seg.status = UNCERTAIN if result.uncertain else TRANSLATED
            seg.note = "modelo marcou uncertain" if result.uncertain else REVIEWED_NOTE
            if tm is not None:
                key = cache_key(seg.source_text, glossary.version, glossary.target_lang, engine.name)
                tm.put(key, seg.source_text, seg.target_text, result.uncertain,
                       engine.name, glossary.version, glossary.target_lang)
        if tm is not None:
            tm.commit()
    emit("review_completed", count=len(targets), changed=changed)
    return {"sent": len(targets), "changed": changed}
