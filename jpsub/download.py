"""通过 yt-dlp 下载 niconico / YouTube 视频(走代理)。

流程:提取视频 id -> 用 yt-dlp 列出格式 -> 选分辨率/大小最小的含视频流格式 -> 下载到目标目录。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from . import settings

WATCH_RE = re.compile(r"(?:https?://(?:www\.)?nicovideo\.jp/watch/)?(sm\d+)", re.I)

_YT_RE = re.compile(r"(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/(?:watch|shorts|live)|youtu\.be/)", re.I)


def is_youtube(url: str) -> bool:
    return bool(_YT_RE.search(url.strip()))


_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# 常见网盘/文件分享域名,出现在简介行内则整行剔除(避免 B 站审核应激)
_NETDISK_RE = re.compile(
    r"(mega\.nz|drive\.google|docs\.google\.com|dropbox\.com|pan\.baidu\.com"
    r"|1drv\.ms|onedrive\.live|mediafire|pixeldrain|gofile\.io|catbox\.moe"
    r"|0x0\.st|gigafile\.nu|firestorage|getuploader|fastlist"
    r"|disk\.yandex|transfer\.sh|wetransfer|send\.space|file\.io|anonfiles"
    r"|krakenfiles|bagi\.co\.in|tmpfiles\.org|litterbox|pomf\.se|uguu\.se)",
    re.I,
)


def _safe_title(info: dict, fallback: str) -> str:
    """取视频标题作文件名,清理非法字符,超长截断,失败回退到视频 id。"""
    title = (info.get("title") or "").strip()
    title = _ILLEGAL.sub("_", title).rstrip(". ")
    if not title:
        return fallback
    return title[:100]


def extract_video_id(text: str) -> str | None:
    """从 URL 或裸 id(如 sm43168834)中提取视频 id。"""
    m = WATCH_RE.match(text.strip())
    return m.group(1) if m else None


def _known_id(url: str) -> str | None:
    """从 URL 粗提取视频 id(nico 的 sm 号或 YouTube 的视频 id),用于跳过下载检查。"""
    vid = extract_video_id(url)
    if vid:
        return vid
    m = re.search(r"(?:v=|youtu\.be/|shorts/|live/)([\w-]{6,})", url)
    return m.group(1) if m else None


def _proxy_args() -> list[str]:
    return ["--proxy", settings.PROXY] if settings.PROXY else []


def _ffmpeg_args() -> list[str]:
    """告诉 yt-dlp ffmpeg 位置(Windows 上可能不在 PATH,放程序目录/bin 下)。

    注意:yt-dlp 把该值当文件路径,传裸名 ffmpeg(走 PATH)会导致合并被静默跳过,
    因此只在解析出真实存在的二进制文件时才传,并把名字解析为绝对路径。
    """
    bin_path = Path(settings.binary("ffmpeg"))
    if not bin_path.is_file():
        return []
    return ["--ffmpeg-location", str(bin_path.resolve())]


def _net_args() -> list[str]:
    """提高下载并发:分片流开 32 并发;装了 aria2c 则用它多连接下载,吃满带宽。"""
    args = ["--concurrent-fragments", "32"]
    aria2 = shutil.which("aria2c")
    if aria2:
        args += [
            "--downloader", aria2,
            "--downloader-args", "aria2c:-x 32 -s 32 -k 1M --file-allocation=none",
        ]
    return args


def _cookies_args() -> list[str]:
    return (
        ["--cookies-from-browser", settings.COOKIES_FROM_BROWSER]
        if settings.COOKIES_FROM_BROWSER
        else []
    )


def _run_ytdlp(
    args: list[str], *, check: bool = True, quiet: bool = False, progress=None
) -> subprocess.CompletedProcess:
    """调用外部 yt-dlp(Windows 用项目根目录 yt-dlp.exe,Linux 用系统 yt-dlp)。

    quiet=True 时不打印进度/日志;若提供 progress 则解析下载百分比回调,
    按当前目标文件名区分视频/音频流,回调形如 progress("视频 43%")。
    失败时把 stderr 抛出。
    """
    cmd = [settings.binary("yt-dlp"), *_ffmpeg_args(), *_net_args(), *_cookies_args(), *_proxy_args(), *args]
    if not quiet:
        return subprocess.run(cmd, check=check)
    if progress:
        cmd.append("--newline")  # 管道下逐行输出进度,便于解析百分比
    import tempfile

    stream = ""  # 当前下载的流:"视频"/"音频"(双流按 Destination 行切换)
    dest_n = 0  # 双流时第 1 个 Destination 为视频、第 2 个为音频(YouTube 用数字格式 id)
    with tempfile.TemporaryFile("w+") as errf:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf, text=True)
        for line in p.stdout:
            if "[download] Destination:" in line:
                dest_n += 1
                if ".fvideo" in line:
                    stream = "视频"
                elif ".faudio" in line:
                    stream = "音频"
                else:
                    stream = "视频" if dest_n == 1 else "音频"
            if progress:
                m = re.search(r"\[download\]\s+([\d.]+)%", line)
                if m:
                    progress(f"{stream} {float(m.group(1)):.0f}%".strip())
        p.wait()
        if check and p.returncode != 0:
            errf.seek(0)
            raise SystemExit(f"yt-dlp 下载失败:\n{errf.read().strip()}")
        return subprocess.CompletedProcess(cmd, p.returncode)


def _load_glossary() -> dict[str, str]:
    """加载项目根目录 glossary.txt(简介/tag 文本替换用);不存在则返回空。"""
    p = Path(__file__).resolve().parent.parent / "glossary.txt"
    if not p.is_file():
        return {}
    gloss: dict[str, str] = {}
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        k, sep, v = ln.partition("\t")
        if not sep:
            k, _, v = ln.partition(" ")
        k, v = k.strip(), v.strip()
        if k and v:
            gloss[k] = v
    return gloss


def glossary_sub(text: str) -> str:
    """按名词对照表替换文本(长词优先),无表时原样返回。"""
    gloss = _load_glossary()
    for k in sorted(gloss, key=len, reverse=True):
        if k in text:
            text = text.replace(k, gloss[k])
    return text


def _extract_info(url: str) -> dict:
    """用 yt-dlp -J 获取视频信息(不下载)。"""
    cmd = [settings.binary("yt-dlp"), "-J", "--no-warnings", *_cookies_args(), *_proxy_args(), url]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"yt-dlp 获取视频信息失败:\n{r.stderr.strip()}")
    return json.loads(r.stdout)


def _pick_formats(info: dict) -> tuple[dict | None, dict | None]:
    """从格式列表里选 (视频格式, 音频格式)。

    视频:在 ≥360p 且非 lowest(最低画质) 的格式里选最小的;没有则放宽。
    音频:选码率最低的纯音频流(无纯音频流则返回 None,让 yt-dlp 自己拼)。
    """
    fmts = info.get("formats", [])
    vid_fmts = [
        f for f in fmts
        if f.get("vcodec") not in (None, "none")
        and f.get("acodec") in (None, "none")
    ]
    if not vid_fmts:
        vid_fmts = [f for f in fmts if f.get("vcodec") not in (None, "none")]
    if not vid_fmts:
        raise SystemExit("错误:未找到包含视频流的格式")
    def size(f: dict) -> float:
        s = f.get("filesize") or f.get("filesize_approx")
        if s:
            return float(s)
        return (f.get("tbr") or 0) * 125 * (info.get("duration") or 1)

    # 画质设置:"lowest"=最低画质变体,"best"=最高画质,"360p"/"480p"/"720p"=指定分辨率
    q = settings.NICO_VIDEO_QUALITY
    if q == "best":
        pool = [f for f in vid_fmts if "-lowest" not in (f.get("format_id") or "")] or vid_fmts
        video = max(pool, key=size)
    elif q == "lowest":
        pool = [f for f in vid_fmts if "-lowest" in (f.get("format_id") or "")] or vid_fmts
        video = min(pool, key=size)
    else:  # "360p"/"480p"/"720p":指定分辨率,优先普通变体
        try:
            h = int(str(q).rstrip("pP"))
        except ValueError:
            raise SystemExit(f"错误:NICO_VIDEO_QUALITY 无法识别:{q!r}(应为 lowest/best/360p/480p/720p)")
        exact = [f for f in vid_fmts if (f.get("height") or 0) == h]
        normal_exact = [f for f in exact if "-lowest" not in (f.get("format_id") or "")]
        ge = [f for f in vid_fmts if (f.get("height") or 0) >= h]
        normal_ge = [f for f in ge if "-lowest" not in (f.get("format_id") or "")]
        pool = normal_exact or exact or normal_ge or ge or vid_fmts
        video = min(pool, key=size)

    aud_fmts = [
        f for f in fmts
        if f.get("acodec") not in (None, "none")
        and f.get("vcodec") in (None, "none")
    ]
    if aud_fmts:
        if settings.NICO_AUDIO_QUALITY == "best":
            audio = max(aud_fmts, key=lambda f: f.get("abr") or size(f))
        else:  # "lowest"
            audio = min(aud_fmts, key=lambda f: f.get("abr") or size(f))
    else:
        audio = None
    return video, audio


def _yt_fmt_expr() -> str:
    """YouTube 的 yt-dlp 格式选择表达式(按 NICO_VIDEO_QUALITY 映射分辨率上限)。"""
    q = settings.NICO_VIDEO_QUALITY
    if q == "best":
        return "bestvideo*+bestaudio/best"
    if q == "lowest":
        return "worstvideo*+worstaudio/worst"
    try:
        h = int(str(q).rstrip("pP"))
    except ValueError:
        raise SystemExit(f"错误:NICO_VIDEO_QUALITY 无法识别:{q!r}(应为 lowest/best/360p/480p/720p)")
    return f"bestvideo*[height<={h}]+bestaudio/best[height<={h}]/best"


def download(
    url: str,
    out_dir: Path,
    *,
    comment: str | None = None,
    quiet: bool = False,
    progress=None,
) -> Path:
    """下载视频(按 settings.NICO_VIDEO_QUALITY/NICO_AUDIO_QUALITY)并合并,返回视频文件路径。

    视频保存到 out_dir/<标题>_<视频id> 工作目录内;
    comment 为视频描述,写入工作目录 .jpsub/comment.txt 供翻译提示使用。
    quiet=True 时不打印任何过程信息(批量模式由状态板统一展示);
    quiet 下 progress("视频 43%"/"音频 88%") 回调双流下载进度。
    """

    def _say(msg: str):
        if not quiet:
            print(msg)
    vid = extract_video_id(url)
    yt = is_youtube(url)
    if vid and not yt and not url.lower().startswith("http"):
        url = f"https://www.nicovideo.jp/watch/{vid}"

    # 三件套(视频+info.txt+cover.jpg)齐全则整体跳过下载
    kid = _known_id(url)
    if kid and out_dir.is_dir():
        for d in out_dir.glob(f"*_{kid}"):
            if not d.is_dir():
                continue
            done = next(
                (p for p in d.iterdir() if p.is_file() and p.suffix.lower() in (".mp4", ".mkv", ".webm")),
                None,
            )
            if done and (d / "info.txt").is_file() and (d / "cover.jpg").is_file():
                _say(f"已完整,跳过下载:{done}")
                return done

    info = _extract_info(url)
    if yt:
        fmt = _yt_fmt_expr()
        _say(f"选定格式:{fmt}")
    else:
        video, audio = _pick_formats(info)
        if audio:
            fmt = f"{video['format_id']}+{audio['format_id']}"
            _say(
                f"选定格式:{video['format_id']}({video.get('height')}p)"
                f" + {audio['format_id']}({audio.get('abr')}kbps)"
            )
        else:
            fmt = video["format_id"]
            _say(f"选定格式:{fmt}(无独立音频流)")
    vid = info.get("id", "video")
    name = _safe_title(info, vid)
    # 工作目录:<标题>_<视频id> 防重名
    work_dir = out_dir / f"{name}_{vid}"
    out_dir.mkdir(parents=True, exist_ok=True)
    # 标题里的 % 会破坏 -o 占位符模板,模板中需转义为 %%
    # 只匹配文件(排除子目录),优先视频扩展名
    out_file = next(
        (p for ext in (".mp4", ".mkv", ".webm") for p in sorted(work_dir.glob(f"{name}{ext}"))),
        None,
    )
    if out_file is None:
        _run_ytdlp([
            "-f", fmt,
            "--merge-output-format", "mp4",
            "-o", str(work_dir / f"{name.replace('%', '%%')}.%(ext)s"),
            "--no-warnings",
            url,
        ], quiet=quiet, progress=progress)
        out_file = next(
            (p for ext in (".mp4", ".mkv", ".webm") for p in sorted(work_dir.glob(f"{name}{ext}"))),
            None,
        ) or next((p for p in work_dir.glob(f"{name}.*") if p.is_file()), None)
        if out_file is None:
            raise SystemExit(f"错误:下载后未找到 {work_dir}/{name}.*")
    # 写视频元信息,供翻译参考;剔除网盘/外部下载链接避免 B 站审核问题
    _date = info.get("upload_date") or ""
    _date = f"{_date[:4]}-{_date[4:6]}-{_date[6:8]}" if len(_date) == 8 else ""
    desc = glossary_sub(
        "\n".join(
            line for line in (info.get("description") or "").splitlines()
            if not _NETDISK_RE.search(line)
        ).strip()
    )
    tags = ", ".join(info.get("tags") or [])
    tags = glossary_sub(tags)
    info_path = out_file.parent / "info.txt"

    class _SafeDict(dict):  # 未知占位符原样保留,避免自定义模板 KeyError
        def __missing__(self, k):
            return "{" + k + "}"

    # info 里的 URL 去掉跟踪参数:niconico 取干净 watch 链接,YouTube 只保留 v=
    if vid.lower().startswith(("sm", "nm")):
        clean_url = f"https://www.nicovideo.jp/watch/{vid}"
    else:
        from urllib.parse import parse_qs, urlencode, urlparse

        _u = urlparse(info.get("webpage_url") or url)
        _q = {k: v[-1] for k, v in parse_qs(_u.query).items() if k in ("v",)}
        clean_url = _u._replace(query=urlencode(_q)).geturl()
    if not info_path.exists():
        info_path.write_text(
            settings.INFO_TEMPLATE.format_map(
                _SafeDict(
                    url=clean_url,
                    title=info.get("title") or "",
                    uploader=info.get("uploader") or info.get("channel") or "",
                    date=_date,
                    description=desc,
                    tags=tags,
                )
            )
            + "\n",
            encoding="utf-8",
        )
    if comment:
        from . import handoff

        comment_path = handoff.comment_path(work_dir)
        comment_path.parent.mkdir(parents=True, exist_ok=True)
        comment_path.write_text(comment, encoding="utf-8")
        _say(f"视频描述:{comment} -> {comment_path}")
    cover = out_file.parent / "cover.jpg"
    if not cover.exists() and (info.get("thumbnail") or "").startswith("http"):
        try:  # 封面下载失败不影响主流程
            import urllib.request

            req = urllib.request.Request(info["thumbnail"], headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=30).read()
            cover.write_bytes(data)
            _say(f"封面:{cover}")
        except Exception as e:  # noqa: BLE001
            _say(f"封面下载失败(忽略):{e}")
    _say(f"下载完成:{out_file}")
    return out_file
