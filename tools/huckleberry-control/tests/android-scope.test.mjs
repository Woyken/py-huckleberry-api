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

test('device implementation retains narrow navigation and final replay boundary', async () => {
  const source = await readFile(new URL('src/dev/local/huckleberry/a11yprobe/ProbeService.java', android), 'utf8');
  assert.doesNotMatch(source, /ACTION_SET_TEXT|dispatchGesture|takeScreenshot|injectInputEvent/);
  assert.match(source, /tab-button-home/);
  assert.match(source, /tab-button-stat-tabs/);
  assert.ok(source.indexOf('state.consume(actionId)') < source.indexOf('target.performAction('));
  assert.match(source, /result.incomplete/);
  const provider = await readFile(new URL('src/dev/local/huckleberry/a11yprobe/ProbeProvider.java', android), 'utf8');
  assert.match(provider, /Binder.getCallingUid\(\) != 2000/);
  assert.match(provider, /keys.equals\(request.keySet\(\)\)/);
});
