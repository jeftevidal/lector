"""Modelo de dados do pipeline (SPEC §3)."""

from __future__ import annotations

from dataclasses import dataclass, field


# status possíveis de um Segment
PENDING = "pending"
CACHED = "cached"
TRANSLATED = "translated"
UNCERTAIN = "uncertain"
ERROR = "error"
SKIPPED = "skipped"


@dataclass
class Segment:
    seg_id: str            # estável: f"{arquivo}#{xpath}#{ordinal}" (ou #attr:nome)
    file: str              # ex.: "OEBPS/xhtml/Chapter03.html"
    xpath: str             # localização do nó no DOM
    kind: str              # "block" | "attr" | "ncx-label"
    source_html: str = ""  # innerHTML original (com tags inline) — vazio p/ attr/ncx
    source_text: str = ""  # texto p/ o modelo: inline vira placeholder ⟦n⟧…⟦/n⟧
    placeholders: dict[str, str] = field(default_factory=dict)
    attr_name: str = ""    # para kind="attr": qual atributo (alt/title)
    target_text: str = ""  # resposta do modelo (com placeholders)
    status: str = PENDING
    note: str = ""         # incerteza/erro reportado

    @property
    def word_count(self) -> int:
        return len(self.source_text.split())


@dataclass
class Batch:
    batch_id: str
    file: str
    segments: list[Segment]

    @property
    def word_count(self) -> int:
        return sum(s.word_count for s in self.segments)


@dataclass
class QAFinding:
    severity: str   # "BLOCK" | "WARN"
    check: str
    file: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - conveniência de log
        return f"[{self.severity}] {self.check} — {self.file}: {self.detail}"
