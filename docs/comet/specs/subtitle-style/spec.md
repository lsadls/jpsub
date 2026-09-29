# 烧录字幕样式（subtitle-style）

## Overview

`jpsub` 的字幕样式目前只能通过 `settings.py`（`FONT` / `FONT_SIZE` / `OUTLINE_COLOR` / `OUTLINE_WIDTH` / `SHADOW`）或命令行 `--font` / `--font-size` 调整。本能力在浏览器「译文调整器」里提供字幕样式控件（文字颜色、描边颜色、描边宽度、位置），样式按视频保存在工作目录 `.jpsub/style.json`，并由所有 ASS 生成与烧录入口共同读取。

样式只要落在生成的 ASS 样式行（`PrimaryColour` / `OutlineColour` / `Outline` / `Alignment`），由既有 ffmpeg `subtitles=` 烧录链路消费；bcc 输出把文字颜色同步到 `font_color`。

## Requirement: 样式控件与默认值

edit 页「字幕样式」组提供 4 个控件：文字颜色（`#RRGGBB` 取色器）、描边颜色（取色器）、描边宽度（0–4，0 表示无描边）、位置（底部/顶部居中）。没有工作目录样式文件时，控件初始值为内置默认：文字颜色 `#FFFFFF`、描边颜色取自 `settings.OUTLINE_COLOR`、描边宽度取自 `settings.OUTLINE_WIDTH`、位置「底部」。不含字号、字体、阴影、背景板与横向位置控件。

### Scenario: 无样式文件时的控件默认值

- WHEN 在无 `.jpsub/style.json` 的工作目录打开 edit 页
- THEN 「字幕样式」组显示文字颜色 `#FFFFFF`、描边颜色 `#FFA500`、描边宽度 `1`、位置「底部」
- AND 不显示字号、字体、阴影、背景板、横向位置控件

## Requirement: 样式保存与回填

样式改动即写入工作目录 `.jpsub/style.json`，字段为 `color`、`outline_color`、`outline_width`、`position`（取值 `bottom`｜`top`）。重新打开或刷新 edit 页时，4 个控件回填为文件中已保存的值。样式文件与 `crop.json`、`masks.json` 互不影响。

### Scenario: 改动落盘并回填

- WHEN 在 edit 页把文字颜色改为 `#FF0000` 并触发样式保存
- THEN `<工作目录>/.jpsub/style.json` 中 `color` 为 `#FF0000`
- AND 重新打开 edit 页时文字颜色控件显示 `#FF0000`

## Requirement: 样式生效范围与取值优先级

所有 ASS 生成与烧录入口都读取工作目录样式文件：edit 页「烧录进视频」「生成字幕」、主页「烧录」「生成字幕」、命令行 `render`、`run --burn`、`maskapply --burn`。取值优先级为「工作目录 `.jpsub/style.json` > `settings` 默认」；命令行的 `--font` / `--font-size` 仍只覆盖字体与字号。不为样式新增命令行参数。主页「下载+烧录」在新工作目录尚无样式文件，按默认样式烧录。

### Scenario: 命令行不带样式参数也使用工作目录样式

- WHEN 工作目录 `.jpsub/style.json` 的 `color` 为 `#FF0000`，执行 `jpsub render <工作目录>`
- THEN 生成的 `.ass` 中 `PrimaryColour` 对应红色
- AND 执行 `jpsub run <视频> --burn` 时烧录结果同样使用该红色

## Requirement: 烧录结果与所选样式一致

烧录产物在字幕出现时刻的像素应符合所选样式：文字颜色为所选文字颜色，描边宽度为 0 时没有描边色像素，位置为「顶部」时字幕位于画面上半部。

### Scenario: 红字无描边顶部

- WHEN 样式为文字颜色 `#FF0000`、描边宽度 `0`、位置「顶部」，执行 edit 页「烧录进视频」
- THEN 输出 `.burned.mp4` 在字幕时刻抽帧的字幕笔画像素以红色为主（R>180 且 G<100 且 B<100）
- AND 字幕位于画面上半部（y < 画面高度/2）

## Requirement: 默认样式不回归

没有工作目录样式文件时，`jpsub render` 产出的 `.ass` 样式行与改动前逐字段一致；bcc 的 `font_color` 为 `#FFFFFF`。

### Scenario: 无样式文件时的样式行

- WHEN 工作目录没有 `.jpsub/style.json`，执行 `jpsub render <工作目录>`
- THEN `.ass` 样式行含 `PrimaryColour=&H00FFFFFF`、`OutlineColour=&H0000A5FF`、`Outline=1`、`Shadow=0`、`Alignment=2`
- AND 「生成字幕」选 bcc 格式时输出 `.bcc` 的 `font_color` 为 `#FFFFFF`

## Requirement: bcc 文字颜色同步

bcc 输出的 `font_color` 等于当前文字颜色；bcc 的其余字段（`font_size`、`background_alpha`、`background_color`、`stroke`）保持现状。

### Scenario: bcc 同步文字颜色

- WHEN 文字颜色为 `#FF0000`，「生成字幕」选 bcc 格式
- THEN 输出 `.bcc` 的 `font_color` 为 `#FF0000`

## Requirement: 非法样式值容错

样式文件中的越界或非法值不得导致渲染/烧录失败：`outline_width` 钳制到 0–4 的整数，非法颜色（不符合 `#RRGGBB`）与非法 `position` 回退默认值，文件损坏或字段缺失时按默认样式处理。

### Scenario: 非法样式值仍可渲染

- WHEN `.jpsub/style.json` 写入 `{"color": "red", "outline_width": 99, "position": "left"}`
- THEN `jpsub render <工作目录>` 成功产出 `.ass`
- AND `Outline` 被钳制到 4（`Outline=4`）
- AND `PrimaryColour` 与 `Alignment` 回退默认（白字、`Alignment=2`）

## 不变量

- 无样式文件时，烧录与生成字幕输出与改动前一致（默认值不变）。
- `--font` / `--font-size` 的含义与默认值不变；不新增样式相关命令行参数。
- ASS 折行、`_DOT_RUN` 截短、bcc 其余字段与文件格式不变；`segments.json` 格式不变。
- 页面仍是自包含内联 HTML/JS，沿用现有深色主题，不引入外部资源或新依赖。
