import test from 'node:test';
import assert from 'node:assert/strict';
import { main, runTermuxSmoke } from '../scripts/termux-smoke.mjs';

const options = {
  profilePath: '/synthetic-private/enrollment.json', session: 'synthetic_private_session', exclusiveAccess: true,
};
const navigation = { ...options, navigate: true, actionPrefix: 'synthetic_private_actions' };

function harness(overrides = {}) {
  const calls = [];
  let tab = 'home';
  const snapshot = selectedTab => ({
    protocol: 1, status: 'ok', source: 'android-accessibility', backendCaptured: false,
    selectedTab, childVerified: true, guardComplete: true, truncated: false,
    identity: { user: 12, task: 987654, device: 'SYNTHETIC_PRIVATE_DEVICE' },
    nodes: [{ text: 'SYNTHETIC_PRIVATE_RECORD' }],
  });
  const controller = {
    async connect() { calls.push(['connect']); return this; },
    async status() {
      calls.push(['status']);
      return { protocol: 1, status: 'ok', serviceConnected: true, owner: null,
        capabilities: { inspect: true, navigationOnly: true, trackingWrites: false, screenshots: false, typing: false } };
    },
    async identity() { calls.push(['identity']); return { user: 12, task: 987654 }; },
    async claim(session) { calls.push(['claim', session]); return { status: 'ok', owner: session }; },
    async inspect(session) { calls.push(['inspect', session]); return snapshot(tab); },
    async actionStatus(session, id) { calls.push(['actionStatus', session, id]); return { protocol: 1, status: 'ok', consumed: false }; },
    async navigate(session, from, to, id) {
      calls.push(['navigate', session, from, to, id]); tab = to; return { status: 'verified' };
    },
    async release(session) { calls.push(['release', session]); return { status: 'ok', released: session }; },
    async launch() { throw new Error('Runner must never launch automatically'); },
    ...overrides,
  };
  const dependencies = {
    async loadProfile(path) {
      calls.push(['loadProfile', path]);
      return { transport: 'rish', androidUser: 12, expectedChild: 'SYNTHETIC_PRIVATE_CHILD' };
    },
    createController(profile) { calls.push(['createController', profile]); return controller; },
  };
  return { calls, controller, dependencies, snapshot };
}

test('default is observation-only and releases only after verified completion', async () => {
  const h = harness();
  const report = await runTermuxSmoke(options, h.dependencies);
  assert.equal(report.status, 'passed');
  assert.equal(report.mode, 'observation');
  assert.equal(report.claimState, 'released');
  assert.equal(report.navigationInvoked, false);
  assert.equal(report.checks.baselineVerified, true);
  assert.equal(h.calls.filter(call => call[0] === 'inspect').length, 1);
  assert.equal(h.calls.filter(call => ['navigate', 'actionStatus', 'launch'].includes(call[0])).length, 0);
});

test('navigation is explicit, uses stable IDs exactly once and observes both postconditions', async () => {
  const h = harness();
  const report = await runTermuxSmoke(navigation, h.dependencies);
  assert.equal(report.status, 'passed');
  assert.equal(report.checks.reportsVerified, true);
  assert.equal(report.checks.homeVerified, true);
  assert.deepEqual(h.calls.filter(call => call[0] === 'navigate').map(call => call.slice(2)), [
    ['home', 'reports', `${navigation.actionPrefix}-reports`],
    ['reports', 'home', `${navigation.actionPrefix}-home`],
  ]);
  assert.equal(h.calls.filter(call => call[0] === 'inspect').length, 3);
  assert.equal(h.calls.at(-1)[0], 'release');
});

test('invalid/missing opt-ins fail before profile or Android access', async () => {
  for (const change of [
    { profilePath: undefined }, { session: '../escape' }, { exclusiveAccess: false },
    { navigate: 'true' }, { navigate: true }, { actionPrefix: 'unused_prefix' },
    { navigate: true, actionPrefix: 'x'.repeat(65) },
  ]) {
    const h = harness();
    const report = await runTermuxSmoke({ ...options, ...change }, h.dependencies);
    assert.equal(report.status, 'blocked');
    assert.equal(report.phase, 'options');
    assert.deepEqual(h.calls, []);
  }
});

