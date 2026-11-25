# Lector (`epub-translate`)

Local EPUB book translator to Brazilian Portuguese (`pt-BR`) featuring a localhost web interface, an interactive terminal wizard, and a CLI powered by AI CLIs installed on your machine. Original `.epub` files are never modified.

---

## Overview

Lector (`epub-translate` `0.3.0`) extracts translatable text blocks from `.epub` chapters while replacing inline XHTML/XML markup with opaque placeholders (`⟦n⟧…⟦/n⟧`), translates segments in batches through local AI CLIs, validates terminology and structural integrity, and repacks a valid `.epub` alongside a Markdown verification report (`.report.md`).

---

## Features

- **Structure-preserving EPUB pipeline:** Protects inline tags (`<em>`, `<strong>`, `<a>`, etc.) and preserves images, stylesheets, XML prologues, CRLF line endings, and OPF metadata (`lxml>=5.0`).
- **Single-screen Web UI (`Screen v3`):** Local FastAPI server (`127.0.0.1:8788`) with vanilla HTML/CSS/JS frontend (no Node runtime or build step), real-time SSE progress updates, chapter-level status, side-by-side segment review, and a visual glossary editor.
- **Multi-CLI provider support:** Supports `claude`, `codex`, `agy`, and `ollama` (configured via `models.json`), plus deterministic `identity` and `pseudo` engines for testing and dry runs.
- **Shared SQLite translation memory:** Stores translations in `cache/lector.tmcache` (SQLite WAL mode) with exact lookup, intra-book deduplication, optional cross-model cache reuse (`--reuse-cache`), and batched cross-cache preview lookups (`v0.3.0`).
- **Resilience & quota recovery:** Automatic retries with backoff, placeholder repair rounds, a circuit breaker after consecutive failed batches, optional automatic fallback provider/model (`Se a cota acabar`), clean interruption/cancellation (`taskkill /T /F` on Windows), and resume from `last_job.json`.
- **Verification & structural QA:** Post-translation checks for glossary mappings, preserved terms, and multi-digit numbers (`verify.py`, with optional second-pass review for uncertain segments), plus structural QA gates (`qa.py`) prior to atomic EPUB packaging.
- **Strict localhost security:** Middleware restricts `Host` and `Origin` headers to `127.0.0.1`, `localhost`, and `[::1]`, rejecting external/LAN requests (`HTTP 403`).

---

## Requirements

- **OS:** Windows 10 or Windows 11 (supports long paths `>= 255` characters).
- **Python:** `3.11` or higher on `PATH` (select *Add Python to PATH* during Python setup).
- **AI CLI (at least one installed and authenticated):**
  - `claude` (Claude Code CLI)
  - `codex` (OpenAI Codex CLI)
  - `agy` (Antigravity CLI)
  - `ollama` (local models via Ollama)
  - *(Custom executable paths can also be specified in the Web UI or `models.json`.)*
- **Internet connection (first run):** Needed to install Python dependencies into `.venv` (`fastapi>=0.110`, `uvicorn>=0.27`, `lxml>=5.0`, `rich>=13.0`, `questionary>=2.0`, `python-multipart>=0.0.9`; dev dependencies: `pytest>=8.0`, `httpx2>=0.1`).

---

## Starting and Stopping the Server

### Start (`iniciar servidor.bat`)

Double-click **`iniciar servidor.bat`** in the repository root (or inside `release/` or the packaged `epub-translate-*` folder).

What `iniciar servidor.bat` does:
1. Locates the `epub-translate-*` application folder and delegates to its `iniciar servidor.bat`.
2. Checks for Python `>= 3.11` and creates a local `.venv` if it does not exist yet.
3. Verifies required Python packages and installs them automatically via `pip install -e ".[dev]"` (falling back to `pip install -e .` if needed).
4. Scans `PATH` for `claude`, `codex`, `agy`, and `ollama` (warns if none are found; you can still simulate or configure binary paths in the UI).
5. Starts the local web server at `http://127.0.0.1:8788` (`python -m epub_translate.web`) and opens the browser.

Any command-line flags passed to `iniciar servidor.bat` are forwarded to `epub_translate.web`, for example:

