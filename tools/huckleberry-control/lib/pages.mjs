import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';

const CATALOG = JSON.parse(readFileSync(new URL('../controls.json', import.meta.url), 'utf8'));
const SELECTOR_ORDER = { id: 0, text: 1, path: 2 };
const FALLBACK = {
  commands: ['inspect', 'act'],
  note: 'Use the raw commands to discover a stable selector, then propose a catalog update. Do not guess or use coordinates.',
};

export function catalog() {
  return CATALOG;
}

export function pageSummaries() {
  return CATALOG.routes.map(({ id, title, entry, controls, limitations }) => ({
    id, title, entry: entry ?? null, controls: controls.length,
    ...(limitations ? { limitations } : {}),
  }));
}

export function getPage(id) {
  const page = CATALOG.routes.find((candidate) => candidate.id === id);
  if (!page) {
    const error = new Error(`Unknown page: ${id}; run pages to list known routes`);
    error.details = { code: 'unknown_page', page: id, pages: CATALOG.routes.map((route) => route.id) };
    throw error;
  }
  return page;
}

function selectorEntries(control) {
  return control.selectors
    .map((selector) => {
      const kind = Object.keys(selector)[0];
      return { kind, value: selector[kind] };
    })
    .sort((left, right) => SELECTOR_ORDER[left.kind] - SELECTOR_ORDER[right.kind]);
}

function selectorMatches(node, selector) {
  if (!node.visible) return false;
  if (selector.kind === 'id') return node.id === selector.value;
  if (selector.kind === 'text') return node.text === selector.value;
  if (selector.kind === 'path') return node.path === selector.value;
  return false;
}

function matchCounts(control, snapshot) {
  return selectorEntries(control).map((selector) => ({
    ...selector,
    matches: (snapshot.nodes ?? []).filter((node) => selectorMatches(node, selector)).length,
  }));
}

function selectorValues(control) {
  return control.selectors.map((selector) => Object.values(selector)[0]);
}

function targetFor(selector) {
  return {
    id: selector.kind === 'id' ? selector.value : '',
    text: selector.kind === 'text' ? selector.value : '',
    path: selector.kind === 'path' ? selector.value : '',
  };
}

export class ControlDriftError extends Error {
  constructor(message, details) {
    super(message);
    this.name = 'ControlDriftError';
    this.details = { code: 'control_drifted', fallback: FALLBACK, ...details };
  }
}

export function controlFor(page, nameOrSelector) {
  const key = String(nameOrSelector ?? '').trim().toLowerCase();
  const matches = page.controls.filter((control) =>
    control.name.toLowerCase() === key ||
    selectorValues(control).some((value) => String(value).toLowerCase() === key));
  if (matches.length === 0) {
    throw new ControlDriftError(`Unknown control ${nameOrSelector} on page ${page.id}`, {
      page: page.id, control: nameOrSelector,
    });
  }
  if (matches.length > 1) {
    throw new ControlDriftError(`Ambiguous control ${nameOrSelector} on page ${page.id}`, {
      code: 'control_ambiguous', page: page.id, control: nameOrSelector,
    });
  }
  return matches[0];
}

export function resolveControl(page, nameOrSelector, snapshot) {
  if (!snapshot || typeof snapshot !== 'object') throw new Error('A live snapshot is required');
  const control = controlFor(page, nameOrSelector);
  const selectors = matchCounts(control, snapshot);
  const unique = selectors.find((selector) => selector.matches === 1);
  if (unique) {
    return { control, selector: unique, target: targetFor(unique) };
  }
  const ambiguous = selectors.find((selector) => selector.matches > 1);
  if (ambiguous) {
    throw new ControlDriftError(`Control ${control.name} is ambiguous on page ${page.id}`, {
      code: 'control_ambiguous', page: page.id, control: control.name, selectors,
    });
  }
  throw new ControlDriftError(`Control ${control.name} is not present on page ${page.id}`, {
    page: page.id, control: control.name, selectors,
  });
}

function entryPresent(page, snapshot) {
  if (!page.entry?.selectors?.length) return null;
  return page.entry.selectors.some((selector) => {
    const kind = Object.keys(selector)[0];
    return (snapshot.nodes ?? []).some((node) => selectorMatches(node, { kind, value: selector[kind] }));
  });
}

export function pageModel(page, snapshot) {
  if (!snapshot || typeof snapshot !== 'object') throw new Error('A live snapshot is required');
  const controls = page.controls.map((control, index) => {
    const selectors = matchCounts(control, snapshot);
    return {
      ref: `${page.id}.c${index + 1}`,
      name: control.name,
      actions: control.actions,
      ...(control.mutates === undefined ? {} : { mutates: control.mutates }),
      ...(control.warn ? { warn: control.warn } : {}),
      ...(control.notes ? { notes: control.notes } : {}),
      selectors,
      present: selectors.some((selector) => selector.matches > 0),
      unique: selectors.some((selector) => selector.matches === 1),
    };
  });
  return {
    page: {
      id: page.id,
      title: page.title,
      entry: page.entry ?? null,
      ...(page.limitations ? { limitations: page.limitations } : {}),
    },
    screen: {
      selectedTab: snapshot.selectedTab,
      truncated: snapshot.truncated,
      guardComplete: snapshot.guardComplete,
    },
    entryPresent: entryPresent(page, snapshot),
    presentCount: controls.filter((control) => control.present).length,
    controls,
    missing: controls.filter((control) => !control.present).map((control) => control.name),
    fallback: FALLBACK,
  };
}

