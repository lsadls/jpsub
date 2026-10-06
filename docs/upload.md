# 投稿(upload)

把工作目录成品投稿到 B 站:打码视频(若有)优先于原视频,标题默认 info.txt 原标题(弹窗可改),封面 `cover.jpg` 自动上传。

## 一次性登录

```bash
biliup login          # biliup-rs 扫码,产物 cookies.json 在当前目录
```

把 `cookies.json` 放到 `~/.jpsub/`(与 settings.BILIUP_COOKIE 一致即可)。也可用命令:

```bash
jpsub upload <工作目录> --login   # 自动调 biliup-rs 并保存 cookies
```

cookie 过期时投稿会报「登录失败」,删除 cookies.json 重新扫码即可。

## 配置(.env)

| 变量 | 说明 |
| --- | --- |
| `BILIUP_COOKIE` | cookies.json 路径,默认 `~/.jpsub/cookies.json` |
| `UPLOAD_TID` | 分区 tid,默认 22(鬼畜调教);26 音MAD / 126 人力VOCALOID / 216 鬼畜剧场 |
| `UPLOAD_PREFIX` | 标题前缀(如 `[搬运]`),主页「保存前缀」会更新运行时值 |
| `UPLOAD_TAGS` | 兜底标签(info.txt 无标签行时用),逗号分隔 |
| `UPLOAD_DELAY` | 定时发布延迟秒数,0=立即 |

## 命令行

```bash
jpsub upload <工作目录>                        # 标题默认原标题+前缀,标签取 info
jpsub upload <工作目录> --title "..." --desc "..." --tags "a,b" --tid 22 --delay 3600
```

- 视频成品定位:工作目录内 `*.masked.*`(打码产物)> 原视频
- 标题:默认 info.txt 原标题(不翻译),可在网页弹窗里改;标题前缀存 `UPLOAD_PREFIX`(网页「保存前缀」更新运行时值),投稿时自动拼在标题前
- 简介:原标题/原作者/投稿时间/原视频 URL + 多行原简介(网盘行下载时已剔除)
- 标签:直接用 info.txt 标签行;无标签行时回退 `UPLOAD_TAGS`
- 分区:主页下拉选(中文),默认 `UPLOAD_TID`(鬼畜调教)
- 转载声明(copyright=2)自动填 info.txt 首行的原视频 URL
- 成功后 BV 号写入工作目录 `upload.json`(`{bvid, aid, time}`);**重复投稿先查此文件拦截**,重投需手动删除

## 主页

选中条目 →「投稿」按钮:弹窗预填标题(默认 info 原标题)/简介/标签/分区(中文下拉)/定时发布(秒),可编辑后「确认投稿」,标题前缀文本框可保存长期有效;进度显示在任务列表。批量脚本同样支持:

```
upload <条目名>
upload <条目名> --delay 3600
```

## 风险提示

- 标题/简介含敏感词可能过审失败:简介沿用网盘行剔除 + glossary 替换,标题走 glossary 预替换,必要时投稿前在弹窗里手动改
- `cookies.json` 含登录态,不要提交进仓库
