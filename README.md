# VideoTranslateTool

YouTube 视频下载 + 双语字幕 + 在线播放的本地工具，可打包成单个 exe，双击即用。

## 功能

- 解析 YouTube 链接，选择清晰度下载（自动合成 H.264 + AAC 的 mp4，本地播放音画同步）
- 在线播放：先自动缓冲完整视频到临时目录再播放，无卡顿；同一链接复用缓存，退出自动清理
- 双语字幕浮层（英文 + 中文），支持 仅英文 / 仅中文 / 关闭 切换，全屏播放字幕同样显示
- 中文字幕优先使用视频自带的 YouTube 官方中文轨；没有时使用本地 OPUS-MT 离线模型翻译
- 暂停 / 继续下载，断点续传

## 翻译引擎（完全离线、免费）

按以下顺序工作，无需联网、无需任何账号或密钥：

1. YouTube 官方中文字幕轨（视频自带时直接使用）
2. **OPUS-MT en→zh 离线模型**（`Helsinki-NLP/opus-mt-en-zh` 经 CTranslate2 int8 转换，约 80 MB）

两者都拿不到译文时，中文字幕留空，界面只显示英文原文。

### 获取离线模型

模型**不随仓库分发**（体积大），打包前先放在项目根目录的 `models/` 文件夹：

```bat
python fetch_model.py
```

脚本默认从 `hf-mirror.com` 下载并支持断点续传，可用环境变量 `HF_ENDPOINT` 指定其他镜像。

许可：OPUS-MT 为 **Apache-2.0**，可自由商用，无附加限制。

## 从源码运行

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python app.py
```

浏览器会自动打开 `http://127.0.0.1:5000`。

## 打包单文件 exe

```bat
build.bat
```

或手动执行 `python -m PyInstaller --noconfirm yt_downloader.spec`，产物在 `dist\VideoTranslateTool.exe`。

模型会一并打包进 exe，因此 `dist\VideoTranslateTool.exe` 是**真正单文件**：拷到任何机器双击即可，无需再带 `models\` 文件夹。

首次启动时，模型会一次性解压到 `%LOCALAPPDATA%\VideoTranslateTool\models\`，之后每次启动直接复用，不再重复解压。若在 exe 同目录放了自己的 `models\` 文件夹，则优先使用它（方便替换模型）；模型完全缺失时，中文字幕留空并只显示英文。

配置读取顺序：exe 同目录 `config.json` -> 打包内嵌 -> 当前工作目录；空值不覆盖。

## 配置（可留空）

| 字段 | 说明 |
| --- | --- |
| `background_image` | 界面背景图路径 |

参考 `config.example.json`，复制为 `config.json` 后按需填写。

## 目录结构

```
app.py               Flask 后端（下载 / 字幕 / 翻译 / 播放接口）
static/              前端 JS 与样式
templates/           页面模板
fetch_model.py       下载离线翻译模型
yt_downloader.spec   PyInstaller 打包配置
config.example.json  配置模板
models/              离线翻译模型（fetch_model.py 获取，不纳入版本库；打包时会内嵌进 exe）
  opus-mt-en-zh-ct2/                OPUS-MT 主模型，Apache-2.0
```

## 免责声明

仅供个人学习研究使用，请遵守 YouTube 服务条款及内容版权规定，勿用于商业用途。
