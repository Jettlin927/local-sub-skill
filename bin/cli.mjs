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
  };
  if (agent === 'all') return Object.entries(map);
  if (map[agent]) return [[agent, map[agent]]];
  throw new Error(`Unsupported --agent "${agent}". Choose: claude, codex, antigravity, or all.`);
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
  console.log(`local-sub-skill — 100% Local Video → Chinese Subtitle CLI & AI Agent Skill

Usage:
  npx local-sub-skill install [--agent claude|codex|antigravity|all]
      Install SKILL.md + scripts into your AI coding agent skills folder.

  npx local-sub-skill setup [--asr-model-dir <dir>] [--skip-whisper]
      Pull Ollama Hy-MT2-1.8B Q4_K_M and Faster-Whisper large-v3-turbo weights.

  npx local-sub-skill translate <input.srt> <output.zh.srt> [--source ja|en|auto]
      Translate an existing SRT subtitle file to Chinese completely offline.

  npx local-sub-skill run <video.mp4> [--language ja|en|auto] [--dry-run]
      End-to-end local Faster-Whisper transcription + Ollama Hy-MT2-1.8B translation.
`);
}

function main(argv) {
  const [cmd, ...rest] = argv;
  if (!cmd || cmd === '-h' || cmd === '--help' || cmd === 'help') {
    printHelp();
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

  console.error(`Unknown command: ${cmd}\n`);
  printHelp();
  return 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === __filename) {
  process.exitCode = main(process.argv.slice(2));
}
