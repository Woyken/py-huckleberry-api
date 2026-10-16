import { writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { endpoint, targets, validateAttachment, withSession, adb, RUNTIME_DIR, delay } from './runtime.mjs';

export async function withHuckleberry(session, action) {
  return withSession(session, async (state) => {
    await validateAttachment(state);
    let playwright;
    try { playwright = createRequire(join(RUNTIME_DIR, 'package.json'))('playwright-core'); }
    catch { throw new Error('Missing pinned runtime dependency. Run this CLI with setup (no browser download), then retry.'); }
    playwright.selectors.setTestIdAttribute('data-qa');
    const browser = await playwright.chromium.connectOverCDP(endpoint(state), { noDefaults: true, timeout: 15000 });
    try {
      // Match the page ID from this app PID's endpoint, not an arbitrary localhost page.
      const appTargets = await targets(state);
      const expected = appTargets.find((target) => target.type === 'page' && /^Huckleberry\b/.test(target.title));
      let page;
      for (const candidate of browser.contexts().flatMap((context) => context.pages())) {
        const cdp = await candidate.context().newCDPSession(candidate);
        try {
          const info = await cdp.send('Target.getTargetInfo');
          if (info.targetInfo.targetId === expected.id) page = candidate;
        } finally { await cdp.detach(); }
        if (page) break;
      }
      if (!page) throw new Error('Huckleberry page target missing after attachment. Retry start; no UI action performed.');
      page.setDefaultTimeout(10000);
      return await action(page, state);
    } finally { await browser.close(); } // Disconnect this CDP client; leave app running.
  });
}

export function locate(page, selector) {
  if (!selector) throw new Error('Specify qa:attribute-value or an explicit CSS selector.');
  return (selector.startsWith('qa:') ? page.getByTestId(selector.slice(3)) : page.locator(selector)).filter({ visible: true });
}

export async function activeRoot(page) {
  const overlays = page.locator('ion-modal, ion-alert, ion-action-sheet, ion-popover').filter({ visible: true });
  return await overlays.count() ? overlays.last() : page.locator('body');
}

export async function inspect(page, selector) {
  const elements = selector ? locate(page, selector) : (await activeRoot(page)).locator('[data-qa], input, textarea, button, .num-key');
  return elements.evaluateAll((nodes) => nodes.filter((node) => node.checkVisibility()).map((node) => ({
    tag: node.tagName.toLowerCase(),
    id: node.id || undefined,
    qa: node.getAttribute('data-qa') || undefined,
    role: node.getAttribute('role') || undefined,
    placeholder: node.getAttribute('placeholder') || undefined,
    // Never include input values or HTML dumps. Notes remain visible via snapshot.
    text: (node.innerText || '').trim().slice(0, 160),
  })).filter((node) => node.qa !== 'app'));
}

export async function snapshot(page) {
  let previous = '';
  let stable = 0;
  // Up to ~2 seconds of settling. A stable snapshot is NOT persistence proof.
  for (let attempt = 0; attempt < 12; attempt++) {
    const current = await page.locator('body').ariaSnapshot();
    const busy = await page.evaluate(() => document.getAnimations().some((animation) =>
      animation.playState === 'running' && animation.effect?.getTiming().iterations !== Infinity
    ));
    stable = current === previous && !busy ? stable + 1 : 0;
    previous = current;
    if (stable >= 3) break;
    await delay(150);
  }
  const name = page.getByTestId('childSelector_name').filter({ visible: true });
  const profile = await name.count() === 1 ? (await name.innerText()).trim() : null;
  // Always include QA controls: partially hidden accessibility trees can otherwise
  // look valid while omitting the active form. No aria attributes are changed.
  const controls = await inspect(page);
  const briefControls = controls.filter((node) => node.qa || node.id).map((node) =>
    `${node.qa ? `qa:${node.qa}` : `#${node.id}`} [${node.tag}] ${node.text.replace(/\s+/g, ' ').slice(0, 80)}`.trim()
  );
  return {
    observedAt: new Date().toISOString(),
    profile,
    source: previous.trim() ? 'accessibility' : 'visible-dom-fallback',
    content: previous.trim() || briefControls,
    ...(previous.trim() ? { controls: briefControls } : {}),
    warning: 'UI evidence only. Cached screens, pending sync, and premium demo figures are not Firebase persistence evidence.',
  };
}

export async function keypad(page, digits) {
  if (!/^\d{1,6}$/.test(digits || '')) throw new Error('keypad requires 1–6 integer digits; this helper is for the bottle ml keypad, not decimals or time pickers.');
  const pad = page.getByTestId('numpad').filter({ visible: true });
  await pad.waitFor({ state: 'visible' }); // A click may return before Ionic presents its overlay.
  if (await pad.count() !== 1 || !/Volume \(ml\)/i.test(await pad.getByTestId('numpad_title').innerText())) {
    throw new Error('Open the bottle volume keypad in mL first. This command does not open or save a tracking record.');
  }
  const display = pad.locator('.numpad-input');
  const backspace = pad.locator('.num-key').filter({ has: page.locator('ion-icon[name="backspace-outline"]') });
  // Clear the actual displayed digits, with a strict bound for unexpected layouts.
  for (let i = 0; i < 12 && (await display.innerText()).trim() !== '0'; i++) await backspace.click();
  if ((await display.innerText()).trim() !== '0') throw new Error('Could not clear keypad; no amount committed. Inspect the dialog.');
  for (const digit of digits) await pad.locator('.num-key').filter({ hasText: new RegExp(`^\\s*${digit}\\s*$`) }).click();
  const expected = String(Number(digits));
  const actual = (await display.innerText()).trim();
  if (actual !== expected) throw new Error(`Keypad shows ${actual}, expected ${expected}; no amount committed.`);
  await page.getByTestId('numpad_button2').click();
  await pad.waitFor({ state: 'hidden' });
  const amount = (await page.locator('#feed_bottle_set_amount').innerText()).trim();
  if (!new RegExp(`^${expected}\\s*ml$`, 'i').test(amount)) throw new Error(`Form amount mismatch: ${amount}. Record not saved.`);
  return { amount, recordSaved: false };
}

export async function checkpoint(page, state, output) {
  if (!output) throw new Error('checkpoint requires --out <path.json>. Store evidence in the current investigation, not the skill.');
  const evidence = {
    ...(await snapshot(page)),
    session: state.session,
    serial: state.serial,
    avd: state.avd,
    deviceTime: await adb(['-s', state.serial, 'shell', 'date', '+%Y-%m-%dT%H:%M:%S%z']),
    deviceTimezone: await adb(['-s', state.serial, 'shell', 'getprop', 'persist.sys.timezone']),
    backendCaptured: false,
  };
  await writeFile(output, JSON.stringify(evidence, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
  return { saved: output, observedAt: evidence.observedAt, backendCaptured: false };
}
