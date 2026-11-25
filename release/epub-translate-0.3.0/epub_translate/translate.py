"""Translate (SPEC §4.3/§5): o ÚNICO ponto semântico do pipeline.

O motor nunca vê tag — só strings com placeholders. Contrato de I/O validado
(ids, contagem, placeholders); reprovou → 1 retry de reparo; persistiu →
segmentos ERROR e o pipeline segue (o livro sai com o original nesses blocos).

Política de retry (GR-REPLAY):
- erro TRANSITÓRIO (timeout, exit!=0, envelope/JSON inválido, rate-limit):
  até MAX_ATTEMPTS por batch, backoff exponencial BACKOFF_BASE**tentativa s.
- erro PERMANENTE (segmento específico reprovado 2x na validação): ERROR na
  hora — retry de bug é loop caro que esconde a causa.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Callable

from . import usage as usage_log
from .cache import TMCache, cache_key
from .extract import PLACEHOLDER_RE
from .glossary import Glossary
from .model import Batch, CACHED, ERROR, PENDING, Segment, TRANSLATED, UNCERTAIN

MAX_ATTEMPTS = 3          # tentativas por batch para erro transitório
BACKOFF_BASE = 5.0        # 5s, 25s
BATCH_MAX_SEGMENTS = 60
BATCH_MAX_WORDS = 2000


# Windows: não abrir janela de console a cada chamada de CLI externo.
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


class TransientEngineError(Exception):
    """Falha da chamada inteira (CLI/parse) — retryable."""


class QuotaExhaustedError(TransientEngineError):
    """Cota/limite de uso do provedor esgotado: repetir não adianta, o job deve parar."""

    def __init__(self, message: str, provider: str = "", reset: str = ""):
        super().__init__(message)
        self.provider = provider
        self.reset = reset


class EngineUnavailableError(QuotaExhaustedError):
    """O motor falhou em vários lotes seguidos (CLI deslogado, modelo inválido, serviço fora do ar)."""


BREAKER_BATCHES = 2  # lotes seguidos totalmente falhos que disparam o disjuntor
MAX_CMDLINE = 30000 if os.name == "nt" else 120000  # limite de linha de comando (Windows: 32.767)


def _kill_tree(proc) -> None:
    """Mata o processo e seus filhos (no Windows, filhos seguram o pipe e travariam o communicate)."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                           timeout=10, **_NO_WINDOW)
        except Exception:
            pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.communicate(timeout=5)
    except Exception:
        pass


QUOTA_RE = re.compile(r"quota(?! project)|resource_exhausted|usage limit|rate.?limit|limit reached|insufficient_quota|credit balance|too many requests|code 429|status 429|http 429", re.I)
RESET_RE = re.compile(r"resets? in ([0-9hms ]+)", re.I)


def _check_quota(provider: str, text: str) -> None:
    if QUOTA_RE.search(text or ""):
        match = RESET_RE.search(text)
        raise QuotaExhaustedError(text.strip()[:300], provider, match.group(1).strip() if match else "")


@dataclass
class EngineResult:
    seg_id: str
    text: str
    uncertain: bool = False


class Engine:
    """Interface do motor. name entra na chave de cache."""

    name = "engine"
    batch_segments = BATCH_MAX_SEGMENTS
    batch_words = BATCH_MAX_WORDS
    timeout = 600

    def translate(self, segments: list[Segment], glossary: Glossary) -> list[EngineResult]:
        raise NotImplementedError


class IdentityEngine(Engine):
    """Mock determinístico: devolve o texto igual (round-trip de testes)."""

    name = "identity"

    def translate(self, segments, glossary):
        return [EngineResult(s.seg_id, s.source_text) for s in segments]


