// Exercise the compatibility patch in the supplied compiled WeChat client.
// This checks request construction; it does not run WeChat's UI or authorization.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync(path.join(__dirname, '../mp-weixin/common/vendor.js'), 'utf8');
const begin = source.indexOf('function request(_ref)');
const end = source.indexOf('return requestRes;\n}', begin);
assert(begin >= 0 && end > begin, 'Compiled request module was not found');
const saved = new Map(), sent = [];
let outcome = 'network-failure', loginId = 1;
const store = {default: {state: {token: 'fixture-user-a'}, commit() {}}};
const uni = {
  getStorage() {}, getStorageSync(key) {return saved.get(key)},
  setStorageSync(key, value) {saved.set(key, value)}, removeStorageSync(key) {saved.delete(key)},
  request(value) {
    sent.push(JSON.parse(JSON.stringify(value.data)));
    if (value.url.endsWith('/user/user/login')) { value.success({statusCode: 200, data: {code: 1, data: {id: loginId}}}); return; }
    if (outcome === 'network-failure') {value.fail({data: 'fixture disconnect'})}
    else {value.success({statusCode: outcome === 'system-failure' ? 503 : (outcome === 'expired-token' ? 401 : 200), data: {code: outcome === 'success' ? 1 : 0, data: {id: 1}}})}
  }
};
const context = vm.createContext({uni, _store: store, _env: {baseUrl: 'http://127.0.0.1:8081'}, Promise, Object, Date, Math});
vm.runInContext(source.slice(begin, end + 'return requestRes;\n}'.length), context);
const submit = params => context.request({url: '/user/order/submit', method: 'POST', params});
(async () => {
  await assert.rejects(submit({addressBookId: 1, estimatedDeliveryTime: '2026-10-12 11:30'}));
  const first = sent[0]; assert(/^[A-Za-z0-9_-]{8,64}$/.test(first.requestKey));
  outcome = 'success'; await submit({addressBookId: 2, estimatedDeliveryTime: '2026-10-12 11:31'});
  assert.deepStrictEqual(sent[1], first); assert(!saved.has('sky-checkout-pending-fixture-user-a'));
  await submit({addressBookId: 1}); assert.notStrictEqual(sent[2].requestKey, first.requestKey);
  outcome = 'network-failure'; await assert.rejects(submit({addressBookId: 1}));
  const old = sent[3].requestKey;
  store.default.state.token = 'fixture-user-b'; outcome = 'business-failure';
  await assert.rejects(submit({addressBookId: 2}));
  assert.notStrictEqual(sent[4].requestKey, old); assert(!saved.has('sky-checkout-pending-fixture-user-b'));
  await context.request({url: '/user/user/login', method: 'POST', params: {code: 'local-demo'}});
  outcome = 'network-failure'; await assert.rejects(submit({addressBookId: 1}));
  const stable = sent[sent.length - 1];
  outcome = 'system-failure'; await assert.rejects(submit({addressBookId: 2})); assert.deepStrictEqual(sent[sent.length - 1], stable);
  outcome = 'expired-token'; await assert.rejects(submit({addressBookId: 3}));
  store.default.state.token = 'fixture-renewed-token';
  await context.request({url: '/user/user/login', method: 'POST', params: {code: 'local-demo'}});
  outcome = 'success'; await submit({addressBookId: 4}); assert.deepStrictEqual(sent[sent.length - 1], stable);
  outcome = 'network-failure'; await assert.rejects(submit({addressBookId: 5}));
  const userOne = sent[sent.length - 1]; loginId = 2;
  await context.request({url: '/user/user/login', method: 'POST', params: {code: 'local-second'}});
  await assert.rejects(submit({addressBookId: 6})); assert.notStrictEqual(sent[sent.length - 1].requestKey, userOne.requestKey);
  loginId = 1; await context.request({url: '/user/user/login', method: 'POST', params: {code: 'local-demo'}});
  outcome = 'success'; await submit({addressBookId: 7}); assert.deepStrictEqual(sent[sent.length - 1], userOne);
  console.log('PASS 8 WeChat protocol checks: key format, exact retry payload, new order, user isolation, business rejection cleanup, 503 retention, token renewal/401 retention, per-user draft restoration');
})().catch(error => {console.error(error); process.exitCode = 1});
