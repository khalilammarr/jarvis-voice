"""Small atomic-write helper shared by the state files Jarvis keeps on disk."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_atomic(path: Path, text: str, mode: int = 0o600) -> None:
    """Write ``text`` to ``path`` via a temp file + rename, so readers never see half a file."""
    path = path.resolve()  # write through a dotfiles symlink instead of replacing it
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())  # never leave an empty file after a power cut
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
