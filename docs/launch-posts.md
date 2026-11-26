# local-sub-skill Distribution & Launch Kit

## 1. Ready-to-Submit Awesome-List PR Entries

### A. `BehiSecc/awesome-claude-skills` (10.4k★ — under `## 🎬 Media & Content`)
```markdown
- [local-sub-skill](https://github.com/Jettlin927/local-sub-skill) - 100% local video/WebVTT-to-Chinese & styled bilingual (`.zh.srt` / `.bilingual.srt` / `.bilingual.ass`) skill & CLI powered by Faster-Whisper (`large-v3-turbo`) + Ollama (`Hy-MT2-1.8B`), with custom glossary support, batch folder mode, drag-and-drop Web Studio, and `npx local-sub-skill install`.
```

### B. `EndoTheDev/Awesome-Ollama` (503★ — under `## Terminal`)
```markdown
| [local-sub-skill](https://github.com/Jettlin927/local-sub-skill) | 100% local video/WebVTT/SRT to Chinese, bilingual SRT & styled ASS subtitle CLI, batch runner & Web Studio powered by Hy-MT2-1.8B + Faster-Whisper (with custom glossary & VRAM-safe unload) | npm | MIT |
```

### C. `ancs21/awesome-openai-whisper` (110★ — under `## Applications`)
```markdown
* [local-sub-skill - 100% local Faster-Whisper + Ollama Hy-MT2-1.8B video-to-Chinese & styled bilingual subtitle (`.zh.srt` / `.bilingual.ass`) CLI, Web Studio, and Claude/Codex skill](https://github.com/Jettlin927/local-sub-skill)
```

---

## 2. Viral Community Posts (Zero Cold Outreach)

### A. V2EX (`/go/share` or `/go/create`) & Linux.do
**Title:** 开源了一个 100% 本地离线的视频/字幕翻译 Skill 与拖拽工作台：Faster-Whisper + 腾讯 Hy-MT2-1.8B（6–8G 显存可跑，支持双语字幕与整季批量处理）

**Body:**
之前把 2 小时的英文/日语公开课和纪录片丢给云端 API 翻字幕，又贵又慢还得传隐私文件。于是把本地跑通的生产流抽成了一个零外部翻译依赖的开源工具 + AI Agent Skill（MIT 协议）：**`local-sub-skill`**

一行命令装进 Claude Code / Codex / Antigravity 或直接当 CLI 用：
```bash
npx local-sub-skill install
npx local-sub-skill setup
npx local-sub-skill gui   # 启动本地 127.0.0.1 拖拽工作台
```

**几个解决真实痛点的细节：**
1. **8GB 显存不爆**：先自动卸载 Ollama 翻译模型再调 Whisper ASR，不会两个模型抢显存 OOM；macOS/Linux 自动回退 Python `faster-whisper`。
2. **字幕时间轴零错位**：纯 Python 标准库解析 SRT，遇到 1.8B 模型漏翻行号自动二分拆批 + 单条回退，带假名残留率检测和断点续传缓存。
3. **双语 + 批量文件夹 + YouTube `.vtt` 原生支持**：支持一键生成 `.zh.srt` + `.bilingual.srt`（中上外下 / 外上中下），原生清洗 `yt-dlp` 下载的 WebVTT (`.vtt`) 标签，支持 `npx local-sub-skill batch` 整季视频或字幕文件夹递归处理。
4. **图形界面**：内置零依赖本地网页多文件拖拽工作台（`npx local-sub-skill gui`）与 Windows `DropVideoOrSrtHere.bat` 拖入即翻。

GitHub: https://github.com/Jettlin927/local-sub-skill  
npm: https://www.npmjs.com/package/local-sub-skill

---

### B. r/LocalLLaMA
**Title:** Built a 100% local Faster-Whisper + Ollama (Hy-MT2-1.8B Q4_K_M) subtitle translator & drag-and-drop studio that fits in 6–8 GB VRAM

**Body:**
Translating 2-hour Japanese/English lectures or films with cloud LLM APIs adds up fast and leaks local files. I extracted my local pipeline into an MIT open-source CLI, drag-and-drop local web studio, and Claude Code / Codex skill: **`local-sub-skill`**.

- **Stack**: `Faster-Whisper large-v3-turbo` (with automatic Python `faster-whisper` fallback on macOS/Linux) + `Tencent Hy-MT2-1.8B Q4_K_M` via Ollama (~1.1 GB weights).
- **VRAM hygiene**: Automatically sends `keep_alive: 0` to Ollama before starting Whisper so 8 GB GPUs don't OOM.
- **SRT reliability**: Zero-dependency Python translation core with exact timestamp preservation, binary-split + single-cue fallback when a 1.8B model drops an ID, digest-keyed cache, `.bilingual.srt` output, and `batch` folder mode.
- **Zero-terminal option**: `npx local-sub-skill gui` launches a local `127.0.0.1` drag-and-drop studio for `.srt` files and video folders.

Try it: `npx local-sub-skill doctor` / `npx local-sub-skill gui`  
Repo: https://github.com/Jettlin927/local-sub-skill

---

### C. 小红书 (Xiaohongshu — Lead Magnet + ¥19 懒人拖拽包引流)
**标题：** 8G显存白嫖本地AI字幕！生肉视频一键出双语字幕，不花一分钱API费🔥

**正文：**
看生肉公开课、日剧、油管视频每次调云端翻译都要花钱还限速？
我把自己常用的本地离线方案打包开源了：**Faster-Whisper + 腾讯 Hy-MT2-1.8B（仅1.1G）**，断网也能把 `.mp4/.mkv/.srt` 变成完美对齐的 `.zh.srt` 和 `.bilingual.srt` 双语字幕！

✅ 支持日语/英语 → 中文 + 中英/中日双语字幕（PotPlayer / IINA 直接自动加载）
✅ 支持整季文件夹批量跑，断电也能断点续传
✅ Windows / Mac / Linux 都能用，自带浏览器拖拽工作台

💻 程序员/极客直接开源免费用：`npx local-sub-skill gui`（GitHub搜 `Jettlin927/local-sub-skill`）
🎁 不想折腾环境的同学，我也整理了 **Windows 双击即用拖放桌面整合包（拖视频到图标上直接出双语字幕）**，需要的评论区扣【字幕】～
