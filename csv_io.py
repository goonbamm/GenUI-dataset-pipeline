"""Shared CSV write helpers for consistent encoding policy."""

from __future__ import annotations

from pathlib import Path
from typing import TextIO


def open_csv_for_append(csv_path: Path) -> tuple[TextIO, bool]:
    """Open CSV for append-safe writes and return (file, should_write_header).

    Policy:
    - first create: mode='w', encoding='utf-8-sig' (write BOM once for Excel)
    - append: mode='a', encoding='utf-8' (avoid mid-file BOM insertion)
    """
    file_exists = csv_path.exists()
    mode = "a" if file_exists else "w"
    encoding = "utf-8" if file_exists else "utf-8-sig"
    should_write_header = not file_exists
    return csv_path.open(mode, encoding=encoding, newline=""), should_write_header
