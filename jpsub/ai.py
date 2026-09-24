"""调用 OpenAI 兼容 API,翻译句子列表(原文 -> 中文译文)。

提示词为 SKILL.md 的简化版;分批请求,译文以 dict 形式返回,由调用方
写回 segments.json。"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request

from . import settings

# SKILL.md 简化版系统提示词
SYSTEM_PROMPT = """你是日语字幕翻译,把用户提供的日文字幕逐句翻译为简体中文。

背景:视频是日本例区文化(如淫梦文化)的二次创作(如 BB 剧场),注意例区用语、
网络梗和人物绰号的既定译法,同一专有名词全篇译法一致。人名和专有名词周围加空格,输出里连续的省略号不能超过两个, 对错位/缺失的符号自动修正/补全

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


# 内容审查拦截的特征(英文/中文),命中则本批整批失败,单句问题会连累同批其他句
_CENSOR_PATTERNS = (
    "data_inspection_failed",
    "inappropriate content",
    "content_policy",
    "content_filter",
    "content policy",
    "内容审核",
    "内容不合规",
)


class CensoredError(RuntimeError):
    """API 以内容审查为由拒绝了本次请求。

    批量请求时无法定位是哪一句触发,故由上层停止本轮并改用 batch_size=1 重跑。
    """


def _is_censored_error(err: Exception) -> bool:
    text = str(err).lower()
    return any(p.lower() in text for p in _CENSOR_PATTERNS)


