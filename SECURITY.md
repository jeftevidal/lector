# Security Policy

## Reporting a vulnerability

Please **do not open a public GitHub issue** for security vulnerabilities.

Report security issues by opening a [private security advisory](https://github.com/jeftevidal/lector/security/advisories/new)
on GitHub. You will receive a response within 7 days. If the issue is confirmed,
we will aim to release a patch within 30 days.

## Threat model

Lector (`v0.3.0`) is a **local single-user workstation tool** (FastAPI web UI +
terminal CLI) for translating EPUB files. The following describes what the
project is and is not designed to defend against.

### In scope

- **Local data confidentiality.** Input `.epub` files, translated outputs,
  reports (`.report.md`), and the SQLite translation memory cache
  (`cache/lector.tmcache`) live under the local working directory controlled by
  the operating-system user. We rely on OS filesystem permissions/ACLs; no
  additional encryption at rest is provided. The original `.epub` file is never
  modified in place.

- **Loopback-only web server by default.** `iniciar servidor.bat` and
  `python -m epub_translate.web` bind to `127.0.0.1:8788` by default. If you
  explicitly pass `--host 0.0.0.0` (or another non-loopback address), the web
  UI has no built-in authentication; protect it with firewall rules or an
  authenticated TLS reverse proxy.

- **Outbound data to AI CLI providers.** Translation segments are sent to the
  CLI provider you select (`claude`, `codex`, `agy`, or `ollama`). Cloud CLI
  providers send book text to their respective services. Use `ollama` with a
  local model when content must not leave your machine.

- **Release integrity.** GitHub Release archives (`Lector-v0.3.0.zip`) are
  published with `.sha256` checksum companions so you can verify integrity
  before extracting.

### Out of scope

- **Multi-user / multi-tenant isolation.** Lector is a single-user local tool
  with no user accounts, RBAC, or per-user cache isolation.
- **Encryption at rest.** Cache and output files are stored as standard
  filesystem files.
- **Direct untrusted-Internet exposure.** The web server is not designed to be
  exposed directly to the public Internet without external network controls.

## Supported versions

Only the latest release (`0.3.x`) receives security fixes. Older versions are
not backported.
