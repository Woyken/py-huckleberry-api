import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { ACTIONS, PROTOCOL } from '../lib/protocol.mjs';

const catalog = JSON.parse(await readFile(new URL('../controls.json', import.meta.url), 'utf8'));
const allowedSelectorKeys = new Set(['id', 'text', 'path']);

test('control catalog is versioned, machine-readable and selector-bounded', () => {
  assert.equal(catalog.schemaVersion, 1);
  assert.equal(catalog.package, 'com.huckleberry_labs.app');
  assert.equal(catalog.bridgeProtocol, PROTOCOL);
  assert.ok(catalog.capturedAt);
  assert.ok(Array.isArray(catalog.routes) && catalog.routes.length >= 30);
  assert.ok(Array.isArray(catalog.limitations) && catalog.limitations.length > 0);
  const ids = new Set();
  for (const route of catalog.routes) {
    assert.match(route.id, /^[a-z][a-z0-9-]*$/);
    assert.ok(!ids.has(route.id), `duplicate route: ${route.id}`);
    ids.add(route.id);
    assert.ok(route.title);
    assert.ok(Array.isArray(route.controls));
    for (const control of route.controls) {
      assert.ok(control.name, `${route.id}: control name`);
      assert.ok(Array.isArray(control.selectors), `${route.id}/${control.name}: selectors`);
      assert.ok(Array.isArray(control.actions), `${route.id}/${control.name}: actions`);
      if (control.mutates !== undefined) assert.equal(typeof control.mutates, 'boolean');
      for (const selector of control.selectors) {
        const keys = Object.keys(selector);
        assert.equal(keys.length, 1, `${route.id}/${control.name}: one selector kind`);
        assert.ok(allowedSelectorKeys.has(keys[0]), `${route.id}/${control.name}: selector kind`);
        assert.ok(typeof selector[keys[0]] === 'string' && selector[keys[0]].length > 0);
        if (selector.path) {
          assert.match(selector.path, /^root(?:\/\d{1,4}){0,60}$/, `${route.id}/${control.name}: path`);
          assert.ok(!selector.path.includes('...'), `${route.id}/${control.name}: literal path`);
        }
      }
      for (const action of control.actions) assert.ok(Object.hasOwn(ACTIONS, action), `${route.id}/${control.name}: action ${action}`);
    }
  }
});

test('control catalog covers the API-relevant feature families', () => {
  const ids = new Set(catalog.routes.map(route => route.id));
  for (const id of ['home', 'settings', 'child', 'reports-list', 'reports-summary', 'sleep-form',
    'bottle-form', 'nursing-form', 'solids-picker', 'diaper-form', 'potty-form', 'pump-form',
    'medicine-form', 'growth-form', 'temperature-form', 'activity-form', 'milestones',
    'customize-home']) assert.ok(ids.has(id), `missing route ${id}`);
  const names = new Set(catalog.routes.flatMap(route => route.controls.map(control => control.name)));
  for (const name of ['Sleep card', 'Bottle type', 'Temperature Celsius', 'Units metric',
    'Add photo', 'Diaper rash', 'Left amount', 'Bottle type', 'Save']) {
    assert.ok(names.has(name), `missing control ${name}`);
  }
});

test('catalog does not embed capture paths or evidence files', () => {
  const text = JSON.stringify(catalog);
  assert.doesNotMatch(text, /\/tmp\/opencode|\/data\/data|\.json\/|trees\//);
});
