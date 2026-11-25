"""Cache de tradução (translation memory) em SQLite (SPEC §7).

GR-REPLAY: a chave é derivada da ENTRADA (texto+glossário+idioma+modelo),
nunca do relógio/ordem. Rodar de novo com a mesma chave não duplica efeito —
o cache É o mecanismo de resume: re-rodar o job pula tudo que já foi feito.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from pathlib import Path


def cache_key(source_text: str, glossary_version: str, target_lang: str, model: str) -> str:
    payload = "\x00".join([source_text, glossary_version, target_lang, model])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TMCache:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        self._lock = threading.RLock()
        self.conn.execute("PRAGMA journal_mode=WAL")  # leitores não bloqueiam o escritor (lotes em paralelo)
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS tm (
                cache_key TEXT PRIMARY KEY,
                source_text TEXT NOT NULL,
                target_text TEXT NOT NULL,
                uncertain INTEGER NOT NULL DEFAULT 0,
                model TEXT NOT NULL,
                glossary_version TEXT NOT NULL,
                target_lang TEXT NOT NULL,
                ts REAL NOT NULL
            )"""
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS ix_tm_src ON tm(source_text, glossary_version, target_lang)")
        self.conn.commit()

    def get(self, key: str) -> tuple[str, bool] | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT target_text, uncertain FROM tm WHERE cache_key = ?", (key,)
            ).fetchone()
        return (row[0], bool(row[1])) if row else None

    def get_compatible(
        self, source_text: str, glossary_version: str, target_lang: str, current_model: str,
    ) -> tuple[str, bool, str] | None:
        """Find the newest compatible translation from a different engine/model."""
        with self._lock:
            row = self._compat_row(source_text, glossary_version, target_lang, current_model)
        return (row[0], bool(row[1]), row[2]) if row else None

    def _compat_row(self, source_text, glossary_version, target_lang, current_model):
        return self.conn.execute(
            """SELECT target_text, uncertain, model FROM tm
               WHERE source_text=? AND glossary_version=? AND target_lang=? AND model<>?
                 AND model NOT IN ('pseudo', 'identity')
               ORDER BY ts DESC, cache_key ASC LIMIT 1""",
            (source_text, glossary_version, target_lang, current_model),
        ).fetchone()

    def put(self, key: str, source_text: str, target_text: str, uncertain: bool,
            model: str, glossary_version: str, target_lang: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO tm VALUES (?,?,?,?,?,?,?,?)",
                (key, source_text, target_text, int(uncertain), model,
                 glossary_version, target_lang, time.time()),
            )

    def commit(self) -> None:
        with self._lock:
            self.conn.commit()

    def stats(self) -> int:
        with self._lock:
            return self.conn.execute("SELECT COUNT(*) FROM tm").fetchone()[0]

    def get_many(self, keys: list[str]) -> dict[str, tuple[str, bool]]:
        """Busca várias chaves de uma vez (blocos de 500): bem menos idas ao SQLite que uma por segmento."""
        found: dict[str, tuple[str, bool]] = {}
        with self._lock:
            for i in range(0, len(keys), 500):
                chunk = keys[i:i + 500]
                marks = ",".join("?" * len(chunk))
                for key, target, uncertain in self.conn.execute(
                        f"SELECT cache_key, target_text, uncertain FROM tm WHERE cache_key IN ({marks})", chunk):
                    found[key] = (target, bool(uncertain))
        return found

    @staticmethod
    def preview_counts(path: str | Path, segments, glossary_version: str, target_lang: str,
                       model: str, reuse_cache: bool = False,
                       by_file: dict | None = None) -> tuple[int, int]:
        """Count cache matches read-only (query_only), without creating or changing a cache."""
        cache_path = Path(path)
        if not cache_path.is_file():
            return 0, 0
        try:
            conn = sqlite3.connect(str(cache_path), timeout=30)
            try:
                conn.execute("PRAGMA query_only=ON")
                exact = cross = 0
                keys = {seg.seg_id: cache_key(seg.source_text, glossary_version, target_lang, model) for seg in segments}
                hit: set[str] = set()
                values = list(keys.values())
                for i in range(0, len(values), 500):
                    chunk = values[i:i + 500]
                    marks = ",".join("?" * len(chunk))
                    hit.update(r[0] for r in conn.execute(f"SELECT cache_key FROM tm WHERE cache_key IN ({marks})", chunk))
                # Cross-cache lookup optimization: batch check unique source texts
                cross_hit_sources: set[str] = set()
                if reuse_cache:
                    needed_sources = list({seg.source_text for seg in segments if keys[seg.seg_id] not in hit})
                    for i in range(0, len(needed_sources), 500):
                        chunk = needed_sources[i:i + 500]
                        marks = ",".join("?" * len(chunk))
                        params = [glossary_version, target_lang, model, *chunk]
                        for (src,) in conn.execute(
                            f"SELECT DISTINCT source_text FROM tm WHERE glossary_version=? AND target_lang=? AND model<>? "
                            f"AND model NOT IN ('pseudo', 'identity') AND source_text IN ({marks})",
                            params,
                        ):
                            cross_hit_sources.add(src)

                for seg in segments:
                    if keys[seg.seg_id] in hit:
                        exact += 1
                        if by_file is not None:
                            by_file[seg.file] = by_file.get(seg.file, 0) + 1
                    elif reuse_cache and seg.source_text in cross_hit_sources:
                        cross += 1
                        if by_file is not None:
                            by_file[seg.file] = by_file.get(seg.file, 0) + 1
                return exact, cross
            finally:
                conn.close()
        except sqlite3.OperationalError:
            # A legacy or empty file is not a cache; preview stays read-only.
            return 0, 0

    def close(self) -> None:
        with self._lock:
            self.conn.commit()
            self.conn.close()
