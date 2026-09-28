"""跨模块复用的小工具:产物目录定位、工作目录/视频查找、info 标签读取、
浏览器页面(http.server)公共服务助手。"""
from __future__ import annotations

from pathlib import Path

from . import handoff

VID_EXTS = (".mp4", ".mkv", ".webm")


def output_root() -> Path:
    """统一产物根目录:项目根(包上一级)下的 output/,与运行时 cwd 无关。"""
    return Path(__file__).resolve().parent.parent / "output"


def default_work(video: Path) -> Path:
    """默认工作目录:output/ 下以视频名新建。"""
    return output_root() / video.stem


def work_of(video: Path) -> Path:
    """视频所属工作目录:视频已在工作目录内则为其所在目录,否则默认目录。"""
    p = video.parent
    if (
        (p / handoff.HIDDEN_DIR).is_dir()
        or (p / "info.txt").is_file()  # download 新布局 <标题>_<id>/ 内必有 info.txt
        or (p / "segments.json").is_file()
    ):
        return p
    return default_work(video)


def work_of_name(root: Path, name: str) -> Path:
    """按列表名找工作目录:<name> 目录。"""
    return root / name


def find_video(work: Path) -> Path | None:
    """找工作目录内的视频:优先与目录同名,否则取目录内第一个视频文件。"""
    stem = handoff.item_stem(work)
    for ext in VID_EXTS:
        v = work / (stem + ext)
        if v.exists():
            return v
    for ext in VID_EXTS:
        for v in sorted(work.glob(f"*{ext}")):
            if v.is_file():
                return v
    return None


def named_video(root: Path, name: str) -> Path | None:
    """按条目名找视频:同 find_video。"""
    return find_video(work_of_name(root, name))


def load_tags(work: Path) -> str:
    """读工作目录 info.txt 的「标签:」行,返回逗号分隔串(无则空串)。"""
    f = work / "info.txt"
    if not f.is_file():
        return ""
    for ln in f.read_text(encoding="utf-8").splitlines():
        if ln.startswith("标签:"):
            return ln.partition("标签:")[2].strip()
    return ""


# ---- 备份(cli/edit/mask 共用) ----


def backup(work: Path, *files: Path, name: str = "") -> None:
    """覆盖前把存在的文件打包为 work/backup/<备份名|时间戳>.tar.gz,防止误操作。

    name 为空用时间戳(人可读格式),指定则用自定义名;同秒/同名追加 -n 序号。"""
    import tarfile
    import time

    ts = name.strip() or time.strftime("%Y%m%d-%H%M%S")
    ts = "".join(c if c not in '\\/:*?"<>|' else "-" for c in ts).strip("- ")
    bdir = handoff.backup_dir(work)
    bdir.mkdir(parents=True, exist_ok=True)
    dest = bdir / f"{ts}.tar.gz"
    n = 1
    while dest.exists():  # 同一秒多次保存:追加序号,保证每次备份独立
        dest = bdir / f"{ts}-{n}.tar.gz"
        n += 1
    flist = [f for f in files if f.is_file()]
    if not flist:
        return
    with tarfile.open(dest, "w:gz") as tf:
        for f in flist:
            tf.add(f, arcname=f.name)


