"""Terminal presentation kept separate from the translation pipeline."""

from __future__ import annotations

import shutil
import sys
from contextlib import nullcontext
from pathlib import Path

try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
    from rich.table import Table
except ImportError:  # allows the core CLI to remain usable before installation
    box = Console = Panel = Progress = Table = None


def _console():
    if not Console:
        return None
    stream = sys.stdout
    if getattr(stream, "encoding", "").lower().replace("-", "") != "utf8":
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    return Console(legacy_windows=False, safe_box=True)


def provider_choices(catalog: dict) -> list[tuple[str, str]]:
    options = []
    for provider, cfg in catalog.get("providers", {}).items():
        binary = cfg.get("binary", provider)
        state = "instalado" if shutil.which(binary) else "não encontrado"
        options.append((f"{provider} — {state}", provider))
    options.extend([("identity — teste sem tradução", "identity"), ("pseudo — teste local", "pseudo")])
    return options


def wizard(input_path: str | None, catalog: dict, preferences: dict,
           exact_cache: int = 0, cross_cache: int = 0, default_glossary: str = "") -> dict:
    try:
        import questionary
    except ImportError as exc:
        raise RuntimeError("Modo interativo requer a dependência questionary; execute instalar.bat.") from exc

    if not input_path:
        input_path = questionary.text("Caminho do arquivo EPUB:").ask()
    if not input_path:
        raise KeyboardInterrupt
    default_provider = preferences.get("provider", "claude")
    providers = dict(provider_choices(catalog))
    default_provider = default_provider if default_provider in providers else "claude"
    provider = questionary.select(
        "Provedor CLI:", choices=[questionary.Choice(title, value=value)
                                  for title, value in provider_choices(catalog)],
        default=default_provider,
    ).ask()
    if not provider:
        raise KeyboardInterrupt
    if provider in catalog.get("providers", {}):
        suggested = catalog["providers"][provider].get("models", [])
        choices = [questionary.Choice(m, value=m) for m in suggested]
        choices.append(questionary.Choice("Digitar modelo personalizado…", value="__custom__"))
        default_model = preferences.get("model")
        model = questionary.select("Modelo (ou escolha personalizado):", choices=choices,
                                  default=default_model if default_model in suggested else suggested[0]).ask()
        if model == "__custom__":
            model = questionary.text("Nome do modelo:", default=preferences.get("model", "")).ask()
    else:
        model = provider
    if not model:
        raise KeyboardInterrupt
    target_lang = questionary.text("Idioma de destino:", default=preferences.get("target_lang", "pt-BR")).ask()
    if not target_lang:
        raise KeyboardInterrupt
    glossary = questionary.text("Caminho do glossário (Enter para nenhum):",
                               default=default_glossary or preferences.get("glossary", "")).ask()
    label = (f"Reutilizar {cross_cache} segmento(s) do cache de outro modelo?" if cross_cache
             else "Permitir reaproveitar cache de outro modelo se houver correspondências?")
    reuse = questionary.confirm(label, default=preferences.get("reuse_cache", False)).ask()
    return {"input": input_path, "provider": provider, "model": model, "target_lang": target_lang,
            "glossary": glossary or None, "reuse_cache": bool(reuse)}


def show_book_summary(book, plan, engine_name: str, estimate: int,
                      exact_cache: int = 0, cross_cache: int = 0) -> None:
    c = _console()
    rows = [
        ("EPUB", f"{book.epub_version} - {len(book.files)} arquivos - spine {len(book.spine)}"),
        ("Idioma", f"{book.language or '?'} → {plan.glossary.target_lang}"),
        ("Conteúdo", f"{len(plan.docs)} documentos - {len(plan.segments)} segmentos - {sum(s.word_count for s in plan.pending)} palavras"),
        ("Motor", engine_name), ("Estimativa", f"~{estimate} chamada(s) - sem chamada ao motor"),
        ("Cache", f"{exact_cache} exatos - {cross_cache} de outros modelos"),
    ]
    if c and Table and Panel:
        table = Table(box=box.SIMPLE)
        table.add_column("Resumo", style="cyan", no_wrap=True)
        table.add_column("Valor")
        for key, value in rows:
            table.add_row(key, value)
        c.print(Panel(table, title="EPUB Translate - prévia", border_style="blue"))
    else:
        print("EPUB Translate - prévia")
        for key, value in rows:
            print(f"  {key}: {value}")


def show_qa(findings, stats, output_path: Path | None = None) -> None:
    c = _console()
    blocks = sum(f.severity == "BLOCK" for f in findings)
    warns = sum(f.severity == "WARN" for f in findings)
    if c and Panel and Table:
        table = Table(box=box.SIMPLE)
        table.add_column("QA", style="bold")
        table.add_column("Quantidade", justify="right")
        table.add_row("BLOCK", str(blocks))
        table.add_row("WARN", str(warns))
        table.add_row("Segmentos", str(stats.counts))
        if output_path:
            table.add_row("Saída", str(output_path))
        c.print(Panel(table, title="Resultado", border_style="red" if blocks else "green"))
        for finding in findings:
            c.print(str(finding), style="red" if finding.severity == "BLOCK" else "yellow")
    else:
        print(f"QA: {blocks} BLOCK, {warns} WARN - {stats.counts}")
        for finding in findings:
            print(str(finding))


def translation_progress(total_batches: int):
    if not Progress:
        return nullcontext(lambda event: print(f"[{event.get('event')}] {event}"))

    class _ProgressContext:
        def __enter__(self):
            self.progress = Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
                                     TextColumn("{task.completed}/{task.total}"), TimeRemainingColumn())
            self.progress.__enter__()
            self.task = self.progress.add_task("Preparando tradução", total=total_batches)
            def emit(event):
                kind = event.get("event")
                if kind == "batch_started":
                    self.progress.update(self.task, total=event["total"],
                                         description=f"{event.get('file', '')} - lote {event['index']}/{event['total']}")
                elif kind == "batch_completed":
                    self.progress.advance(self.task)
                elif kind in {"retry", "repair", "repair_failed"}:
                    self.progress.console.print(f"{kind}: {event}", style="yellow")
                elif kind == "cache_summary":
                    self.progress.console.print(f"Cache: {event['exact']} exatos - {event['cross_model']} entre modelos")
            self.emit = emit
            return emit
        def __exit__(self, *args):
            self.progress.__exit__(*args)
    return _ProgressContext()
