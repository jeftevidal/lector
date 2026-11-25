"""Servidor local do Lector; não faz chamadas externas por padrão."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import webbrowser
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lxml import etree
from fastapi import FastAPI, Request, File, Form, HTTPException, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .cache import TMCache
from .config import (BOOKS_DIR, atomic_write, atomic_write_bytes, CACHE_PATH, LAST_JOB_PATH, OUTPUT_DIR, MODELS_PATH, PREFERENCES_PATH, PROJECT_ROOT,
                     batch_settings, load_catalog, load_preferences, save_preferences)
from .glossary import Glossary
from .ingest import IngestError, load_book
from .pipeline import apply_plan, build_plan
from .qa import has_block
from .report import build_report_md
from .translate import (AgyCliEngine, ClaudeCliEngine, CodexCliEngine, FailoverEngine,
                        OllamaCliEngine, PseudoEngine, QuotaExhaustedError)

app = FastAPI(title="Lector", docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=500)
STATIC = Path(__file__).with_name("web_static")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
JOBS: dict[str, dict[str, Any]] = {}
LOCK = threading.Lock()
CONFIG_LOCK = threading.RLock()  # glossário, preferências e models.json: leitura-modificação-escrita sem corrida entre abas
MAX_UPLOAD_BYTES = 300 * 1024 * 1024
KEEP_JOBS = 5
BOOKS_MAX_AGE_DAYS = 90
_PLAN_CACHE: dict[tuple, Any] = {}
INDEX_HTML: str | None = None
_BINARY_CACHE: dict[str, tuple[float, str | None]] = {}
_BINARY_CACHE_TTL = 30.0


RUNNING = {"Traduzindo", "Iniciando", "Cancelando"}
LANG_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}


def _host_of(value: str) -> str:
    value = value.split("://")[-1].split("/")[0]
    if value.startswith("["):
        return value.split("]")[0] + "]"
    return value.rsplit(":", 1)[0] if ":" in value else value


@app.middleware("http")
async def local_only(request, call_next):
    """Bloqueia DNS-rebinding e requisições de outras origens (CSRF) contra a API local."""
    host = request.headers.get("host", "")
    origin = request.headers.get("origin")
    if _host_of(host) not in ALLOWED_HOSTS or (origin and _host_of(origin) not in ALLOWED_HOSTS):
        return JSONResponse({"detail": "Origem não permitida"}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "public, max-age=3600")
    return response


def _json(path: Path, fallback: dict | None = None, tolerant: bool = False) -> dict:
    if not path.exists():
        return fallback or {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if tolerant and not isinstance(value, dict):
            raise json.JSONDecodeError("não é objeto", "", 0)
    except (OSError, json.JSONDecodeError) as exc:
        if tolerant:  # estado de execução corrompido: guarda o arquivo ruim e segue
            try:
                path.replace(path.with_name(path.name + ".bad"))
            except OSError:
                pass
            return fallback or {}
        raise HTTPException(400, f"JSON inválido em {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise HTTPException(400, f"{path.name} precisa conter um objeto JSON")
    return value


def _save(path: Path, value: dict) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _binary(entry: dict) -> str | None:
    configured = str(entry.get("binary", ""))
    if not configured:
        return None
    now = time.monotonic()
    cached = _BINARY_CACHE.get(configured)
    if cached is not None and (now - cached[0] < _BINARY_CACHE_TTL):
        return cached[1]
    if Path(configured).is_file():
        resolved = str(Path(configured).resolve())
    else:
        resolved = shutil.which(configured)
    _BINARY_CACHE[configured] = (now, resolved)
    return resolved


def _store_upload(upload: UploadFile) -> Path:
    """Guarda o EPUB enviado na pasta do projeto (cache/livros), com nome estável pelo conteúdo."""
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1()
    size = 0
    with tempfile.NamedTemporaryFile(dir=BOOKS_DIR, suffix=".part", delete=False) as stream:
        part = Path(stream.name)
        try:
            while chunk := upload.file.read(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Arquivo grande demais (limite de 300 MB)")
                digest.update(chunk)
                stream.write(chunk)
        except BaseException:
            stream.close()
            part.unlink(missing_ok=True)
            raise
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", Path(upload.filename or "book").stem).strip(" .")[:150] or "book"
    target = BOOKS_DIR / f"{stem}-{digest.hexdigest()[:10]}.epub"
    if target.exists():
        part.unlink()
    else:
        part.replace(target)
    return target


def _epub_title(path: Path) -> str:
    """Título do livro nos metadados do EPUB (dc:title); vazio se não achar."""
    try:
        with zipfile.ZipFile(path) as zf:
            opf = next((n for n in zf.namelist() if n.endswith(".opf")), None)
            match = re.search(r"<dc:title[^>]*>([^<]+)", zf.read(opf).decode("utf-8", "replace")) if opf else None
    except (OSError, zipfile.BadZipFile, KeyError):
        return ""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", match.group(1)).strip(" .") if match else ""


def _output_name(source: Path, target_lang: str) -> str:
    """Nome do EPUB traduzido: nome original do arquivo + (IDIOMA). A cópia em cache/livros perde o sufixo de hash;
    se o nome original se perdeu (cópias antigas: 'livro', 'tmp...'), usa o título do próprio EPUB."""
    title = re.sub(r"-[0-9a-f]{10}$", "", source.stem) if source.parent == BOOKS_DIR else source.stem
    if re.fullmatch(r"(livro|book|tmp[A-Za-z0-9_]*)", title):
        title = _epub_title(source) or title
    return f"{title} ({target_lang.upper()}).epub"


def _reset_seconds(text: str) -> int:
    """'4h18m' / '3h35m33s' / '12m' -> segundos (0 se não reconhecer)."""
    def part(pattern: str) -> int:
        match = re.search(pattern, text or "")
        return int(match.group(1)) if match else 0
    return (part(r"([0-9]+) ?h") * 60 + part(r"([0-9]+) ?m(?!s)")) * 60 + part(r"([0-9]+) ?s")


def _quota_active(prev: dict) -> bool:
    """O job anterior parou por cota e o tempo de volta informado pelo provedor ainda não passou."""
    seconds = _reset_seconds(prev.get("quota_reset", ""))
    if not (prev.get("quota") and prev.get("quota_reason") != "unavailable" and seconds):
        return False
    try:
        stopped = datetime.fromisoformat(str(prev.get("updated"))).timestamp()
    except ValueError:
        return False
    return time.time() < stopped + seconds


def _cleanup_books(max_age_days: int = BOOKS_MAX_AGE_DAYS) -> int:
    """Remove cópias de EPUBs enviados que não são usadas há muito tempo (o original continua onde você o escolheu).
    Nunca remove o livro do último job (é o que o Retomar usa). Devolve quantas cópias removeu."""
    last = _json(LAST_JOB_PATH, {}, tolerant=True).get("input")
    keep = Path(last).resolve() if last else None
    removed = 0
    for book in BOOKS_DIR.glob("*.epub"):
        if (keep and book.resolve() == keep) or time.time() - book.stat().st_mtime < max_age_days * 86400:
            continue
        book.unlink(missing_ok=True)
        removed += 1
    return removed


def _prune_jobs() -> None:
    """Mantém na memória só os últimos jobs encerrados (eventos e resultado de livros grandes pesam)."""
    finished = sorted((j.get("updated", 0), jid) for jid, j in JOBS.items() if j["status"] not in RUNNING)
    for _, jid in finished[:-KEEP_JOBS] if KEEP_JOBS else finished:
        JOBS.pop(jid, None)


def _summary(segments, findings) -> dict:
    errors: dict[str, int] = {}
    for s in segments:
        if s.status == "error":
            errors[s.file] = errors.get(s.file, 0) + 1
    return {"total": len(segments), "uncertain": sum(s.status == "uncertain" for s in segments),
            "errors": sum(errors.values()), "errors_by_file": errors,
            "warn": sum(f.severity == "WARN" for f in findings)}


def _limits(provider: str, model: str, mode: str) -> dict:
    return batch_settings(provider, model, mode, load_catalog())


def _engine(provider: str, model: str, settings: dict):
    args = {key: settings[key] for key in ("timeout", "batch_segments", "batch_words", "binary") if key in settings}
    if provider == "pseudo":
        return PseudoEngine()
    if provider == "claude":
        return ClaudeCliEngine(model=model, claude_bin=args.pop("binary", "claude"), **args)
    cls = {"codex": CodexCliEngine, "agy": AgyCliEngine, "ollama": OllamaCliEngine}.get(provider)
    if cls is None:
        raise HTTPException(400, "Provedor inválido")
    return cls(model=model, **args)


@app.get("/", response_class=HTMLResponse)
def index():
    global INDEX_HTML
    if INDEX_HTML is None:
        INDEX_HTML = (STATIC / "index.html").read_text(encoding="utf-8")
    return INDEX_HTML


@app.get("/api/state")
def state():
    catalog = load_catalog()
    providers = {name: {**entry, "installed": bool(_binary(entry)), "path": _binary(entry)}
                 for name, entry in catalog["providers"].items()}
    glossary = Glossary.load(PROJECT_ROOT / "glossary.json")
    last_job = _json(LAST_JOB_PATH, {}, tolerant=True)
    active = next(({"id": jid, "status": j["status"]} for jid, j in JOBS.items() if j["status"] in RUNNING), None)
    if last_job.get("status") == "Traduzindo" and active is None:
        last_job["status"] = "Parada"  # o servidor foi reiniciado no meio do job: ele pode ser retomado
        _save(LAST_JOB_PATH, last_job)
    return {"providers": providers, "models": catalog, "preferences": {"reuse_cache": True, "review_uncertain": True, **load_preferences()},
            "glossary": {"translate_map": glossary.translate_map, "keep_in_source": glossary.keep_in_source,
                         "never_translate": glossary.never_translate, "main_content_only": glossary.main_content_only,
                         "tone": glossary.tone}, "last_job": last_job, "active_job": active}


@app.get("/api/usage")
def usage_summary():
    """Uso medido pelo Lector (não é a cota oficial do provedor)."""
    from . import usage
    return {"usage": usage.summary(), "windows": list(usage.WINDOWS)}


@app.get("/api/usage/official")
def official_usage_endpoint(refresh: bool = False):
    """Cota oficial (claude/codex). Só consulta a rede se o usuário ligou a opção nas preferências."""
    if not load_preferences().get("official_usage", False):
        return {"enabled": False, "providers": {}}
    from . import official_usage
    return {"enabled": True, "providers": official_usage.snapshot(force=refresh)}


@app.post("/api/providers/verify")
def verify_provider(body: dict):
    with CONFIG_LOCK:
        return _verify_provider(body)


def _verify_provider(body: dict):
    _BINARY_CACHE.clear()
    provider, binary = body.get("provider"), body.get("binary", "")
    catalog = load_catalog()
    if provider not in catalog["providers"] or not isinstance(binary, str):
        raise HTTPException(400, "Provedor ou caminho inválido")
    catalog["providers"][provider]["binary"] = binary.strip() or provider
    _save(MODELS_PATH, catalog)
    path = _binary(catalog["providers"][provider])
    return {"provider": provider, "installed": bool(path), "path": path}


def _inspect_path(path: Path, body: dict) -> dict:
    try:
        glossary = Glossary.load(PROJECT_ROOT / "glossary.json")
        glossary.main_content_only = bool(body.get("main_content_only", glossary.main_content_only))
        glossary.target_lang = str(body.get("target_lang", "pt-BR"))
        if body.get("source_lang"):
            glossary.source_lang = str(body["source_lang"])
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size, glossary.version, glossary.main_content_only,
               glossary.source_lang, glossary.target_lang)
        cached = _PLAN_CACHE.get(key)  # trocar modelo/preset na tela não reanalisa o livro
        if cached:
            book, plan = cached
        else:
            book = load_book(path)
            plan = build_plan(book, glossary)
            _PLAN_CACHE.clear()
            _PLAN_CACHE[key] = (book, plan)
    except (IngestError, ValueError, OSError, etree.XMLSyntaxError, zipfile.BadZipFile) as exc:
        raise HTTPException(400, f"EPUB inválido: {exc}") from exc
    provider = body.get("provider", "claude")
    model = str(body.get("model", "sonnet"))
    mode = body.get("batch_mode", "default")
    engine_name = f"claude-cli:{model}" if provider == "claude" else f"{provider}:{model}"
    cache_path = CACHE_PATH
    cached_by_file: dict[str, int] = {}
    exact, cross = TMCache.preview_counts(cache_path, plan.pending, glossary.version,
                                          glossary.target_lang, engine_name,
                                          bool(body.get("reuse_cache", True)), cached_by_file)
    settings = _limits(provider, model, mode) if provider in load_catalog()["providers"] else {"batch_segments": 60, "batch_words": 2000}
    chapters: dict[str, int] = {}
    for seg in plan.pending:
        chapters[seg.file] = chapters.get(seg.file, 0) + 1
    batches = {}
    if provider in load_catalog()["providers"]:
        for preset in ("less", "default", "more"):
            lim = _limits(provider, model, preset)
            batches[preset] = {"segments": lim["batch_segments"], "words": lim["batch_words"],
                               "calls": plan.estimate_calls(lim["batch_segments"], lim["batch_words"])}
    return {"filename": path.name, "epub_version": book.epub_version, "language": book.language,
            "chapters": [{"file": f, "total": n, "cached": cached_by_file.get(f, 0)} for f, n in chapters.items()],
            "chapter_count": len(book.spine), "batches": batches, "skipped_files": plan.skipped_files,
            "segments": len(plan.pending), "words": sum(s.word_count for s in plan.pending),
            "calls": plan.estimate_calls(settings["batch_segments"], settings["batch_words"]),
            "exact_cache": exact, "cross_cache": cross, "pending": max(0, len(plan.pending)-exact-cross)}


def _options(raw: str | None) -> dict:
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Opções inválidas") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "Opções devem ser um objeto JSON")
    if not LANG_RE.match(str(body.get("target_lang", "pt-BR"))) or not LANG_RE.match(str(body.get("source_lang") or "en")):
        raise HTTPException(400, "Idioma inválido")
    return body


def _epub_path(value: str) -> Path:
    path = Path(value)
    if path.suffix.lower() != ".epub" or not path.is_file():
        raise HTTPException(400, "Caminho precisa ser um arquivo .epub existente")
    return path


@app.post("/api/inspect")
def inspect(file: UploadFile | None = File(None), path: str | None = Form(None),
            options: str | None = Form(None)):
    body = _options(options)
    if file is not None:
        suffix = Path(file.filename or "book.epub").suffix.lower()
        if suffix != ".epub":
            raise HTTPException(400, "Selecione um arquivo EPUB")
        stored = _store_upload(file)  # guardado já na inspeção: o job reaproveita, sem enviar o livro de novo
        return {**_inspect_path(stored, body), "book_path": str(stored)}
    if path:
        return _inspect_path(_epub_path(path), body)
    raise HTTPException(400, "Envie um EPUB ou informe um caminho local")


def _run_job(job_id: str, source: Path, options: dict):
    job = JOBS[job_id]
    emit_lock = job.setdefault("lock", threading.Lock())

    def emit(event: dict):
        with emit_lock:  # vários lotes em paralelo emitem ao mesmo tempo: grava um de cada vez
            _emit_locked(event)

    def _emit_locked(event: dict):
        event["timestamp"] = event.get("ts", datetime.now(timezone.utc).isoformat())
        job["events"].append(event)
        job["updated"] = time.time()
        if event.get("event") == "batch_completed":
            job["completed"] = job.get("completed", 0) + 1
        job["max_done"] = max(job.get("max_done", 0), event.get("done_segments", 0) or 0)
        if event.get("event") in {"batch_completed", "cancelled", "quota_exhausted"}:
            _save(LAST_JOB_PATH, {"id": job_id, "input": str(source), "output": str(job["output"]),
                                  "status": "Parada" if event["event"] in {"cancelled", "quota_exhausted"} else "Traduzindo",
                                  "quota": event["event"] == "quota_exhausted",
                                  **({"quota_provider": event.get("provider", ""), "quota_model": event.get("model", ""),
                                      "quota_engine": event.get("engine", ""), "quota_reset": event.get("reset", ""),
                                      "quota_reason": event.get("reason", "quota")}
                                     if event["event"] == "quota_exhausted" else {}),
                                  "done": job["max_done"], "total": event.get("total_segments", 0),
                                  "batch": job.get("completed", 0) if event["event"] == "batch_completed" else max(job.get("completed", 0), event.get("index", 0)),
                                  "batches": event.get("total", 0),
                                  "updated": event["timestamp"]})
    try:
        catalog = load_catalog()
        with CONFIG_LOCK:
            prefs = {**load_preferences(), **options}
            save_preferences({k: prefs[k] for k in ("provider", "model", "target_lang", "output_dir", "reuse_cache", "batch_mode", "fallback", "review_uncertain", "official_usage") if k in prefs})
        glossary = Glossary.load(PROJECT_ROOT / "glossary.json")
        glossary.target_lang = str(prefs.get("target_lang", "pt-BR"))
        glossary.source_lang = str(prefs.get("source_lang") or glossary.source_lang)
        glossary.main_content_only = bool(prefs.get("main_content_only", glossary.main_content_only))
        book = load_book(source)
        plan = build_plan(book, glossary)
        provider, model = prefs.get("provider", "pseudo"), prefs.get("model", "pseudo")
        settings = _limits(provider, model, prefs.get("batch_mode", "default")) if provider in catalog["providers"] else {}
        engine = _engine(provider, model, settings)
        # "Mais rápido" (preset less) lança até 3 lotes em paralelo; o catálogo pode limitar com max_workers
        workers = min(3, int(catalog["providers"].get(provider, {}).get("max_workers", 3))) if prefs.get("batch_mode") == "less" else 1
        fallback = None
        fb = str(prefs.get("fallback") or "")
        if "|" in fb and fb != f"{provider}|{model}":
            fb_provider, fb_model = fb.split("|", 1)
            if fb_provider in catalog["providers"]:
                fallback = _engine(fb_provider, fb_model, _limits(fb_provider, fb_model, prefs.get("batch_mode", "default")))
        for eng in (engine, fallback):
            if eng is not None:
                eng.cancel_check = job["cancel"].is_set
        prev = _json(LAST_JOB_PATH, {}, tolerant=True)
        primary_name = engine.name
        skip_primary = bool(fallback is not None and prev.get("quota_provider") == provider
                            and prev.get("quota_model") == model and _quota_active(prev))
        first, second = (fallback, engine) if skip_primary else (engine, fallback)
        engine = FailoverEngine(first, second, on_switch=lambda old, new: emit(
            {"event": "engine_switched", "from": old, "to": new}))
        if skip_primary:  # a cota do principal ainda não voltou: começa direto no reserva
            emit({"event": "engine_switched", "from": primary_name, "to": first.name, "resumed": True})
        retry_files = options.get("retry_files")
        only_files = {str(f) for f in retry_files} if isinstance(retry_files, list) and retry_files else None
        default_out = OUTPUT_DIR if source.parent == BOOKS_DIR else source.parent
        output_dir = Path(prefs.get("output_dir") or default_out)
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / _output_name(source, glossary.target_lang)
        if output.resolve().parent != output_dir.resolve():
            raise ValueError("destino de saída inválido")
        job["output"] = str(output)
        counts: dict[str, int] = {}
        for seg in plan.pending:
            counts[seg.file] = counts.get(seg.file, 0) + 1
        job["chapters"] = [{"file": f, "total": n} for f, n in counts.items()]
        emit({"event": "plan", "chapters": job["chapters"], "total_segments": len(plan.pending)})
        cache = TMCache(CACHE_PATH)
        try:
            result = apply_plan(plan, engine, cache, progress=emit,
                               reuse_cache=bool(prefs.get("reuse_cache", True)) or bool(options.get("retry_errors")),
                               review=bool(prefs.get("review_uncertain", True)),
                               workers=workers, only_files=only_files,
                               is_cancelled=job["cancel"].is_set)
        finally:
            cache.close()
        result.stats.output_path = str(output)
        report = output.with_suffix(".report.md")
        atomic_write(report, build_report_md(result.stats, plan.segments, result.findings))
        if result.epub_bytes is not None:
            atomic_write_bytes(output, result.epub_bytes)
        job["result"] = {"stats": result.stats.__dict__, "findings": [f.__dict__ for f in result.findings],
                          "segments": [{"n": n, "id": s.seg_id, "file": s.file, "source": s.source_text,
                                        "translation": s.target_text, "status": s.status, "note": s.note}
                                       for n, s in enumerate(plan.segments, 1)], "summary": _summary(plan.segments, result.findings),
                          "report": str(report),
                          "download": f"/api/jobs/{job_id}/download", "report_text": report.read_text(encoding="utf-8"),
                          "cancelled": result.cancelled}
        final = "Parada" if result.cancelled else ("Concluído" if result.epub_bytes else "QA bloqueou")
        emit({"event": "finished", "status": final, "total_segments": len(plan.segments)})
        prev = _json(LAST_JOB_PATH, {}, tolerant=True)
        translatable = [s for s in plan.segments if s.status != "skipped"]  # plan.pending esvazia depois de traduzir
        done = sum(s.status in ("translated", "cached", "uncertain") for s in translatable)
        errors = sum(s.status == "error" for s in translatable)
        _save(LAST_JOB_PATH, {"id": job_id, "input": str(source), "output": str(output), "status": final,
                              "done": done if final != "Parada" else max(done, prev.get("done", 0)),
                              "total": len(translatable), "batch": prev.get("batch", 0),
                              "batches": prev.get("batches", 0), "errors": errors,
                              "updated": datetime.now(timezone.utc).isoformat()})
        job["status"] = final  # só depois do evento final: o SSE só encerra com status ≠ rodando
    except QuotaExhaustedError as exc:
        job["quota"] = {"provider": exc.provider, "reset": exc.reset, "message": str(exc)}
        emit({"event": "finished", "status": "Parada", "quota": True})
        job["status"] = "Parada"
    except Exception as exc:
        try:
            prev = _json(LAST_JOB_PATH, {}, tolerant=True)
            _save(LAST_JOB_PATH, {**prev, "id": job_id, "input": str(source), "status": "Erro", "error": str(exc)[:300],
                                  "quota": False, "updated": datetime.now(timezone.utc).isoformat()})
        except Exception:
            pass
        emit({"event": "error", "message": str(exc)})
        job["status"] = "Erro"


@app.post("/api/jobs", status_code=202)
def start_job(options: str = Form(...), file: UploadFile | None = File(None), path: str | None = Form(None)):
    body = _options(options)
    with LOCK:
        if any(j["status"] in RUNNING for j in JOBS.values()):
            raise HTTPException(409, "Já existe uma tradução em andamento")
        if file:
            source = _store_upload(file)
        elif path:
            source = _epub_path(path)
        else:
            raise HTTPException(400, "Envie um EPUB ou informe um caminho")
        if not source.is_file():
            raise HTTPException(400, "Arquivo EPUB não encontrado")
        _prune_jobs()
        if source.parent == BOOKS_DIR:
            os.utime(source)  # livro em uso: não conta como antigo na limpeza
        jid = uuid.uuid4().hex
        JOBS[jid] = {"status": "Iniciando", "events": [], "cancel": threading.Event(), "updated": time.time()}
        JOBS[jid]["status"] = "Traduzindo"
        threading.Thread(target=_run_job, args=(jid, source, body), daemon=True).start()
    return {"id": jid, "status": "Traduzindo"}


@app.get("/api/jobs/{job_id}/events")
async def events(job_id: str, request: Request):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Tradução não encontrada")
    try:  # reconexão do EventSource: continua do último evento recebido, sem repetir
        start = int(request.headers.get("last-event-id", "-1")) + 1
    except ValueError:
        start = 0
    async def stream():
        sent = max(0, start)
        while True:
            while sent < len(job["events"]):
                event = job["events"][sent]
                yield f"id: {sent}\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                sent += 1
            if job["status"] not in RUNNING and sent >= len(job["events"]):
                break
            yield ": ping\n\n"
            await asyncio.sleep(0.5)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Tradução não encontrada")
    job["cancel"].set()
    if job["status"] in {"Traduzindo", "Iniciando"}:
        job["status"] = "Cancelando"  # ainda conta como rodando até a thread encerrar (evita corrida com Retomar)
    return {"status": "Parada solicitada"}


@app.get("/api/jobs/{job_id}/result")
def result(job_id: str, filter: str = "all", limit: int = 0):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Tradução não encontrada")
    if "result" not in job:
        return {"status": job["status"], "events": job["events"]}
    value = dict(job["result"])
    if filter == "uncertain":
        value["segments"] = [s for s in value["segments"] if s["status"] == "uncertain"]
    elif filter == "warn":
        warned_files = {f["file"] for f in value["findings"] if f["severity"] == "WARN"}
        value["segments"] = [s for s in value["segments"] if s["file"] in warned_files]
    elif filter == "error":
        value["segments"] = [s for s in value["segments"] if s["status"] == "error"]
    value["matching"] = len(value["segments"])
    if limit > 0:
        value["segments"] = value["segments"][:limit]
    return value


@app.get("/api/jobs/{job_id}/report")
def report(job_id: str):
    job = JOBS.get(job_id)
    if not job or "result" not in job:
        raise HTTPException(404, "Relatório ainda não está disponível")
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(job["result"]["report_text"])


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str):
    job = JOBS.get(job_id)
    path = Path(job.get("output", "")) if job else Path()
    if not job or not path.is_file():
        raise HTTPException(404, "EPUB ainda não está disponível")
    return FileResponse(path, filename=path.name, media_type="application/epub+zip")


@app.post("/api/open-folder")
def open_folder(body: dict):
    raw = body.get("path")
    if not isinstance(raw, str) or not raw.strip() or raw.startswith("\\\\"):
        raise HTTPException(400, "Caminho inválido")
    path = Path(raw).resolve()
    if path.is_file():
        path = path.parent
    if not path.exists():
        raise HTTPException(404, "Pasta não encontrada")
    if os.name == "nt":
        os.startfile(str(path))
    else:
        subprocess.Popen(["xdg-open", str(path)])
    return {"opened": str(path)}


@app.put("/api/glossary")
def update_glossary(body: dict):
    with CONFIG_LOCK:
        return _update_glossary(body)


def _update_glossary(body: dict):
    current = _json(PROJECT_ROOT / "glossary.json", {})
    for key in ("translate_map", "keep_in_source", "never_translate", "main_content_only"):
        if key in body:
            if key == "translate_map" and (not isinstance(body[key], dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in body[key].items())):
                raise HTTPException(400, "translate_map deve mapear textos para textos")
            if key in {"keep_in_source", "never_translate"} and (not isinstance(body[key], list) or not all(isinstance(v, str) for v in body[key])):
                raise HTTPException(400, f"{key} deve ser uma lista de textos")
            if key == "main_content_only" and not isinstance(body[key], bool):
                raise HTTPException(400, "main_content_only deve ser booleano")
            current[key] = body[key]
    _save(PROJECT_ROOT / "glossary.json", current)
    return {"saved": True}


@app.put("/api/preferences")
def update_preferences(body: dict):
    with CONFIG_LOCK:
        return _update_preferences(body)


def _update_preferences(body: dict):
    allowed = {"provider", "model", "target_lang", "output_dir", "reuse_cache", "batch_mode", "fallback", "review_uncertain", "official_usage"}
    prefs = load_preferences()
    prefs.update({k: v for k, v in body.items() if k in allowed})
    if prefs.get("batch_mode", "default") not in {"less", "default", "more"}:
        raise HTTPException(400, "batch_mode inválido")
    if not isinstance(prefs.get("fallback", ""), str):
        raise HTTPException(400, "fallback inválido")
    save_preferences(prefs)
    return prefs


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(prog="lector-web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    _cleanup_books()
    for part in BOOKS_DIR.glob("*.part"):  # restos de envios interrompidos (lixo criado pelo próprio Lector)
        part.unlink(missing_ok=True)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://{args.host}:{args.port}")).start()
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