def backup_names(work: Path, fname: str) -> list[str]:
    """backup/ 下含 fname 的备份(tar.gz 归档),按备份时间(mtime)新→旧排序。"""
    import tarfile

    bdir = handoff.backup_dir(work)
    if not bdir.is_dir():
        return []
    out = []
    for p in sorted(
        bdir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        if p.is_file() and p.name.endswith(".tar.gz"):
            try:
                with tarfile.open(p, "r:gz") as tf:
                    if fname in tf.getnames():
                        out.append(p.name)
            except (tarfile.TarError, OSError):
                continue
        elif p.is_dir() and (p / fname).is_file():
            out.append(p.name)
    return out


def backup_src(work: Path, name: str, fname: str) -> Path:
    """按备份名取 fname 的可读路径:旧目录直接返回,tar.gz 解到临时文件。"""
    import tempfile
    import tarfile

    p = handoff.backup_dir(work) / name
    if p.is_dir():
        src = p / fname
        if src.is_file():
            return src
    elif p.is_file() and p.name.endswith(".tar.gz"):
        with tarfile.open(p, "r:gz") as tf:
            m = tf.extractfile(fname)
            if m is None:
                raise FileNotFoundError(f"找不到备份:{p / fname}")
            tmp = tempfile.NamedTemporaryFile(
                delete=False, suffix=Path(fname).suffix
            )
            tmp.write(m.read())
            tmp.close()
            return Path(tmp.name)
    raise FileNotFoundError(f"找不到备份:{p / fname}")


# ---- 浏览器页面公共服务助手(home / edit / mask 共用) ----


def http_bytes(handler, data: bytes, ctype: str, code: int = 200) -> None:
    """发送字节响应(统一 Content-Length)。"""
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def http_json(handler, obj) -> None:
    """发送 JSON 响应。"""
    import json

    http_bytes(
        handler,
        json.dumps(obj, ensure_ascii=False).encode(),
        "application/json",
    )


def http_page(handler, html: str) -> None:
    """发送 HTML 页面。"""
    http_bytes(handler, html.encode(), "text/html; charset=utf-8")


def http_video(handler, fp: Path) -> None:
    """带 Range 的视频流式响应(供 <video> 元素 seek 取帧)。
    开放区间一次性发完余量(浏览器读够会自行断连);支持 Range 使 seek 不用从头传。
    不要限小块:小块会让浏览器缓冲一放完就断流重连,播放卡顿且频繁重试。"""
    size = fp.stat().st_size
    start, end, code = 0, size - 1, 200
    rng = handler.headers.get("Range") or ""
    if rng.startswith("bytes="):
        a, _, b = rng.partition("=")[2].partition("-")
        if a or b:
            start = int(a) if a else 0
            end = min(int(b), size - 1) if b else size - 1
            code = 206
    handler.send_response(code)
    handler.send_header("Content-Type", "video/mp4")
    handler.send_header("Accept-Ranges", "bytes")
    if code == 206:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.send_header("Content-Length", str(end - start + 1))
    handler.end_headers()
    with fp.open("rb") as f:
        f.seek(start)
        left = end - start + 1
        while left > 0:
            chunk = f.read(min(1 << 16, left))
            if not chunk:
                break
            try:
                handler.wfile.write(chunk)
            except Exception:  # noqa: BLE001  拖动时浏览器会中断连接
                return
            left -= len(chunk)


def render_page(page: str, mapping: dict) -> str:
    """@@TOKEN@@ 占位符替换(不能用 %-formatting:CSS/JS 的 % 会冲突)。"""
    for key, val in mapping.items():
        page = page.replace(key, val)
    return page


def pick_server(handler_cls, ports: range):
    """在端口区间里挑一个空闲端口起 ThreadingHTTPServer。"""
    from http.server import ThreadingHTTPServer

    for p in ports:
        try:
            return ThreadingHTTPServer(("127.0.0.1", p), handler_cls)
        except OSError:
            continue
    raise SystemExit(f"错误:{ports.start}~{ports.stop - 1} 端口都被占用")


def open_browser(srv, delay: float = 0.3) -> str:
    """延迟打开浏览器,返回服务 URL。"""
    import threading
    import webbrowser

    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    threading.Timer(delay, lambda: webbrowser.open(url)).start()
    return url


class IdleQuit:
    """页面关闭即退出:pagehide 发 /quit 后调 on_quit,超时窗口内无新请求才 shutdown
    (排除刷新误触);handler 里每次请求前调 touch()。"""

    def __init__(self, timeout: float = 1.5):
        import time

        self.timeout = timeout
        self._last = time.time()
        self.srv = None  # 服务起来后再绑

    def touch(self) -> None:
        import time

        self._last = time.time()

    def on_quit(self) -> None:
        import threading

        threading.Timer(2.0, self._check).start()

    def _check(self) -> None:
        import time

        if time.time() - self._last > self.timeout:
            self.srv.shutdown()


# 浏览器编辑器通用自动保存:操作只标脏,60 秒(或页面隐藏)才真正落盘。
# saveFn(auto) 的 auto=true 表示自动保存,服务端据此跳过备份;edit(字幕)与 mask(打码)共用。
AUTOSAVE_JS = """
function mkAutosave(saveFn,ms=60000){
  let dirty=false;
  window.markDirty=()=>{dirty=true};
  const flush=()=>{if(dirty){dirty=false;try{saveFn(true)}catch(e){}}};
  setInterval(flush,ms);
  addEventListener('pagehide',()=>{if(dirty){dirty=false;try{saveFn(true)}catch(e){}}});
}
"""
