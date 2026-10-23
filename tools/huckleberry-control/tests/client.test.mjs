import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, unlink, rmdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Controller, saveEvidence } from '../lib/client.mjs';

function controller() {
  return new Controller({ transport: 'rish', androidUser: 12 }, { ready: true });
}

test('library users receive the same profile validation as the CLI', () => {
  assert.throws(() => new Controller({ transport: 'rish', androidUser: 0 }), /personal/);
  assert.throws(() => new Controller({ transport: 'auto', androidUser: 12 }), /transport/);
});

test('rish controllers reuse one shell; ADB controllers keep the stateless adapter', () => {
  const rish = new Controller({ transport: 'rish', androidUser: 12 });
  const adb = new Controller({ transport: 'adb', serial: 'emulator-5580', avd: 'huck34', androidUser: 0 });
  assert.equal(rish.transport.constructor.name, 'PersistentTransport');
  assert.equal(adb.transport.constructor.name, 'Transport');
});

test('low-level public requests cannot bypass profile/task enrollment', async () => {
  const client = controller();
  client.transport.capture = async () => { throw new Error('Must not execute'); };
  client.identity = async () => ({ user: 12, task: 123 });
  await assert.rejects(client.request({ method: 'claim', session: 'session', user: 0 }), /enrolled profile/);
  await assert.rejects(client.request({ method: 'tree', session: 'session', user: 12, task: 999 }), /current task/);
});

function launchHarness() {
  const client = controller();
  const calls = { status: 0, claim: 0, commands: [], observations: 0, releases: 0 };
  client.status = async () => { calls.status++; return { owner: 'session' }; };
  client.claim = async () => { calls.claim++; return { owner: 'session' }; };
  client.release = async () => { calls.releases++; throw new Error('Must not automatically release'); };
  client.transport.shell = async script => {
    calls.commands.push(script);
    if (script.startsWith('/system/bin/pm path ')) return 'package:/data/app/synthetic/base.apk';
    if (script.includes('.ProfileLauncher ')) return '';
    throw new Error('Unexpected synthetic command');
  };
  return { client, calls, launches: () => calls.commands.filter(script => script.includes('.ProfileLauncher ')).length };
}

test('successful launch dispatches and observes once without resetting the screen', async () => {
  const h = launchHarness();
  const snapshot = { status: 'ok', selectedTab: 'reports' };
  h.client.inspect = async () => { h.calls.observations++; return snapshot; };
  assert.equal(await h.client.launch('session'), snapshot);
  assert.equal(h.launches(), 1);
  assert.equal(h.calls.observations, 1);
  assert.equal(h.calls.claim, 1);
  assert.equal(h.calls.releases, 0);
});

test('late accessibility task cache retries observation without relaunching', async () => {
  const h = launchHarness();
  const snapshot = { status: 'ok', selectedTab: 'home' };
  h.client.inspect = async () => {
    if (++h.calls.observations === 1) throw new Error('Bridge refused request: focused_task_not_found');
    return snapshot;
  };
  assert.equal(await h.client.launch('session'), snapshot);
  assert.equal(h.launches(), 1);
  assert.equal(h.calls.observations, 2);
  assert.equal(h.calls.status, 1);
  assert.equal(h.calls.claim, 1);
  assert.equal(h.calls.releases, 0);
});

test('launch task-cache observation retries are bounded and retain ownership', async () => {
  const h = launchHarness();
  h.client.inspect = async () => { h.calls.observations++; throw new Error('Bridge refused request: focused_task_not_found'); };
  await assert.rejects(h.client.launch('session'), /focused_task_not_found/);
  assert.equal(h.launches(), 1);
  assert.equal(h.calls.observations, 3);
  assert.equal(h.calls.claim, 1);
  assert.equal(h.calls.releases, 0);
});

test('launch never retries identity, permission, corruption or other observation failures', async () => {
  for (const reason of ['Refusing wrong Android user, app, or task',
    'Huckleberry is not the focused window in the enrolled user', 'Bridge refused request: service_not_enabled',
    'Bridge refused request: ambiguous_window', 'Task changed during observation',
    'Android result transfer was incomplete or corrupt; never replay an uncertain action']) {
    const h = launchHarness();
    const error = new Error(reason);
    h.client.inspect = async () => { h.calls.observations++; throw error; };
    await assert.rejects(h.client.launch('session'), actual => actual === error);
    assert.equal(h.launches(), 1);
    assert.equal(h.calls.observations, 1);
    assert.equal(h.calls.releases, 0);
  }
});

test('uncertain launcher dispatch is never resent or treated as a readback race', async () => {
  const h = launchHarness();
  h.client.transport.shell = async script => {
    h.calls.commands.push(script);
    if (script.startsWith('/system/bin/pm path ')) return 'package:/data/app/synthetic/base.apk';
    throw new Error('Synthetic launcher result lost');
  };
  h.client.inspect = async () => { h.calls.observations++; throw new Error('Must not inspect after uncertain dispatch'); };
  await assert.rejects(h.client.launch('session'), /launcher result lost/);
  assert.equal(h.launches(), 1);
  assert.equal(h.calls.observations, 0);
  assert.equal(h.calls.releases, 0);
});

test('one shared workflow dispatches once and verifies the destination', async () => {
  const client = controller();
  const requests = [];
  let observations = 0;
  client.inspect = async () => ({ task: 123, selectedTab: observations++ ? 'reports' : 'home', identity: { user: 12 } });
  client.request = async request => { requests.push(request); return { protocol: 1, status: 'dispatched' }; };
  const result = await client.navigate('session', 'home', 'reports', 'action_unique');
  assert.equal(result.status, 'verified');
  assert.equal(requests.length, 1);
  assert.equal(requests[0].user, 12);
});

test('incorrect source tab does not dispatch', async () => {
  const client = controller();
  client.inspect = async () => ({ task: 123, selectedTab: 'reports' });
  client.request = async () => { throw new Error('Must not dispatch'); };
  await assert.rejects(client.navigate('session', 'home', 'reports', 'action_unique'), /preconditions/);
});

test('post-dispatch failures are never retried with another action ID', async () => {
  const client = controller();
  let observations = 0, dispatches = 0;
  client.inspect = async () => {
    if (observations++) throw new Error('connection lost');
    return { task: 123, selectedTab: 'home' };
  };
  client.request = async () => { dispatches++; return { status: 'dispatched' }; };
  await assert.rejects(client.navigate('session', 'home', 'reports', 'action_unique'), /connection lost/);
  assert.equal(dispatches, 1);
});

test('duplicate/uncertain replies stop rather than generating another request', async () => {
  for (const status of ['duplicate_suppressed', 'uncertain', 'request_expired']) {
    const client = controller();
    let count = 0;
    client.inspect = async () => ({ task: 123, selectedTab: 'home' });
    client.request = async () => { count++; return { status }; };
    await assert.rejects(client.navigate('session', 'home', 'reports', 'action_unique'), /not repeated/);
    assert.equal(count, 1);
  }
});

test('private evidence refuses overwrite', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'hb-debug-test-'));
  const path = join(directory, 'evidence.json');
  try {
    await saveEvidence(path, { backendCaptured: false });
    await assert.rejects(saveEvidence(path, { overwritten: true }), /EEXIST/);
    assert.equal(JSON.parse(await readFile(path, 'utf8')).backendCaptured, false);
  } finally { await unlink(path); await rmdir(directory); }
});
