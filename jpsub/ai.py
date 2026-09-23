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
网络梗和人物绰号的既定译法,同一专有名词全篇译法一致。人名和专有名词周围加空格

规则:
- 忠实原意,语气自然简洁,不添油加醋、不省略信息;
- 保留「」、感叹号、破折号等语气标记;
- 惯用语/俗语按中文习惯意译,不逐字直译;
- 注意隐含的说话对象与语气功能(安慰、吐槽、反问等);
- 只输出译文,不要日文原文、不要注音、不要解释
- 不要思考人物关系或故事情节等, 只思考翻译。

输出格式:与输入逐行对应,每行输出一句简体中文译文,不写编号、
不写原文、不解释,必须覆盖输入的所有行,行序与输入一致。"""

# 额度/余额耗尽类错误的特征(中英文),命中则不重试、直接终止并提醒用户
_QUOTA_PATTERNS = (
    "insufficient",
    "quota",
    "balance",
    "arrears",
    "exceeded",
    "余额",
    "额度",
    "欠费",
    "充值",
)


def _is_quota_error(err: Exception) -> bool:
    text = str(err).lower()
    return any(p.lower() in text for p in _QUOTA_PATTERNS)


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
        except urllib.error.HTTPError as e:  # 读取响应体里的具体错误信息
            detail = e.read().decode("utf-8", "replace")[:500]
            e = RuntimeError(f"HTTP {e.code}: {detail or e.reason}")
            if _is_quota_error(e):  # 额度/余额耗尽,重试无意义
                raise RuntimeError(f"API 额度不足,请充值或更换模型:{e}") from None
            err = e
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
        except Exception as e:  # 网络抖动/限流/响应异常,退避重试
            if _is_quota_error(e):  # 额度/余额耗尽,重试无意义
                raise RuntimeError(f"API 额度不足,请充值或更换模型:{e}") from None
            err = e
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"API 调用失败(已重试 {retries} 次):{err}")


def _parse_reply_lines(reply: str, count: int) -> list[str]:
    """把模型回复解析为与输入 batch 等长的译文列表(按行序对应)。

    优先识别「编号<TAB>译文」格式(模型有时自作主张加编号,剥掉即可);
    否则按行序直接对应。返回长度恒为 count,缺行补空串。
    """
    reply = re.sub(r"```[a-zA-Z]*\n?|```", "", reply)
    lines = [ln.strip().strip("*`") for ln in reply.splitlines()]
    lines = [ln for ln in lines if ln]
    num_re = re.compile(r"^\s*(\d+)\s*(?:\t|[.、。:：)）\]])\s*(.+?)\s*$")
    numbered = {}
    for ln in lines:
        m = num_re.match(ln)
        if m:
            numbered[int(m.group(1))] = m.group(2)
    if len(numbered) >= count and all(1 <= k <= count for k in numbered):
        return [numbered.get(i, "") for i in range(1, count + 1)]
    out = []
    for ln in lines:  # 行序:剥掉可能存在的编号前缀
        m = num_re.match(ln)
        out.append(m.group(2) if m else ln)
    if len(out) < count:
        out += [""] * (count - len(out))
    return out[:count]


def read_lines(path: Path) -> list[tuple[str, str]]:
    """读取「键<TAB>文本」清单(键为时间轴),返回 [(键, 文本), ...]。

    无 TAB 的行按行序编号 1..N(用户手动删了时间轴也能处理)。
    """
    items: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if "\t" in line:
            key, _, text = line.partition("\t")
            items.append((key.strip(), text.strip()))
        else:
            items.append((str(len(items) + 1), line.strip()))
    return items


def read_done(path: Path) -> set[str]:
    """读取已有译文的键集合(用于续翻);译文为空的行不算。"""
    if not path.exists():
        return set()
    done: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if "\t" in line:
            key, _, text = line.partition("\t")
            if text.strip():
                done.add(key.strip())
    return done


def translate_file(
    in_path: Path,
    out_path: Path,
    cfg: dict,
    *,
    batch_size: int = 10,
    comment: str | None = None,
) -> int:
    """分批翻译,结果按时间轴键追加写入 out_path;返回本次新翻句数。

    cfg 含 api_base/api_key/model,可选 proxy。
    comment 为视频描述,追加到系统提示词里引导翻译风格。
    整个任务在同一个多轮对话里完成:系统提示词只在首轮发送,
    之后每批作为对话延续,前缀命中服务商 prompt cache 可省 token。
    发给 AI 的只有段落原文(不带键),回复按行序对应;
    写回 out 时带上时间轴键。已有译文的键自动跳过,可中断后重跑续翻。
    """
    items = read_lines(in_path)
    done = read_done(out_path)
    todo = [(i, t) for i, t in items if i not in done]
    system = SYSTEM_PROMPT
    if comment:
        system += (
            f"\n\n视频背景描述:{comment}\n翻译时请结合该描述选择合适的语气与用词。"
        )
    messages: list[dict] = [{"role": "system", "content": system}]
    new_text = 0
    with (
        out_path.open("a", encoding="utf-8") as f,
        tqdm(total=len(todo), unit="句", desc="翻译中") as bar,
    ):
        for off in range(0, len(todo), batch_size):
            batch = todo[off : off + batch_size]
            messages.append({"role": "user", "content": "\n".join(t for _, t in batch)})
            try:
                reply = _chat(messages, cfg)
                got = _parse_reply_lines(reply, len(batch))
                missing = [j for j, zh in enumerate(got) if not zh]
                if missing:  # 缺行:在同一对话里补问一轮(只发缺的原文,按行序)
                    messages.append({"role": "assistant", "content": reply})
                    fix_src = [batch[j][1] for j in missing]
                    fix_user = (
                        "以下句子没有返回译文,请逐行输出它们的中文译文,"
                        "行序与给出顺序一致,不要编号:\n" + "\n".join(fix_src)
                    )
                    messages.append({"role": "user", "content": fix_user})
                    reply2 = _chat(messages, cfg)
                    messages.append({"role": "assistant", "content": reply2})
                    got2 = _parse_reply_lines(reply2, len(missing))
                    for j, zh in zip(missing, got2):
                        if zh:
                            got[j] = zh
                else:
                    messages.append({"role": "assistant", "content": reply})
            except RuntimeError as e:
                if "额度不足" in str(e):  # 剩余 token 耗尽:终止并提醒
                    raise
                # 其他单批失败不中断整体,回退原文并继续
                print(f"\n警告:本批({batch[0][0]}~{batch[-1][0]})翻译失败:{e}")
                messages.pop()  # 移除未得到回复的 user 消息,保持对话历史一致
                got = [""] * len(batch)
            except Exception as e:  # 单批失败不中断整体,回退原文并继续
                print(f"\n警告:本批({batch[0][0]}~{batch[-1][0]})翻译失败:{e}")
                messages.pop()  # 移除未得到回复的 user 消息,保持对话历史一致
                got = [""] * len(batch)
            for (i, _src), zh in zip(batch, got):
                if not zh:  # 缺行兜底:保留原句并提示
                    print(f"警告:{i} 未返回译文,已保留原文")
                    zh = _src
                f.write(f"{i}\t{zh}\n")  # 写回时带上时间轴键
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
