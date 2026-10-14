// Execute the actual Vue component's compiled methods with mocked network/storage.
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const ts = require('../project-rjwm-admin-vue-ts/node_modules/typescript');
const crypto = require('crypto');
const source = fs.readFileSync(require('path').join(__dirname, '../project-rjwm-admin-vue-ts/src/views/meal/Customer.vue'), 'utf8');
const script = source.match(/<script lang="ts">([\s\S]*?)<\/script>/)[1];
const compiled = ts.transpileModule(script, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2018}}).outputText;
const saved = new Map(), sent = [], notices = [];
let outcome = 'network';
const context = vm.createContext({exports: {}, require: () => ({default: {extend: value => value}}),
  window: {crypto: crypto.webcrypto},
  localStorage: {getItem: key => saved.get(key) || null, setItem: (key, value) => saved.set(key, value), removeItem: key => saved.delete(key)},
  fetch: async (_, options) => {
    sent.push(JSON.parse(options.body));
    if (outcome === 'network') throw new Error('lost response');
    return {status: outcome === 'system' ? 503 : 200,
      json: async () => ({code: outcome === 'success' ? 1 : 0, data: {id: 1}, msg: 'fixture'})};
  }
});
vm.runInContext(compiled, context);
const component = context.exports.default;
function actor(id) {
  const value = Object.assign(component.data(), {userId: id, token: 'token-' + id, sessionId: 1,
    $message: {success: () => {}, error: () => {}, warning: text => notices.push(text)}});
  for (const [key, method] of Object.entries(component.methods)) value[key] = method.bind(value);
  value.loadCart = value.loadHistory = async () => {};
  return value;
}
(async () => {
  let user = actor('a');
  await user.submit();
  const first = sent[0];
  assert(/^[a-f0-9]{32}$/.test(first.requestKey));
  user.newOrder(); assert.strictEqual(notices.length, 1); assert(user.pendingRequest);
  user = actor('a'); user.restoreDraft(); user.contactName = 'changed';
  outcome = 'system'; await user.submit(); assert.deepStrictEqual(sent[1], first); assert(user.pendingRequest);
  outcome = 'success'; await user.submit(); assert.deepStrictEqual(sent[2], first); assert(user.createdOrder);
  user.newOrder(); await user.submit(); assert.notStrictEqual(sent[3].requestKey, first.requestKey);
  user.newOrder(); outcome = 'business'; await user.submit(); assert.strictEqual(user.pendingRequest, null);
  outcome = 'network'; await user.submit(); const otherPending = sent[5].requestKey;
  user = actor('b'); user.restoreDraft(); assert.strictEqual(user.pendingRequest, null);
  await user.submit(); assert.notStrictEqual(sent[6].requestKey, otherPending);
  console.log('PASS 7 campus client protocol checks: random key, uncertain outcome blocks new key, restore exact payload, 503 retains draft, confirmed success/new order, business rejection cleanup, user isolation');
})().catch(error => {console.error(error); process.exitCode = 1});
