"""Glossario JSON and prompt assembly."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from string import Template

DEFAULT_BLOCK_TAGS = [
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "th",
    "caption", "figcaption", "blockquote", "dt", "dd",
]
DEFAULT_DONT_DESCEND = ["code", "pre", "script", "style"]

OPEN, CLOSE = "⟦", "⟧"


@dataclass
class Glossary:
    target_lang: str = "pt-BR"
    source_lang: str = "en"
    tone: str = "didatico, tratar o leitor por 'voce'"
    keep_in_source: list[str] = field(default_factory=list)
    translate_map: dict[str, str] = field(default_factory=dict)
    never_translate: list[str] = field(default_factory=lambda: [
        "nomes proprios", "nomes de fontes tipograficas", "titulos de obras",
        "codigo", "URLs", "siglas",
    ])
    skip_files: list[str] = field(default_factory=list)
    main_content_only: bool = True
    dont_descend: list[str] = field(default_factory=lambda: list(DEFAULT_DONT_DESCEND))
    block_tags: list[str] = field(default_factory=lambda: list(DEFAULT_BLOCK_TAGS))
    translatable_attrs: list[str] = field(default_factory=lambda: ["alt", "title"])
    ignore_attr_values: dict[str, list[str]] = field(default_factory=lambda: {"alt": ["image"]})
    version: str = "default"
    note: str = ""

    @classmethod
    def load(cls, path: str | Path | None) -> "Glossary":
        if path is None:
            return cls()
        raw = Path(path).read_bytes()
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ValueError(f"Glossario deve ser um arquivo JSON UTF-8 valido: {e}") from e
        if not isinstance(data, dict):
            raise ValueError("A raiz do glossario JSON deve ser um objeto.")
        allowed = cls.__dataclass_fields__
        g = cls(**{k: v for k, v in data.items() if k in allowed})
        g.version = hashlib.sha256(raw).hexdigest()[:16]
        return g

    def system_prompt(self) -> str:
        template = Template(Path(__file__).with_name("prompt.md").read_text(encoding="utf-8"))
        pairs = "; ".join(f"{k} -> {v}" for k, v in self.translate_map.items()) or "(nenhum)"
        return template.substitute(
            source_lang=self.source_lang, target_lang=self.target_lang, tone=self.tone,
            keep_in_source=", ".join(self.keep_in_source) or "(nenhum)",
            never_translate="; ".join(self.never_translate), translate_map=pairs,
            open=OPEN, close=CLOSE,
        ).strip()
