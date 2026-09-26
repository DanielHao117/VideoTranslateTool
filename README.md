# VideoTranslateTool

YouTube 视频下载 + 双语字幕 + 在线播放的本地工具，可打包成单个 exe，双击即用。

> 仓库地址：https://github.com/DanielHao117/VideoTranslateTool

## 功能

- 解析 YouTube 链接，选择清晰度下载（自动合成 H.264 + AAC 的 mp4，本地播放音画同步）
- 在线播放：先自动缓冲完整视频到临时目录再播放，无卡顿；同一链接复用缓存，退出自动清理
- 双语字幕浮层（英文 + 中文），支持 仅英文 / 仅中文 / 关闭 切换，全屏播放字幕同样显示
- 中文字幕优先使用视频自带的 YouTube 官方中文轨；没有时使用本地 OPUS-MT 离线模型翻译
- 暂停 / 继续下载，断点续传

## 环境要求

- **Windows 10 / 11**
- **Python 3.9 或更高**（开发环境为 Python 3.14）
- **无需单独安装 ffmpeg**：视频合成所需的 ffmpeg 已通过 `imageio-ffmpeg` 依赖内置
- 访问 YouTube 需要网络可正常连通（如有需要请自备代理）

## 快速开始（从源码运行）

推荐直接双击 `run.bat`：它会自动创建虚拟环境、安装依赖并启动程序。

```bat
git clone https://github.com/DanielHao117/VideoTranslateTool.git
cd VideoTranslateTool
run.bat
```

也可以手动执行：

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python app.py
```

浏览器会自动打开 `http://127.0.0.1:5000`。

> 首次使用若没有下载离线翻译模型，中文字幕仅在该视频自带 YouTube 官方中文轨时显示；否则只显示英文原文。下载模型的方法见下文。

## 翻译引擎（完全离线、免费）

按以下顺序工作，无需联网、无需任何账号或密钥：

1. YouTube 官方中文字幕轨（视频自带时直接使用）
2. **OPUS-MT en→zh 离线模型**（`Helsinki-NLP/opus-mt-en-zh` 经 CTranslate2 int8 转换，约 80 MB）

两者都拿不到译文时，中文字幕留空，界面只显示英文原文。

### 获取离线模型

模型**不随仓库分发**（体积大）。打包 exe **无需手动下载**——`build.bat` 会在打包前自动下载到 `models/` 并内嵌进 exe，产出的单文件 exe 开箱即可离线翻译。

如需手动下载，在项目根目录执行：

```bat
python fetch_model.py
```

脚本默认从 `hf-mirror.com` 下载并支持断点续传，可用环境变量 `HF_ENDPOINT` 指定其他镜像。

许可：OPUS-MT 为 **Apache-2.0**，可自由商用，无附加限制。

## 打包单文件 exe

```bat
build.bat
```

或手动执行 `python -m PyInstaller --noconfirm yt_downloader.spec`，产物在 `dist\VideoTranslateTool.exe`。

模型会一并打包进 exe，因此 `dist\VideoTranslateTool.exe` 是**真正单文件**：拷到任何机器双击即可，无需再带 `models\` 文件夹。

首次启动时，模型会一次性解压到 `%LOCALAPPDATA%\VideoTranslateTool\models\`，之后每次启动直接复用，不再重复解压。若在 exe 同目录放了自己的 `models\` 文件夹，则优先使用它（方便替换模型）；模型完全缺失时，中文字幕留空并只显示英文。

配置读取顺序：exe 同目录 `config.json` -> 打包内嵌 -> 当前工作目录；空值不覆盖。

## 配置（可留空）

程序**无需任何配置即可运行**。如需自定义，参考 `config.example.json`，复制为 `config.json` 后按需填写：

| 字段 | 说明 |
| --- | --- |
| `background_image` | 界面背景图路径 |

> `config.json` 已被 `.gitignore` 排除，属于本地个人配置，不会提交到仓库。

## 目录结构

```
app.py               Flask 后端（下载 / 字幕 / 翻译 / 播放接口）
static/              前端 JS 与样式
templates/           页面模板
fetch_model.py       下载离线翻译模型
yt_downloader.spec   PyInstaller 打包配置
config.example.json  配置模板
run.bat              一键运行（自动建虚拟环境并启动）
build.bat            一键打包 exe
models/              离线翻译模型（fetch_model.py 获取，不纳入版本库；打包时会内嵌进 exe）
  opus-mt-en-zh-ct2/                OPUS-MT 主模型，Apache-2.0
```

## 常见问题

**Q：需要额外安装 ffmpeg 吗？**
不需要，依赖里的 `imageio-ffmpeg` 已自带可用的 ffmpeg。

**Q：中文字幕不显示怎么办？**
先确认视频是否自带 YouTube 官方中文轨；若没有，请运行 `python fetch_model.py` 下载离线模型后重试。

**Q：端口 5000 被占用启动失败？**
关闭占用该端口的程序后重试。

**Q：打包后的 exe 需要带 `models\` 文件夹吗？**
不需要，模型已内嵌进 exe。

## 免责声明

仅供个人学习研究使用，请遵守 YouTube 服务条款及内容版权规定，勿用于商业用途。

## 许可证

本仓库暂未附加开源许可证，在添加之前默认保留所有权利。内置的 OPUS-MT 翻译模型为 Apache-2.0 许可。
