"""Medidor de uso do Lector: cada chamada a um CLI é registrada (tokens/custo) em cache/usage.jsonl.

Mede só o que o Lector gastou; não é a cota oficial do provedor (nenhum CLI a informa de forma estável).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import config

_LOCK = threading.Lock()
PRUNE_BYTES = 1_000_000  # acima disso, descarta o que passou da maior janela (7 dias)
WINDOWS = {"5h": 5 * 3600, "24h": 24 * 3600, "7d": 7 * 24 * 3600}


def _path() -> Path:
    return config.USAGE_PATH


def record(provider: str, model: str, tokens_in: int = 0, tokens_out: int = 0,
           cost_usd: float = 0.0, seconds: float = 0.0) -> None:
    """Acrescenta uma linha ao registro. Nunca levanta: medir não pode derrubar a tradução."""
    try:
        row = {"ts": round(time.time(), 1), "provider": provider, "model": model, "in": int(tokens_in),
               "out": int(tokens_out), "cost": round(float(cost_usd), 6), "sec": round(float(seconds), 1)}
        with _LOCK:
            path = _path()
            path.parent.mkdir(parents=True, exist_ok=True)
            lead = ""
            if path.is_file() and path.stat().st_size:  # linha anterior cortada (queda no meio da escrita)
                with path.open("rb") as probe:
                    probe.seek(-1, 2)
                    lead = "" if probe.read(1) == b"\n" else "\n"
            with path.open("a", encoding="utf-8") as stream:
                stream.write(lead)
                stream.write(json.dumps(row) + "\n")
            if path.stat().st_size > PRUNE_BYTES:
                _prune(path)
    except Exception:
        pass


def _prune(path: Path) -> None:
    """Mantém só as linhas dentro de 7 dias (chamado com _LOCK preso); ignora linhas corrompidas."""
    cutoff = time.time() - max(WINDOWS.values())
    keep = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            if float(json.loads(line).get("ts", 0)) >= cutoff:
                keep.append(line)
        except (json.JSONDecodeError, ValueError, AttributeError):
            continue
    config.atomic_write(path, "\n".join(keep) + ("\n" if keep else ""))


def summary(now: float | None = None) -> dict[str, dict]:
    """Por provedor e janela (5h, 24h, 7d): chamadas, tokens de entrada/saída e custo."""
    now = now or time.time()
    out: dict[str, dict] = {}
    path = _path()
    if not path.is_file():
        return out
    with _LOCK:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        age = now - float(row.get("ts", 0))
        bucket = out.setdefault(row.get("provider", "?"), {w: {"calls": 0, "in": 0, "out": 0, "cost": 0.0} for w in WINDOWS})
        for window, span in WINDOWS.items():
            if age <= span:
                cell = bucket[window]
                cell["calls"] += 1
                cell["in"] += int(row.get("in", 0))
                cell["out"] += int(row.get("out", 0))
                cell["cost"] = round(cell["cost"] + float(row.get("cost", 0.0)), 6)
    return out


def extract(provider: str, envelope) -> tuple[int, int, float]:
    """(tokens_in, tokens_out, custo) a partir da saída do CLI; zeros quando o CLI não informa."""
    try:
        if provider == "claude" and isinstance(envelope, dict):
            u = envelope.get("usage") or {}
            tin = int(u.get("input_tokens", 0)) + int(u.get("cache_creation_input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0))
            return tin, int(u.get("output_tokens", 0)), float(envelope.get("total_cost_usd", 0) or 0)
        if provider == "agy" and isinstance(envelope, dict):
            u = envelope.get("usage") or {}
            return int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0)) + int(u.get("thinking_tokens", 0)), 0.0
        if provider == "codex" and isinstance(envelope, str):
            for line in reversed(envelope.splitlines()):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                u = event.get("usage") if isinstance(event, dict) else None
                if u:
                    return int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0)), 0.0
    except (TypeError, ValueError):
        pass
    return 0, 0, 0.0
