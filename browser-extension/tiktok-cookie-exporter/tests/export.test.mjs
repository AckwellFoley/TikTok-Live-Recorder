import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { buildExport, normalizeCookies, SITE } from "../cookies.mjs";

const cookie = (overrides = {}) => ({
  name: "sessionid_ss", value: "fake-test-session", domain: ".tiktok.com", path: "/", secure: true,
  ...overrides,
});

test("permission scope is TikTok cookies only and no background process", async () => {
  const manifest = JSON.parse(await readFile(new URL("../manifest.json", import.meta.url), "utf8"));
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.permissions, ["cookies"]);
  assert.deepEqual(manifest.host_permissions, ["https://*.tiktok.com/*"]);
  assert.equal(manifest.background, undefined);
  assert.equal(manifest.content_scripts, undefined);
});

test("export excludes unrelated, lookalike, nonapplicable and empty cookies", () => {
  const selected = normalizeCookies([
    cookie(),
    cookie({ name: "sessionid", domain: "www.tiktok.com" }),
    cookie({ domain: ".tiktok.com.evil.test" }),
    cookie({ domain: ".nottiktok.com" }),
    cookie({ domain: ".example.com" }),
    cookie({ domain: "shop.tiktok.com" }),
    cookie({ path: "/private" }),
    cookie({ value: "" }),
    cookie({ value: 42 }),
    null,
  ]);
  assert.equal(selected.length, 2);
  assert.deepEqual(selected.map(c => c.name), ["sessionid", "sessionid_ss"]);
});

test("export has the import schema and only the needed cookie metadata", () => {
  const payload = buildExport([cookie({ httpOnly: true, storeId: "test", session: true })]);
  assert.deepEqual(payload, {
    format: "tiktok-recorder-cookies", version: 1, site: SITE,
    cookies: [{ name: "sessionid_ss", value: "fake-test-session", domain: ".tiktok.com", path: "/", secure: true }],
  });
});

test("logged-out cookies never produce a login export", () => {
  assert.throws(() => buildExport([cookie({ name: "ttwid" })]), /Sign into tiktok.com/);
  assert.throws(() => buildExport([]), /Sign into tiktok.com/);
  assert.throws(() => buildExport([cookie({ domain: "evil.test" })]), /Sign into tiktok.com/);
});
