import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { getInstallTargets, installSkill } from '../bin/cli.mjs';

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
