"""本地日文字幕 OCR(PaddleOCR,检测+识别,无字返回空串不幻觉)。"""
from __future__ import annotations

import os
from pathlib import Path

from . import settings

_LANG = "japan"          # 日文
_SCORE = 0.5             # 置信度阈值,低于则丢弃该行

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


class NoTextFilter:
    """无字帧判据(检测框有无),供跳过无字画面的识别。

    单级 mobile 检测,检出任一文本框即判有字。保守:只要检出 1 个框
    就认为有字,宁可漏删不误杀。
    """

    def __init__(self, threads: int | None = None) -> None:
        self._threads = threads or _prepare_env()
        self._fast = None

    def _model(self, name: str):
        from paddleocr import TextDetection

        return TextDetection(
            model_name=name, cpu_threads=self._threads, enable_mkldnn=False
        )

    def _detect(self, model, image_path: Path) -> bool:
        for res in model.predict(str(image_path)):
            polys = res["dt_polys"]
            if polys is not None and len(polys) > 0:
                return True
        return False

    def has_text_image(self, image) -> bool:
        """对掩膜化图片(白字黑底)判定有无文字。

        输入为已剔除静态水印的文字掩膜,低亮度水印/背景纹理不再触发检测框。
        """
        import numpy as np

        if self._fast is None:
            self._fast = self._model(settings.OCR_DET_MODEL)
        arr = np.asarray(image.convert("RGB"))
        for res in self._fast.predict(arr):
            polys = res["dt_polys"]
            if polys is not None and len(polys) > 0:
                return True
        return False

    def filter(self, image_paths: list[Path]) -> list[Path]:
        """返回其中含文字的帧(保持原顺序)。"""
        return [p for p in image_paths if self.has_text(p)]


class PaddleOcrEngine:
    """PaddleOCR 薄封装。模型在首次构造时下载并缓存。"""

    name = "paddleocr"

    def __init__(self) -> None:
        threads = _prepare_env()
        from paddleocr import PaddleOCR

        self._ocr = PaddleOCR(
            lang=_LANG,
            text_detection_model_name=settings.OCR_DET_MODEL,
            text_recognition_model_name=settings.OCR_REC_MODEL,
            cpu_threads=threads,
            enable_mkldnn=False,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        self._filter: NoTextFilter | None = None

    def has_text(self, image_path: Path) -> bool:
        """快速判据:画面里有无文字,无字的帧可跳过识别。"""
        if self._filter is None:
            self._filter = NoTextFilter()
        return self._filter.has_text(image_path)

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
