"""共享的进度输出捕获:主页/编辑器等 GUI 的后台任务统一用 LineCapture
接住 print/tqdm 输出,整理成完整行后回调,各处展示行为一致。"""
from __future__ import annotations

import re


def sanitize(line: str) -> str:
    """去 ANSI 颜色码;tqdm 进度条压缩成「名称 百分比 (数字)」。"""
    line = re.sub(r"\x1b\[[0-9;]*m", "", line)
    m = re.match(r"^(.*?):\s*(\d+)%\|[^|]*\|\s*([^[\s]+)", line)
    if m:
        return f"{m.group(1).strip()} {m.group(2)}% ({m.group(3)})"
    return line


class LineCapture:
    """stdout/stderr 替身:按 \\r/\\n 切行,经 sanitize 后逐行回调 on_line。

    echo 传真实流可同步落地终端(便于排查);guard 为可选的()
    返回 False 即抛「已被用户终止」,供主页终止按钮使用。
    """

    def __init__(self, on_line, *, echo=None, guard=None):
        self.on_line = on_line
        self.echo = echo
        self.guard = guard
        self._buf = ""

    def write(self, s: str) -> int:
        if self.guard is not None and not self.guard():
            raise RuntimeError("已被用户终止")
        if self.echo is not None:
            try:
                self.echo.write(s)
            except Exception:  # noqa: BLE001
                pass
        self._buf += s
        parts = re.split(r"[\r\n]", self._buf)
        self._buf = parts[-1]  # 残尾留给下次拼接(tqdm 按 \r 原地刷新)
        for p in parts[:-1]:
            line = sanitize(p.strip())
            if line:
                self.on_line(line)
        return len(s)

    def flush(self) -> None:
        pass


class Reporter:
    """各流水线阶段共用的单行进度输出(三态合一):
    - on_line 给定(GUI/主页):每行文本回调给上层展示;
    - 否则 quiet=True 静默、quiet=False 终端 \\r 原地刷新(不刷屏)。
    close() 完成后换行,让后续输出另起一行。
    """

    def __init__(self, *, on_line=None, quiet: bool = False):
        self.on_line = on_line
        self.quiet = quiet

    def line(self, text: str) -> None:
        if self.on_line is not None:
            self.on_line(text)
        elif not self.quiet:
            print(f"\r{text}  ", end="", flush=True)  # 尾部补空格盖掉上一行残留

    def close(self) -> None:
        if not self.quiet:
            print()