export function newActionId(prefix = 'page') {
  return `${prefix}-${Date.now().toString(36)}-${randomUUID().replaceAll('-', '').slice(0, 12)}`;
}

export class PageObject {
  constructor(definition, controller, session, snapshot) {
    this.definition = definition;
    this.controller = controller;
    this.session = session;
    this.snapshot = snapshot ?? null;
    this.refs = new Map();
    if (snapshot) this.#indexRefs();
  }

  static async open(controller, session, pageId) {
    const definition = getPage(pageId);
    const snapshot = await controller.inspect(session);
    return new PageObject(definition, controller, session, snapshot);
  }

  #indexRefs() {
    this.refs = new Map(this.definition.controls.map((control, index) =>
      [`${this.definition.id}.c${index + 1}`, control]));
  }

  #resolveName(nameOrRef) {
    if (this.refs.has(nameOrRef)) return this.refs.get(nameOrRef).name;
    if (/^[a-z][a-z0-9-]*\.c\d+$/.test(nameOrRef)) {
      throw new ControlDriftError(`Stale or foreign control ref ${nameOrRef} on page ${this.definition.id}`, {
        code: 'stale_ref', page: this.definition.id, ref: nameOrRef,
      });
    }
    return nameOrRef;
  }

  async refresh() {
    this.snapshot = await this.controller.inspect(this.session);
    this.#indexRefs();
    return this;
  }

  async observe() {
    await this.refresh();
    return this.model();
  }

  model() {
    if (!this.snapshot) throw new Error('Page object has no live snapshot; call observe() or refresh() first');
    return pageModel(this.definition, this.snapshot);
  }

  control(nameOrSelector) {
    return new ControlHandle(this, controlFor(this.definition, this.#resolveName(String(nameOrSelector))));
  }

  async act(nameOrSelector, action, { value = '', actionId = newActionId(), snapshot } = {}) {
    if (!action) throw new Error('An action is required');
    const name = this.#resolveName(String(nameOrSelector));
    const current = snapshot ?? this.snapshot ?? await this.controller.inspect(this.session);
    const resolved = resolveControl(this.definition, name, current);
    if (!resolved.control.actions.includes(action)) {
      throw new ControlDriftError(`Action ${action} is not declared for ${resolved.control.name} on page ${this.definition.id}`, {
        code: 'action_not_declared', page: this.definition.id, control: resolved.control.name, action,
      });
    }
    const ref = `${this.definition.id}.c${this.definition.controls.indexOf(resolved.control) + 1}`;
    try {
      const reply = await this.controller.act(this.session, {
        ...resolved.target, action, value, actionId,
      });
      return {
        ...reply,
        page: this.definition.id,
        ref,
        control: resolved.control.name,
        selector: resolved.selector.kind,
      };
    } catch (error) {
      if (error && typeof error === 'object' && !error.actionId) error.actionId = actionId;
      throw error;
    } finally {
      this.snapshot = null;
      this.refs.clear();
    }
  }

  close() {
    this.snapshot = null;
    this.refs.clear();
    return { closed: true, released: false };
  }

  async release() {
    this.close();
    return this.controller.release(this.session);
  }

  async rawSnapshot() {
    return this.controller.inspect(this.session);
  }

  async rawAct(request) {
    if (!request || request.acknowledgeUnmapped !== true) {
      const error = new Error('rawAct requires acknowledgeUnmapped: true; prefer observe() and act(ref)');
      error.details = { code: 'raw_act_unacknowledged', fallback: FALLBACK };
      throw error;
    }
    const { acknowledgeUnmapped, ...action } = request;
    try {
      return await this.controller.act(this.session, action);
    } finally {
      this.close();
    }
  }

  toJSON() {
    return this.model();
  }
}

export class ControlHandle {
  constructor(page, control) {
    this.page = page;
    this.control = control;
  }

  click(options = {}) {
    return this.page.act(this.control.name, 'click', options);
  }

  longClick(options = {}) {
    return this.page.act(this.control.name, 'long-click', options);
  }

  setText(value, options = {}) {
    return this.page.act(this.control.name, 'set-text', { ...options, value });
  }

  scroll(direction, options = {}) {
    if (direction !== 'forward' && direction !== 'backward') throw new Error('direction must be forward or backward');
    return this.page.act(this.control.name, `scroll-${direction}`, options);
  }

  focus(options = {}) {
    return this.page.act(this.control.name, 'focus', options);
  }

  select(options = {}) {
    return this.page.act(this.control.name, 'select', options);
  }

  act(action, options = {}) {
    return this.page.act(this.control.name, action, options);
  }

  toJSON() {
    return {
      name: this.control.name,
      selectors: this.control.selectors,
      actions: this.control.actions,
      ...(this.control.mutates === undefined ? {} : { mutates: this.control.mutates }),
      ...(this.control.warn ? { warn: this.control.warn } : {}),
    };
  }
}
