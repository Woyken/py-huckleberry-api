import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, unlink, rmdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Controller, saveEvidence } from '../lib/client.mjs';

function controller() {
  return new Controller({ transport: 'rish', androidUser: 12, expectedChild: 'Test Child' }, { ready: true });
}

test('library users receive the same profile validation as the CLI', () => {
  assert.throws(() => new Controller({ transport: 'rish', androidUser: 0, expectedChild: 'Test Child' }), /personal/);
  assert.throws(() => new Controller({ transport: 'auto', androidUser: 12, expectedChild: 'Test Child' }), /transport/);
});

test('low-level public requests cannot bypass profile/task enrollment', async () => {
  const client = controller();
  client.transport.capture = async () => { throw new Error('Must not execute'); };
  client.identity = async () => ({ user: 12, task: 123 });
  await assert.rejects(client.request({ method: 'claim', session: 'session', user: 0, child: 'Test Child' }), /enrolled profile/);
  await assert.rejects(client.request({ method: 'tree', session: 'session', user: 12, child: 'Test Child', task: 999 }), /current task/);
});

test('one shared workflow dispatches once and verifies the destination', async () => {
  const client = controller();
  const requests = [];
  let observations = 0;
  client.inspect = async () => ({ task: 123, childVerified: true, selectedTab: observations++ ? 'reports' : 'home', identity: { user: 12 } });
  client.request = async request => { requests.push(request); return { protocol: 1, status: 'dispatched' }; };
  const result = await client.navigate('session', 'home', 'reports', 'action_unique');
  assert.equal(result.status, 'verified');
  assert.equal(requests.length, 1);
  assert.equal(requests[0].user, 12);
  assert.equal(requests[0].child, 'Test Child');
});

test('missing child or incorrect source does not dispatch', async () => {
  const client = controller();
  client.inspect = async () => ({ task: 123, childVerified: false, selectedTab: 'home' });
  client.request = async () => { throw new Error('Must not dispatch'); };
  await assert.rejects(client.navigate('session', 'home', 'reports', 'action_unique'), /preconditions/);
});

test('post-dispatch failures are never retried with another action ID', async () => {
  const client = controller();
  let observations = 0, dispatches = 0;
  client.inspect = async () => {
    if (observations++) throw new Error('connection lost');
    return { task: 123, childVerified: true, selectedTab: 'home' };
  };
  client.request = async () => { dispatches++; return { status: 'dispatched' }; };
  await assert.rejects(client.navigate('session', 'home', 'reports', 'action_unique'), /connection lost/);
  assert.equal(dispatches, 1);
});

test('duplicate/uncertain replies stop rather than generating another request', async () => {
  for (const status of ['duplicate_suppressed', 'uncertain', 'request_expired']) {
    const client = controller();
    let count = 0;
    client.inspect = async () => ({ task: 123, childVerified: true, selectedTab: 'home' });
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
