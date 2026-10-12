import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

globalThis.HTMLElement = class { attachShadow() {} };
globalThis.customElements = { get() {}, define() {} };
globalThis.window = {};
const source = await readFile(new URL("../custom_components/service_route_sync/frontend/wifi-history-card.js", import.meta.url), "utf8");
const { WifiHistoryCard } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

function card() {
  const value = new WifiHistoryCard();
  value.render = () => {};
  value.config = { entry_id: "entry" };
  value.isConnected = true;
  return value;
}
const page = { columns: Array(17).fill("field"), rows: [Array(17).fill(null)], after: 1, ceiling: 1, more: false };

test("nonadmin never requests private history", async () => {
  const value = card();
  value._hass = { user: { is_admin: false }, callWS() { assert.fail("Unauthorized read"); } };
  await value.load();
  assert.equal(value.page, null);
});

test("authenticated request uses fixed endpoint and preserves unknown cells", async () => {
  const value = card();
  value._hass = { user: { is_admin: true }, async callWS(request) {
    assert.deepEqual(request, { type: "service_route_sync/wifi_history", entry_id: "entry",
      source: "observations", after: 0, limit: 50 });
    return page;
  } };
  await value.load();
  assert.deepEqual(value.page, page);
  assert.equal(value.loading, false);
});

test("obsolete response cannot replace a new source or reveal data after permission change", async () => {
  const value = card();
  let finish;
  value._hass = { user: { is_admin: true }, callWS: () => new Promise(resolve => { finish = resolve; }) };
  const pending = value.load();
  value.hass = { user: { is_admin: false } };
  finish(page);
  await pending;
  assert.equal(value.page, null);
});

test("malformed replies show bounded generic error", async () => {
  const value = card();
  value._hass = { user: { is_admin: true }, callWS: async () => ({ ...page, after: -1 }) };
  await value.load();
  assert.equal(value.page, null);
  assert.match(value.error, /nicht geladen/);
});

test("source reset waits for the obsolete request before starting another", async () => {
  const value = card();
  let finish;
  let requests = 0;
  value._hass = { user: { is_admin: true }, callWS: () => {
    requests++;
    return requests === 1 ? new Promise(resolve => { finish = resolve; }) : Promise.resolve(page);
  } };
  const first = value.load();
  value.source = "legacy";
  value.reset();
  assert.equal(requests, 1);
  finish(page);
  await first;
  await Promise.resolve();
  assert.equal(requests, 2);
  assert.deepEqual(value.page, page);
});

test("busy retries are bounded and permission errors are not retried", async () => {
  const value = card();
  let requests = 0;
  value._hass = { user: { is_admin: true }, callWS: async () => {
    if (++requests === 1) throw { code: "busy" };
    return page;
  } };
  await value.load();
  assert.equal(requests, 2);
  assert.deepEqual(value.page, page);
  value.page = null;
  requests = 0;
  value._hass.callWS = async () => { requests++; throw { code: "unauthorized" }; };
  await value.load();
  assert.equal(requests, 1);
  assert.match(value.error, /nicht geladen/);
});
