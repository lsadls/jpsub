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
from .progress import Reporter

def system_prompt(src: str | None = None, tgt: str | None = None, prompt_file: str | None = None) -> str:
    """生成系统提示词:优先读 prompt_file(settings.PROMPT_FILE / --prompt 参数),
    文件里可用 {src}/{tgt}/{punct} 占位符;否则用内置默认。"""
    src = settings.lang_name(src or settings.SOURCE_LANG)
    tgt = tgt or settings.TARGET_LANG
    zh_out = "中" in tgt  # 中文目标语才强制全角标点
    punct = (
        "- 标点一律用中文全角:?!,省略号用「……」,不要出现半角 ? ! 或「......」\n"
        if zh_out
        else ""
    )
    path = prompt_file or settings.PROMPT_FILE
    if path:
        from pathlib import Path
        p = Path(path)
        if not p.is_absolute():
            # 相对路径优先按当前工作目录找,找不到则回退到项目根目录
            root = Path(__file__).resolve().parent.parent
            p = root / p
        tpl = p.read_text(encoding="utf-8")
        return tpl.replace("{src}", src).replace("{tgt}", tgt).replace("{punct}", punct)
    return f"""你是{src}字幕翻译,把用户提供的{src}字幕逐句翻译为{tgt}。

背景:视频是日本例区文化(如淫梦文化)的二次创作(如 BB 剧场),注意例区用语、
网络梗和人物绰号的既定译法,同一专有名词全篇译法一致。人名和专有名词周围加空格,输出里连续的省略号不能超过两个, 对错位/缺失的符号自动修正/补全

规则:
- 忠实原意,语气自然简洁,不添油加醋、不省略信息;
- 保留「」、感叹号、破折号等语气标记;
- 惯用语/俗语按{tgt}习惯意译,不逐字直译;
- 注意隐含的说话对象与语气功能(安慰、吐槽、反问等);
- 只输出译文,不要{src}原文、不要注音、不要解释
- 不要思考人物关系或故事情节等, 只思考翻译。
{punct}
输出格式:输入每行开头是一个数字编号,输出与输入逐行对应,每行以相同编号开头
(数字后接译文),编号与输入一致、不许跳过任何一行(看不懂的乱码句也要给编号并尽量音译),
不写原文、不解释,行序与输入一致。"""

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


def _norm_punct(s: str) -> str:
    """译文标点规范化:省略号统一「……」,中文/全角字符后的半角标点转全角。"""
    s = re.sub(r"\.{3,}", "……", s)
    s = re.sub(
        r"(?<=[\u3000-\u9fff\uff00-\uffef「」『』—…·])([,!?;:~])",
        lambda m: {",": "，", "!": "！", "?": "？", ";": "；", ":": "：", "~": "～"}[m.group()],
        s,
    )
    return s


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


