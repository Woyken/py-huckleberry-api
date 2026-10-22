import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { execFile, spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer } from 'node:net';
import { promisify } from 'node:util';
import { join, basename } from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
import { ROOT, validateConfig, validateSession, parseForwards, sameForward, pendingForwardRemovals } from '../scripts/runtime.mjs';
import { main } from '../scripts/huckleberry.mjs';
import { protocolNumber, verifyProtocols, readServerProtocol, validateAdbEnvironment } from '../scripts/adb-safety.mjs';

const exec = promisify(execFile);

test('portable skill frontmatter and direct references', async () => {
  const text = await readFile(join(ROOT, 'SKILL.md'), 'utf8');
  const front = /^---\r?\n([\s\S]*?)\r?\n---/.exec(text)?.[1];
  assert.ok(front);
  const name = /^name: (.+)$/m.exec(front)?.[1];
  assert.equal(name, basename(ROOT));
  assert.match(name, /^[a-z0-9]+(-[a-z0-9]+)*$/);
  assert.ok(name.length <= 64 && !/claude|anthropic/.test(name));
  const description = /^description: (.+)$/m.exec(front)?.[1];
  assert.ok(description && description.length <= 1024 && !/[<>]/.test(description));
  assert.ok(text.split('\n').length < 150);
  for (const match of text.matchAll(/\]\((references\/[^)]+)\)/g)) {
    assert.ok((await readFile(join(ROOT, match[1]), 'utf8')).length > 0);
  }
});

test('physical, malformed and odd-port device config rejected', () => {
  assert.equal(validateConfig({ avd: 'huck34', serial: 'emulator-5580' }).port, 5580);
  for (const serial of ['Pixel_9_Pro', 'adb-phone._tcp', 'emulator-5581', 'emulator-1', 'emulator-9999', 'emulator-5580;bad']) {
    assert.throws(() => validateConfig({ avd: 'huck34', serial }));
  }
  assert.throws(() => validateConfig({ avd: '../other', serial: 'emulator-5580' }));
});

test('session validation prevents implicit ownership and unsafe IDs', () => {
  assert.equal(validateSession('test-session_1'), 'test-session_1');
  for (const value of [undefined, '', 'ab', '../escape', 'a space', 'x'.repeat(81)]) assert.throws(() => validateSession(value));
});

test('forward ownership matches all three fields, not merely the port', () => {
  const state = { serial: 'emulator-5580', forward: { port: 61234, socket: 'webview_devtools_remote_123' } };
  const rules = parseForwards('emulator-5580 tcp:61234 localabstract:webview_devtools_remote_123\r\nphone tcp:9223 tcp:9999\r\n');
  assert.equal(rules.length, 2);
  assert.equal(sameForward(rules[0], state), true);
  assert.equal(sameForward(rules[1], state), false);
  for (const change of [{ serial: 'phone' }, { local: 'tcp:9223' }, { remote: 'tcp:9999' }]) {
    assert.equal(sameForward({ ...rules[0], ...change }, state), false);
  }
});

test('pending forward cleanup removes only rules absent from the pre-allocation baseline', () => {
  const state = {
    serial: 'emulator-5580',
    pendingForward: {
      socket: 'webview_devtools_remote_123',
      before: [{ serial: 'emulator-5580', local: 'tcp:61234', remote: 'localabstract:webview_devtools_remote_123' }],
    },
  };
  const rules = parseForwards([
    'emulator-5580 tcp:61234 localabstract:webview_devtools_remote_123',
    'emulator-5580 tcp:61235 localabstract:webview_devtools_remote_123',
    'emulator-5580 tcp:61236 localabstract:webview_devtools_remote_999',
    'phone tcp:61237 localabstract:webview_devtools_remote_123',
  ].join('\r\n'));
  assert.deepEqual(pendingForwardRemovals(rules, state).map((rule) => rule.local), ['tcp:61235']);
  assert.deepEqual(pendingForwardRemovals(rules, { ...state, pendingForward: null }), []);
});

test('CLI argument failures occur before device access', async () => {
  for (const args of [['nonsense'], ['click'], ['fill', 'qa:notes'], ['checkpoint'], ['wait', 'qa:item', '--hidden', '--text', 'x'], ['wait', 'qa:item', '--exact']]) {
    await assert.rejects(main(args));
  }
});

test('script syntax valid and help has no dependency/device requirement', async () => {
  for (const name of (await readdir(join(ROOT, 'scripts'))).filter((file) => file.endsWith('.mjs'))) {
    await exec(process.execPath, ['--check', join(ROOT, 'scripts', name)]);
  }
  const result = await exec(process.execPath, [join(ROOT, 'scripts', 'huckleberry.mjs'), '--help'], { cwd: process.env.TEMP || ROOT });
  assert.match(result.stdout, /start --session/);
});

