"""在线日文字幕 OCR。默认百度 OCR(通用文字识别),可选 OpenAI 兼容视觉 API。

本地 PaddleOCR 引擎已归档到 archive/ocr_paddle.py;本模块保持相同接口
(run / run_lines / run_many),调用方无需改动。

配置(settings.py):
- OCR_PROVIDER = "baidu"(默认)或 "openai"
- baidu:BAIDU_OCR_API_KEY / BAIDU_OCR_SECRET_KEY
- openai:OCR_API_BASE / OCR_API_KEY(留空回退 API_BASE / API_KEY)+ OCR_MODEL

百度按次计费:多帧拼接成一图一次请求,回来后按 y 坐标/编号行拆回各帧,
单次消耗降为逐帧的约 1/15;4 个免费额度接口轮换,单个用尽自动切下一个。
"""
from __future__ import annotations

import base64
import io
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

from . import settings


def text_boost(im):
    """特殊画面(火焰/熔岩等亮暖背景上的浅白字,直接识别会整行漏读)的文字凸显。

    默认关闭(settings.OCR_BOOST / 命令行 --ocr-boost 开启),普通画面原图直读。

    增强方法:蓝通道与 vivid 通道各自的「局部对比软增强」取大——白字在蓝
    通道上远高于暖背景,彩色重点字(红/黄)在 vivid 通道上更高,两者都保留。
    触发条件(不满足则原样返回,行为不变):
    - 暖背景:整帧中位 R-B >= 30;
    - 低对比:白字像素(亮且低饱和)的 vivid 中位数与整帧中位数差 < 160
      (暗底白字 gap 很大,原图直读效果最好,不做增强)。
    """
    try:
        import numpy as np
        from PIL import Image, ImageChops, ImageFilter

        if not settings.OCR_BOOST:
            return im
        rgb = im.convert("RGB")
        w, h = rgb.size
        small = np.asarray(
            rgb.resize((max(1, w // 2), max(1, h // 2))), dtype=np.int16
        )  # 半采样:64px 级缩略图会把细字笔画与背景混色,判定失真
        v = small.max(axis=2)
        sat = v - small.min(axis=2)
        if int(np.median(small[..., 0]) - np.median(small[..., 2])) < 30:
            return im
        white = (v > 170) & (sat < 60)
        if white.sum() < 50 or int(np.median(v[white])) - int(np.median(v)) >= 160:
            return im  # 无白字/暗底高对比:原图直读
        r, g, b = rgb.split()
        vivid = ImageChops.lighter(ImageChops.lighter(r, g), b)
        blur = vivid.filter(ImageFilter.GaussianBlur(radius=6))

        def soft(ch):
            return ImageChops.subtract(ch, ch.filter(ImageFilter.GaussianBlur(radius=6))).point(
                lambda p: min(255, p * 6)
            )

        merged = np.maximum(np.asarray(soft(b)), np.asarray(soft(vivid))).astype(np.uint8)
        out = Image.fromarray(merged).filter(ImageFilter.MedianFilter(3))
        return out
    except Exception:  # noqa: BLE001  numpy/PIL 不可用:退回原图
        return im


# 本次进程内成功计费的百度 OCR 请求数(每次调用消耗 1 次月额度)
REQUESTS = 0


class BaiduOcrEngine:
    """百度通用文字识别:多个免费额度接口轮换,单个额度用尽自动切下一个。

    可用接口及月额度(同一个 AK/SK):
    - accurate_basic   高精度版      1000 次/月
    - accurate_general 高精度含位置  500 次/月
    - general_basic    标准版        1000 次/月
    - general          标准含位置版  1000 次/月
    共 3500 次/月;已用尽接口记录在 ~/.jpsub/baidu_ocr_state.json,月初自动重置。
    """

    name = "baidu"

    _TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
    # 逐帧兜底接口(按优先级,高精度优先)
    _ENDPOINTS = (
        "accurate_basic",
        "accurate_general",
        "general_basic",
        "general",
    )
    _token: str = ""
    _token_expire: float = 0.0
    _exhausted: set[str] = set()

    # ---------- 多帧合并识别(按次计费,合并成一张图只算一次) ----------

    _SEP = 44          # 帧间黑色分隔带高度(px,内含白色编号供无位置接口切分)
    # 拼接图总高上限:官方硬限长边 4096px(超过报 216202 静默回退到按编号切分,编号误读即整批错帧);
    # 内部检测会对长图整体缩小:3438px 起行数虽稳但框精度下降/行合并增多(字符数 -15%),
    # 3056px 以内质量稳定,取 3100(8 帧/批)
    _MAX_H = 3100
    _MAX_W = 2560      # 拼接图宽度上限(留出小图 2 倍放大的空间)
    # 拼接批量接口:位置版按行 y 坐标切分,无位置版按分隔带编号行切分
    _BATCH_ENDPOINTS = (
        ("accurate_general", True),  # 高精度含位置版 500/月
        ("accurate_basic", False),   # 高精度版 1000/月
        ("general", True),           # 标准含位置版 1000/月
        ("general_basic", False),    # 标准版 1000/月
    )
    # 含位置版接口(按优先级):仅这两个接口返回 location,供敏感词定位使用
    _POS_ENDPOINTS = ("accurate_general", "general")

    @classmethod
    def _state_file(cls) -> Path:
        return Path.home() / ".jpsub" / "baidu_ocr_state.json"

    @classmethod
    def _load_state(cls) -> None:
        """读取已用尽接口;跨月自动重置(额度按月刷新)。"""
        month = time.strftime("%Y-%m")
        f = cls._state_file()
        try:
            data = json.loads(f.read_text())
        except Exception:  # noqa: BLE001
            data = {}
        if data.get("month") == month:
            cls._exhausted = set(data.get("exhausted", []))
        else:
            cls._exhausted = set()
            cls._save_state(month)

    @classmethod
    def _save_state(cls, month: str | None = None) -> None:
        f = cls._state_file()
        f.parent.mkdir(exist_ok=True)
        f.write_text(
            json.dumps(
                {"month": month or time.strftime("%Y-%m"), "exhausted": sorted(cls._exhausted)}
            )
        )

    @classmethod
    def _get_token(cls) -> str:
        """AK/SK 换 access_token,带缓存(提前 60s 过期)。"""
        if cls._token and time.time() < cls._token_expire:
            return cls._token
        key = settings.BAIDU_OCR_API_KEY
        secret = settings.BAIDU_OCR_SECRET_KEY
        if not key or not secret:
            raise SystemExit(
                "错误:未配置百度 OCR。请在 settings.py 设置 BAIDU_OCR_API_KEY/"
                "BAIDU_OCR_SECRET_KEY,或把 OCR_PROVIDER 改为 openai"
            )
        url = (
            f"{cls._TOKEN_URL}?grant_type=client_credentials"
            f"&client_id={urllib.parse.quote(key)}"
            f"&client_secret={urllib.parse.quote(secret)}"
        )
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        token = data.get("access_token")
        if not token:
            raise RuntimeError(f"百度 OCR 获取 access_token 失败:{data}")
        cls._token = token
        cls._token_expire = time.time() + float(data.get("expires_in", 2592000)) - 60
        return token

    def _call(self, endpoint: str, b64: str) -> dict:
        """调一个 OCR 接口,返回原始 JSON。"""
        body = urllib.parse.urlencode(
            {
                "image": b64,
                "language_type": settings.ocr_language(),
                "detect_direction": "false",
                "detect_language": "false",
                "paragraph": "false",
                "probability": "false",
            }
        ).encode()
        url = (
            f"https://aip.baidubce.com/rest/2.0/ocr/v1/{endpoint}"
            f"?access_token={self._get_token()}"
        )
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
            if data.get("error_code") is None:
                global REQUESTS
                REQUESTS += 1  # 计费成功请求计数,run 结束后展示消耗
            return data

    def run(self, image_path: Path) -> str:
        """识别单行/整帧图片,返回日文文本(可能为空串)。

        额度用尽(error 17/19)自动切换下一个接口;QPS 超限(18)短暂等待重试。
        """
        if not BaiduOcrEngine._token:
            BaiduOcrEngine._load_state()
        with Image.open(image_path) as im0:
            im = text_boost(im0)
            if im is im0 and (im0.width >= 1600 or not settings.OCR_UPSCALE):
                return image_path.read_bytes()  # 未增强且无需放大:原图字节直送
            return self._run_b64(base64.b64encode(self._upscale_bytes(im)).decode())

    def _upscale_bytes(self, im, factor: int = 2) -> bytes:
        """PIL 图转 PNG 字节;小图(<1600px)2x 放大改善小字识别,可关。"""
        if im.width < 1600 and settings.OCR_UPSCALE:
            im = im.resize((im.width * factor, im.height * factor), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue()

    def _run_b64(self, b64: str) -> str:
        last_err: Exception | None = None
        for endpoint in self._ENDPOINTS:
            if endpoint in self._exhausted:
                continue
            for attempt in range(3):  # 网络/QPS 类错误重试 3 次
                try:
                    data = self._call(endpoint, b64)
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    time.sleep(2 * (attempt + 1))
                    continue
                code = data.get("error_code")
                if code in (17, 19):  # 日/总量超限额:标记用尽,换下一个接口
                    self._exhausted.add(endpoint)
                    self._save_state()
                    print(f"[baidu-ocr] {endpoint} 额度用尽,切换下一个接口")
                    last_err = RuntimeError(f"{endpoint} 额度用尽:{data.get('error_msg')}")
                    break
                if code is not None:
                    last_err = RuntimeError(
                        f"百度 OCR {endpoint} 错误:{code} {data.get('error_msg')}"
                    )
                    time.sleep(2 * (attempt + 1))
                    continue
                return "\n".join(w["words"] for w in data.get("words_result", []))
        raise RuntimeError(f"OCR 请求失败(所有接口均不可用):{last_err}")

    def run_lines(self, image_path: Path) -> str:
        """screen 布局:多行文本按图片中的行用 '\\n' 拼回。"""
        return self.run(image_path)

    def run_lines_pos(
        self, image_path: Path
    ) -> list[tuple[str, tuple[int, int, int, int]]] | None:
        """含位置版识别:返回 [(行文本, (x, y, w, h))],坐标为原图像素。

        只用含位置版接口(accurate_general -> general),不占用无位置版接口额度
        (两者额度独立,普通识别仍走原接口)。两个接口都额度用尽/不可用时标记并
        返回 None,由上层降级为算法定位;成功但无文字时返回空列表。
        """
        if not BaiduOcrEngine._token:
            BaiduOcrEngine._load_state()
        with Image.open(image_path) as im0:
            im = text_boost(im0)
            scale = 1.0
            if im.width < 1600 and settings.OCR_UPSCALE:  # 小图放大改善小字识别
                scale = 2.0
                im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        for endpoint in self._POS_ENDPOINTS:
            if endpoint in self._exhausted:
                continue
            for attempt in range(3):  # 网络/QPS 类错误重试 3 次
                try:
                    data = self._call(endpoint, b64)
                except Exception:  # noqa: BLE001
                    time.sleep(2 * (attempt + 1))
                    continue
                code = data.get("error_code")
                if code in (17, 19, 3):  # 额度用尽/接口不支持:标记后换下一个
                    self._exhausted.add(endpoint)
                    self._save_state()
                    print(f"[baidu-ocr] {endpoint} 不可用({code}),切换下一个含位置版接口")
                    break
                if code is not None:
                    time.sleep(2 * (attempt + 1))
                    continue
                rows: list[tuple[str, tuple[int, int, int, int]]] = []
                for row in data.get("words_result", []):
                    words = (row.get("words") or "").strip()
                    loc = row.get("location") or {}
                    if not words or not loc:
                        continue
                    rows.append(
                        (
                            words,
                            (
                                round(loc.get("left", 0) / scale),
                                round(loc.get("top", 0) / scale),
                                round(loc.get("width", 0) / scale),
                                round(loc.get("height", 0) / scale),
                            ),
                        )
                    )
                return rows
        return None  # 含位置版全部不可用:由上层降级为算法定位

    def run_many(
        self, image_paths: list[Path], *, lines: bool = False, on_item=None
    ) -> list[str]:
        """多帧合并识别:拼接成一图一次请求,再按 y 坐标/编号行拆回各帧。

        拼接接口额度用尽时依次换下一个;全部用尽后逐帧 run() 兜底(同样轮换)。
        on_item(i, 文本) 每有一帧文本确定就回调一次(拼接批内逐帧),供上层刷进度/增量落盘。
        """
        from PIL import Image

        out: list[str] = [""] * len(image_paths)
        i = 0
        while i < len(image_paths):
            imgs = [text_boost(Image.open(p)) for p in image_paths[i:]]
            # 统一缩放,避免拼接图超限;小图放大 2 倍改善小字识别(可关)
            w, h = imgs[0].size
            scale = min(2.0 if w < 1600 else 1.0, self._MAX_W / w)
            if not settings.OCR_UPSCALE:
                scale = min(1.0, self._MAX_W / w)
            if scale != 1.0:
                w, h = round(w * scale), round(h * scale)
                imgs = [im.resize((w, h)) for im in imgs]
            step = max(1, min(15, (self._MAX_H - self._SEP) // (h + self._SEP)))
            n = min(step, len(imgs))
            try:
                canvas = self._stitch(imgs[:n], w, h)
                out[i : i + n] = self._run_stitched(canvas, n, h)
            except Exception:  # noqa: BLE001  拼接通道全部不可用,逐帧兜底
                for j, p in enumerate(image_paths[i : i + n]):
                    out[i + j] = self.run(p)
            if on_item:
                for j in range(i, i + n):
                    on_item(j, out[j])
            i += n
        return out

    # 编号字体候选:跨平台系统字体,全缺失时退回 Pillow 内置字体
    _MARKER_FONTS = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/wenquanyi/wqy-zenhei/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/msyhbd.ttc",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    )

    @classmethod
    def _marker_font(cls, size: int):
        """编号绘制字体:依次尝试系统字体,全部不可用则用内置默认字体。"""
        from PIL import ImageFont

        for fp in cls._MARKER_FONTS:
            try:
                return ImageFont.truetype(fp, size)
            except OSError:
                continue
        for kw in ({"size": size}, {}):
            try:
                return ImageFont.load_default(**kw)
            except TypeError:
                continue
        return None

    def _stitch(self, imgs, w: int, h: int):
        """把若干帧纵向拼成一张图:黑色分隔带内画白色编号(供无位置接口切分)。"""
        from PIL import Image, ImageDraw

        canvas = Image.new("RGB", (w, len(imgs) * (h + self._SEP)), (0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        font = self._marker_font(34) if len(imgs) > 1 else None
        for j, im in enumerate(imgs):
            y0 = j * (h + self._SEP)
            if j > 0 and font is not None:  # 第 0 帧上方无编号
                label = str(j)
                tw = draw.textlength(label, font=font)
                draw.text(((w - tw) / 2, y0 + 4), label, font=font, fill=(255, 255, 255))
            canvas.paste(im, (0, y0 + self._SEP))
        return canvas

    def _run_stitched(self, canvas, n: int, frame_h: int) -> list[str]:
        """一次请求识别拼接图,把识别行拆回 n 帧并返回文本列表。

        依次尝试 _BATCH_ENDPOINTS:位置版按 y 坐标归属,无位置版按编号行切分;
        额度用尽(17/19)标记后换下一个接口,其余错误短暂重试。
        """
        import io

        buf = io.BytesIO()
        canvas.save(buf, format="WEBP", quality=90)  # webp 体积比 jpeg 小得多,且百度实测支持
        b64 = base64.b64encode(buf.getvalue()).decode()
        marker = re.compile(r"^\D{0,3}(\d{1,3})\D{0,3}$")
        last_err: Exception | None = None
        for endpoint, has_pos in self._BATCH_ENDPOINTS:
            if endpoint in self._exhausted:
                continue
            texts: list[str] = [""] * n
            ok = False
            for attempt in range(3):  # 网络/QPS 类错误重试
                try:
                    data = self._call(endpoint, b64)
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    time.sleep(2 * (attempt + 1))
                    continue
                code = data.get("error_code")
                if code in (17, 19, 3):  # 额度用尽/方法不支持(永久):标记后换下一个接口
                    self._exhausted.add(endpoint)
                    self._save_state()
                    print(f"[baidu-ocr] {endpoint} 不可用({code}),切换下一个接口")
                    last_err = RuntimeError(f"{endpoint} 不可用:{data.get('error_msg')}")
                    break
                if code is not None:
                    last_err = RuntimeError(
                        f"百度 OCR {endpoint} 错误:{code} {data.get('error_msg')}"
                    )
                    time.sleep(2 * (attempt + 1))
                    continue
                rows = data.get("words_result", [])
                if has_pos:
                    stride = frame_h + self._SEP
                    for w_row in rows:
                        top = w_row.get("location", {}).get("top", 0)
                        words = w_row["words"].strip()
                        idx, local = divmod(top - self._SEP, stride)
                        if local < 0:
                            continue  # 首帧上方的分隔带
                        if local >= frame_h:
                            # 检测框 top 偏小几 px,下一帧的帧首行会落进本帧尾部:
                            # 纯数字是分隔带里的编号(丢弃),文本行归属下一帧
                            if words.isdigit():
                                continue
                            idx += 1
                            if idx >= n:
                                continue
                        idx = min(n - 1, max(0, idx))
                        texts[idx] = (texts[idx] + "\n" + words).strip()
                else:
                    cur = 0
                    hits = 0
                    for w_row in rows:
                        words = w_row["words"].strip()
                        m = marker.match(words.replace(" ", ""))
                        if m and 0 < int(m.group(1)) < n:
                            cur = int(m.group(1))
                            hits += 1
                            continue
                        texts[cur] = (texts[cur] + "\n" + words).strip()
                    if rows and n > 1 and hits == 0:
                        # 有识别行却无任何编号:编号渲染失败或接口不识别,整批无法切分,
                        # 不能把全部文本并入首帧 → 判该接口失败,换下一个
                        last_err = RuntimeError(f"{endpoint} 无帧编号,无法切分拼接图")
                        break
                ok = True
                break
            if ok:
                return [t.strip() for t in texts]
        raise RuntimeError(f"拼接识别失败(所有接口均不可用):{last_err}")


class ApiOcrEngine:
    """调用 OpenAI 兼容视觉 API 识别图片中的日文文本。"""

    name = "api"

    def _endpoint(self) -> tuple[str, str, str]:
        base = settings.OCR_API_BASE or settings.API_BASE
        key = settings.OCR_API_KEY or settings.API_KEY
        model = settings.OCR_MODEL
        if not base or not key or not model:
            raise SystemExit(
                "错误:未配置在线 OCR。请在 settings.py 设置 OCR_API_BASE/"
                "OCR_API_KEY/OCR_MODEL(或回退用的 API_BASE/API_KEY + OCR_MODEL)"
            )
        return base.rstrip("/"), key, model

    @staticmethod
    def _image_data_url(image_path: Path) -> str:
        im = text_boost(Image.open(image_path))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        data = buf.getvalue()
        b64 = base64.b64encode(data).decode()
        suffix = image_path.suffix.lstrip(".").lower()
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(
            suffix, "jpeg"
        )
        return f"data:image/{mime};base64,{b64}"

    def _chat(self, data_url: str) -> str:
        base, key, model = self._endpoint()
        body = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"你是 OCR 引擎。识别图片中的{settings.source_lang_name()}文本,原样输出文本,"
                        "多行文本按图片中的行用换行分隔。"
                        "没有文字时输出空字符串。不要解释、不要翻译、不要标注。"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        }
        req = urllib.request.Request(
            base + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        last_err: Exception | None = None
        for attempt in range(3):  # 网络/限流类错误重试 3 次
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read().decode())
                return (data["choices"][0]["message"]["content"] or "").strip()
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"OCR API 请求失败:{last_err}")

    def run(self, image_path: Path) -> str:
        """识别单行/整帧图片,返回日文文本(可能为空串)。"""
        return self._chat(self._image_data_url(image_path))

    def run_lines(self, image_path: Path) -> str:
        """screen 布局:多行文本按图片中的行用 '\\n' 拼回。"""
        return self.run(image_path)

    def run_many(self, image_paths: list[Path], *, lines: bool = False) -> list[str]:
        """批量识别多帧(逐张请求,并行交给上层多进程)。"""
        return [self.run(p) for p in image_paths]


def make_engine():
    """按 settings.OCR_PROVIDER 返回 OCR 引擎(baidu 默认 / openai 可选)。"""
    provider = settings.OCR_PROVIDER
    if provider == "openai":
        return ApiOcrEngine()
    return BaiduOcrEngine()