test('ADB and personal-user profiles are refused before controller construction', async () => {
  for (const profile of [
    { transport: 'adb', androidUser: 0, expectedChild: 'Synthetic', serial: 'emulator-5580', avd: 'synthetic_avd' },
    { transport: 'rish', androidUser: 0, expectedChild: 'Synthetic' },
  ]) {
    const h = harness();
    h.dependencies.loadProfile = async () => profile;
    const report = await runTermuxSmoke(options, h.dependencies);
    assert.equal(report.status, 'blocked');
    assert.equal(h.calls.some(call => call[0] === 'createController'), false);
  }
});

test('any existing claim, including the same session, is never taken over', async () => {
  for (const owner of [options.session, 'synthetic_other_owner']) {
    const h = harness();
    const status = await h.controller.status();
    h.calls.length = 0;
    h.controller.status = async () => ({ ...status, owner });
    const report = await runTermuxSmoke(options, h.dependencies);
    assert.equal(report.reason, 'existing_claim');
    assert.equal(report.claimState, 'not_acquired');
    assert.equal(h.calls.some(call => ['identity', 'claim', 'release'].includes(call[0])), false);
  }
});

test('service and capability mismatches cannot reach claim or input', async () => {
  for (const patch of [{ serviceConnected: false }, { capabilities: { inspect: true, screenshots: true } }]) {
    const h = harness();
    const status = await h.controller.status(); h.calls.length = 0;
    h.controller.status = async () => ({ ...status, ...patch });
    const report = await runTermuxSmoke(options, h.dependencies);
    assert.equal(report.status, 'blocked');
    assert.equal(h.calls.some(call => ['claim', 'navigate', 'release'].includes(call[0])), false);
  }
});

test('identity/focus failure occurs before acquiring a session', async () => {
  const h = harness({ async identity() { throw new Error('SYNTHETIC_PRIVATE_DEVICE'); } });
  const report = await runTermuxSmoke(options, h.dependencies);
  assert.equal(report.reason, 'identity_failed');
  assert.equal(report.claimState, 'not_acquired');
  assert.equal(h.calls.some(call => call[0] === 'claim'), false);
});

test('bad, partial or wrong-child baseline retains the claim without navigation/release', async () => {
  const patches = [{ childVerified: false }, { guardComplete: false }, { truncated: true },
    { selectedTab: null }, { source: 'cdp' }, { backendCaptured: true }];
  for (const patch of patches) {
    const h = harness();
    h.controller.inspect = async () => ({ ...h.snapshot('home'), ...patch });
    const report = await runTermuxSmoke(navigation, h.dependencies);
    assert.equal(report.status, 'blocked');
    assert.equal(report.claimState, 'retained');
    assert.equal(report.navigationInvoked, false);
    assert.equal(h.calls.some(call => ['navigate', 'release'].includes(call[0])), false);
  }
});

test('observation may inspect Reports, but navigation never automatically resets a non-Home start', async () => {
  const h = harness();
  h.controller.inspect = async () => h.snapshot('reports');
  assert.equal((await runTermuxSmoke(options, h.dependencies)).status, 'passed');
  const next = harness();
  next.controller.inspect = async () => next.snapshot('reports');
  const report = await runTermuxSmoke(navigation, next.dependencies);
  assert.equal(report.reason, 'unexpected_selected_tab');
  assert.equal(next.calls.some(call => ['navigate', 'release'].includes(call[0])), false);
});

test('consumed or malformed action-status preflight stops before either navigation', async () => {
  for (const action of [{ protocol: 1, status: 'ok', consumed: true }, { protocol: 1, status: 'ok' }]) {
    const h = harness({ async actionStatus() { return action; } });
    const report = await runTermuxSmoke(navigation, h.dependencies);
    assert.equal(report.status, 'blocked');
    assert.equal(report.claimState, 'retained');
    assert.equal(h.calls.some(call => ['navigate', 'release'].includes(call[0])), false);
  }
});

test('uncertain claim outcome is not auto-released or called unowned', async () => {
  const h = harness({ async claim() { throw new Error('response lost'); } });
  const report = await runTermuxSmoke(options, h.dependencies);
  assert.equal(report.status, 'uncertain');
  assert.equal(report.claimState, 'unknown');
  assert.equal(h.calls.some(call => ['inspect', 'release'].includes(call[0])), false);
});