def _chat(
    messages: list[dict],
    cfg: dict,
    *,
    temperature: float = 0.3,
    retries: int = 3,
    quiet: bool = False,
    timeout: int = 8,
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
            with opener.open(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
            try:
                content = data["choices"][0]["message"]["content"]
                return content
            except (KeyError, IndexError, TypeError):
                raise ValueError(f"API 响应格式异常:{data}") from None
        except urllib.error.HTTPError as e:  # 读取响应体里的具体错误信息
            detail = e.read().decode("utf-8", "replace")[:500]
            e = RuntimeError(f"HTTP {e.code}: {detail or e.reason}")
            if _is_quota_error(e):  # 额度/余额耗尽,重试无意义
                raise RuntimeError(f"API 额度不足,请充值或更换模型:{e}") from None
            if _is_censored_error(e):  # 内容审查,重试同一批无意义
                raise CensoredError(str(e)) from None
            err = e
            if attempt < retries - 1:
                if not quiet:
                    print(
                        f"  请求失败:{e}\n  {2 * (attempt + 1)}s 后重试...", flush=True
                    )
                time.sleep(2 * (attempt + 1))
        except Exception as e:  # 网络抖动/限流/响应异常,退避重试
            if _is_quota_error(e):  # 额度/余额耗尽,重试无意义
                raise RuntimeError(f"API 额度不足,请充值或更换模型:{e}") from None
            if _is_censored_error(e):
                raise CensoredError(str(e)) from None
            err = e
            if attempt < retries - 1:
                if not quiet:
                    print(
                        f"  请求失败:{e}\n  {2 * (attempt + 1)}s 后重试...", flush=True
                    )
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


def translate_texts(
    items: list[tuple[str, str]],
    cfg: dict,
    *,
    batch_size: int = 10,
    comment: str | None = None,
    glossary: dict[str, str] | None = None,
    progress=None,
    quiet: bool = False,
    on_batch=None,
    timeout: int = 8,
) -> dict[str, str]:
    """分批翻译句子列表,返回 {键: 译文};未返回译文的键记 UNTRANSLATED_MARK。

    `items` 是 [(键, 原文), ...],键原样带回(通常为时间轴键或原文本身)。
    cfg 含 api_base/api_key/model。comment 为视频描述,追加到系统提示词里
    引导翻译风格。整个任务在同一个多轮对话里完成:系统提示词只在首轮发送,
    之后每批作为对话延续,前缀命中服务商 prompt cache 可省 token。
    批量失败/内容审查的降级语义由调用方处理(捕获 CensoredError 后改
    batch_size=1 重跑未完成部分)。
    """
    from .handoff import UNTRANSLATED_MARK

    system = SYSTEM_PROMPT
    if comment:
        system += (
            f"\n\n视频背景描述:{comment}\n翻译时请结合该描述选择合适的语气与用词。"
        )
    if glossary:
        # 名词对照表随系统提示词一次性发送(每轮对话只发一次,前缀命中 prompt cache)
        system += (
            "\n\n名词对照表(必须严格遵守,原文出现以下词时译成对应中文):\n"
            + "\n".join(f"{k} → {v}" for k, v in glossary.items())
        )
    messages: list[dict] = [{"role": "system", "content": system}]
    results: dict[str, str] = {}
    # 长句自动收缩批大小:平均超 100 字每批 1 句,超 50 字每批 2 句,防止长句博客体单批过长漏行
    if items:
        avg_len = sum(len(t) for _, t in items) / len(items)
        orig_bs = batch_size
        if avg_len > 100:
            batch_size = 1
        elif avg_len > 50:
            batch_size = 2
        if batch_size < orig_bs and not quiet:
            print(f"句子平均 {avg_len:.0f} 字,批大小 {orig_bs} -> {batch_size}")
    t0 = time.monotonic()  # 性能指标起点(整体耗时/剩余/每句用时)
    done = 0
    for off in range(0, len(items), batch_size):
        batch = items[off : off + batch_size]
        nb = off // batch_size + 1
        total_batches = (len(items) + batch_size - 1) // batch_size
        nchars = sum(len(t) for _, t in batch)
        if not quiet:
            print(
                f"\r[第 {nb}/{total_batches} 批] 发送 {len(batch)} 句({nchars} 字)",
                end="",
                flush=True,
            )
        messages.append({"role": "user", "content": "\n".join(t for _, t in batch)})
        try:
            reply = _chat(messages, cfg, quiet=quiet, timeout=timeout)
            got = _parse_reply_lines(reply, len(batch))
            from .handoff import is_junk_tr

            # 空行或模型把拒绝语当译文,都算缺行,先在同一对话里补问重试
            missing = [j for j, zh in enumerate(got) if not zh or is_junk_tr(zh)]
            if missing:  # 缺行:在同一对话里补问一轮(只发缺的原文,按行序)
                messages.append({"role": "assistant", "content": reply})
                fix_src = [batch[j][1] for j in missing]
                fix_user = (
                    "以下句子没有返回译文,请逐行输出它们的中文译文,"
                    "行序与给出顺序一致,不要编号:\n" + "\n".join(fix_src)
                )
                messages.append({"role": "user", "content": fix_user})
                reply2 = _chat(messages, cfg, quiet=quiet, timeout=timeout)
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
            if isinstance(e, CensoredError) and batch_size > 1:
                # 单句敏感连累整批:停止本轮,由上层改用 batch_size=1 重跑
                raise CensoredError(
                    f"本批({batch[0][0]}~{batch[-1][0]})触发内容审查:{e}"
                ) from None
            # 其他单批失败不中断整体,回退原文并继续
            if not quiet:
                print(
                    f"\n警告:本批({batch[0][0]}~{batch[-1][0]})翻译失败:{e}",
                    flush=True,
                )
            messages.pop()  # 移除未得到回复的 user 消息,保持对话历史一致
            got = [""] * len(batch)
        except Exception as e:  # 单批失败不中断整体,回退原文并继续
            if not quiet:
                print(
                    f"\n警告:本批({batch[0][0]}~{batch[-1][0]})翻译失败:{e}",
                    flush=True,
                )
            messages.pop()  # 移除未得到回复的 user 消息,保持对话历史一致
            got = [""] * len(batch)
        for (i, _src), zh in zip(batch, got):
            from .handoff import is_junk_tr

            if not zh or is_junk_tr(zh):  # 空行或模型把拒绝语当译文:记为未译占位
                if not quiet:
                    print(f"警告:{i} 未返回有效译文,已标记 {UNTRANSLATED_MARK}")
                zh = UNTRANSLATED_MARK
            results[i] = zh
        if on_batch:  # 每批回传部分结果,调用方可在中断时保留已完成部分
            on_batch({i: zh for (i, _), zh in zip(batch, got)})
        if not quiet:  # 性能指标与批进度同行,原地刷新不刷屏
            done += len(batch)
            el = time.monotonic() - t0
            per = el / done if done else 0.0
            rem = per * (len(items) - done)
            print(
                f"\r[第 {nb}/{total_batches} 批] 发送 {len(batch)} 句({nchars} 字) "
                f"[{int(el // 60):02d}:{el % 60:04.1f}"
                f"<{int(rem // 60):02d}:{rem % 60:04.1f}, {per:.2f}s/句]  ",
                end="",
                flush=True,
            )
        if progress:
            progress(min(off + len(batch), len(items)), len(items))
    if not quiet:
        print()  # 结束后换行,让后续输出另起一行
    return results


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
