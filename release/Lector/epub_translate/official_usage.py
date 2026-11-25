"""Cota oficial dos provedores (opcional, desligada por padrão).

Usa os endereços internos que os próprios CLIs usam para mostrar o uso da conta. Eles NÃO são documentados
e podem mudar sem aviso; usar o token de login fora do CLI pode contrariar os termos do provedor.
Por isso: só roda quando o usuário liga a opção; lê apenas o token de acesso (nunca grava credenciais,
nunca renova token, nunca registra o token em log); e qualquer falha vira uma mensagem simples.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

CLAUDE_URL = "https://api.anthropic.com/api/oauth/usage"
CODEX_URL = "https://chatgpt.com/backend-api/wham/usage"
TIMEOUT = 10
CACHE_SECONDS = 60

_LOCK = threading.Lock()
_FETCH_LOCK = threading.Lock()  # chamadas simultâneas esperam a primeira e usam o cache dela
_CACHE: dict[str, tuple[float, dict]] = {}


def _home() -> Path:
    return Path.home()


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _get(url: str, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise RuntimeError("login expirado: abra o CLI para renová-lo") from None
        raise RuntimeError(f"o provedor respondeu HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        raise RuntimeError("não foi possível consultar o provedor") from None
    if not isinstance(body, dict):
        raise RuntimeError("resposta inesperada do provedor")
    return body


def _percent(window: dict | None, *keys: str) -> float | None:
    for key in keys:
        value = (window or {}).get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0.0, min(100.0, float(value)))
    return None


def _epoch(value) -> float | None:
    """Aceita epoch (s ou ms) ou texto ISO 8601; devolve epoch em segundos."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) / 1000 if value > 1e11 else float(value)
    if isinstance(value, str):
        from datetime import datetime
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _window(raw: dict | None, percent_keys: tuple[str, ...], reset_keys: tuple[str, ...]) -> dict | None:
    pct = _percent(raw, *percent_keys)
    if pct is None:
        return None
    reset = next((_epoch(raw.get(k)) for k in reset_keys if _epoch(raw.get(k)) is not None), None)
    return {"used_percent": round(pct, 1), "resets_at": reset}


def parse_claude(body: dict) -> dict:
    keys, resets = ("utilization", "used_percentage", "used_percent", "percent"), ("resets_at", "reset_at")
    return {"session": _window(body.get("five_hour"), keys, resets), "weekly": _window(body.get("seven_day"), keys, resets)}


def parse_codex(body: dict) -> dict:
    limit = body.get("rate_limit") if isinstance(body.get("rate_limit"), dict) else {}
    keys, resets = ("used_percent", "used_percentage", "utilization"), ("reset_at", "resets_at")
    return {"session": _window(limit.get("primary_window"), keys, resets),
            "weekly": _window(limit.get("secondary_window"), keys, resets), "plan": body.get("plan_type")}


def fetch_claude() -> dict:
    data = _read_json(_home() / ".claude" / ".credentials.json")
    oauth = (data or {}).get("claudeAiOauth")
    token = oauth.get("accessToken") if isinstance(oauth, dict) else None
    if not token:
        raise RuntimeError("login do Claude não encontrado")
    expires = _epoch(oauth.get("expiresAt"))
    if expires is not None and expires < time.time():
        raise RuntimeError("login expirado: abra o claude para renová-lo")
    body = _get(CLAUDE_URL, {"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20",
                             "User-Agent": "claude-code/2.1.0"})
    result = parse_claude(body)
    result["plan"] = oauth.get("subscriptionType")
    return result


def fetch_codex() -> dict:
    data = _read_json(_home() / ".codex" / "auth.json")
    tokens = (data or {}).get("tokens")
    token = tokens.get("access_token") if isinstance(tokens, dict) else None
    if not token:
        raise RuntimeError("login do Codex não encontrado")
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "codex-cli"}
    if tokens.get("account_id"):
        headers["ChatGPT-Account-Id"] = str(tokens["account_id"])
    return parse_codex(_get(CODEX_URL, headers))


FETCHERS = {"claude": fetch_claude, "codex": fetch_codex}


def snapshot(force: bool = False) -> dict[str, dict]:
    """Uso oficial por provedor. Cada entrada traz `session`/`weekly` ou `error`. Cache de 60 s."""
    with _FETCH_LOCK:
        return _snapshot_locked(force)


def _snapshot_locked(force: bool) -> dict[str, dict]:
    out: dict[str, dict] = {}
    now = time.time()
    for name, fetcher in FETCHERS.items():
        with _LOCK:
            cached = _CACHE.get(name)
        if cached and not force and now - cached[0] < CACHE_SECONDS:
            out[name] = cached[1]
            continue
        try:
            value = {**fetcher(), "error": None}
        except RuntimeError as exc:
            value = {"session": None, "weekly": None, "error": str(exc)}
        except Exception:  # nunca vazar detalhes (podem conter cabeçalhos)
            value = {"session": None, "weekly": None, "error": "falha inesperada ao consultar o provedor"}
        with _LOCK:
            _CACHE[name] = (now, value)
        out[name] = value
    return out