test('uncertain Reports action is never retried, followed by Home, or released', async () => {
  const h = harness();
  let requests = 0;
  h.controller.navigate = async () => { requests++; throw new Error('SYNTHETIC_PRIVATE_RECORD'); };
  const report = await runTermuxSmoke(navigation, h.dependencies);
  assert.equal(report.status, 'uncertain');
  assert.equal(report.phase, 'reports_navigation');
  assert.equal(report.claimState, 'retained');
  assert.equal(requests, 1);
  assert.equal(h.calls.some(call => call[0] === 'release'), false);
});

test('post-Reports observation failure stops before return-Home input', async () => {
  const h = harness();
  let observations = 0;
  h.controller.inspect = async () => {
    if (observations++) throw new Error('private path /synthetic-private/account');
    return h.snapshot('home');
  };
  const report = await runTermuxSmoke(navigation, h.dependencies);
  assert.equal(report.status, 'uncertain');
  assert.equal(report.phase, 'reports_observation');
  assert.equal(h.calls.filter(call => call[0] === 'navigate').length, 1);
  assert.equal(h.calls.some(call => call[0] === 'release'), false);
});

test('release failure after success remains uncertain ownership', async () => {
  const h = harness({ async release() { throw new Error('lost response'); } });
  const report = await runTermuxSmoke(options, h.dependencies);
  assert.equal(report.status, 'uncertain');
  assert.equal(report.claimState, 'unknown');
  assert.equal(report.checks.released, false);
});

test('uncertain return-Home never retries or releases after verified Reports', async () => {
  const h = harness();
  const navigate = h.controller.navigate;
  let requests = 0;
  h.controller.navigate = async (...args) => {
    requests++;
    if (args[2] === 'home') throw new Error('SYNTHETIC_PRIVATE_DEVICE');
    return navigate(...args);
  };
  const report = await runTermuxSmoke(navigation, h.dependencies);
  assert.equal(report.status, 'uncertain');
  assert.equal(report.phase, 'home_navigation');
  assert.equal(report.checks.reportsVerified, true);
  assert.equal(report.checks.homeVerified, false);
  assert.equal(requests, 2);
  assert.equal(h.calls.some(call => call[0] === 'release'), false);
});

test('unconfirmed release and malformed owner status are never called success', async () => {
  const h = harness({ async release() { return { status: 'ok', released: 'different_session' }; } });
  const report = await runTermuxSmoke(options, h.dependencies);
  assert.equal(report.status, 'uncertain');
  assert.equal(report.reason, 'release_not_confirmed');
  const malformed = harness();
  const status = await malformed.controller.status();
  delete status.owner;
  malformed.calls.length = 0;
  malformed.controller.status = async () => status;
  const blocked = await runTermuxSmoke(options, malformed.dependencies);
  assert.equal(blocked.reason, 'unexpected_status_protocol');
  assert.equal(malformed.calls.some(call => call[0] === 'claim'), false);
});

test('reports omit private profiles, account trees, identifiers and raw exception messages', async () => {
  const success = await runTermuxSmoke(navigation, harness().dependencies);
  const failure = await runTermuxSmoke(options, harness({ async identity() {
    throw new Error(`${options.profilePath} SYNTHETIC_PRIVATE_CHILD SYNTHETIC_PRIVATE_DEVICE`);
  } }).dependencies);
  for (const report of [success, failure]) {
    const text = JSON.stringify(report);
    for (const secret of [options.profilePath, options.session, navigation.actionPrefix,
      'SYNTHETIC_PRIVATE_CHILD', 'SYNTHETIC_PRIVATE_RECORD', 'SYNTHETIC_PRIVATE_DEVICE', '987654']) {
      assert.equal(text.includes(secret), false);
    }
    assert.equal(report.backendCaptured, false);
    assert.equal(report.trackingWrites, false);
  }
});

test('invalid CLI arguments are sanitized without any device access', async () => {
  const output = [];
  const log = console.log;
  console.log = value => output.push(value);
  try {
    const report = await main(['--unknown-secret-flag', 'SYNTHETIC_PRIVATE_DATA']);
    assert.equal(report.reason, 'invalid_arguments');
    assert.equal(output.join('').includes('SYNTHETIC_PRIVATE_DATA'), false);
  } finally { console.log = log; }
});
