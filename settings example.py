COOKIES_FROM_BROWSER = ""  # 登录/地区限制时填浏览器名,如 "chrome"(见 七.9)
# https://platform.qianwenai.com
# 注册的免费额度够几百个视频了
API_BASE = ""  # 例如 "https://maas.qianwenaiapi.com/compatible-mode/v1"
API_KEY = ""  # 例如 "sk-xxx"
MODEL = ""  # 例如 "qwen3.7-flash"
# 翻译速度很大程度上受各家模型影响,deepseek每秒token在200-300,且会听话的按提示词不多想,其他家的模型明显慢一些,有些还会自作聪明的试图理清人物关系和剧情导致更慢
# qwen容易对输入应激(特别是AI拓也), 使用mimo, 基本不会应激


PROXY = ""  # 例如 "http://127.0.0.1:1080",留空不走代理

# niconico 下载质量
# VIDEO:"lowest"=最低画质,"best"=最高画质,"360p"/"480p"/"720p"=指定分辨率
NICO_VIDEO_QUALITY = "360p"
# AUDIO:"lowest"=最低码率,"best"=最高码率, 一般只有64k/192k 两种
NICO_AUDIO_QUALITY = "lowest"

# OCR 模型(PaddleOCR PP-OCRv5 系列)
# 可选 _mobile_ 或 _medium_;实测 medium 在普通剧场字幕检测和识别上提升很小,但速度慢得多. 如果默认模型不理想可尝试
OCR_DET_MODEL = "PP-OCRv5_mobile_det"
OCR_REC_MODEL = "PP-OCRv5_mobile_rec"

# ---------- 字幕生成参数 ----------

# 抽帧:每秒抽几帧;CROP:截取视频底部高度的比例(字幕区域)
FPS = 2.0
CROP = "0.78:0.02:0.01:0.01"

# 关键帧筛选:掩膜差异阈值(越小越灵敏,漏段少但误判多);静止多少帧算停顿(越小越容易收尾);连续变化超过多少帧强制识别
DIFF_THRESHOLD = 2.0
SETTLE_FRAMES = 1
MAX_RUN = 10

# 段落合并:文本相似度阈值
SIMILARITY = 0.85

# 翻译:每次请求翻译的句数
BATCH_SIZE = 10

# ASS 字幕样式(位置交给播放器默认处理)
FONT = "Noto Sans CJK SC"  # 字体
FONT_SIZE = 20  # 字号
MAX_CHARS = 20  # 每行最大字数(超出折行)
OUTLINE_COLOR = (255, 165, 0)  # 描边颜色(RGB,当前为橙色)
OUTLINE_WIDTH = 1  # 描边宽度
SHADOW = 0  # 阴影宽度
