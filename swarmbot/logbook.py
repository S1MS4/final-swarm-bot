"""Per-session logging: a JSONL event stream plus frame dumps on surprise.

When an unattended run goes wrong overnight, the only evidence is what was
written down.  Frames are dumped on unexpected states specifically so a
detector bug can be reproduced offline via tools/inspect.py.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from . import config
from .capture import save_image


@dataclass
class Logbook:
    directory: Path
    echo: bool = True
    _frames: int = 0

    @classmethod
    def create(cls, root: Path | None = None, echo: bool = True) -> "Logbook":
        root = Path(root or config.RUNS)
        directory = root / datetime.now().strftime("%Y%m%d-%H%M%S")
        directory.mkdir(parents=True, exist_ok=True)
        return cls(directory=directory, echo=echo)

    @property
    def path(self) -> Path:
        return self.directory / "log.jsonl"

    def event(self, kind: str, **fields) -> None:
        record = {"t": datetime.now().isoformat(timespec="milliseconds"), "kind": kind, **fields}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        if self.echo:
            print(self._format(record), flush=True)

    def dump_frame(self, frame: np.ndarray, tag: str) -> Path:
        self._frames += 1
        path = self.directory / f"{self._frames:03d}-{tag}.png"
        save_image(path, frame)
        return path

    def error(self, message: str, **fields) -> None:
        record = {"t": datetime.now().isoformat(timespec="milliseconds"), "kind": "error",
                  "message": message, **fields}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        print(f"  !! {message}", file=sys.stderr, flush=True)

    @staticmethod
    def _format(record: dict) -> str:
        kind = record["kind"]
        detail = " ".join(
            f"{k}={v}" for k, v in record.items() if k not in ("t", "kind") and v is not None
        )
        return f"  [{record['t'][11:19]}] {kind:16s} {detail}"
