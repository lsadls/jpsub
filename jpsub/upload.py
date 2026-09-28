"""B 站投稿:工作目录成品(打码视频>原视频)+ AI 翻译标题 → biliup 上传提交。

登录:一次性 `biliup-rs login` 扫码,cookies.json 放 settings.BILIUP_COOKIE
(默认 ~/.jpsub/cookies.json);token 过期由调用方提示重新登录。

产物:BV 号写回工作目录 upload.json({bvid, aid, time}),重复投稿先查此文件拦截。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import settings, utils


def upload_json(work: Path) -> Path:
    """工作目录 upload.json 路径(投稿记录)。"""
    return work / "upload.json"


def cookie_path() -> Path:
    return Path(settings.BILIUP_COOKIE).expanduser()


def read_info(work: Path) -> dict:
    """解析工作目录 info.txt:首行 URL、原标题/原作者/投稿时间、多行原简介、标签。

    原简介从「原简介:」起(含同行内容)连续收集到「标签:」行前,保留换行。"""
    f = work / "info.txt"
    out = {"url": "", "title": "", "uploader": "", "date": "", "desc": "", "tags": ""}
    if not f.is_file():
        return out
    lines = f.read_text(encoding="utf-8").splitlines()
    if lines:
        out["url"] = lines[0].strip()
    desc: list[str] = []
    in_desc = False
    for ln in lines[1:]:
        if ln.startswith("标签:"):
            out["tags"] = ln.partition("标签:")[2].strip()
            in_desc = False
        elif ln.startswith("原标题:"):
            out["title"] = ln.partition("原标题:")[2].strip()
        elif ln.startswith("原作者:"):
            out["uploader"] = ln.partition("原作者:")[2].strip()
        elif ln.startswith("投稿时间:"):
            out["date"] = ln.partition("投稿时间:")[2].strip()
        elif ln.startswith("原简介:"):
            desc.append(ln.partition("原简介:")[2].strip())
            in_desc = True
        elif in_desc:
            desc.append(ln)
    out["desc"] = "\n".join(desc).strip()
    return out


def pick_tags(info_tags: str) -> list[str]:
    """投稿标签:直接用 info.txt 标签行;为空回退 settings.UPLOAD_TAGS。"""
    tags = [t.strip() for t in info_tags.split(",") if t.strip()]
    if tags:
        return tags
    return [x.strip() for x in str(settings.UPLOAD_TAGS or "").split(",") if x.strip()]


def load_prefix() -> str:
    """标题前缀(settings.UPLOAD_PREFIX,网页保存到运行时配置)。"""
    return str(getattr(settings, "UPLOAD_PREFIX", "") or "")


def save_prefix(text: str) -> None:
    settings.UPLOAD_PREFIX = text


def build_meta(work: Path) -> dict:
    """组装稿件元信息(主页确认表单预填用):{title, desc, tags, tid, delay}。

    标题默认 info.txt 原标题(不翻译,由用户在弹窗里填);简介 = 原标题/原作者/
    投稿时间/原视频 URL + 多行原简介(网盘行下载时已剔除);标签直接用 info 标签行。"""
    info = read_info(work)
    desc = info["desc"]
    head = "\n".join(
        x
        for x in (
            f"原标题:{info['title']}",
            f"原作者:{info['uploader']}",
            f"投稿时间:{info['date']}",
            f"原视频:{info['url']}",
        )
        if x.partition(":")[2]
    )
    if head and desc:
        desc = f"{head}\n\n{desc}"
    elif head:
        desc = head
    return {
        "title": info["title"] or "",
        "orig_title": info["title"],
        "desc": desc,
        "tags": ", ".join(pick_tags(info["tags"])),
        "tid": settings.UPLOAD_TID,
        "delay": settings.UPLOAD_DELAY,
        "source": info["url"],
    }


def _find_upload_video(work: Path) -> Path:
    """定位投稿成品:打码视频(*.masked.mp4/mkv/webm) > 原视频。"""
    masked = [
        p
        for ext in utils.VID_EXTS
        for p in sorted(work.glob(f"*masked{ext}"))
    ]
    if masked:
        return masked[0]
    v = utils.find_video(work)
    if v is None:
        raise SystemExit(f"错误:{work} 里没有视频文件")
    return v


def login() -> None:
    """扫码登录:优先调用 biliup-rs login,产物 cookies.json 拷到 BILIUP_COOKIE。"""
    p = cookie_path()
    if p.is_file():
        print(f"已有登录态:{p}(重新登录请先删除)")
        return
    rs = shutil.which("biliup")
    if not rs:
        raise SystemExit(
            "未找到 biliup-rs(命令名 biliup)。请先安装:biliup-rs releases 下载后放入 PATH,"
            f"运行 `biliup login` 扫码,再把 cookies.json 放到 {p}"
        )
    import subprocess

    subprocess.run([rs, "login"], check=True)
    if Path("cookies.json").is_file():
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2("cookies.json", p)
        print(f"登录态已保存:{p}")
    else:
        raise SystemExit(f"登录后没找到 cookies.json,请手动放到 {p}")


def upload(
    work: Path,
    *,
    title: str | None = None,
    desc: str | None = None,
    tags: str | None = None,
    tid: int | None = None,
    delay: int | None = None,
) -> dict:
    """投稿:上传视频 → 提交稿件 → BV 写回 upload.json。返回 {bvid, aid}。"""
    rec = upload_json(work)
    if rec.is_file():
        try:
            old = json.loads(rec.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            old = {}
        raise SystemExit(
            f"该条目已投稿过:{old.get('bvid', '?')}(如需重投请删除 {rec})"
        )

    cp = cookie_path()
    if not cp.is_file():
        raise SystemExit(
            f"未登录:先运行 `jpsub upload <工作目录> --login` 或 biliup-rs login,"
            f"并把 cookies.json 放到 {cp}"
        )

    meta = build_meta(work)
    prefix = load_prefix()
    title = (title or meta["title"]).strip() or meta["title"]
    if prefix:  # 网页保存的标题前缀
        title = prefix + title
    title = title[:80]
    desc = meta["desc"] if desc is None else desc
    tag_list = [t.strip() for t in (tags or meta["tags"]).split(",") if t.strip()]
    tid = int(tid or meta["tid"])
    delay = int(delay if delay is not None else meta["delay"]) or 0

    video_path = _find_upload_video(work)
    print(f"投稿视频:{video_path}")

    from biliup.plugins.bili_webup import BiliBili, Data

    data = Data()
    data.copyright = 2  # 转载
    data.source = meta.get("source") or "https://www.bilibili.com"
    data.tid = tid
    data.title = title[:80]
    data.desc = desc
    data.set_tag(tag_list)
    cover = work / "cover.jpg"
    with BiliBili(data) as bili:
        try:
            bili.login(str(cp), str(cp))
        except Exception as e:  # noqa: BLE001  cookie 过期等:提示重新扫码
            raise SystemExit(
                f"登录失败(cookies 可能已过期):{e}\n"
                f"删除 {cp} 后重新 `biliup-rs login` 扫码"
            ) from None
        part = bili.upload_file(str(video_path), "AUTO", 3)
        part["title"] = title[:80]
        data.append(part)
        if delay:
            data.delay_time(int(time.time()) + delay)
        if cover.is_file():
            try:
                data.cover = bili.cover_up(str(cover)).replace("http:", "")
            except Exception as e:  # noqa: BLE001  封面失败不影响投稿
                print(f"封面上传失败(忽略):{e}")
        ret = bili.submit("web")
    if ret.get("code") != 0:
        raise SystemExit(f"投稿提交失败:{ret}")
    out = {"bvid": ret["data"]["bvid"], "aid": ret["data"]["aid"],
           "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    rec.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"投稿成功:{out['bvid']} https://www.bilibili.com/video/{out['bvid']}")
    return out
