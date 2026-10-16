import test from 'node:test';
import assert from 'node:assert/strict';
import { parseIdentity, verifyWorkProfile } from '../lib/identity.mjs';

const component = 'com.huckleberry_labs.app/.MainActivity';
const activity = `topResumedActivity=ActivityRecord{abc u12 ${component} t123}`;
const focus = `  mCurrentFocus=Window{def u12 ${component}}`;

test('Android 14/17 activity formats correlate an explicit user/package/task', () => {
  assert.deepEqual(parseIdentity(activity, focus, { androidUser: 12 }), { user: 12, component, task: 123 });
  assert.equal(parseIdentity(activity.replace('topResumedActivity=', 'mResumedActivity: '), focus, { androidUser: 12 }).task, 123);
});

test('personal copy, other apps, unknown focus and multiple resumed tasks fail closed', () => {
  for (const [a, w] of [[activity.replace('u12', 'u0'), focus], [activity, focus.replace('u12', 'u0')],
    [activity, 'mCurrentFocus=null'], [activity.replace(component, 'other.app/.MainActivity'), focus],
    [activity + '\n' + activity.replace('t123', 't456'), focus], ['', focus]]) {
    assert.throws(() => parseIdentity(a, w, { androidUser: 12 }));
  }
});

test('work-profile check inspects the enrolled user, not a neighboring block', () => {
  const users = '  UserInfo{0:Owner:13}\n    Type: android.os.usertype.full.SYSTEM\n'
    + '  UserInfo{12:Test:1030}\n    Type: android.os.usertype.profile.MANAGED\n'
    + '  UserInfo{13:Other:10}\n    Type: android.os.usertype.full.SECONDARY\n';
  verifyWorkProfile(users, 12);
  assert.throws(() => verifyWorkProfile(users, 0));
  assert.throws(() => verifyWorkProfile(users, 13));
  assert.throws(() => verifyWorkProfile(users, 99));
});
