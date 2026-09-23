"""翻译缓存:JSON 持久化、原子写,跨批次/跨视频复用,零重复 token。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class TranslationCache:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data: dict[str, str] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))

    def get(self, src: str) -> str | None:
        return self._data.get(src)

    def put(self, src: str, dst: str) -> None:
        self._data[src] = dst

    def __len__(self) -> int:
        return len(self._data)

    def save(self) -> None:
        """原子写:先写临时文件再替换,避免中断留下半截 JSON。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
