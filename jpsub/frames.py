"""ffmpeg 抽帧与帧间差异检测(布局感知)。"""
from __future__ import annotations

import os
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageStat

from . import settings

# 帧差比较用的缩小尺寸,抗 JPEG 噪声又保留字幕变化信号
_DIFF_SIZE = (128, 32)


def extract_frames(
    video: Path,
    out_dir: Path,
    *,
    fps: float = 2.0,
    crop: str | float = "0.75:0.03:0.03:0.03",
    start: float | None = None,
    end: float | None = None,
    quiet: bool = False,
) -> list[Path]:
    """裁剪画面字幕区后按 fps 抽帧。

    `crop` 支持两种写法:
    - 单数字(如 0.25):取底部该占比高度,兼容旧用法;>=1.0 表示全屏不裁。
    - `上:下:左:右` 四边距(如 0.75:0.03:0.03:0.03):从各边裁掉该比例,
      保留中间区域。默认裁掉顶部 75% 留底部字幕带。

    `start`/`end`(秒)限定只处理该时间区间,用于避开片头/片尾(如片尾滚动的
    素材名单)等不含正片字幕的画面。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(out_dir / "frame_%06d.jpg")
    text = str(crop)
    if ":" in text:  # 上:下:左:右 四边距
        parts = [float(v) for v in text.split(":")]
        if len(parts) != 4 or not all(0 <= v < 1 for v in parts):
            raise ValueError(f"crop 四边距应为 0~1 的 上:下:左:右,得到 {crop}")
        top, bottom, left, right = parts
        if top + bottom >= 1 or left + right >= 1:
            raise ValueError(f"crop 边距之和过大,得到 {crop}")
        vf = (
            f"crop=iw*{1 - left - right:.6f}:ih*{1 - top - bottom:.6f}:"
            f"iw*{left:.6f}:ih*{top:.6f},fps={fps}"
        )
    else:
        crop_ratio = float(text)
        if crop_ratio >= 1.0:
            vf = f"fps={fps}"
        else:
            if not 0 < crop_ratio <= 1:
                raise ValueError(f"crop_ratio 必须在 (0,1] 内,得到 {crop_ratio}")
            vf = f"crop=iw:ih*{crop_ratio}:0:ih*(1-{crop_ratio}),fps={fps}"
    cmd = [settings.binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y"]
    if not quiet:
        cmd.append("-stats")  # 进度统计(stderr),批量模式由状态板统一展示故省略
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(video)]
    if end is not None:
        cmd += ["-t", str(end - (start or 0))]
    cmd += ["-vf", vf, "-q:v", "2", pattern]
    subprocess.run(cmd, check=True)
    return sorted(out_dir.glob("frame_*.jpg"))


def _binary_mask(image: Image.Image, *, bright_th: int = 170) -> Image.Image:
    """全分辨率二值文字掩膜(0/255),text_mask 用。"""
    rgb = image.convert("RGB")
    r, g, b = rgb.split()
    vivid = ImageChops.lighter(ImageChops.lighter(r, g), b)   # 每像素 max(R,G,B)
    blurred = vivid.filter(ImageFilter.GaussianBlur(radius=6))
    local = ImageChops.subtract(vivid, blurred)               # 局部对比度(文字为正)
    # 阈值 25:文字笔画局部对比远高于半透明框透出的条纹纹理(实测两者差一个
    # 数量级),单独该条件即可分离文字与背景,暗文字(混光后 vivid≈160)也不漏
    binary = local.point(lambda p: 255 if p >= 25 else 0)
    # 亮度门槛:白色文字远高于此,而半透明字幕框的条纹动画、边框装饰、
    # 背景中低亮度纹理都被剔除——否则剧烈动画场景(如条纹闪烁的对话框)里
    # 静止文字的掩膜会被噪声淹没,静态判定完全失效导致吞段。
    # 核心掩膜用更高门槛 210:亮背景场景(天空/火焰)的噪声 vivid 多落在
    # 170~210,唯有白字核心能干净分离,供换段判定使用。
    bright = vivid.point(lambda p: 255 if p >= bright_th else 0)
    binary = ImageChops.multiply(binary, bright)
    # 中值滤波去掉孤立噪点,笔画(2px 以上)保留
    binary = binary.filter(ImageFilter.MedianFilter(3))
    # 膨胀一格:桥接文字上掠过的扫描线暗纹、把滚动的虚线边框连成实线
    #(实线恒在→被 strip_static 剔除),静止区的差异信号随之归零
    binary = binary.filter(ImageFilter.MaxFilter(3))
    if ImageStat.Stat(binary).mean[0] < 0.5:
        if bright_th >= 210:
            return binary  # 核心掩膜允许为空(无白字),不做兜底
        # 兜底:高通会把大面积实心块(纯色画面/整屏字幕卡)内部清零;若局部
        # 掩膜几乎为空,回退到全局阈值(max 通道均值 + 余量,夹在 [80,240]),
        # 保证纯色帧之间仍有差异信号。
        hist = vivid.histogram()
        mean = sum(i * h for i, h in enumerate(hist)) / max(1, sum(hist))
        thresh = min(240, max(80, mean + 40))
        binary = vivid.point(lambda p: 255 if p >= thresh else 0)
    return binary


def text_mask(image: Image.Image) -> Image.Image:
    """把一帧转成"文字掩膜":文字像素为 255,背景为 0。

    用 **max(R,G,B) 通道**而非灰度:白字以及鲜红/黄/蓝/绿等重点词都能被当成"文字"
    (灰度会把纯红≈76、纯蓝≈29 压暗而漏掉)。

    局部对比度:原图减去高斯模糊后的"背景估计",只留高频的文字笔画。
    半透明底框是低频大面积色块,减法后被抵消,不会再被当成文字;框的灰度
    随背后画面波动也不影响掩膜。局部对比阈值取 25:文字笔画与模糊背景的差
    远高于条纹纹理的差。先按原始分辨率二值化、
    再用 NEAREST 缩放,保证掩膜只有 0/255,比较结果稳定。
    """
    binary = _binary_mask(image)
    return binary.resize(_DIFF_SIZE, Image.Resampling.NEAREST)


def core_mask(image: Image.Image) -> Image.Image:
    """高门槛"文字核心掩膜":只留白字核心,供换段判定。

    亮背景场景(明亮天空、火焰、高对比动画)的主掩膜会被背景噪声淹没,
    旧文字消失时的"消失比例"被稀释而无法触发换段。核心掩膜把门槛提到
    210,实测能把这些场景的背景完全剔除、只留文字笔画。暗文字(vivid<210)
    在此掩膜中为空,换段判定自动退化为不触发,与旧行为一致。
    """
    binary = _binary_mask(image, bright_th=210)
    return binary.resize(_DIFF_SIZE, Image.Resampling.NEAREST)


def _progress(i: int, total: int, label: str) -> None:
    """单行刷新的进度显示(不换行,完成后由调用方换行)。"""
    print(f"\r{label} {i}/{total}", end="", flush=True)


def _mask_pair(p: Path) -> tuple[Image.Image, Image.Image]:
    """子进程入口:一帧的主掩膜+核心掩膜(每帧只打开一次)。"""
    with Image.open(p) as im:
        return text_mask(im), core_mask(im)


def _mask_one(p: Path) -> Image.Image:
    """子进程入口:一帧的主掩膜。"""
    with Image.open(p) as im:
        return text_mask(im)


def _parallel(fn, paths: list[Path], label: str, quiet: bool = False):
    """多进程并行生成掩膜,吃满逻辑核;帧少时退回单进程。"""
    n = len(paths)
    workers = min(os.cpu_count() or 1, n)
    if workers <= 1:
        return [fn(p) for p in paths]
    chunk = max(1, n // (workers * 4))
    out: list = [None] * n
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, res in enumerate(ex.map(fn, paths, chunksize=chunk)):
            out[i] = res
            done += 1
            if not quiet:
                _progress(done, n, label)
    if not quiet:
        print()
    return out


def text_core_masks(paths: list[Path], quiet: bool = False):
    """批量生成主掩膜与核心掩膜(多进程并行)。"""
    pairs = _parallel(_mask_pair, paths, "生成掩膜", quiet)
    masks = [m for m, _ in pairs]
    cores = [c for _, c in pairs]
    return masks, cores


def text_masks(paths: list[Path], quiet: bool = False) -> list[Image.Image]:
    """批量生成"文字掩膜":每帧只算一次,供相邻比较与去重复用(多进程并行)。"""
    return _parallel(_mask_one, paths, "生成掩膜", quiet)


def strip_static(masks: list[Image.Image], ratio: float = 0.8) -> list[Image.Image]:
    """剔除全程静止的像素(如字幕区内固定水印)。

    对所有帧掩膜逐像素求平均,占比 > `ratio` 的像素视为始终存在的水印,
    从每个掩膜中减去。水印永不消失,会把换段/事后合并的"消失比例"分母
    撑大(消失的只有旧字幕文字),导致整句替换被误判为"打字续写"而吞段。
    """
    if len(masks) < 2:
        return masks
    import numpy as np

    stack = np.stack([np.asarray(m, dtype=np.uint16) for m in masks])
    static = (stack.mean(axis=0) > ratio * 255).astype(np.uint8) * 255
    if not static.any():
        return masks
    static_img = Image.fromarray(static, mode="L")
    return [ImageChops.subtract(m, static_img) for m in masks]


def mask_diff(a: Image.Image, b: Image.Image) -> float:
    """两张"文字掩膜"的平均绝对差(0-255)。"""
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]


def frame_diff(a: Path, b: Path) -> float:
    """两帧"文字掩膜"的平均绝对差(0-255)。

    只比较**文字像素**:字幕带内的背景亮度波动、半透明底、压缩噪声都被阈值滤掉,
    只有字幕的增删/切换才产生差异。这样即使字幕带里混入轻微动态也不会误判为"变化"。
    """
    with Image.open(a) as ia, Image.open(b) as ib:
        return mask_diff(text_mask(ia), text_mask(ib))


def has_changed(prev: Path | None, cur: Path, threshold: float) -> bool:
    """prev 为 None 或掩膜差异 >= threshold 时视为变化。"""
    if prev is None:
        return True
    return frame_diff(prev, cur) >= threshold
