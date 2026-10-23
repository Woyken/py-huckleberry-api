import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const android = new URL('../android/', import.meta.url);

test('debug bridge does not request networking, screenshots or gestures', async () => {
  const manifest = await readFile(new URL('AndroidManifest.xml', android), 'utf8');
  const service = await readFile(new URL('res/xml/bridge_service.xml', android), 'utf8');
  assert.doesNotMatch(manifest, /uses-permission|android:debuggable="true"/);
  assert.match(manifest, /android:permission="android.permission.DUMP"/);
  assert.match(manifest, /android.permission.BIND_ACCESSIBILITY_SERVICE/);
  assert.match(service, /canRetrieveWindowContent="true"/);
  assert.doesNotMatch(service, /canPerformGestures|canTakeScreenshot/);
});

test('device implementation retains semantic actions and final replay boundary', async () => {
  const source = await readFile(new URL('src/dev/local/huckleberry/a11yprobe/ProbeService.java', android), 'utf8');
  assert.doesNotMatch(source, /dispatchGesture|takeScreenshot|injectInputEvent|GLOBAL_ACTION_HOME/);
  assert.match(source, /ACTION_SET_TEXT/);
  assert.match(source, /ACTION_SCROLL_FORWARD/);
  assert.match(source, /GLOBAL_ACTION_BACK/);
  assert.match(source, /tab-button-home/);
  assert.match(source, /tab-button-stat-tabs/);
  assert.ok(source.indexOf('state.consume(actionId)') < source.indexOf('target.performAction('));
  assert.match(source, /result.incomplete/);
  assert.match(source, /target_not_unique/);
  assert.match(source, /expectation_failed/);
  assert.match(source, /result\.form/);
  assert.match(source, /AccessibilityNodeInfo\.obtain\(node\)/);
  assert.doesNotMatch(source, /childVerified|child_not_verified|resetChild/);
  const state = await readFile(new URL('src/dev/local/huckleberry/a11yprobe/BridgeState.java', android), 'utf8');
  assert.match(state, /put\("protocol", 3\)/);
  assert.match(state, /semanticActions/);
  assert.match(state, /bridge-protocol1/);
  const provider = await readFile(new URL('src/dev/local/huckleberry/a11yprobe/ProbeProvider.java', android), 'utf8');
  assert.match(provider, /Binder.getCallingUid\(\) != 2000/);
  assert.match(provider, /keys.equals\(request.keySet\(\)\)/);
  assert.doesNotMatch(provider, /"protocol":1/);
});
