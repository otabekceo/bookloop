// Unit tests for the app's pure logic. Run from frontend/:  node --test scripts/logic.test.mjs
// (Node 22.18+/24 runs these TypeScript modules directly; they have no React/Expo imports.)
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { mergeMessages, withOutbox } from "../src/chatMessages.ts";
import { enumLabel, enumKey, genreOrInterestLabel } from "../src/i18n/enums.ts";
import { distanceLabel, distanceOrArea } from "../src/distance.ts";
import { deliverScanResult, takeScanResult } from "../src/scanResult.ts";

const locale = (l) => JSON.parse(readFileSync(new URL(`../src/i18n/locales/${l}.json`, import.meta.url), "utf8"));
/** Minimal i18next-like t(): nested lookup, {{var}} interpolation, defaultValue fallback. */
const makeT = (dict) => (key, opts = {}) => {
  const v = key.split(".").reduce((o, k) => (o && typeof o === "object" ? o[k] : undefined), dict);
  if (typeof v !== "string") return opts.defaultValue ?? key;
  return v.replace(/\{\{(\w+)\}\}/g, (_, k) => String(opts[k] ?? ""));
};

const m = (id, created_at, extra = {}) => ({ id, created_at, ...extra });

test("mergeMessages: appends new, updates existing, never duplicates, keeps time order", () => {
  const cur = [m("a", "2026-01-01T10:00:00.000"), m("b", "2026-01-01T10:00:01.000")];
  const merged = mergeMessages(cur, [m("b", "2026-01-01T10:00:01.000", { text: "edited" }), m("c", "2026-01-01T10:00:02.000"), m("a", "2026-01-01T10:00:00.000")]);
  assert.deepEqual(merged.map((x) => x.id), ["a", "b", "c"]);
  assert.equal(merged.find((x) => x.id === "b").text, "edited");
  // Out-of-order arrival (poll result older than a message already added by a send response).
  const out = mergeMessages([m("x", "2026-01-01T10:00:05.000")], [m("w", "2026-01-01T10:00:04.000")]);
  assert.deepEqual(out.map((x) => x.id), ["w", "x"]);
  assert.equal(mergeMessages(cur, []), cur); // no-op keeps identity (no needless re-render)
});

test("withOutbox: pending shown until the server copy with the same client_id arrives", () => {
  const outbox = [
    { client_id: "c1", text: "one", status: "sending", created_at: "t" },
    { client_id: "c2", text: "two", status: "failed", created_at: "t" },
  ];
  const before = withOutbox([m("s0", "t0")], outbox, "me");
  assert.deepEqual(before.map((x) => x.id), ["s0", "c1", "c2"]);
  assert.equal(before[1]._outbox.status, "sending");
  const after = withOutbox([m("s0", "t0"), m("s1", "t1", { client_id: "c1", text: "one" })], outbox, "me");
  assert.deepEqual(after.map((x) => x.id), ["s0", "s1", "c2"]); // c1 not shown twice
});

test("enum labels are translated in all five languages, stored value unchanged", () => {
  const ru = makeT(locale("ru"));
  assert.equal(enumKey("Self-development"), "self_development");
  assert.equal(enumKey("Like New"), "like_new");
  assert.equal(enumLabel(ru, "condition", "Like New"), "Как новая");
  assert.equal(enumLabel(ru, "genre", "Fiction"), "Художественная литература");
  assert.equal(enumLabel(ru, "bookStatus", "Available"), locale("ru").status.available);
  assert.equal(enumLabel(ru, "bookLanguage", "Spanish"), "Испанский"); // legacy value still labelled
  assert.equal(enumLabel(ru, "genre", "Unknown genre"), "Unknown genre"); // never a raw key
  assert.equal(genreOrInterestLabel(ru, "Poetry"), "Поэзия");
  for (const l of ["en", "uz", "ru", "it", "ar"]) {
    const t = makeT(locale(l));
    for (const g of ["Fiction", "Self-development", "Science"]) assert.notEqual(enumLabel(t, "genre", g), `enums.genre.${enumKey(g)}`);
  }
});

test("distances of 100 m or less are only ever 'within 100 m'", () => {
  for (const l of ["en", "uz", "ru", "it", "ar"]) {
    const t = makeT(locale(l));
    const within = locale(l).location.within100m;
    for (const km of [0.01, 0.05, 0.1]) assert.equal(distanceLabel(t, km), within);
    assert.notEqual(distanceLabel(t, 0.2), within);
    assert.equal(distanceLabel(t, 999), null); // unknown
    assert.equal(distanceOrArea(t, { distance_km: 999, city: null, neighborhood: null }), locale(l).location.notSet);
  }
});

test("scan hand-off is delivered exactly once", () => {
  assert.equal(takeScanResult(), null);
  deliverScanResult({ isbn: "9780000000000" });
  assert.deepEqual(takeScanResult(), { isbn: "9780000000000" });
  assert.equal(takeScanResult(), null);
});
