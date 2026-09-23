"""本地日文字幕 OCR(PaddleOCR,检测+识别,无字返回空串不幻觉)。"""
from __future__ import annotations

import contextlib
import os
import sys
import warnings
from pathlib import Path

from . import settings

_SCORE = 0.5             # 置信度阈值,低于则丢弃该行

# Paddle 每次启动都会打印的已知无害信息/警告,过滤掉(其余输出正常透出)
_KNOWN_NOISE = (
    "ccache",                       # 未装 ccache 的提示(含 shell 的 which 报错)
    "OMP_NUM_THREADS",              # 多线程提示,本就刻意开启多线程
    "Creating model:",
    "Model files already exist",
    "Using official model",
)


@contextlib.contextmanager
def _quiet_paddle():
    """fd 级临时重定向 stdout/stderr,丢弃已知噪音行,其余结束后原样输出。"""
    import tempfile

    with tempfile.TemporaryFile("w+b") as tf1, tempfile.TemporaryFile("w+b") as tf2:
        saved1, saved2 = os.dup(1), os.dup(2)
        os.dup2(tf1.fileno(), 1)
        os.dup2(tf2.fileno(), 2)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                yield
        finally:
            os.dup2(saved1, 1)
            os.dup2(saved2, 2)
            os.close(saved1)
            os.close(saved2)
            for f, stream in ((tf1, sys.stdout), (tf2, sys.stderr)):
                f.seek(0)
                for line in f.read().decode("utf-8", "replace").splitlines():
                    if line.strip() and not any(p in line for p in _KNOWN_NOISE):
                        print(line, file=stream)

# 检测/识别模型在 settings.OCR_DET_MODEL/OCR_REC_MODEL 里配置
# (实测 medium 在检测和识别上提升很小,但速度慢得多,不建议)


def _prepare_env() -> int:
    """配置 Paddle 缓存/线程/mkldnn 环境,返回逻辑核数。须在 import paddleocr 前调用。"""
    cache = Path(__file__).resolve().parent.parent / "models"
    cache.mkdir(exist_ok=True)
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(cache))
    # Paddle 默认按物理核数起线程,超线程下只用一半;启动前用逻辑核数覆盖
    threads = os.cpu_count() or 8
    os.environ.setdefault("OMP_NUM_THREADS", str(threads))
    # 新版 Paddle(PIR) 在 onednn 下报 ConvertPirAttribute2RuntimeAttribute 错误,禁用
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    return threads


class PaddleOcrEngine:
    """PaddleOCR 薄封装。模型在首次构造时下载并缓存。"""

    name = "paddleocr"

    def __init__(self) -> None:
        threads = _prepare_env()
        with _quiet_paddle():
            from paddleocr import PaddleOCR

            self._ocr = PaddleOCR(
                text_detection_model_name=settings.OCR_DET_MODEL,
                text_recognition_model_name=settings.OCR_REC_MODEL,
                cpu_threads=threads,
                enable_mkldnn=False,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )

    def _predict(self, image_path: Path) -> list[tuple[int, str]]:
        """对一张图做检测+识别,返回 [(行顶部 y, 文本), ...] 按 y 排序。"""
        results = self._ocr.predict(str(image_path))
        rows: list[tuple[int, str]] = []
        for res in results:
            texts = res["rec_texts"]
            scores = res["rec_scores"]
            boxes = res["rec_boxes"]
            for box, text, score in zip(boxes, texts, scores):
                if score >= _SCORE and text.strip():
                    rows.append((int(box[1]), text.strip()))
        rows.sort(key=lambda r: r[0])
        return rows

    def run(self, image_path: Path) -> str:
        """识别单行/整帧图片,返回日文文本(可能为空串)。"""
        return "\n".join(t for _, t in self._predict(image_path))

    def run_lines(self, image_path: Path) -> str:
        """screen 布局:多行文本按 y 从上到下用 '\\n' 拼回。"""
        return self.run(image_path)

    def run_many(self, image_paths: list[Path], *, lines: bool = False) -> list[str]:
        """批量识别多帧(逐张推理,并行交给上层多进程)。"""
        return [self.run(p) for p in image_paths]
