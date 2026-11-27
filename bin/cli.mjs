#!/usr/bin/env node
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PKG_ROOT = path.resolve(__dirname, '..');
const SCRIPTS_DIR = path.join(PKG_ROOT, 'scripts');
const PKG_JSON = JSON.parse(fs.readFileSync(path.join(PKG_ROOT, 'package.json'), 'utf8'));

function detectPython() {
  const candidates = process.platform === 'win32' ? ['python', 'py', 'python3'] : ['python3', 'python'];
  for (const cmd of candidates) {
    const res = spawnSync(cmd, ['--version'], { stdio: 'ignore' });
    if (res.status === 0) return cmd;
  }
  return 'python';
}

function copyDirRecursive(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    if (entry.name === '__pycache__' || entry.name.endsWith('.pyc')) continue;
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      copyDirRecursive(srcPath, destPath);
    } else {
      fs.copyFileSync(srcPath, destPath);
    }
  }
}

export function getInstallTargets(agent, homeDir = os.homedir()) {
  const map = {
    claude: path.join(homeDir, '.claude', 'skills', 'local-sub-skill'),
    codex: path.join(homeDir, '.codex', 'skills', 'local-sub-skill'),
    antigravity: path.join(homeDir, '.gemini', 'antigravity', 'skills', 'local-sub-skill'),
    cursor: path.join(homeDir, '.cursor', 'skills', 'local-sub-skill'),
    openclaw: path.join(homeDir, '.openclaw', 'skills', 'local-sub-skill'),
  };
  if (agent === 'all') return Object.entries(map);
  if (map[agent]) return [[agent, map[agent]]];
  throw new Error(
    `Unsupported --agent "${agent}". Choose: claude, codex, antigravity, cursor, openclaw, or all.`,
  );
}

export function installSkill({ agent = 'all', homeDir = os.homedir() } = {}) {
  const targets = getInstallTargets(agent, homeDir);
  const installed = [];
  for (const [name, destDir] of targets) {
    fs.mkdirSync(destDir, { recursive: true });
    fs.copyFileSync(path.join(PKG_ROOT, 'SKILL.md'), path.join(destDir, 'SKILL.md'));
    copyDirRecursive(SCRIPTS_DIR, path.join(destDir, 'scripts'));
    installed.push({ agent: name, path: destDir });
  }
  return installed;
}

function runPythonScript(scriptName, forwardArgs) {
  const py = detectPython();
  const scriptPath = path.join(SCRIPTS_DIR, scriptName);
  const res = spawnSync(py, [scriptPath, ...forwardArgs], {
    stdio: 'inherit',
    env: { ...process.env, PYTHONPATH: SCRIPTS_DIR },
  });
  return res.status ?? 1;
}

function printHelp() {
  console.log(`local-sub-skill v${PKG_JSON.version} — 100% Local Video, Audio & URL → Chinese & Bilingual Subtitle CLI & AI Agent Skill

Usage:
  npx local-sub-skill install [--agent claude|codex|antigravity|cursor|openclaw|all]
      Install SKILL.md + scripts into your AI coding agent skills folder.

  npx local-sub-skill setup [--asr-model-dir <dir>] [--skip-whisper]
      Pull Ollama Hy-MT2-1.8B Q4_K_M and Faster-Whisper large-v3-turbo weights.

  npx local-sub-skill translate <input.srt|vtt|ass|ssa> [output.srt] [--source ja|en|ko|fr|de|es|ru|pt|auto] [--target zh|zh-Hant|en|ja|ko|fr|de|es|ru|pt] [--model <ollama-model>] [--shift <sec>] [--bilingual] [--ass] [--notes] [--bilingual-order zh-first|source-first] [--glossary <file>]
      Translate an existing .srt, WebVTT (.vtt), or ASS/SSA (.ass/.ssa) subtitle file offline (with optional timestamp shift + .bilingual.srt/.ass/.md).

  npx local-sub-skill run <video|audio|url> [--language ja|en|ko|fr|de|es|ru|pt|auto] [--target zh|zh-Hant|en|ja|ko|fr|de|es|ru|pt] [--model <ollama-model>] [--shift <sec>] [--extract-subs] [--mux] [--burn] [--bilingual] [--ass] [--notes] [--glossary <file>] [--dry-run]
      End-to-end local Faster-Whisper (for video/audio .mp3/.m4a/.wav, embedded ffmpeg track, or YouTube/Bilibili URL via yt-dlp) + Ollama translation, optional .subbed.mkv soft-mux or .burned.mp4 hardsub burn-in.

  npx local-sub-skill batch <folder> [--recursive] [--subtitles-only] [--language ja|en|ko|fr|de|es|ru|pt|auto] [--target zh|zh-Hant|en|ja|ko|fr|de|es|ru|pt] [--model <ollama-model>] [--shift <sec>] [--extract-subs] [--mux] [--burn] [--bilingual] [--ass] [--notes] [--glossary <file>] [--continue-on-error] [--dry-run]
      Batch-generate translated subtitles (plus .bilingual.srt, .bilingual.ass, .bilingual.md, optional .subbed.mkv / .burned.mp4) for every video, audio file, and standalone .srt/.vtt/.ass in a folder.

  npx local-sub-skill gui [--port 8765] [--no-browser]
      Launch the zero-dependency local Multi-File Drag-and-Drop Web Studio (127.0.0.1).

  npx local-sub-skill doctor [--json] [--strict]
      Check Ollama, hy-mt2-local, Faster-Whisper, ffmpeg, yt-dlp, and GPU/CPU readiness across Windows/macOS/Linux.
`);
}

function main(argv) {
  const [cmd, ...rest] = argv;
  if (!cmd || cmd === '-h' || cmd === '--help' || cmd === 'help') {
    printHelp();
    return 0;
  }
  if (cmd === '-v' || cmd === '--version' || cmd === 'version') {
    console.log(PKG_JSON.version);
    return 0;
  }

  if (cmd === 'install') {
    let agent = 'all';
    for (let i = 0; i < rest.length; i++) {
      if (rest[i] === '--agent' && rest[i + 1]) {
        agent = rest[++i];
      }
    }
    const installed = installSkill({ agent });
    for (const item of installed) {
      console.log(`✓ Installed local-sub-skill for ${item.agent} -> ${item.path}`);
    }
    console.log('\nNext step: run `npx local-sub-skill setup` to pull local models.');
    return 0;
  }

  if (cmd === 'setup') {
    return runPythonScript('setup_local_models.py', rest);
  }

  if (cmd === 'translate') {
    return runPythonScript('local_translation.py', rest);
  }

  if (cmd === 'run') {
    return runPythonScript('run_single_video.py', rest);
  }

  if (cmd === 'batch') {
    return runPythonScript('run_batch.py', rest);
  }

  if (cmd === 'gui') {
    return runPythonScript('gui.py', rest);
  }

  if (cmd === 'doctor') {
    return runPythonScript('doctor.py', rest);
  }

  console.error(`Unknown command: ${cmd}\n`);
  printHelp();
  return 1;
}

export function isInvokedAsMain(
  argv1 = process.argv[1],
  entryFile = __filename,
  realpath = fs.realpathSync,
) {
  if (!argv1) return false;
  try {
    return realpath(argv1) === realpath(entryFile);
  } catch {
    return path.resolve(argv1) === path.resolve(entryFile);
  }
}

if (isInvokedAsMain()) {
  process.exitCode = main(process.argv.slice(2));
}
