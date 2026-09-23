"""调用 OpenAI 兼容 API,把 translate-in.txt 翻译成 translate-out.txt。

提示词为 SKILL.md 的简化版;分批请求,每批结果立即追加写盘,支持中断续翻。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path

from tqdm import tqdm

from . import settings

# SKILL.md 简化版系统提示词
SYSTEM_PROMPT = """你是日语字幕翻译,把用户提供的日文字幕逐句翻译为简体中文。

背景:视频是日本例区文化(如淫梦文化)的二次创作(如 BB 剧场),注意例区用语、
网络梗和人物绰号的既定译法,同一专有名词全篇译法一致。

规则:
- 忠实原意,语气自然简洁,不添油加醋、不省略信息;
- 保留「」、感叹号、破折号等语气标记;
- 惯用语/俗语按中文习惯意译,不逐字直译;
- 注意隐含的说话对象与语气功能(安慰、吐槽、反问等);
- 只输出译文,不要日文原文、不要注音、不要解释
- 不要思考人物关系或故事情节等, 只思考翻译。

输出格式:与输入同编号,每行 `编号<TAB>中文译文`,必须覆盖输入的所有行。"""

_LINE_RE = re.compile(r"^\s*(\d+)\s*\t(.*)$")


def _chat(
    messages: list[dict], cfg: dict, *, temperature: float = 0.3, retries: int = 3
) -> str:
    """调用 OpenAI 兼容 /chat/completions 端点,带简单重试。

    cfg 含 api_base/api_key/model。API 请求不走代理(代理只用于
    pip/模型下载,见 settings.py)。
    messages 为完整对话历史;系统提示词只放在第一条,后续批次在同一
    对话上下文里继续,配合服务商 prompt cache 省去重复前缀的开销。
    """
    url = cfg["api_base"].rstrip("/") + "/chat/completions"
    body = json.dumps(
        {
            "model": cfg["model"],
            "temperature": temperature,
            "messages": messages,
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['api_key']}",
        },
    )
    # 用空 ProxyHandler 屏蔽环境变量代理,保证 API 直连
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    err: Exception | None = None
    for attempt in range(retries):
        try:
            with opener.open(req, timeout=300) as resp:
                data = json.loads(resp.read())
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                raise ValueError(f"API 响应格式异常:{data}") from None
        except Exception as e:  # 网络抖动/限流/响应异常,退避重试
            err = e
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"API 调用失败(已重试 {retries} 次):{err}")


def read_lines(path: Path) -> list[tuple[str, str]]:
    """读取「编号<TAB>文本」清单,返回 [(编号, 文本), ...]。"""
    items: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        m = _LINE_RE.match(line)
        if not m:
            raise ValueError(f"{path} 格式错误(应为 编号<TAB>文本):{line!r}")
        items.append((m.group(1), m.group(2).strip()))
    return items


def read_done(path: Path) -> set[str]:
    """读取已有译文的编号集合(用于续翻)。"""
    if not path.exists():
        return set()
    done: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _LINE_RE.match(line)
        if m:
            done.add(m.group(1))
    return done


def _parse_reply(reply: str) -> dict[str, str]:
    """从模型回复解析「编号 -> 译文」;容忍 markdown、空格/标点分隔等格式。"""
    out: dict[str, str] = {}
    # 去掉 markdown 代码围栏
    reply = re.sub(r"```[a-zA-Z]*\n?|```", "", reply)
    # 编号 + (TAB/空格/常用标点) + 文本
    line_re = re.compile(r"^\s*(\d+)\s*(?:\t|[.、。:：)）\]])\s*(.+?)\s*$")
    num_re = re.compile(r"^\s*(\d+)\s+(\S.*)$")
    for raw in reply.splitlines():
        line = raw.strip().strip("*`")
        m = line_re.match(line) or num_re.match(line)
        if m and m.group(2).strip():
            out[m.group(1)] = m.group(2).strip()
    return out


def translate_file(
    in_path: Path,
    out_path: Path,
    cfg: dict,
    *,
    batch_size: int = 10,
    comment: str | None = None,
) -> int:
    """分批翻译,结果按编号追加写入 out_path;返回本次新翻句数。

    cfg 含 api_base/api_key/model,可选 proxy。
    comment 为视频描述,追加到系统提示词里引导翻译风格。
    整个任务在同一个多轮对话里完成:系统提示词只在首轮发送,
    之后每批作为对话延续,前缀命中服务商 prompt cache 可省 token。
    已有译文的编号自动跳过,可中断后重跑续翻。
    """
    items = read_lines(in_path)
    done = read_done(out_path)
    todo = [(i, t) for i, t in items if i not in done]
    system = SYSTEM_PROMPT
    if comment:
        system += f"\n\n视频背景描述:{comment}\n翻译时请结合该描述选择合适的语气与用词。"
    messages: list[dict] = [{"role": "system", "content": system}]
    new_text = 0
    with (
        out_path.open("a", encoding="utf-8") as f,
        tqdm(total=len(todo), unit="句", desc="翻译中") as bar,
    ):
        for off in range(0, len(todo), batch_size):
            batch = todo[off : off + batch_size]
            user = "\n".join(f"{i}\t{t}" for i, t in batch)
            messages.append({"role": "user", "content": user})
            try:
                reply = _chat(messages, cfg)
                got = _parse_reply(reply)
                missing = [i for i, _ in batch if i not in got]
                if missing:  # 缺号:在同一对话里补问一轮
                    messages.append({"role": "assistant", "content": reply})
                    fix_user = (
                        "以下编号没有返回译文,请只重新输出这些编号的译文,"
                        "每行格式为 编号<TAB>中文:\n" + "\n".join(i for i in missing)
                    )
                    messages.append({"role": "user", "content": fix_user})
                    reply2 = _chat(messages, cfg)
                    messages.append({"role": "assistant", "content": reply2})
                    got.update(_parse_reply(reply2))
                else:
                    messages.append({"role": "assistant", "content": reply})
            except Exception as e:  # 单批失败不中断整体,回退原文并继续
                print(f"\n警告:本批(第 {batch[0][0]}~{batch[-1][0]} 句)翻译失败:{e}")
                messages.pop()  # 移除未得到回复的 user 消息,保持对话历史一致
                got = {}
            for i, _src in batch:
                zh = got.get(i)
                if not zh:  # 缺号兜底:保留原句并提示
                    print(f"警告:第 {i} 句未返回译文,已保留原文")
                    zh = _src
                f.write(f"{i}\t{zh}\n")
            f.flush()
            new_text += len(batch)
            bar.update(len(batch))
    return new_text


def resolve_config(args) -> dict:
    """解析出完整配置 dict(api_base/api_key/model)。

    优先级:命令行参数 > 环境变量 > settings.py 里的变量。
    """
    cfg = {
        "api_base": args.api_base
        or os.environ.get("JPSUB_API_BASE")
        or os.environ.get("OPENAI_BASE_URL")
        or settings.API_BASE,
        "api_key": args.api_key
        or os.environ.get("JPSUB_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or settings.API_KEY,
        "model": args.model or os.environ.get("JPSUB_MODEL") or settings.MODEL or "",
    }
    if not cfg["api_base"]:
        raise SystemExit("缺少 API 端点:用 --api-base 或环境变量 JPSUB_API_BASE")
    if not cfg["api_key"]:
        raise SystemExit("缺少 API key:用 --api-key 或环境变量 JPSUB_API_KEY")
    if not cfg["model"]:
        raise SystemExit("缺少模型名:用 --model 或环境变量 JPSUB_MODEL")
    return cfg
