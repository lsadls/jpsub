"""通过 yt-dlp 下载 niconico 视频(走代理)。

流程:提取视频 id -> 用 yt-dlp 列出格式 -> 选分辨率/大小最小的含视频流格式 -> 下载到目标目录。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import settings

WATCH_RE = re.compile(r"(?:https?://(?:www\.)?nicovideo\.jp/watch/)?(sm\d+)", re.I)

_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


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
    cmd = [settings.binary("yt-dlp"), *_ffmpeg_args(), *_cookies_args(), *_proxy_args(), *args]
    if not quiet:
        return subprocess.run(cmd, check=check)
    if progress:
        cmd.append("--newline")  # 管道下逐行输出进度,便于解析百分比
    import tempfile

    stream = ""  # 当前下载的流:"视频"/"音频"(双流按 Destination 行切换)
    with tempfile.TemporaryFile("w+") as errf:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf, text=True)
        for line in p.stdout:
            if "[download] Destination:" in line:
                if ".fvideo" in line:
                    stream = "视频"
                elif ".faudio" in line:
                    stream = "音频"
            if progress:
                m = re.search(r"\[download\]\s+([\d.]+)%", line)
                if m:
                    progress(f"{stream} {float(m.group(1)):.0f}%".strip())
        p.wait()
        if check and p.returncode != 0:
            errf.seek(0)
            raise SystemExit(f"yt-dlp 下载失败:\n{errf.read().strip()}")
        return subprocess.CompletedProcess(cmd, p.returncode)


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


def download(
    url: str,
    out_dir: Path,
    *,
    comment: str | None = None,
    quiet: bool = False,
    progress=None,
) -> Path:
    """下载视频(按 settings.NICO_VIDEO_QUALITY/NICO_AUDIO_QUALITY)并合并,返回视频文件路径。

    视频保存到 out_dir/<标题>.jpsub/ 工作目录内,以视频名命名;
    comment 为视频描述,写入工作目录 comment.txt 供翻译提示使用。
    quiet=True 时不打印任何过程信息(批量模式由状态板统一展示);
    quiet 下 progress("视频 43%"/"音频 88%") 回调双流下载进度。
    """

    def _say(msg: str):
        if not quiet:
            print(msg)
    vid = extract_video_id(url)
    if vid and not url.lower().startswith("http"):
        url = f"https://www.nicovideo.jp/watch/{vid}"

    info = _extract_info(url)
    video, audio = _pick_formats(info)
    vid = info.get("id", "video")
    name = _safe_title(info, vid)
    if audio:
        fmt = f"{video['format_id']}+{audio['format_id']}"
        _say(
            f"选定格式:{video['format_id']}({video.get('height')}p)"
            f" + {audio['format_id']}({audio.get('abr')}kbps)"
        )
    else:
        fmt = video["format_id"]
        _say(f"选定格式:{fmt}(无独立音频流)")
    work_dir = out_dir / f"{name}.jpsub"  # 视频直接下进工作目录(新布局)
    out_dir.mkdir(parents=True, exist_ok=True)
    # 标题里的 % 会破坏 -o 占位符模板,模板中需转义为 %%
    _run_ytdlp([
        "-f", fmt,
        "--merge-output-format", "mp4",
        "-o", str(work_dir / f"{name.replace('%', '%%')}.%(ext)s"),
        "--no-warnings",
        url,
    ], quiet=quiet, progress=progress)
    # 只匹配文件(排除子目录),优先视频扩展名
    out_file = next(
        (p for ext in (".mp4", ".mkv", ".webm") for p in sorted(work_dir.glob(f"{name}{ext}"))),
        None,
    ) or next((p for p in work_dir.glob(f"{name}.*") if p.is_file()), None)
    if out_file is None:
        raise SystemExit(f"错误:下载后未找到 {work_dir}/{name}.*")
    if comment:
        comment_path = out_file.parent / "comment.txt"
        comment_path.parent.mkdir(parents=True, exist_ok=True)
        comment_path.write_text(comment, encoding="utf-8")
        _say(f"视频描述:{comment} -> {comment_path}")
    _say(f"下载完成:{out_file}")
    return out_file
