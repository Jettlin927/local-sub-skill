#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Zero-dependency local Drag-and-Drop Web Studio for local-sub-skill."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import urllib.error
import webbrowser

from doctor import collect_diagnostics
from local_translation import (
    BILINGUAL_ORDERS,
    default_ass_path,
    default_bilingual_path,
    parse_glossary_text,
    strip_subtitle_lang_suffix,
    translate_srt,
)
from run_batch import build_batch_parser, run_batch
from run_single_video import build_parser as build_video_parser, run_video_pipeline


GUI_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>local-sub-skill Studio · 100% 本地字幕工作台</title>
<style>
  :root { --bg:#0b1020; --card:#131b31; --border:#243154; --text:#e8eefc; --muted:#9fb0d9; --accent:#4f8cff; --ok:#22c55e; --warn:#f59e0b; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; background:var(--bg); color:var(--text); line-height:1.5; }
  .wrap { max-width: 920px; margin: 32px auto; padding: 0 20px; }
  header { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; margin-bottom:24px; }
  h1 { font-size: 22px; margin:0; }
  .badge { padding: 4px 12px; border-radius: 999px; font-size: 12px; background: #1c2747; border: 1px solid var(--border); color: var(--muted); }
  .badge.ok { color: #86efac; border-color: #166534; background: #052e16; }
  .badge.warn { color: #fde68a; border-color: #854d0e; background: #422006; }
  .grid { display:grid; grid-template-columns: 1fr 1fr; gap:16px; }
  @media (max-width:760px){ .grid{ grid-template-columns:1fr; } }
  .card { background:var(--card); border:1px solid var(--border); border-radius:14px; padding:18px; }
  .dropzone { border: 2px dashed #3b528d; border-radius: 12px; padding: 30px 16px; text-align:center; cursor:pointer; transition: .15s; background:#0e1529; }
  .dropzone.drag { border-color: var(--accent); background:#162344; }
  label { display:block; font-size:12px; color:var(--muted); margin: 10px 0 4px; }
  input[type=text], select, textarea { width:100%; padding:9px 11px; border-radius:8px; border:1px solid var(--border); background:#0c1224; color:var(--text); font-family: inherit; }
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:10px; }
  button { background:var(--accent); color:#fff; border:none; border-radius:8px; padding:9px 16px; font-weight:600; cursor:pointer; }
  button.secondary { background:#1e2948; color:var(--text); border:1px solid var(--border); }
  button:disabled { opacity:.5; cursor:not-allowed; }
  pre { background:#090e1c; border:1px solid var(--border); border-radius:10px; padding:12px; max-height:260px; overflow:auto; font-size:12px; white-space:pre-wrap; }
  .muted { color:var(--muted); font-size:13px; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>local-sub-skill Studio</h1>
      <div class="muted">100% 本地离线字幕翻译 & 批量视频工作台 · Faster-Whisper + Ollama Hy-MT2-1.8B</div>
    </div>
    <div id="health" class="badge">正在检测本地环境...</div>
  </header>

  <div class="grid">
    <section class="card">
      <h2 style="margin-top:0;font-size:16px">1. 拖拽翻译 .srt / .vtt 字幕（支持多文件）</h2>
      <div id="dropzone" class="dropzone">
        将单个或多个 <b>.srt / .vtt</b> 文件拖到这里，或点击选择<br>
        <span class="muted">兼容 YouTube yt-dlp WebVTT (.vtt)，自动输出 .zh.srt 与 .bilingual.srt</span>
        <input id="fileInput" type="file" accept=".srt,.vtt,text/plain" multiple hidden>
      </div>
      <div class="row">
        <div style="flex:1"><label>源语言</label><select id="srtSource"><option value="ja">日语 (ja)</option><option value="en">英语 (en)</option><option value="auto">自动 (auto)</option></select></div>
        <div style="flex:1"><label>双语顺序</label><select id="srtOrder"><option value="zh-first">中文在上</option><option value="source-first">原文在上</option></select></div>
      </div>
      <div class="row">
        <label class="row"><input id="srtBilingual" type="checkbox" checked> .bilingual.srt</label>
        <label class="row"><input id="srtAss" type="checkbox" checked> .bilingual.ass 特效双语</label>
      </div>
      <label>术语表（可选，每行 <code>原文=译法</code> 或 JSON）</label>
      <textarea id="srtGlossary" rows="2" placeholder="Agent=智能体&#10;RAG=检索增强生成"></textarea>
      <div class="row" id="downloadRow" style="display:none">
        <button id="dlZh">下载 .zh.srt</button>
        <button id="dlBi" class="secondary">下载 .bilingual.srt</button>
        <button id="dlAss" class="secondary">下载 .bilingual.ass</button>
      </div>
      <div id="queueList" style="margin-top:10px;display:flex;flex-direction:column;gap:6px"></div>
    </section>

    <section class="card">
      <h2 style="margin-top:0;font-size:16px">2. 本地视频 / 文件夹批量处理</h2>
      <label>本地视频文件路径或文件夹路径</label>
      <input id="localPath" type="text" placeholder="例如 D:\\Videos\\lecture.mp4 或 D:\\Videos\\Season1">
      <div class="row">
        <div style="flex:1"><label>语言</label><select id="vidLang"><option value="ja">日语 (ja)</option><option value="en">英语 (en)</option><option value="auto">自动 (auto)</option></select></div>
        <div style="flex:1"><label>双语顺序</label><select id="vidOrder"><option value="zh-first">中文在上</option><option value="source-first">原文在上</option></select></div>
      </div>
      <div class="row">
        <label><input id="vidBilingual" type="checkbox" checked> .bilingual.srt</label>
        <label><input id="vidAss" type="checkbox" checked> .bilingual.ass</label>
        <label><input id="vidRecursive" type="checkbox"> 递归子目录</label>
        <label><input id="vidDryRun" type="checkbox" checked> 仅预览 (Dry Run)</label>
      </div>
      <div class="row">
        <button id="runPathBtn">开始处理本地路径</button>
      </div>
    </section>
  </div>

  <section class="card" style="margin-top:16px">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <h2 style="margin:0;font-size:15px">运行日志 / 预览</h2>
      <span id="status" class="muted">就绪</span>
    </div>
    <pre id="output">等待操作...</pre>
  </section>
  <footer class="muted" style="margin-top:16px;text-align:center">
    MIT Open Source by <a href="https://github.com/Jettlin927/local-sub-skill" target="_blank" style="color:#8ab4ff">Jettlin927/local-sub-skill</a> · 估算云端 vs 本地 Agent 成本：<a href="https://jettlin927.github.io/agentcostlab/" target="_blank" style="color:#8ab4ff">AgentCostLab</a>
  </footer>
</div>
<script>
const out = document.getElementById('output');
const statusEl = document.getElementById('status');
const healthEl = document.getElementById('health');
let lastResult = null;

async function refreshDoctor() {
  try {
    const r = await fetch('/api/doctor');
    const d = await r.json();
    healthEl.textContent = d.srt_ready ? 'Ollama + hy-mt2-local 就绪' : '需要启动 Ollama / 模型';
    healthEl.className = 'badge ' + (d.srt_ready ? 'ok' : 'warn');
    if (d.next_actions && d.next_actions.length) {
      out.textContent = '环境检查提示:\\n- ' + d.next_actions.join('\\n- ');
    }
  } catch (e) { healthEl.textContent = '环境检测失败'; }
}
refreshDoctor();

function downloadText(filename, text) {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

async function handleSrtFiles(fileList) {
  const files = Array.from(fileList || []).filter(Boolean);
  if (!files.length) return;
  const queueEl = document.getElementById('queueList');
  queueEl.innerHTML = '';
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    statusEl.textContent = '正在翻译 (' + (i + 1) + '/' + files.length + ') ' + file.name + ' ...';
    const srt_text = await file.text();
    const res = await fetch('/api/translate-srt', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        filename: file.name,
        srt_text,
        source: document.getElementById('srtSource').value,
        bilingual: document.getElementById('srtBilingual').checked,
        ass: document.getElementById('srtAss').checked,
        bilingual_order: document.getElementById('srtOrder').value,
        glossary_text: document.getElementById('srtGlossary').value
      })
    });
    const data = await res.json();
    if (!res.ok) {
      statusEl.textContent = '部分失败';
      out.textContent = file.name + ': ' + (data.error || JSON.stringify(data, null, 2));
      continue;
    }
    lastResult = data;
    statusEl.textContent = '完成 (' + (i + 1) + '/' + files.length + ' · ' + data.stats.cue_count + ' 条字幕)';
    out.textContent = (data.bilingual_srt || data.zh_srt).slice(0, 4000);
    document.getElementById('downloadRow').style.display = 'flex';
    document.getElementById('dlBi').style.display = data.bilingual_srt ? 'inline-block' : 'none';
    document.getElementById('dlAss').style.display = data.bilingual_ass ? 'inline-block' : 'none';
    const row = document.createElement('div');
    row.className = 'row';
    const label = document.createElement('span');
    label.className = 'muted';
    label.textContent = data.filename_zh + ' (' + data.stats.cue_count + ' 条)';
    const btnZh = document.createElement('button');
    btnZh.textContent = '下载 .zh.srt';
    btnZh.onclick = () => downloadText(data.filename_zh, data.zh_srt);
    row.append(label, btnZh);
    if (data.bilingual_srt) {
      const btnBi = document.createElement('button');
      btnBi.className = 'secondary';
      btnBi.textContent = '下载 .bilingual.srt';
      btnBi.onclick = () => downloadText(data.filename_bilingual, data.bilingual_srt);
      row.append(btnBi);
    }
    if (data.bilingual_ass) {
      const btnAss = document.createElement('button');
      btnAss.className = 'secondary';
      btnAss.textContent = '下载 .bilingual.ass';
      btnAss.onclick = () => downloadText(data.filename_ass, data.bilingual_ass);
      row.append(btnAss);
    }
    queueEl.append(row);
  }
}

const dz = document.getElementById('dropzone');
const fi = document.getElementById('fileInput');
dz.onclick = () => fi.click();
fi.onchange = () => handleSrtFiles(fi.files);
dz.ondragover = (e) => { e.preventDefault(); dz.classList.add('drag'); };
dz.ondragleave = () => dz.classList.remove('drag');
dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove('drag'); handleSrtFiles(e.dataTransfer.files); };

document.getElementById('dlZh').onclick = () => lastResult && downloadText(lastResult.filename_zh, lastResult.zh_srt);
document.getElementById('dlBi').onclick = () => lastResult && lastResult.bilingual_srt && downloadText(lastResult.filename_bilingual, lastResult.bilingual_srt);
document.getElementById('dlAss').onclick = () => lastResult && lastResult.bilingual_ass && downloadText(lastResult.filename_ass, lastResult.bilingual_ass);

document.getElementById('runPathBtn').onclick = async () => {
  const path = document.getElementById('localPath').value.trim();
  if (!path) return;
  statusEl.textContent = '执行中...';
  const res = await fetch('/api/run-path', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      path,
      language: document.getElementById('vidLang').value,
      bilingual: document.getElementById('vidBilingual').checked,
      ass: document.getElementById('vidAss').checked,
      bilingual_order: document.getElementById('vidOrder').value,
      recursive: document.getElementById('vidRecursive').checked,
      continue_on_error: true,
      dry_run: document.getElementById('vidDryRun').checked
    })
  });
  const data = await res.json();
  statusEl.textContent = res.ok ? '完成' : '失败';
  out.textContent = JSON.stringify(data, null, 2);
};
</script>
</body>
</html>
"""


def _safe_stem(filename: str) -> tuple[str, str]:
    raw = Path(filename or "subtitle.srt")
    ext = ".vtt" if raw.suffix.lower() == ".vtt" else ".srt"
    stem = strip_subtitle_lang_suffix(raw.stem)
    cleaned = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in stem).strip()
    return (cleaned or "subtitle"), ext


def handle_translate_srt_payload(payload: dict) -> tuple[int, dict]:
    srt_text = payload.get("srt_text")
    if not isinstance(srt_text, str) or not srt_text.strip():
        return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "srt_text must be a non-empty string"}
    order = payload.get("bilingual_order", "zh-first")
    if order not in BILINGUAL_ORDERS:
        return HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"bilingual_order must be one of {BILINGUAL_ORDERS}"}

    base, src_ext = _safe_stem(str(payload.get("filename", "subtitle.srt")))
    if srt_text.lstrip().startswith("WEBVTT"):
        src_ext = ".vtt"
    bilingual = bool(payload.get("bilingual", True))
    ass = bool(payload.get("ass", False))
    raw_glossary = payload.get("glossary_text", "")
    try:
        glossary = parse_glossary_text(raw_glossary) if isinstance(raw_glossary, str) and raw_glossary.strip() else {}
    except (ValueError, json.JSONDecodeError) as error:
        return HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"Invalid glossary: {error}"}
    with tempfile.TemporaryDirectory(prefix="local-sub-gui-") as tmp_dir:
        tmp = Path(tmp_dir)
        src_path = tmp / f"{base}.source{src_ext}"
        zh_path = tmp / f"{base}.zh.srt"
        bi_path = default_bilingual_path(zh_path) if bilingual else None
        ass_path = default_ass_path(zh_path) if ass else None
        src_path.write_text(srt_text, encoding="utf-8")
        try:
            stats = translate_srt(
                src_path,
                zh_path,
                model=str(payload.get("model", "hy-mt2-local")),
                source=str(payload.get("source", "ja")),
                target=str(payload.get("target", "zh")),
                host=str(payload.get("host", os.environ.get("OLLAMA_HOST", "http://localhost:11434"))),
                cache_path=tmp / "cache.json",
                checkpoint_path=tmp / "checkpoint.json",
                bilingual_path=bi_path,
                bilingual_order=order,
                ass_path=ass_path,
                glossary=glossary,
            )
        except urllib.error.URLError as error:
            return HTTPStatus.BAD_GATEWAY, {"ok": False, "error": f"Ollama unreachable: {error}"}
        except (ValueError, RuntimeError) as error:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(error)}

        return HTTPStatus.OK, {
            "ok": True,
            "filename_zh": zh_path.name,
            "zh_srt": zh_path.read_text(encoding="utf-8"),
            "filename_bilingual": bi_path.name if bi_path else None,
            "bilingual_srt": bi_path.read_text(encoding="utf-8") if bi_path and bi_path.is_file() else None,
            "filename_ass": ass_path.name if ass_path else None,
            "bilingual_ass": ass_path.read_text(encoding="utf-8") if ass_path and ass_path.is_file() else None,
            "stats": stats,
        }


def handle_run_path_payload(payload: dict) -> tuple[int, dict]:
    raw_path = payload.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "path is required"}
    target = Path(raw_path).expanduser().resolve()
    if not target.exists():
        return HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Path not found: {target}"}

    language = str(payload.get("language", "ja"))
    bilingual = bool(payload.get("bilingual", True))
    ass = bool(payload.get("ass", False))
    order = str(payload.get("bilingual_order", "zh-first"))
    dry_run = bool(payload.get("dry_run", False))

    cli_flags = ["--language", language, "--bilingual-order", order]
    if bilingual:
        cli_flags.append("--bilingual")
    if ass:
        cli_flags.append("--ass")
    if dry_run:
        cli_flags.append("--dry-run")

    if target.is_dir():
        if payload.get("recursive"):
            cli_flags.append("--recursive")
        if payload.get("continue_on_error", True):
            cli_flags.append("--continue-on-error")
        args = build_batch_parser().parse_args([str(target), *cli_flags])
        summary = run_batch(args)
        return HTTPStatus.OK, summary

    args = build_video_parser().parse_args([str(target), *cli_flags])
    result = run_video_pipeline(args.video, args)
    return HTTPStatus.OK, result


def make_handler(doctor_fn=collect_diagnostics):
    class StudioHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            return

        def _send_json(self, status: int, body: dict) -> None:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _read_json(self) -> dict | None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except ValueError:
                return None
            return parsed if isinstance(parsed, dict) else None

        def do_GET(self) -> None:
            if self.path == "/" or self.path.startswith("/?"):
                encoded = GUI_HTML.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return
            if self.path == "/api/doctor":
                self._send_json(HTTPStatus.OK, doctor_fn())
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

        def do_POST(self) -> None:
            payload = self._read_json()
            if payload is None:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid JSON body"})
                return
            if self.path == "/api/translate-srt":
                status, data = handle_translate_srt_payload(payload)
                self._send_json(status, data)
                return
            if self.path == "/api/run-path":
                try:
                    status, data = handle_run_path_payload(payload)
                except Exception as error:
                    status, data = HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(error)}
                self._send_json(status, data)
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

    return StudioHandler


def create_server(host: str = "127.0.0.1", port: int = 8765, doctor_fn=collect_diagnostics) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(doctor_fn=doctor_fn))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch the local-sub-skill Drag-and-Drop Web Studio.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open a web browser.")
    args = parser.parse_args(argv)

    server = create_server(host=args.host, port=args.port)
    actual_port = server.server_address[1]
    url = f"http://{args.host}:{actual_port}/"
    print(f"local-sub-skill Studio running at {url} (Ctrl+C to stop)", flush=True)
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