class PseudoEngine(Engine):
    """Mock determinístico: altera o texto visivelmente sem tocar placeholders."""

    name = "pseudo"
    batch_segments = BATCH_MAX_SEGMENTS
    batch_words = BATCH_MAX_WORDS

    def translate(self, segments, glossary):
        out = []
        for s in segments:
            parts = PLACEHOLDER_RE.split(s.source_text)
            marks = PLACEHOLDER_RE.findall(s.source_text)
            buf = []
            for i, p in enumerate(parts):
                buf.append(p.upper())
                if i < len(marks):
                    buf.append(marks[i])
            out.append(EngineResult(s.seg_id, "".join(buf)))
        return out


def _request_text(segments: list[Segment], glossary: Glossary) -> str:
    payload = json.dumps({"target_lang": glossary.target_lang,
                          "segments": [{"id": s.seg_id, "text": s.source_text} for s in segments]},
                         ensure_ascii=False)
    return ("Translate each segment. Return only valid JSON matching this schema exactly: "
            '{"segments":[{"id":"...","text":"...","uncertain":false}]}. '
            "Preserve every placeholder exactly and keep all ids unchanged.\n\n" + payload)


def _json_payload(text: str) -> dict:
    """Parse direct JSON or common CLI envelopes into the strict segments object."""
    if isinstance(text, dict):
        data = text
    else:
        try:
            data = json.loads(text.strip())
        except json.JSONDecodeError:
        # Codex --json streams JSONL; use the final agent message event.
            events = []
            for line in text.splitlines():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            data = None
            for event in reversed(events):
                item = event.get("item", {})
                if item.get("type") == "agent_message" and item.get("text"):
                    data = item["text"]
                    break
            if data is None:
                raise TransientEngineError("saída do CLI não contém JSON parseável")
    for _ in range(3):
        if isinstance(data, str):
            inner = re.sub(r"^```(?:json)?\s*|\s*```$", "", data.strip(), flags=re.S).strip()
            try:
                data = json.loads(inner)
                continue
            except json.JSONDecodeError as exc:
                raise TransientEngineError(f"resposta interna não é JSON: {exc}") from exc
        if isinstance(data, dict) and "segments" in data:
            rows = data["segments"]
            if not isinstance(rows, list) or any(
                not isinstance(row, dict)
                or not isinstance(row.get("id"), str)
                or not isinstance(row.get("text"), str)
                or not isinstance(row.get("uncertain"), bool)
                for row in rows
            ):
                raise TransientEngineError(
                    "contrato JSON inválido: cada segmento deve conter id/text/uncertain tipados"
                )
            return data
        if isinstance(data, dict):
            item = data.get("item", {})
            if isinstance(item, dict) and item.get("type") == "agent_message" and item.get("text"):
                data = item["text"]
                continue
            nested = next((data[k] for k in ("result", "response", "text") if k in data), None)
            if nested is not None:
                data = nested
                continue
            content = data.get("content")
            if isinstance(content, list):
                text_items = [x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text"]
                if text_items:
                    data = "\n".join(text_items)
                    continue
        break
    raise TransientEngineError("a saída não corresponde a {segments:[{id,text,uncertain}]}")


class CliEngine(Engine):
    """Common subprocess/error handling for local command-line providers."""
    provider = ""
    cancel_check = None  # callable() -> bool; quando definido, o CLI é interrompido ao cancelar

    def __init__(self, model: str, binary: str | None = None, timeout: int = 600,
                 batch_segments: int = BATCH_MAX_SEGMENTS, batch_words: int = BATCH_MAX_WORDS):
        self.model = model
        self.binary = binary or self.provider
        self.timeout = timeout
        self.batch_segments = batch_segments
        self.batch_words = batch_words
        # Keep the old Claude cache namespace so existing resume entries remain valid.
        self.name = f"claude-cli:{model}" if self.provider == "claude" else f"{self.provider}:{model}"
        self.calls = 0
        self.total_seconds = 0.0
        self._stats_lock = threading.Lock()

    def command_and_input(self, segments: list[Segment], glossary: Glossary) -> tuple[list[str], str | None]:
        raise NotImplementedError

    def _run_cancellable(self, command: list[str], input_text: str | None):
        proc = subprocess.Popen(command, stdin=subprocess.PIPE if input_text is not None else None,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace", **_NO_WINDOW)
        deadline = time.time() + self.timeout
        payload = input_text
        while True:
            try:
                out, err = proc.communicate(payload, timeout=1)
                return subprocess.CompletedProcess(command, proc.returncode, out, err)
            except subprocess.TimeoutExpired:
                payload = None
                if self.cancel_check is not None and self.cancel_check():
                    _kill_tree(proc)
                    raise TransientEngineError("cancelado pelo usuário") from None
                if time.time() > deadline:
                    _kill_tree(proc)
                    raise

    def translate(self, segments, glossary):
        executable = self.binary if ("\\" in self.binary or ":" in self.binary) else shutil.which(self.binary)
        if not executable:
            raise TransientEngineError(f"binário `{self.binary}` não encontrado no PATH")
        command, input_text = self.command_and_input(segments, glossary)
        if sum(len(arg) + 1 for arg in command) > MAX_CMDLINE:
            if len(segments) > 1:  # prompt grande demais para a linha de comando: divide o lote
                half = len(segments) // 2
                return self.translate(segments[:half], glossary) + self.translate(segments[half:], glossary)
            raise TransientEngineError("segmento grande demais para a linha de comando do " + self.provider)
        command[0] = executable
        t0 = time.time()
        try:
            proc = self._run_cancellable(command, input_text)
        except FileNotFoundError as exc:
            raise TransientEngineError(f"binário `{self.binary}` não encontrado no PATH") from exc
        except OSError as exc:
            raise TransientEngineError(f"falha ao executar {self.provider}: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TransientEngineError(f"timeout ({self.timeout}s) no {self.provider}") from exc
        finally:
            with self._stats_lock:
                self.calls += 1
                self.total_seconds += time.time() - t0
        if proc.returncode != 0:
            _check_quota(self.provider, (proc.stderr or "") + (proc.stdout or ""))
            raise TransientEngineError(f"{self.provider} exit={proc.returncode}: {(proc.stderr or proc.stdout)[:300]}")
        envelope = proc.stdout
        if self.provider == "claude":
            try:
                outer = json.loads(envelope)
                if outer.get("is_error"):
                    _check_quota(self.provider, str(outer.get("result")))
                    raise TransientEngineError(f"Claude reportou erro: {str(outer.get('result'))[:300]}")
            except json.JSONDecodeError as exc:
                raise TransientEngineError(f"envelope do Claude não é JSON: {exc}") from exc
            envelope = outer
            usage_log.record(self.provider, self.model, *usage_log.extract("claude", outer)[:2],
                             cost_usd=usage_log.extract("claude", outer)[2], seconds=time.time() - t0)
        if self.provider == "agy":
            try:
                outer = json.loads(envelope)
            except json.JSONDecodeError as exc:
                raise TransientEngineError(f"envelope do agy não é JSON: {exc}") from exc
            if outer.get("status") != "SUCCESS":
                _check_quota(self.provider, str(outer))
                raise TransientEngineError(f"agy reportou erro: {str(outer)[:300]}")
            usage_log.record(self.provider, self.model, *usage_log.extract("agy", outer)[:2], seconds=time.time() - t0)
            envelope = json.dumps(str(outer.get("response", "")))
        if self.provider in ("codex", "ollama"):
            usage_log.record(self.provider, self.model, *usage_log.extract(self.provider, proc.stdout)[:2],
                             seconds=time.time() - t0)
        return [EngineResult(str(row["id"]), str(row["text"]), bool(row.get("uncertain", False)))
                for row in _json_payload(envelope).get("segments", [])]


class ClaudeCliEngine(CliEngine):
    provider = "claude"

    def __init__(self, model: str = "sonnet", timeout: int = 600, claude_bin: str = "claude", **limits):
        super().__init__(model, claude_bin, timeout, **limits)

    def command_and_input(self, segments, glossary):
        return ([self.binary, "-p", "--output-format", "json", "--model", self.model,
                 "--system-prompt", glossary.system_prompt()], _request_text(segments, glossary))


class CodexCliEngine(CliEngine):
    provider = "codex"

    def command_and_input(self, segments, glossary):
        return ([self.binary, "exec", "--json", "--model", self.model, "--sandbox", "read-only",
                 "--skip-git-repo-check", "-"], glossary.system_prompt() + "\n\n" + _request_text(segments, glossary))


class AgyCliEngine(CliEngine):
    provider = "agy"

    def command_and_input(self, segments, glossary):
        prompt = glossary.system_prompt() + "\n\n" + _request_text(segments, glossary)
        return ([self.binary, "--print", prompt, "--model", self.model, "--output-format", "json"], None)


class OllamaCliEngine(CliEngine):
    provider = "ollama"

    def command_and_input(self, segments, glossary):
        return ([self.binary, "run", self.model, "--format", "json"],
                glossary.system_prompt() + "\n\n" + _request_text(segments, glossary))


class FailoverEngine(Engine):
    """Usa o motor principal; se a cota acabar, passa para o reserva e repete o mesmo lote."""

    def __init__(self, primary: Engine, fallback: Engine | None = None, on_switch=None):
        self.engines = [primary] + ([fallback] if fallback else [])
        self.index = 0
        self._lock = threading.Lock()
        self.on_switch = on_switch
        self.batch_segments = primary.batch_segments
        self.batch_words = primary.batch_words
        self.timeout = getattr(primary, "timeout", 600)

    @property
    def current(self) -> Engine:
        return self.engines[self.index]

    @property
    def name(self) -> str:
        return self.current.name

    def translate(self, segments, glossary):
        while True:
            used = self.current
            try:
                return used.translate(segments, glossary)
            except QuotaExhaustedError:
                with self._lock:
                    if self.current is used:  # só o primeiro lote a falhar troca de motor
                        if self.index + 1 >= len(self.engines):
                            raise
                        old = used.name
                        self.index += 1
                        if self.on_switch:
                            self.on_switch(old, self.current.name)


def make_batches(segments: list[Segment], max_segments: int = BATCH_MAX_SEGMENTS,
                 max_words: int = BATCH_MAX_WORDS) -> list[Batch]:
    """Agrupa segmentos PENDING por arquivo, ~60 segmentos ou ~2000 palavras."""
    if max_segments < 1 or max_words < 1:
        raise ValueError("batch limits must be positive integers")
    batches: list[Batch] = []
    current: list[Segment] = []
    current_file = None
    words = 0

    def flush():
        nonlocal current, words
        if current:
            batches.append(Batch(f"b{len(batches)+1:04d}", current_file, current))
            current, words = [], 0

    for seg in segments:
        if seg.status != PENDING:
            continue
        if current_file != seg.file:
            # cauda pequena de um capítulo segue no mesmo lote (menos chamadas ao CLI)
            if len(current) >= max_segments * 0.5 or words >= max_words * 0.5:
                flush()
                current_file = seg.file
            elif current_file is None:
                current_file = seg.file
        if len(current) >= max_segments or words + seg.word_count > max_words:
            flush()
        current.append(seg)
        words += seg.word_count
    flush()
    return batches


def _validate_batch(segments: list[Segment], results: list[EngineResult]) -> dict[str, str]:
    """Valida o contrato do batch inteiro. Retorna {seg_id: motivo} dos reprovados."""
    by_id = {r.seg_id: r for r in results}
    bad: dict[str, str] = {}
    if set(by_id) != {s.seg_id for s in segments}:
        missing = {s.seg_id for s in segments} - set(by_id)
        for sid in missing:
            bad[sid] = "segmento ausente na resposta"
    for s in segments:
        r = by_id.get(s.seg_id)
        if r is None:
            continue
        if not r.text.strip():
            bad[s.seg_id] = "resposta vazia"
            continue
        src = sorted(PLACEHOLDER_RE.findall(s.source_text))
        tgt = sorted(PLACEHOLDER_RE.findall(r.text))
        if src != tgt:
            bad[s.seg_id] = f"placeholders divergem ({src} vs {tgt})"
    return bad


def _chain_cancel(engine, extra):
    """Faz o CLI em andamento também ser interrompido quando `extra()` for verdadeiro (ex.: cota esgotada)."""
    saved = []
    for eng in getattr(engine, "engines", [engine]):
        prev = getattr(eng, "cancel_check", None)
        saved.append((eng, prev))
        eng.cancel_check = lambda p=prev: bool(extra() or (p is not None and p()))
    return saved


def translate_all(
    segments: list[Segment],
    engine: Engine,
    glossary: Glossary,
    tm: TMCache | None = None,
    log=lambda msg: None,
    sleep=time.sleep,
    progress: Callable[[dict], None] | None = None,
    reuse_cache: bool = False,
    is_cancelled: Callable[[], bool] | None = None,
    workers: int = 1,
    only_files: set[str] | None = None,
) -> dict[str, int]:
    """Traduz todos os segmentos pending, usando cache quando houver.

    Efeito colateral concentrado aqui (GR-ISOLATION): chamadas ao motor e
    escrita no cache. Retorna contadores por status. Segmentos repetidos no livro
    são traduzidos uma vez só; `workers` > 1 envia vários lotes ao motor em paralelo.
    """
    hits = 0
    cross_hits = 0
    started = time.monotonic()
    cancelled = False
    lock = threading.RLock()
    stop = threading.Event()  # cota/motor indisponível: os demais lotes não devem começar
    totals: dict[str, int] = {}
    done_by: dict[str, int] = {}
    err_by: dict[str, int] = {}
    state = {"done": 0}
    for s in segments:
        totals[s.file] = totals.get(s.file, 0) + 1
        if s.status not in (PENDING, ERROR):
            done_by[s.file] = done_by.get(s.file, 0) + 1
            state["done"] += 1
        if s.status == ERROR:
            err_by[s.file] = err_by.get(s.file, 0) + 1

    def mark(seg: Segment, status: str, note: str | None = None) -> None:
        """Muda o status mantendo contadores incrementais (emit() fica O(1))."""
        with lock:
            old = seg.status
            if old not in (PENDING, ERROR):
                done_by[seg.file] -= 1
                state["done"] -= 1
            if old == ERROR:
                err_by[seg.file] -= 1
            seg.status = status
            if note is not None:
                seg.note = note
            if status not in (PENDING, ERROR):
                done_by[seg.file] = done_by.get(seg.file, 0) + 1
                state["done"] += 1
            if status == ERROR:
                err_by[seg.file] = err_by.get(seg.file, 0) + 1

    def emit(event: str, **data) -> None:
        if not progress:
            return
        with lock:
            file = data.setdefault("file", None)
            data.setdefault("chapter_done", done_by.get(file, 0))
            data.setdefault("chapter_total", totals.get(file, 0))
            data.setdefault("chapter_errors", err_by.get(file, 0))
            data.setdefault("done_segments", state["done"])
            data.setdefault("total_segments", len(segments))
            if event in {"cache_summary", "batch_completed", "quota_exhausted", "cancelled"}:
                data.setdefault("chapter_progress", {f: [done_by.get(f, 0), err_by.get(f, 0)] for f in totals})
        progress({"event": event, "elapsed_seconds": round(time.monotonic() - started, 2),
                  "ts": datetime.now(timezone.utc).isoformat(), **data})

    # 1. cache primeiro
    if tm is not None:
        todo = [s for s in segments if s.status == PENDING]
        keys = {s.seg_id: cache_key(s.source_text, glossary.version, glossary.target_lang, engine.name) for s in todo}
        exact_hits = tm.get_many(list(keys.values()))
        for seg in todo:
            key = keys[seg.seg_id]
            got = exact_hits.get(key)
            cache_source = "exact"
            if got is None and reuse_cache:
                compatible = tm.get_compatible(seg.source_text, glossary.version, glossary.target_lang, engine.name)
                if compatible is not None:
                    got = compatible[:2]
                    cache_source = "cross-model"
            if got is not None:
                if cache_source == "cross-model":
                    cross_hits += 1
                else:
                    hits += 1
                seg.target_text, uncertain = got
                mark(seg, UNCERTAIN if uncertain else CACHED)
                emit("cache_hit", seg_id=seg.seg_id, source=cache_source)
        log(f"cache: {hits} exact hits, {cross_hits} cross-model hits")
    emit("cache_summary", exact=hits, cross_model=cross_hits)

    # 2. deduplicação: texto idêntico no livro vai ao motor uma vez só
    leaders: list[Segment] = []
    followers: dict[str, list[Segment]] = {}
    first_seen: dict[str, Segment] = {}
    for seg in segments:
        if seg.status != PENDING or (only_files is not None and seg.file not in only_files):
            continue
        lead = first_seen.get(seg.source_text)
        if lead is None:
            first_seen[seg.source_text] = seg
            leaders.append(seg)
        else:
            followers.setdefault(lead.seg_id, []).append(seg)

    def propagate(leader: Segment) -> None:
        for dup in followers.get(leader.seg_id, ()):
            if dup.status != PENDING:
                continue
            if leader.status in (TRANSLATED, UNCERTAIN):
                dup.target_text = leader.target_text
                mark(dup, leader.status, leader.note)
            elif leader.status == ERROR:
                mark(dup, ERROR, leader.note)

    # 3. batches para o motor
    batches = make_batches(leaders, getattr(engine, "batch_segments", BATCH_MAX_SEGMENTS),
                           getattr(engine, "batch_words", BATCH_MAX_WORDS))

    breaker: dict = {"failed": 0, "segments": []}

    def cancelled_now() -> bool:
        return stop.is_set() or bool(is_cancelled and is_cancelled())

    def run_batch(i: int, batch: Batch):
        if cancelled_now():
            return i, batch, list(batch.segments), "", True
        log(f"batch {i}/{len(batches)} ({batch.file}, {len(batch.segments)} seg, {batch.word_count} palavras)")
        emit("batch_started", index=i, total=len(batches), file=batch.file,
             segments=len(batch.segments), words=batch.word_count)
        pending = list(batch.segments)
        last_error = ""
        attempt = 0
        repair_round = 0
        while pending and attempt < MAX_ATTEMPTS and not cancelled_now():
            attempt += 1
            try:
                results = engine.translate(pending, glossary)
            except QuotaExhaustedError as e:
                with lock:
                    first = not stop.is_set()
                    stop.set()
                if not first:  # outro lote em paralelo já reportou; este só encerra
                    return i, batch, pending, last_error, True
                if tm is not None:
                    tm.commit()
                current = getattr(engine, "current", engine)
                emit("quota_exhausted", index=i, total=len(batches), file=batch.file, engine=current.name,
                     provider=getattr(current, "provider", ""), model=getattr(current, "model", ""),
                     reset=e.reset, reason="quota", message=str(e)[:300])
                raise
            except TransientEngineError as e:
                if cancelled_now():  # parada por cota/cancelamento: a falha é consequência, não um erro do motor
                    return i, batch, pending, last_error, True
                last_error = str(e)
                wait = 2.0 if (attempt == 1 and "not recognized" in last_error) else BACKOFF_BASE ** attempt
                log(f"  erro transitório (tentativa {attempt}/{MAX_ATTEMPTS}): {e}"
                    + (f" — backoff {wait:.0f}s" if attempt < MAX_ATTEMPTS else ""))
                emit("retry", index=i, attempt=attempt, error=str(e), file=batch.file)
                if attempt < MAX_ATTEMPTS:
                    waited = 0.0
                    while waited < wait and not cancelled_now():
                        sleep(min(0.5, wait - waited))
                        waited += 0.5
                continue

            bad = _validate_batch(pending, results)
            by_id = {r.seg_id: r for r in results}
            for seg in pending:
                if seg.seg_id in bad:
                    continue
                r = by_id[seg.seg_id]
                seg.target_text = r.text
                mark(seg, UNCERTAIN if r.uncertain else TRANSLATED, "modelo marcou uncertain" if r.uncertain else None)
                if tm is not None:
                    key = cache_key(seg.source_text, glossary.version, glossary.target_lang, engine.name)
                    tm.put(key, seg.source_text, seg.target_text, r.uncertain,
                           engine.name, glossary.version, glossary.target_lang)
            if tm is not None:
                tm.commit()  # checkpoint atômico por batch

            pending = [s for s in pending if s.seg_id in bad]
            if pending:
                repair_round += 1
                if repair_round >= 2:  # erro permanente: 2 validações reprovadas
                    for seg in pending:
                        mark(seg, ERROR, bad[seg.seg_id])
                    log(f"  {len(pending)} segmento(s) ERROR após 2 reparos: mantêm original")
                    emit("repair_failed", index=i, count=len(pending), file=batch.file)
                    pending = []
                else:
                    log(f"  reparo: {len(pending)} segmento(s) reprovaram — {list(bad.values())[:2]}")
                    emit("repair", index=i, count=len(pending), details=list(bad.values())[:2], file=batch.file)
        for seg in pending:  # esgotou tentativas transitórias (cancelamento deixa PENDING)
            if cancelled_now():
                break
            mark(seg, ERROR, "motor indisponível (tentativas esgotadas)")
        for seg in batch.segments:
            propagate(seg)
        if cancelled_now():
            return i, batch, pending, last_error, True
        emit("batch_completed", index=i, total=len(batches), file=batch.file)
        trip = False
        with lock:  # disjuntor: o motor não está respondendo
            if pending and not cancelled_now():
                breaker["failed"] += 1
                breaker["segments"].extend(pending)
                if breaker["failed"] >= BREAKER_BATCHES and not stop.is_set():
                    stop.set()
                    trip = True
            elif not pending:
                breaker["failed"], breaker["segments"] = 0, []
            failed_count = breaker["failed"]
        if trip:
            current = getattr(engine, "current", engine)
            message = f"{failed_count} lotes seguidos falharam: {last_error[:200]}"
            emit("quota_exhausted", index=i, total=len(batches), file=batch.file, engine=current.name,
                 provider=getattr(current, "provider", ""), model=getattr(current, "model", ""),
                 reset="", reason="unavailable", message=message)
            raise EngineUnavailableError(message, getattr(current, "provider", ""))
        return i, batch, pending, last_error, False

    fatal: Exception | None = None
    skipped_files: list[str] = []
    saved_cancel = _chain_cancel(engine, stop.is_set)
    pool = ThreadPoolExecutor(max_workers=max(1, min(int(workers or 1), len(batches) or 1)))
    try:
        futures = [pool.submit(run_batch, i, b) for i, b in enumerate(batches, 1)]
        for future in as_completed(futures):
            try:
                i, batch, pending, last_error, skipped = future.result()
            except QuotaExhaustedError as exc:
                fatal = fatal or exc
                stop.set()
                continue
            if skipped:
                skipped_files.append(batch.file)
                continue
    finally:
        pool.shutdown(wait=True)
        for eng, prev in saved_cancel:
            eng.cancel_check = prev

    if isinstance(fatal, EngineUnavailableError):  # nada fica como erro: dá para retomar ou trocar de motor
        for seg in segments:
            if seg.status == ERROR and seg.note.startswith("motor indisponível"):
                mark(seg, PENDING, "")
    if fatal is not None:
        if tm is not None:
            tm.commit()
        raise fatal
    if is_cancelled and is_cancelled():
        if tm is not None:
            tm.commit()
        emit("cancelled", index=len(batches), total=len(batches), file=skipped_files[0] if skipped_files else None)
        cancelled = True

    counts: dict[str, int] = {}
    for seg in segments:
        counts[seg.status] = counts.get(seg.status, 0) + 1
    if not cancelled:
        emit("translation_finished", counts=counts, exact_cache_hits=hits, cross_model_cache_hits=cross_hits)
    return counts
