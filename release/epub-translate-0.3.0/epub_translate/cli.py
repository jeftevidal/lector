"""Command line entry point for the EPUB translation workflow."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .cache import TMCache
from .config import (PROJECT_ROOT, batch_settings, load_catalog, load_preferences, provider_settings,
                     save_preferences)
from .glossary import Glossary
from .ingest import IngestError, load_book
from .model import SKIPPED
from .pipeline import apply_plan, build_plan
from .report import build_report_json, build_report_md
from .translate import (BATCH_MAX_SEGMENTS, BATCH_MAX_WORDS, ClaudeCliEngine,
                        AgyCliEngine, CodexCliEngine, IdentityEngine, OllamaCliEngine,
                        PseudoEngine)
from .ui import show_book_summary, show_qa, translation_progress, wizard

PROVIDERS = ["claude", "codex", "agy", "ollama", "identity", "pseudo"]


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="epub-translate", description="Traduz EPUB preservando a estrutura e a marcação.")
    p.add_argument("input", nargs="?", help="arquivo .epub de entrada (nunca é modificado)")
    p.add_argument("--to", dest="target_lang", default=None, help="idioma de destino (ex.: pt-BR)")
    p.add_argument("--from", dest="source_lang", default=None, help="idioma de origem (ex.: en)")
    p.add_argument("--glossary", default=None, help="glossário JSON")
    p.add_argument("--provider", choices=PROVIDERS, default=None, help="CLI local para tradução")
    p.add_argument("--engine", choices=PROVIDERS, default=None, help="alias retrocompatível de --provider")
    p.add_argument("--model", default=None, help="modelo sugerido ou nome customizado")
    p.add_argument("--out", default=None, help="EPUB de saída (default: <base>.<idioma>.epub)")
    p.add_argument("--only", default=None, help="traduz apenas um documento (debug)")
    p.add_argument("--dry-run", action="store_true", help="estima sem chamar o motor")
    p.add_argument("--resume", action="store_true", help="compatibilidade: reexecução já retoma pelo cache")
    p.add_argument("--cache", default=None, help="arquivo .tmcache")
    p.add_argument("--no-cache", action="store_true", help="não lê nem grava cache")
    p.add_argument("--reuse-cache", action="store_true", help="permite reutilizar traduções de outros modelos")
    p.add_argument("--report", default=None, help="caminho do relatório Markdown")
    p.add_argument("--interactive", action="store_true", help="abre wizard com seleção por setas")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _make_engine(provider: str, model: str, settings: dict):
    if provider == "identity":
        return IdentityEngine()
    if provider == "pseudo":
        return PseudoEngine()
    options = {key: settings[key] for key in ("timeout", "batch_segments", "batch_words") if key in settings}
    binary = settings.get("binary", provider)
    if provider == "claude":
        return ClaudeCliEngine(model=model, claude_bin=binary, **options)
    cls = {"codex": CodexCliEngine, "agy": AgyCliEngine, "ollama": OllamaCliEngine}[provider]
    return cls(model=model, binary=binary, **options)


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        catalog = load_catalog()
        preferences = load_preferences()
    except ValueError as exc:
        print(f"ERRO de configuração: {exc}", file=sys.stderr)
        return 2

    selected_provider = args.provider or args.engine
    missing_provider = not selected_provider
    missing_model = selected_provider not in {"identity", "pseudo"} and not args.model
    interactive = args.interactive or (
        sys.stdin.isatty()
        and (not args.input or not args.target_lang or missing_provider or missing_model)
    )
    if interactive:
        try:
            selected = wizard(args.input, catalog, preferences, default_glossary=args.glossary or "")
        except (KeyboardInterrupt, EOFError):
            print("Wizard cancelado.")
            return 0
        except RuntimeError as exc:
            print(f"ERRO: {exc}", file=sys.stderr)
            return 2
        args.input = selected["input"]
        args.provider = selected["provider"]
        args.model = selected["model"]
        args.target_lang = selected["target_lang"]
        args.glossary = selected["glossary"]
        args.reuse_cache = selected["reuse_cache"]

    if not args.input:
        parser.error("informe um EPUB ou use --interactive")
    provider = args.provider or args.engine or preferences.get("provider", "claude")
    model = args.model
    if model is None and preferences.get("provider", "claude") == provider:
        model = preferences.get("model")
    if provider not in {"identity", "pseudo"}:
        settings = provider_settings(provider, model or "", catalog)
        model = model or (settings.get("models") or ["sonnet"])[0]
    else:
        model = provider
    target_lang = args.target_lang or preferences.get("target_lang", "pt-BR")
    glossary_path = args.glossary or preferences.get("glossary") or None

    try:
        glossary = Glossary.load(glossary_path)
    except (ValueError, OSError) as exc:
        print(f"ERRO no glossário: {exc}", file=sys.stderr)
        return 2
    glossary.target_lang = target_lang
    if args.source_lang:
        glossary.source_lang = args.source_lang
    try:
        book = load_book(args.input)
    except IngestError as exc:
        print(f"ERRO de ingest: {exc}", file=sys.stderr)
        return 2
    plan = build_plan(book, glossary, only=args.only)
    settings = batch_settings(provider, model, preferences.get("batch_mode", "default"), catalog) if provider not in {"identity", "pseudo"} else {}
    batch_segments = int(settings.get("batch_segments", BATCH_MAX_SEGMENTS))
    batch_words = int(settings.get("batch_words", BATCH_MAX_WORDS))
    if batch_segments < 1 or batch_words < 1:
        print("ERRO: batch_segments e batch_words devem ser positivos.", file=sys.stderr)
        return 2

    inp = Path(args.input)
    out_path = Path(args.out) if args.out else inp.with_name(f"{inp.stem}.{target_lang}.epub")
    report_path = Path(args.report) if args.report else out_path.with_suffix(".report.md")
    cache_path = Path(args.cache) if args.cache else out_path.with_suffix(".tmcache")
    engine_name = (provider if provider in {"identity", "pseudo"} else
                   f"claude-cli:{model}" if provider == "claude" else f"{provider}:{model}")
    exact_cache = cross_cache = 0
    if not args.no_cache:
        exact_cache, cross_cache = TMCache.preview_counts(
            cache_path, plan.pending, glossary.version, target_lang, engine_name, args.reuse_cache,
        )

    estimate = plan.estimate_calls(batch_segments, batch_words)
    show_book_summary(book, plan, engine_name, estimate, exact_cache, cross_cache)
    for warning in plan.warnings:
        print(f"AVISO: {warning}")
    if plan.skipped_files:
        print(f"Arquivos ignorados pelo glossário: {len(plan.skipped_files)}")

    try:
        save_preferences({"provider": provider, "model": model, "target_lang": target_lang,
                          "glossary": str(glossary_path) if glossary_path else "",
                          "reuse_cache": bool(args.reuse_cache),
                          "output_dir": preferences.get("output_dir", ""),
                          "batch_mode": preferences.get("batch_mode", "default")})
    except OSError as exc:
        print(f"AVISO: não foi possível salvar preferências: {exc}")

    if args.dry_run:
        print("Dry-run concluído. Nenhum motor foi chamado e nenhum cache foi alterado.")
        return 0
    if interactive:
        try:
            import questionary
            if not questionary.confirm("Iniciar a tradução com as opções acima?", default=False).ask():
                print("Tradução cancelada.")
                return 0
        except ImportError:
            print("ERRO: o modo interativo requer questionary.", file=sys.stderr)
            return 2

    engine = _make_engine(provider, model, settings)
    tm = None if args.no_cache else TMCache(cache_path)
    try:
        with translation_progress(estimate) as progress:
            result = apply_plan(plan, engine, tm, log=print if args.verbose else (lambda msg: None),
                                progress=progress, reuse_cache=args.reuse_cache)
    finally:
        if tm is not None:
            tm.close()

    result.stats.output_path = str(out_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(build_report_md(result.stats, plan.segments, result.findings), encoding="utf-8")
    report_path.with_suffix(".json").write_text(
        build_report_json(result.stats, plan.segments, result.findings), encoding="utf-8")
    show_qa(result.findings, result.stats, out_path if result.epub_bytes is not None else None)
    if result.epub_bytes is None:
        print(f"QA BLOCK: EPUB não empacotado. Consulte {report_path}.", file=sys.stderr)
        return 1
    out_path.write_bytes(result.epub_bytes)
    print(f"OK: {out_path}\nRelatório: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
