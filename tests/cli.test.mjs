import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { getInstallTargets, installSkill, isInvokedAsMain } from '../bin/cli.mjs';

const PKG_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('getInstallTargets resolves claude, codex, antigravity, and all', () => {
  const fakeHome = path.join(os.tmpdir(), 'fake-home');
  assert.equal(getInstallTargets('all', fakeHome).length, 3);
  assert.equal(getInstallTargets('claude', fakeHome)[0][0], 'claude');
  assert.throws(() => getInstallTargets('invalid', fakeHome));
});

test('installSkill copies SKILL.md and scripts cleanly without __pycache__', () => {
  const tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'local-sub-skill-test-'));
  try {
    const installed = installSkill({ agent: 'claude', homeDir: tmpHome });
    assert.equal(installed.length, 1);
    const dest = installed[0].path;
    assert.ok(fs.existsSync(path.join(dest, 'SKILL.md')));
    assert.ok(fs.existsSync(path.join(dest, 'scripts', 'local_translation.py')));
    assert.ok(fs.existsSync(path.join(dest, 'scripts', 'hy-mt2-local.Modelfile')));
    assert.ok(!fs.existsSync(path.join(dest, 'scripts', '__pycache__')));
  } finally {
    fs.rmSync(tmpHome, { recursive: true, force: true });
  }
});

test('isInvokedAsMain returns true for symlinked node_modules/.bin entrypoint', () => {
  const realCli = '/tmp/pkg/node_modules/local-sub-skill/bin/cli.mjs';
  const symlinkBin = '/tmp/pkg/node_modules/.bin/local-sub-skill';
  const fakeRealpath = (p) => (p === symlinkBin ? realCli : p);

  assert.equal(isInvokedAsMain(symlinkBin, realCli, fakeRealpath), true);
  assert.equal(isInvokedAsMain('/tmp/other/script.mjs', realCli, fakeRealpath), false);
  assert.equal(isInvokedAsMain(undefined, realCli, fakeRealpath), false);
});

test('packed node_modules/.bin entrypoint prints help output on --help', () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'local-sub-skill-bin-'));
  const npmCmd = process.platform === 'win32' ? 'npm.cmd' : 'npm';
  const shellOpts = process.platform === 'win32' ? { shell: true } : {};

  try {
    const npmEnv = { ...process.env, npm_config_cache: path.join(tmpDir, 'npm-cache') };
    const packRes = spawnSync(npmCmd, ['pack', '--pack-destination', tmpDir], {
      cwd: PKG_ROOT,
      encoding: 'utf8',
      env: npmEnv,
      ...shellOpts,
    });
    assert.equal(packRes.status, 0, `npm pack failed: ${packRes.stderr}`);
    const tgzName = packRes.stdout.trim().split(/\r?\n/).pop();
    const tgzPath = path.join(tmpDir, tgzName);
    assert.ok(fs.existsSync(tgzPath), `Expected packed tarball at ${tgzPath}`);

    const consumerDir = path.join(tmpDir, 'consumer');
    fs.mkdirSync(consumerDir, { recursive: true });
    fs.writeFileSync(
      path.join(consumerDir, 'package.json'),
      JSON.stringify({ name: 'consumer-test', private: true }, null, 2),
    );

    const installRes = spawnSync(
      npmCmd,
      ['install', '--no-audit', '--no-fund', '--offline', tgzPath],
      { cwd: consumerDir, encoding: 'utf8', env: npmEnv, ...shellOpts },
    );
    assert.equal(installRes.status, 0, `npm install failed: ${installRes.stderr}`);

    const binPath = path.join(
      consumerDir,
      'node_modules',
      '.bin',
      process.platform === 'win32' ? 'local-sub-skill.cmd' : 'local-sub-skill',
    );
    assert.ok(fs.existsSync(binPath), `Expected bin entrypoint at ${binPath}`);

    const runRes = spawnSync(binPath, ['--help'], {
      cwd: consumerDir,
      encoding: 'utf8',
      ...shellOpts,
    });
    assert.equal(runRes.status, 0, `bin --help failed: ${runRes.stderr}`);
    assert.match(runRes.stdout, /local-sub-skill/);
    assert.match(runRes.stdout, /npx local-sub-skill install/);
    assert.match(runRes.stdout, /npx local-sub-skill batch/);
    assert.match(runRes.stdout, /npx local-sub-skill gui/);
    assert.match(runRes.stdout, /npx local-sub-skill doctor/);
    assert.match(runRes.stdout, /--bilingual/);
    assert.match(runRes.stdout, /--ass/);
    assert.match(runRes.stdout, /--glossary/);
    assert.match(runRes.stdout, /\.vtt/);
    assert.match(runRes.stdout, /--subtitles-only/);

    const verRes = spawnSync(binPath, ['--version'], {
      cwd: consumerDir,
      encoding: 'utf8',
      ...shellOpts,
    });
    assert.equal(verRes.status, 0, `bin --version failed: ${verRes.stderr}`);
    assert.equal(verRes.stdout.trim(), '1.4.0');
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});
