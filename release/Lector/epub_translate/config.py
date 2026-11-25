"""Editable provider catalog and per-user project preferences."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_PATH = PROJECT_ROOT / "models.json"
PREFERENCES_PATH = PROJECT_ROOT / "preferences.json"
LAST_JOB_PATH = PROJECT_ROOT / "last_job.json"
USAGE_PATH = PROJECT_ROOT / "cache" / "usage.jsonl"  # medidor de uso do Lector (tokens por chamada)
CACHE_DIR = PROJECT_ROOT / "cache"                 # cache de traduções + cópias dos EPUBs enviados
CACHE_PATH = CACHE_DIR / "lector.tmcache"          # cache único, compartilhado entre livros e modelos
BOOKS_DIR = CACHE_DIR / "livros"
OUTPUT_DIR = PROJECT_ROOT / "saida"                # EPUBs traduzidos (quando enviados pela tela)
PRESET_MULTIPLIERS = {"less": 2.0, "default": 1.0, "more": 0.5}

DEFAULT_CATALOG: dict[str, Any] = {
    "providers": {
        "claude": {"binary": "claude", "models": ["sonnet", "opus", "haiku"], "batch_segments": 60, "batch_words": 2000, "timeout": 600},
        "codex": {"binary": "codex", "models": ["gpt-5-codex", "gpt-5"], "batch_segments": 40, "batch_words": 1600, "timeout": 900},
        "agy": {"binary": "agy", "models": ["gemini-3.8-flash-medium", "gemini-4-hydrogen-medium", "claude-sonnet-4-6", "claude-opus-4-6-thinking"], "batch_segments": 40, "batch_words": 1600, "timeout": 600},
        "ollama": {"binary": "ollama", "models": ["qwen2.5:14b", "llama3.1:8b"], "batch_segments": 20, "batch_words": 1000, "timeout": 900, "max_workers": 1},
    }
}


def atomic_write(path: Path, text: str) -> None:
    """Grava em arquivo temporário e troca (os.replace): leitores nunca veem JSON pela metade."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Como atomic_write, para arquivos binários (EPUB final: nunca fica parcial no destino)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default.copy()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Configuração JSON inválida em {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"A raiz de {path.name} deve ser um objeto JSON.")
    return data


def load_catalog(path: str | Path = MODELS_PATH) -> dict[str, Any]:
    """Read an editable catalog; use built-in defaults if it is not present."""
    catalog = _read_json(Path(path), DEFAULT_CATALOG)
    providers = catalog.get("providers")
    if not isinstance(providers, dict):
        raise ValueError("models.json deve conter um objeto 'providers'.")
    for name, entry in providers.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("models"), list):
            raise ValueError(f"Entrada inválida para o provedor {name!r} em models.json.")
        for key, fallback in (("binary", name), ("batch_segments", 60), ("batch_words", 2000), ("timeout", 600)):
            entry.setdefault(key, fallback)
    return catalog


def load_preferences(path: str | Path = PREFERENCES_PATH) -> dict[str, Any]:
    return _read_json(Path(path), {})


def save_preferences(preferences: dict[str, Any], path: str | Path = PREFERENCES_PATH) -> None:
    target = Path(path)
    atomic_write(target, json.dumps(preferences, ensure_ascii=False, indent=2) + "\n")


def provider_settings(provider: str, model: str, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    entry = (catalog or load_catalog()).get("providers", {}).get(provider, {})
    settings = dict(entry)
    settings.update(settings.get("model_settings", {}).get(model, {}))
    settings.pop("model_settings", None)
    return settings


def batch_settings(provider: str, model: str, batch_mode: str = "default",
                   catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    if batch_mode not in PRESET_MULTIPLIERS:
        raise ValueError("batch_mode deve ser less, default ou more")
    settings = provider_settings(provider, model, catalog)
    multiplier = PRESET_MULTIPLIERS[batch_mode]
    settings["batch_segments"] = max(1, int(settings.get("batch_segments", 60) * multiplier))
    settings["batch_words"] = max(1, int(settings.get("batch_words", 2000) * multiplier))
    return settings