test('claim and command locks tested in isolated state, without ADB', { skip: process.platform !== 'win32' }, async () => {
  const temporary = await mkdtemp(join(tmpdir(), 'huckleberry-offline-'));
  try {
    const moduleURL = pathToFileURL(join(ROOT, 'scripts', 'runtime.mjs')).href;
    const source = `
      import assert from 'node:assert/strict';
      import {withSession,readState,release} from ${JSON.stringify(moduleURL)};
      await withSession('owner-one', async state => assert.equal(state.session,'owner-one'), {create:true});
      await assert.rejects(withSession('owner-two', () => {throw Error('must not run')}, {create:true}), /claimed/);
      await withSession('owner-one', async () => {
        await assert.rejects(withSession('owner-one', () => {throw Error('must not run')}), /concurrently/);
      });
      await withSession('owner-one', release); // forward=null: this sends no ADB command.
      assert.equal(await readState(),null);
      console.log('isolated locks passed');
    `;
    const result = await exec(process.execPath, ['--input-type=module', '-e', source], {
      env: { ...process.env, LOCALAPPDATA: temporary }, timeout: 15000,
    });
    assert.match(result.stdout, /isolated locks passed/);
  } finally { await rm(temporary, { recursive: true, force: true }); }
});

test('wire protocol compatibility, not package build numbers, controls ADB safety', () => {
  assert.doesNotThrow(() => validateAdbEnvironment({}));
  for (const key of ['ADB_SERVER_SOCKET', 'ANDROID_ADB_SERVER_ADDRESS', 'ANDROID_ADB_SERVER_PORT']) {
    assert.throws(() => validateAdbEnvironment({ [key]: 'custom' }), /default local ADB server/);
  }
  assert.equal(protocolNumber('Android Debug Bridge version 1.0.41\nVersion 31.0.3'), 41);
  assert.equal(protocolNumber('Android Debug Bridge version 1.0.41\nVersion 34.0.5'), 41);
  assert.doesNotThrow(() => verifyProtocols(41, 41, 41));
  assert.doesNotThrow(() => verifyProtocols(41, null, null));
  assert.throws(() => verifyProtocols(41, 40, 41), /protocols differ/);
  assert.throws(() => verifyProtocols(41, 41, 40), /protocols differ/);
});

test('read-only ADB server version query parses a mock server, without ADB clients/devices', async () => {
  let request;
  const server = createServer((socket) => socket.once('data', (data) => {
    request = data.toString();
    socket.end('OKAY00040029');
  }));
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  try {
    assert.equal(await readServerProtocol(server.address().port), 41);
    assert.equal(request, '000chost:version');
  } finally { server.close(); await once(server, 'close'); }
});

test('same-session command recovers after abrupt Node death; stale diagnostic PID is not lock authority', { skip: process.platform !== 'win32' }, async () => {
  const temporary = await mkdtemp(join(tmpdir(), 'huckleberry-crash-'));
  const moduleURL = pathToFileURL(join(ROOT, 'scripts', 'runtime.mjs')).href;
  let child;
  try {
    const code = `import {withSession} from ${JSON.stringify(moduleURL)};
      await withSession('crash-owner',async()=>{ console.log('READY'); await new Promise(()=>{}); },{create:true});`;
    child = spawn(process.execPath, ['--input-type=module', '-e', code], {
      env: { ...process.env, LOCALAPPDATA: temporary }, stdio: ['ignore', 'pipe', 'pipe'],
    });
    const ended = once(child, 'close');
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Crash fixture did not acquire its mutex in 15s')), 15000);
      child.once('error', (error) => { clearTimeout(timer); reject(error); });
      child.stdout.on('data', (chunk) => { if (chunk.toString().includes('READY')) { clearTimeout(timer); resolve(); } });
    });
    child.kill();
    await ended;
    await writeFile(join(temporary, 'huckleberry-agent', 'command.json'), JSON.stringify({ pid: process.pid, at: 'stale diagnostic' }));
    const recovery = `import assert from 'node:assert/strict';
      import {withSession,release,readState} from ${JSON.stringify(moduleURL)};
      await withSession('crash-owner',async state=>assert.equal(state.session,'crash-owner'));
      await withSession('crash-owner',release);
      assert.equal(await readState(),null); console.log('recovered without task takeover or ADB');`;
    const result = await exec(process.execPath, ['--input-type=module', '-e', recovery], {
      env: { ...process.env, LOCALAPPDATA: temporary }, timeout: 20000,
    });
    assert.match(result.stdout, /recovered/);
  } finally { if (child?.exitCode === null) child.kill(); await rm(temporary, { recursive: true, force: true }); }
});
