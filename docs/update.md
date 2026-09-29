# 更新方式

更新时**只需把新版 `jpsub` 文件夹覆盖旧的即可**：

- `.venv` —— Python 库，不用重装；
- `settings.py` —— 你的配置，迁移到新版的 `settings.py.example` 并重命名为 `settings.py`；
- `bin` —— ffmpeg / yt-dlp，保留。

如果新版说明里提到**新增了依赖库**，按说明更新。

使用打包版解压后需要更新版本时，用仓库里**最新版的 `jpsub` 文件夹和 `settings.py.example`** 覆盖/更新打包版里的对应文件（`settings.py.example` 复制改名 `settings.py` 填入你的配置）。
