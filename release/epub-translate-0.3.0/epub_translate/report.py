"""Relatório (RF10): estatísticas, incertezas, avisos de QA."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from .model import ERROR, QAFinding, Segment, SKIPPED, UNCERTAIN


@dataclass
class RunStats:
    input_path: str = ""
    output_path: str = ""
    target_lang: str = ""
    engine: str = ""
    files_translated: int = 0
    segments_total: int = 0
    words_total: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    cache_hits: int = 0
    engine_calls: int = 0
    engine_seconds: float = 0.0
    packed: bool = False


def build_report_md(stats: RunStats, segments: list[Segment], findings: list[QAFinding]) -> str:
    lines = [
        "# Relatório — epub-translate",
        "",
        f"- **Entrada:** `{stats.input_path}`",
        f"- **Saída:** `{stats.output_path}`" + ("" if stats.packed else " — **NÃO EMPACOTADO (QA BLOCK)**"),
        f"- **Idioma de destino:** {stats.target_lang} - **Motor:** {stats.engine}",
        f"- **Arquivos traduzidos:** {stats.files_translated} - **Segmentos:** {stats.segments_total}"
        f" - **Palavras-fonte:** {stats.words_total}",
        f"- **Status:** {stats.counts}",
        f"- **Cache hits:** {stats.cache_hits} - **Chamadas ao motor:** {stats.engine_calls}"
        f" ({stats.engine_seconds:.0f}s)",
        "",
    ]

    blocks = [f for f in findings if f.severity == "BLOCK"]
    warns = [f for f in findings if f.severity == "WARN"]
    lines.append(f"## QA — {len(blocks)} BLOCK, {len(warns)} WARN")
    lines.append("")
    for f in findings:
        lines.append(f"- **[{f.severity}] {f.check}** — `{f.file}`: {f.detail}")
    if not findings:
        lines.append("- todos os gates passaram")
    lines.append("")

    uncertain = [s for s in segments if s.status == UNCERTAIN]
    lines.append(f"## Segmentos incertos (revisar) — {len(uncertain)}")
    lines.append("")
    for s in uncertain:
        lines.append(f"- `{s.seg_id}`")
        lines.append(f"  - fonte: {s.source_text[:160]}")
        lines.append(f"  - trad.: {s.target_text[:160]}")
    lines.append("")

    errors = [s for s in segments if s.status == ERROR]
    lines.append(f"## Segmentos com erro (mantiveram o original) — {len(errors)}")
    lines.append("")
    for s in errors:
        lines.append(f"- `{s.seg_id}` — {s.note}")
    lines.append("")

    skipped = sum(1 for s in segments if s.status == SKIPPED)
    lines.append(f"_Segmentos sem texto traduzível (pulados): {skipped}_")
    lines.append("")
    return "\n".join(lines)


def build_report_json(stats: RunStats, segments: list[Segment], findings: list[QAFinding]) -> str:
    return json.dumps(
        {
            "stats": asdict(stats),
            "findings": [asdict(f) for f in findings],
            "uncertain": [s.seg_id for s in segments if s.status == UNCERTAIN],
            "errors": [{"seg_id": s.seg_id, "note": s.note} for s in segments if s.status == ERROR],
        },
        ensure_ascii=False, indent=2,
    )
