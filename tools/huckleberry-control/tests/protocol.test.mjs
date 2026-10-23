import test from 'node:test';
import assert from 'node:assert/strict';
import { contentArguments, parseReply, quote, validateSnapshot, identifier } from '../lib/protocol.mjs';
import { validateProfile } from '../lib/config.mjs';
import { main } from '../cli.mjs';

const profile = { transport: 'adb', serial: 'emulator-5580', avd: 'test_avd', androidUser: 0 };
const request = { method: 'tree', session: 'test_session', user: 12, task: 123 };

test('explicit profiles preserve emulator-only and work-profile-only boundaries', () => {
  assert.equal(validateProfile(profile).serial, 'emulator-5580');
  assert.equal(validateProfile({ transport: 'rish', androidUser: 12 }).androidUser, 12);
  for (const change of [{ transport: 'auto' }, { serial: 'phone' }, { serial: '127.0.0.1:5555' },
    { serial: 'emulator-5581' }, { serial: 'emulator-9999' }, { avd: '../test' },
    { androidUser: -1 }, { androidUser: '12' }, { unexpected: true }]) {
    assert.throws(() => validateProfile({ ...profile, ...change }));
  }
  assert.throws(() => validateProfile({ transport: 'rish', androidUser: 0 }));
  assert.throws(() => validateProfile({ transport: 'rish', androidUser: 12, serial: 'phone' }));
});

test('content extras use key:type:value and preserve exact text', () => {
  const args = contentArguments(request);
  assert.ok(args.includes('task:i:123'));
  assert.ok(args.includes('user:i:12'));
  assert.equal(args[args.indexOf('--user') + 1], '0'); // Helper user, not target user.
});

test('unsupported operations, malformed IDs and injection fields are rejected', () => {
  for (const method of ['click', 'fill', 'screenshot', 'delete', 'save', 'install']) assert.throws(() => contentArguments({ method }));
  for (const task of [undefined, -1, 0, 1.5, '123', '1; input tap 1 1']) assert.throws(() => contentArguments({ ...request, task }));
  for (const value of ['../escape', 'id;input', '', 'x'.repeat(81)]) assert.throws(() => identifier(value));
  assert.throws(() => contentArguments({ ...request, extra: 'ignored?' }));
  assert.throws(() => contentArguments({ ...request, child: 'Test Child' }));
  assert.throws(() => contentArguments({ method: 'navigate', session: 'session', user: 12, task: 1, action_id: 'action_123', from: 'home', to: 'sleep' }));
});

test('POSIX quoting keeps spaces, apostrophes and metacharacters literal', () => {
  assert.equal(quote("a'b; $HOME"), "'a'\\''b; $HOME'");
  assert.throws(() => quote('a\0b'));
});

test('semantic action requests require one allowlisted target and bounded values', () => {
  const action = { method: 'action', session: 'test_session', user: 12, task: 123,
    action_id: 'action_123', action: 'click', text: 'Save' };
  assert.ok(contentArguments(action).includes('action:s:click'));
  assert.ok(contentArguments({ ...action, action: 'set-text', text: '', id: 'field', value: 'hello' }).includes('value:s:hello'));
  assert.doesNotThrow(() => contentArguments({ ...action, action: 'back', text: '' }));
  for (const change of [{ action: 'swipe' }, { text: 'Save', id: 'field' }, { text: '' },
    { action: 'back', text: 'Save' }, { action: 'click', text: '', path: 'root/../1' },
    { action: 'set-text', text: '', id: 'field', value: 'x'.repeat(4097) }]) {
    assert.throws(() => contentArguments({ ...action, ...change }));
  }
});

test('one versioned parser accepts Android replies and rejects old/truncated output', () => {
  assert.deepEqual(parseReply('Result: Bundle[{result={"protocol":3,"status":"ok"}}]\r\n'), { protocol: 3, status: 'ok' });
  for (const value of ['', 'usage: adb shell content', 'Result: Bundle[{result={"status":"ok"}}]',
    'Result: Bundle[{result={"protocol":1,"status":"ok"}}]', 'Result: Bundle[{result=[]}]',
    'Result: Bundle[{result={"protocol":3,"status":"ok"}']) assert.throws(() => parseReply(value));
});

test('snapshot validation does not treat malformed or partial data as confirmation', () => {
  const node = { path: 'root/0', depth: 0, className: 'android.widget.Button', id: 'save', text: 'Save',
    description: null, hint: null, bounds: '[0,0][1,1]', visible: true, enabled: true, clickable: true,
    longClickable: false, scrollable: false, checkable: false, checked: false, selected: false,
    editable: false, focusable: true, focused: false, password: false, actions: [16] };
  const snapshot = { protocol: 3, status: 'ok', task: 123, window: 4, nodes: [node], truncated: false,
    guardComplete: true, selectedTab: null };
  assert.equal(validateSnapshot(snapshot, 123), snapshot);
  assert.equal(validateSnapshot({ ...snapshot, selectedTab: 'child' }, 123).selectedTab, 'child');
  assert.throws(() => validateSnapshot(snapshot, 124));
  assert.throws(() => validateSnapshot({ ...snapshot, selectedTab: 'sleep' }, 123));
  assert.throws(() => validateSnapshot({ ...snapshot, nodes: [{}] }, 123));
  assert.throws(() => validateSnapshot({ ...snapshot, nodes: [{ ...node, visible: 'true' }] }, 123));
});

test('invalid CLI commands fail before profile/device access', async () => {
  for (const args of [['unknown'], ['inspect'], ['navigate', 'sleep', '--session', 'session', '--action-id', 'action_123', '--from', 'home'],
    ['status', '--out', 'file'], ['inspect', '--session', '../escape'], ['status', '--from', 'home']]) {
    await assert.rejects(main(args));
  }
});