# 每次请求的 usage 累计(prompt_tokens/completion_tokens/...),供外部统计 token 消耗
_USAGE: list[dict] = []


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
    pip/模型下载,见 .env)。
    messages 为完整对话历史;系统提示词只放在第一条,后续批次在同一
    对话上下文里继续,配合服务商 prompt cache 省去重复前缀的开销。
    """
    url = cfg["api_base"].rstrip("/") + "/chat/completions"
    body: dict = {
        "model": cfg["model"],
        "temperature": temperature,
        "messages": messages,
    }
    if settings.REASONING_EFFORT:  # 关闭推理模型思考,省输出 token
        body["reasoning_effort"] = settings.REASONING_EFFORT
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
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
                u = data.get("usage") or {}
                _USAGE.append(u)  # 累计每次请求用量,供外部统计
                if not quiet:  # 输出每次请求的 token 用量,便于排查异常消耗
                    print(
                        f"  tokens: prompt={u.get('prompt_tokens')} "
                        f"completion={u.get('completion_tokens')} {u.get('completion_tokens_details') or ''}",
                        flush=True,
                    )
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


def _tag_note(tags: str | None) -> str:
    """tag 不再整串发给 AI:本地按 tag 判断内容类型,只发一句短注。"""
    if not tags:
        return ""
    if "拓也" in tags:
        return "\n\n这是「AI拓也」类 AI 续写故事视频。"
    return ""


def _glossary_sub(text: str, glossary: dict[str, str] | None) -> str:
    """把原文里的对照表词条预先替换成译文词,套上 ⟦⟧ 标记让 AI 明确知道
    这是预替换术语(长词优先,避免短词吃掉长词);输出时 AI 会去掉标记。"""
    if not glossary:
        return text
    for k in sorted(glossary, key=len, reverse=True):
        if k in text:
            text = text.replace(k, f"⟦{glossary[k]}⟧")
    return text


def _trim_history(messages: list[dict]) -> None:
    """裁掉翻译对话里的旧轮次,只留 system + 最近 HISTORY_KEEP 轮。

    多轮对话每批都重发全部历史,批数多了 prompt token 线性膨胀;
    翻译连贯性只需最近几轮上下文。keep=0 视为不裁剪。
    """
    keep = settings.HISTORY_KEEP
    if keep <= 0 or len(messages) <= 1 + 2 * keep:
        return
    del messages[1 : len(messages) - 2 * keep]


def _parse_reply_lines(reply: str, count: int) -> list[str] | None:
    """把模型回复解析为与输入 batch 等长的译文列表。

    只认「编号<分隔>译文」格式(编号恰好覆盖 1..count 才可信)。
    无编号回复即使行数恰好相等也不可信:模型可能漏译本轮句子并复读
    上一轮内容,行数守恒但内容错位,按位置回填会整批污染缓存
    ——一律返回 None,调用方整批重问或记未译,禁止猜测式回填。"""
    reply = re.sub(r"```[a-zA-Z]*\n?|```", "", reply)
    lines = [ln.strip().strip("*`") for ln in reply.splitlines()]
    lines = [ln for ln in lines if ln]
    num_re = re.compile(r"^\s*(\d+)\s*(?:\t|[.、。:：)）\]]|\s)\s*(.+?)\s*$")
    numbered = {}
    for ln in lines:
        m = num_re.match(ln)
        if m:
            numbered[int(m.group(1))] = m.group(2)
    if numbered:
        if set(numbered) == set(range(1, count + 1)):
            return [numbered[i] for i in range(1, count + 1)]
    return None  # 编号缺失/跳号/合并:整批不可信,禁止按位置回填


def calc_concurrency(items: list[tuple[str, str]]) -> int:
    """按基准实测标定动态并发:总字数 3792→G16,5230→G24,线性插值,钳制 8~32。

    依据:并发过低时组内对话历史滚雪球,prompt 暴涨;过高时 system 前缀
    ×G 与缺行补问轮增多,成本回升。实测谷底随体量(总字数)在 16~24 之间。
    """
    if not items:
        return 1
    s = sum(len(t) for _, t in items)
    g = round(16 + (s - 3792) * 8 / (5230 - 3792))
    return max(8, min(32, g))


def translate_texts_parallel(
    items: list[tuple[str, str]],
    cfg: dict,
    *,
    concurrency: int = 8,
    comment: str | None = None,
    glossary: dict[str, str] | None = None,
    quiet: bool = False,
    on_batch=None,
    progress=None,
    timeout: int = 8,
    src_lang: str | None = None,
    to_lang: str | None = None,
    prompt_file: str | None = None,
    tags: str | None = None,
    group_batch: int = 20,
    refuse_fix: bool = True,
    fail_log: str | None = None,
) -> dict[str, str]:
    """分组并发翻译:按时间轴顺序切成 concurrency 个连续块,每组一个独立
    多轮对话(共享组内上下文+system 前缀命中 prompt cache),组内小批多行
    请求(每批 group_batch 句,省前缀重复 token);批失败回退逐句重试,
    单句失败只丢一句,记 UNTRANSLATED_MARK,不连累其他句。
    refuse_fix=False:缺行/拒译句不补问(补问轮要整段重发历史),直接记
    UNTRANSLATED_MARK 留给续翻,长文模式用它省 token。
    fail_log:传入文件路径时,本轮所有失败句(拒译/乱码/缺行)追加写入,
    一句一行,供用户提取关键词加进 glossary。
    额度耗尽类错误终止整个任务。"""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from .handoff import UNTRANSLATED_MARK, is_junk_tr

    GROUP_BATCH = group_batch  # 组内每次请求带的句子数

    rep = Reporter(quiet=quiet)
    tgt = to_lang or settings.TARGET_LANG
    zh_out = "中" in tgt
    system = system_prompt(src_lang, to_lang, prompt_file)
    if comment:
        system += (
            f"\n\n视频背景描述:{comment}\n翻译时请结合该描述选择合适的语气与用词。"
        )
    if tags:
        system += _tag_note(tags)
    if glossary:
        system += (
            f"\n\n原文里的 ⟦⟧ 标记是预替换术语(可能为{tgt}词或英文/数字),"
            "输出时去掉括号、括号内文字原样保留,禁止回译或改写。"
        )

    results: dict[str, str] = {}
    fails: list[str] = []  # 本轮失败的原句(拒译/乱码/缺行),结束时写 fail_log
    u0 = len(_USAGE)  # 本轮起始下标,结束时汇总本轮 token 消耗
    lock = threading.Lock()
    state = {"quota": None, "done": 0}
    total = len(items)
    t0 = time.monotonic()
    n_groups = max(1, min(concurrency, total)) if total else 0
    # 连续切块:相邻句落进同组对话,语境连贯;组大小≈total/n_groups
    size = (total + n_groups - 1) // n_groups
    groups = [items[i : i + size] for i in range(0, total, size)]

    def _send(msgs: list[dict], srcs: list[str]) -> list[str]:
        """发一个多行请求,返回等长译文列表(缺行/拒译记 UNTRANSLATED_MARK)。"""
        msgs.append(
            {
                "role": "user",
                "content": "\n".join(
                    f"{j + 1} {_glossary_sub(s, glossary)}" for j, s in enumerate(srcs)
                ),
            }
        )
        reply = _chat(msgs, cfg, quiet=True, timeout=timeout)
        got = _parse_reply_lines(reply, len(srcs))
        if got is None:
            # 行数/编号与请求不符(模型合并句子或漏行):按位置回填必错位。
            # 整批重问一轮(带编号);再失败就整批记未译,绝不猜测回填污染缓存。
            msgs.append({"role": "assistant", "content": reply})
            msgs.append(
                {
                    "role": "user",
                    "content": f"你的返回行数与要求不符。请严格每行以编号 1~{len(srcs)} 开头,"
                    f"一行一句重新输出全部 {len(srcs)} 句的{tgt}译文,禁止合并或省略:\n"
                    + "\n".join(f"{j + 1} {srcs[j]}" for j in range(len(srcs))),
                }
            )
            reply2 = _chat(msgs, cfg, quiet=True, timeout=timeout)
            got = _parse_reply_lines(reply2, len(srcs))
            if got is None:
                msgs.pop()  # 移除未得到有效回复的 user 消息,历史保持干净
                return [UNTRANSLATED_MARK] * len(srcs)
        if any(not z or is_junk_tr(z) for z in got):
            if not refuse_fix:
                # 降级策略:不补问(补问轮整段重发历史),缺行句直接记未译留给续翻
                msgs.append({"role": "assistant", "content": reply})
                return [
                    z if z and not is_junk_tr(z) else UNTRANSLATED_MARK for z in got
                ]
            # 缺行:在同一对话里补问一轮(带原编号,按编号精确回填)
            msgs.append({"role": "assistant", "content": reply})
            missing = [j for j, z in enumerate(got) if not z or is_junk_tr(z)]
            msgs.append(
                {
                    "role": "user",
                    "content": f"以下编号的句子没有返回译文,请每行以相同编号开头输出它们的{tgt}译文:\n"
                    + "\n".join(f"{j + 1} {srcs[j]}" for j in missing),
                }
            )
            reply2 = _chat(msgs, cfg, quiet=True, timeout=timeout)
            got2 = _parse_reply_lines(reply2, len(missing))
            if got2:  # 编号仍不对齐(返回 None)时放弃补问,缺行句保持未译
                for j, z in zip(missing, got2):
                    if z:
                        got[j] = z
            _trim_history(msgs)
        else:
            msgs.append({"role": "assistant", "content": reply})
            _trim_history(msgs)
        return [z if z and not is_junk_tr(z) else UNTRANSLATED_MARK for z in got]

    def _run_group(group: list[tuple[str, str]]) -> None:
        msgs: list[dict] = [{"role": "system", "content": system}]
        censored = 0  # 本组审查命中次数;连续多次说明审查查整个 payload(历史连带拦截)

        def _reset() -> None:
            nonlocal censored
            msgs[:] = [{"role": "system", "content": system}]
            censored = 0

        def _finish(chunk: list[tuple[str, str]], got: list[str]) -> None:
            for (k, s), zh in zip(chunk, got):
                if zh == UNTRANSLATED_MARK:  # 失败句记录,结束时写 fail_log
                    fails.append(s)
                zh = zh.replace("⟦", "").replace("⟧", "")  # AI 忘剥术语标记时兜底
                zh = _norm_punct(zh) if zh_out else zh
                with lock:
                    results[k] = zh
                    state["done"] += 1
                    if on_batch:
                        on_batch({k: zh})
                    if progress:
                        progress(min(state["done"], total), total)
            if not quiet:  # 进度与性能指标同行刷新
                done = state["done"]
                el = time.monotonic() - t0
                per = el / done if done else 0.0
                rem = per * (total - done)
                rep.line(
                    f"[并发 {len(groups)} 对话] {done}/{total} 句 "
                    f"[{int(el // 60):02d}:{el % 60:04.1f}"
                    f"<{int(rem // 60):02d}:{rem % 60:04.1f}, {per:.2f}s/句]"
                )

        def _flush(chunk: list[tuple[str, str]]) -> None:
            """发一批;批失败二分降级(拒译请求不计费,越分越便宜),单句仍失败才标记未译。"""
            if state["quota"]:  # 额度已耗尽,余句直接放弃
                return
            try:
                got = _send(msgs, [s for _, s in chunk])
            except RuntimeError as e:
                if "额度不足" in str(e):
                    with lock:
                        state["quota"] = e
                    return
                msgs.pop()  # 移除未得到回复的 user 消息,历史保持干净
                if len(chunk) > 1:
                    mid = len(chunk) // 2
                    _flush(chunk[:mid])
                    _flush(chunk[mid:])
                    return
                if isinstance(e, CensoredError) and settings.CENSOR_RESET:
                    censored += 1
                    if censored >= 2:
                        _reset()
                if not quiet:
                    print(f"\n警告:{chunk[0][1][:20]}… 翻译失败:{e}", flush=True)
                got = [UNTRANSLATED_MARK]
            except Exception:
                msgs.pop()
                got = [UNTRANSLATED_MARK] * len(chunk)
            _finish(chunk, got)

        # 组内敏感词命中的"毒句"先单发(不连累整批),干净句子成批发送
        gt = glossary or {}
        risky = [(k, s) for k, s in group if any(t in s for t in gt)]
        clean = [(k, s) for k, s in group if not any(t in s for t in gt)]
        for kv in risky:
            _flush([kv])
        for off in range(0, len(clean), GROUP_BATCH):
            _flush(clean[off : off + GROUP_BATCH])

    with ThreadPoolExecutor(max_workers=n_groups) as ex:
        for g in groups:
            ex.submit(_run_group, g)
    rep.close()
    if not quiet:  # token 消耗与价格估算(¥0.02/百万命中,¥1.00/百万未命中,¥2.00/百万输出)
        us = [u for u in _USAGE[u0:] if u]
        pt = sum(u.get("prompt_tokens", 0) for u in us)
        hit = sum((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) for u in us)
        ct = sum(u.get("completion_tokens", 0) for u in us)
        cost = (hit * 0.02 + (pt - hit) * 1.0 + ct * 2.0) / 1e6
        print(f"token 消耗: 命中{hit} 未命中{pt - hit} 输出{ct} ≈¥{cost:.4f}")
    if fails and fail_log:  # 失败句落盘,供用户提取关键词加进 glossary
        try:
            with open(fail_log, "a", encoding="utf-8") as f:
                for s in dict.fromkeys(fails):
                    f.write(s + "\n")
        except OSError:
            pass
    if state["quota"]:
        raise state["quota"]
    return results


def resolve_config(args) -> dict:
    """解析出完整配置 dict(api_base/api_key/model)。

    优先级:命令行参数 > 环境变量 > .env 里的变量。
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
