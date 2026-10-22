import test from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import {
  ControlDriftError, controlFor, getPage, newActionId, PageObject,
  pageModel, pageSummaries, resolveControl,
} from '../lib/pages.mjs';

const exec = promisify(execFile);
const cli = fileURLToPath(new URL('../cli.mjs', import.meta.url));

const snapshot = {
  selectedTab: 'home',
  childVerified: true,
  truncated: false,
  guardComplete: true,
  nodes: [
    { id: 'tab-button-home', text: 'Home', path: 'root/0/0', visible: true },
    { id: 'home_sleep', text: 'Sleep', path: 'root/0/1', visible: true },
    { id: '', text: 'Nursing', path: 'root/0/2', visible: true },
    { id: '', text: 'Nursing', path: 'root/0/3', visible: true },
    { id: 'hidden', text: 'Bottle', path: 'root/0/4', visible: false },
  ],
};

function fakeController(current = snapshot) {
  const calls = [];
  return {
    calls,
    async inspect(session) {
      calls.push(['inspect', session]);
      return current;
    },
    async act(session, request) {
      calls.push(['act', session, request]);
      return { status: 'dispatched', actionId: request.actionId, trackingWrites: false };
    },
    async release(session) {
      calls.push(['release', session]);
      return { status: 'released', session };
    },
  };
}

test('page catalog is listed with stable identifiers', () => {
  const pages = pageSummaries();
  assert.equal(pages.length, 39);
  assert.deepEqual(pages[0], {
    id: 'app-shell',
    title: 'Bottom navigation and persistent shell',
    entry: null,
    controls: 8,
  });
  assert.equal(getPage('home').title, 'Home');
  assert.throws(() => getPage('missing-page'), (error) => {
    assert.equal(error.details.code, 'unknown_page');
    assert.ok(error.details.pages.includes('home'));
    return true;
  });
});

test('every catalog route produces a page model', () => {
  const empty = {
    selectedTab: 'home', childVerified: false, truncated: false, guardComplete: true, nodes: [],
  };
  for (const summary of pageSummaries()) {
    const model = pageModel(getPage(summary.id), empty);
    assert.equal(model.controls.length, summary.controls, summary.id);
    assert.equal(model.presentCount, 0, summary.id);
  }
});

test('controls can be found by name or catalog selector value', () => {
  const home = getPage('home');
  assert.equal(controlFor(home, 'Sleep card').name, 'Sleep card');
  assert.equal(controlFor(home, 'home_sleep').name, 'Sleep card');
  assert.throws(() => controlFor(home, 'missing'), ControlDriftError);
});

test('page model reports live presence without pretending a page is current', () => {
  const model = pageModel(getPage('home'), snapshot);
  assert.equal(model.page.id, 'home');
  assert.equal(model.entryPresent, true);
  assert.equal(model.screen.childVerified, true);
  assert.ok(model.presentCount > 0);
  assert.ok(model.missing.includes('Bottle card'));
  const sleep = model.controls.find((control) => control.name === 'Sleep card');
  assert.equal(sleep.ref, 'home.c1');
  assert.equal(sleep.present, true);
  assert.equal(sleep.unique, true);
  assert.equal(sleep.selectors.find((selector) => selector.kind === 'id').matches, 1);
});

test('selector resolution prefers a unique id and refuses drift or ambiguity', () => {
  const home = getPage('home');
  const resolved = resolveControl(home, 'Sleep card', snapshot);
  assert.equal(resolved.selector.kind, 'id');
  assert.deepEqual(resolved.target, { id: 'home_sleep', text: '', path: '' });

  assert.throws(() => resolveControl(home, 'Bottle card', snapshot), (error) => {
    assert.equal(error.details.code, 'control_drifted');
    assert.equal(error.details.page, 'home');
    assert.equal(error.details.control, 'Bottle card');
    return true;
  });

  assert.throws(() => resolveControl(home, 'Nursing card', snapshot), (error) => {
    assert.equal(error.details.code, 'control_ambiguous');
    return true;
  });
});

test('page object resolves controls and dispatches one declared action', async () => {
  const controller = fakeController();
  const page = await PageObject.open(controller, 'session-1', 'home');
  const result = await page.control('Sleep card').click({ actionId: 'page-test-action-1' });

  assert.equal(result.page, 'home');
  assert.equal(result.ref, 'home.c1');
  assert.equal(result.control, 'Sleep card');
  assert.equal(result.selector, 'id');
  const actions = controller.calls.filter(([kind]) => kind === 'act');
  assert.equal(actions.length, 1);
  assert.deepEqual(actions[0][2], {
    id: 'home_sleep', text: '', path: '', action: 'click', value: '',
    actionId: 'page-test-action-1',
  });
  assert.throws(() => page.model(), /call observe|call refresh/);
});

test('page object exposes snapshot-scoped refs and invalidates them after use', async () => {
  const controller = fakeController();
  const page = await PageObject.open(controller, 'session-1', 'home');
  const model = await page.observe();
  const sleep = model.controls.find((control) => control.name === 'Sleep card');
  assert.equal(sleep.ref, 'home.c1');

  const result = await page.act(sleep.ref, 'click', { actionId: 'page-test-action-3' });
  assert.equal(result.ref, 'home.c1');
  assert.throws(() => page.control(sleep.ref), (error) => {
    assert.equal(error.details.code, 'stale_ref');
    return true;
  });

  assert.deepEqual(page.close(), { closed: true, released: false });
  assert.throws(() => page.model(), /call observe|call refresh/);
});

test('rawAct is an explicit escape hatch and close does not release the claim', async () => {
  const controller = fakeController();
  const page = await PageObject.open(controller, 'session-1', 'home');
  await assert.rejects(
    () => page.rawAct({ actionId: 'page-test-raw-1', action: 'click', id: 'home_sleep' }),
    /acknowledgeUnmapped/,
  );
  const result = await page.rawAct({
    acknowledgeUnmapped: true, actionId: 'page-test-raw-2', action: 'click', id: 'home_sleep',
  });
  assert.equal(result.status, 'dispatched');
  assert.equal(controller.calls.filter(([kind]) => kind === 'release').length, 0);

  const released = await page.release();
  assert.equal(released.status, 'released');
  assert.deepEqual(controller.calls.find(([kind]) => kind === 'release'), ['release', 'session-1']);
});

test('page object rejects actions that the catalog does not declare', async () => {
  const controller = fakeController();
  const page = await PageObject.open(controller, 'session-1', 'home');
  await assert.rejects(
    () => page.act('Sleep card', 'set-text', { value: 'x', actionId: 'page-test-action-2' }),
    (error) => {
      assert.equal(error.details.code, 'action_not_declared');
      assert.equal(error.details.action, 'set-text');
      return true;
    },
  );
  assert.equal(controller.calls.filter(([kind]) => kind === 'act').length, 0);
});

test('pages command lists the catalog without a profile or device', async () => {
  const { stdout } = await exec(process.execPath, [cli, 'pages']);
  const result = JSON.parse(stdout);
  assert.equal(result.count, 39);
  assert.equal(result.bridgeProtocol, 2);
  assert.equal(result.pages[1].id, 'home');
});

test('action ids are unique and valid identifiers', () => {
  const first = newActionId('page');
  const second = newActionId('page');
  assert.match(first, /^page-[a-z0-9]+-[a-f0-9]{12}$/);
  assert.notEqual(first, second);
});
