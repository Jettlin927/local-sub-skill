# local-sub-desktop · Windows 零命令行拖拽版使用指南

> 100% 本地离线字幕翻译与视频配字幕工具（Faster-Whisper + Ollama Hy-MT2-1.8B）
> 无需 API Key · 不上传任何视频或字幕 · 自动生成 `.zh.srt` 与 `.bilingual.srt` 双语字幕

## 30 秒上手（完全无需敲命令）

1. **安装运行环境（仅首次）**
   - 安装 [Ollama](https://ollama.com/) 与 Python 3.10+
   - 双击运行命令行（或执行 `npx local-sub-skill setup`）拉取 `hy-mt2-local` 本地翻译模型（约 1.1 GB）
2. **方式 A：直接把文件拖到图标上**
   - 把任意 `.mp4` / `.mkv` 视频、`.srt` / `.vtt`（YouTube `yt-dlp` WebVTT）字幕文件、或整季视频/字幕文件夹，直接拖到 **`DropVideoOrSrtHere.bat`** 图标上松手即可。
   - 完成后会在原视频同目录生成 `片名.zh.srt`、`片名.bilingual.srt` 与主副样式分明的特效双语字幕 `片名.bilingual.ass`（PotPlayer / VLC / IINA 自动加载）；同目录放置 `glossary.txt`（每行 `原文=译法`）即可自动应用术语表。
3. **方式 B：双击打开可视化拖拽工作台**
   - 双击 **`LaunchGUI.bat`**，浏览器会自动打开本地工作台（`http://127.0.0.1:8765`），支持浏览器直接拖入多个 `.srt` / `.vtt` 字幕队列一键翻译并下载双语字幕，也支持粘贴本地视频/文件夹路径批量处理。

---

*Built by [@Jettlin927](https://github.com/Jettlin927/local-sub-skill) · Also check [AgentCostLab](https://jettlin927.github.io/agentcostlab/)*
