# Changelog

All notable changes to **Lector (`epub-translate`)** are documented in this file.

---

## [0.3.0] - 2026-11-25

### Added
- **Root, release, and package server scripts:**
  - `iniciar servidor.bat`: automatically validates Python `>= 3.11`, creates `.venv` if absent, verifies or installs dependencies (`pip install -e ".[dev]"` with fallback to `pip install -e .`), checks installed AI CLIs, and launches `epub_translate.web` on `http://127.0.0.1:8788`.
  - `parar servidor.bat` and `parar_servidor.ps1`: safely detect and terminate Lector processes listening on port `8788` or running `epub_translate.web`.
- **Release packaging in `release/`:**
  - Packaged standalone directory `release/epub-translate-0.3.0/`, compressed archive `release/epub-translate-0.3.0.zip`, SHA-256 checksum `release/epub-translate-0.3.0.zip.sha256`, and release-level `iniciar servidor.bat` / `parar servidor.bat` launchers.
- **Optimization test suite:**
  - Added `tests/test_optimizations.py` verifying GZip compression, static `Cache-Control` headers, binary lookup caching, batched cross-cache preview lookups, and presence of server batch scripts (67 tests total).

### Changed
- **Web server performance (`epub_translate/web.py`):**
  - Added `GZipMiddleware` (`minimum_size=500`) and `Cache-Control: public, max-age=3600` headers for `/static/*` assets.
  - Cached `web_static/index.html` in memory (`INDEX_HTML`) and added a 30-second TTL cache (`_BINARY_CACHE`) for provider binary discovery, invalidated on `/api/providers/verify`.
- **Cache preview performance (`epub_translate/cache.py`):**
  - Replaced per-segment cross-model SQLite queries in `TMCache.preview_counts` with batched `SELECT DISTINCT source_text` lookups in chunks of 500 unique segments.
- **Text extraction & verification fast paths (`epub_translate/extract.py`, `epub_translate/verify.py`):**
  - Added quick membership checks (`OPEN in ...` and lowercase substring pre-filtering in `_has_term`) to skip unnecessary regex substitutions on plain segments.
- **Launcher consolidation (`lector.bat`):**
  - Updated `lector.bat` to delegate directly to `iniciar servidor.bat`.
- **Version bump:**
  - Updated package version from `0.2.0` to `0.3.0` in `pyproject.toml` and `epub_translate/__init__.py`.

---

## [0.2.0] - 2026-11-24

### Added
- Baseline `epub-translate` `0.2.0` release:
  - Local single-screen Web UI (`Screen v3`) served via FastAPI (`epub_translate/web.py` and `epub_translate/web_static/`) with 3-column layout, SSE progress stream, 9 operational overlays, interactive glossary editor, and localhost-only access middleware.
  - Multi-CLI translation engine (`claude`, `codex`, `agy`, `ollama`, plus `identity` and `pseudo` test engines) with placeholder protection (`⟦n⟧…⟦/n⟧`), SQLite WAL translation memory (`cache/lector.tmcache`), intra-book deduplication, retry/repair/circuit-breaker resilience, and quota failover.
  - Post-translation verification (`verify.py`), optional second-pass review for uncertain segments, structural QA gates (`qa.py`), atomic EPUB repacking (`repack.py`), and `.report.md` reporting.
  - Interactive terminal wizard (`traduzir.bat` / `epub_translate/ui.py`), CLI (`epub_translate/cli.py`), `instalar.bat`, and Portuguese user guide (`LEIA-ME.md`).
