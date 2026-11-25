"""Repack (SPEC §4.7): zip com mimetype STORED como 1ª entrada."""

from __future__ import annotations

import zipfile
from io import BytesIO


def pack(files: dict[str, bytes]) -> bytes:
    """Empacota preservando a ordem do dict (a ordem original do zip)."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, files.get("mimetype", b"application/epub+zip"))
        for name, data in files.items():
            if name == "mimetype":
                continue
            zf.writestr(zipfile.ZipInfo(name), data, zipfile.ZIP_DEFLATED)
    return buf.getvalue()
