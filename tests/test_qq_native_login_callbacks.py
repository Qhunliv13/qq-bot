"""Keep the observed explicit login listener contract inert without native QQ."""
from test_qq_native_login import run_node


def test_all_declared_callbacks_are_explicit_and_inert(tmp_path):
    """Extra native notifications cannot leak payloads or manufacture authentication."""
    run_node(tmp_path, r"""
const f = fixture();
const c = createLoginController(f.options);
c.initialize();
const expected = ['onLoginConnected','onLoginDisConnected','onLoginConnecting',
  'onQRCodeGetPicture','onQRCodeLoginPollingStarted','onQRCodeSessionUserScaned',
  'onQRCodeLoginSucceed','onQRCodeSessionFailed','onLoginFailed',
  'onLogoutSucceed','onLogoutFailed','onUserLoggedIn','onQRCodeSessionQuickLoginFailed',
  'onPasswordLoginFailed','OnConfirmUnusualDeviceFailed','onQQLoginNumLimited',
  'onLoginState','onLoginRecordUpdate'];
assert.deepEqual(Object.keys(f.listener).sort(), expected.sort());
const extra = expected.filter(name => !Object.hasOwn(c.getSnapshot().native_callback_counts, name));
assert.equal(extra.length, 11);
const before = c.getSnapshot();
const hostile = Object.defineProperty({}, 'identity', {get() { throw Error(secret); }});
for (const name of extra) {
  assert.equal(typeof f.listener[name], 'function');
  assert.equal(f.listener[name](hostile), undefined);
}
assert.deepEqual(c.getSnapshot(), before);
assert.equal(c.getIdentity(), null);
assert(!JSON.stringify(c.getSnapshot()).includes(secret));
c.dispose();
""")
