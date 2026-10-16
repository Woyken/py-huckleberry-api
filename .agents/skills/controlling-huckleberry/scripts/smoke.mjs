// Unsaved-form test only. Never invoke inside a backend observation window.
import assert from 'node:assert/strict';
import { parseArgs } from 'node:util';
import { withHuckleberry, keypad } from './ui.mjs';

const { values } = parseArgs({ options: { session: { type: 'string' } } });
const passed = [];
try {
  await withHuckleberry(values.session, async (page) => {
    if (await page.locator('ion-modal, ion-alert, ion-popover').filter({ visible: true }).count()) {
      throw new Error('Smoke test requires Home with no open overlay. Refusing to dismiss existing work.');
    }
    await page.locator('#tab-button-home.tab-selected').waitFor();
    const previous = await page.getByTestId('home_lastBottle').innerText();
    passed.push('Started on Home');
    await page.getByTestId('home_bottle').click();
    await page.getByTestId('addFeed_header').waitFor({ state: 'visible' });
    await page.getByTestId('addFeed_bottle_units_ml').click();
    await page.locator('#feed_bottle_set_amount').click();
    assert.equal((await keypad(page, '60')).amount, '60 ml');
    passed.push('Custom keypad set and verified 60 ml');
    const text = 'UNSAVED AGENT INPUT TEST: żółw 👶';
    const notes = page.getByTestId('addFeed_bottle_notes').locator('textarea');
    await notes.fill(text);
    assert.equal(await notes.inputValue(), text);
    passed.push('Unicode/emoji typed without Android IME helper');
    await page.getByTestId('addFeed_close').click();
    await page.getByTestId('addFeed_header').waitFor({ state: 'hidden' });
    assert.equal(await page.getByTestId('home_lastBottle').innerText(), previous);
    passed.push('Cancelled form; previous bottle summary unchanged');
    await page.locator('#tab-button-stat-tabs').click();
    await page.locator('#tab-button-stat-tabs.tab-selected').waitFor();
    await page.getByTestId('statTabs_header_tab_list').click();
    await page.getByTestId('listView').waitFor({ state: 'visible' });
    passed.push('Reports List opened');
    await page.locator('#tab-button-home').click();
    await page.locator('#tab-button-home.tab-selected').waitFor();
    passed.push('Returned Home');
  });
  console.log(JSON.stringify({ passed, recordSaved: false, sessionStillClaimed: values.session }, null, 2));
} catch (error) {
  console.error(JSON.stringify({ passed, error: error.message, guidance: 'Inspect UI before recovery. No automatic cleanup/save/release was attempted.' }));
  process.exitCode = 1;
}