```bat
"iniciar servidor.bat" --no-browser --port 8788
```

### Stop (`parar servidor.bat`)

Double-click **`parar servidor.bat`** in the repository root (or inside `release/` or the packaged `epub-translate-*` folder).

What `parar servidor.bat` does:
1. Locates the active `epub-translate-*` folder and executes `parar_servidor.ps1` via PowerShell (`-NoProfile -ExecutionPolicy Bypass`).
2. Finds `python*` / `lector-web*` processes listening on port `8788` or running `epub_translate.web`.
3. Terminates those processes cleanly and reports the stopped PIDs (or informs you if port `8788` is already free).

---

## CLI and Terminal Wizard

From inside the `epub-translate-*` folder (after running `instalar.bat` or `iniciar servidor.bat`):

- **Interactive terminal wizard:** Drag and drop an `.epub` file onto `traduzir.bat`.
- **Direct CLI:**

```powershell
.venv\Scripts\python.exe -m epub_translate.cli book.epub --provider claude --model sonnet --to pt-BR
.venv\Scripts\python.exe -m epub_translate.cli book.epub --provider codex --model gpt-5-codex --reuse-cache
.venv\Scripts\python.exe -m epub_translate.cli book.epub --engine pseudo --to pt-BR --dry-run
```

Common CLI flags: `--provider {claude,codex,agy,ollama,identity,pseudo}`, `--model <name>`, `--from <lang>`, `--to <lang>`, `--glossary <path>`, `--reuse-cache`, `--no-cache`, `--only <file>`, `--dry-run`.

---

## Release Folder Structure

```text
lector/
├── README.md
├── CHANGELOG.md
├── .gitignore
├── DESIGN.md                        # Visual design tokens and guidelines
├── spec.md                          # Product & technical specification (Screen v3)
├── iniciar servidor.bat             # Root launcher -> epub-translate-*/iniciar servidor.bat
├── parar servidor.bat               # Root stop script -> epub-translate-*/parar_servidor.ps1
├── epub-translate-0.2.0/            # Working source directory (currently v0.3.0)
└── release/                         # Packaged v0.3.0 distribution
    ├── iniciar servidor.bat         # Release launcher -> epub-translate-0.3.0/iniciar servidor.bat
    ├── parar servidor.bat           # Release stop script -> epub-translate-0.3.0/parar_servidor.ps1
    ├── epub-translate-0.3.0.zip     # Redistributable release archive
    ├── epub-translate-0.3.0.zip.sha256
    └── epub-translate-0.3.0/        # Standalone packaged folder
        ├── epub_translate/          # Python package + web_static/ frontend
        ├── pyproject.toml           # Package metadata (version 0.3.0)
        ├── models.json              # Editable CLI providers & batch defaults
        ├── glossary.json            # Default translation rules & tone
        ├── preferences.json         # Saved UI/CLI preferences
        ├── iniciar servidor.bat     # Setup + start local server
        ├── parar servidor.bat       # Wrapper for parar_servidor.ps1
        ├── parar_servidor.ps1       # Safe process shutdown on port 8788
        ├── instalar.bat             # Manual .venv + dependency installer
        ├── lector.bat               # Legacy launcher (delegates to iniciar servidor.bat)
        ├── traduzir.bat             # Drag-and-drop interactive CLI wizard
        └── LEIA-ME.md               # Detailed user guide in Portuguese (pt-BR)
```

---

## Verification & Tests

Run the automated test suite from inside `epub-translate-0.2.0`:

```powershell
.venv\Scripts\python.exe -m pytest -q tests
```

All `67` tests pass (`test_upgrade.py`, `test_web.py`, and `test_optimizations.py`) using isolated temporary fixtures so local `cache/`, `saida/`, and JSON configuration files are not modified.

---

## Documentation

- [CHANGELOG.md](CHANGELOG.md) — Release history (`v0.2.0` and `v0.3.0`).
- [spec.md](spec.md) — Product and technical specification (`Screen v3`).
- [DESIGN.md](DESIGN.md) — Visual design system tokens.
- [epub-translate-0.2.0/LEIA-ME.md](epub-translate-0.2.0/LEIA-ME.md) — Complete user manual in Portuguese (`pt-BR`).
