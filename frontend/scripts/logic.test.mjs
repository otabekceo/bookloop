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

// ---------------------------------------------------------------- v2.2
import { chatPanel, chatStateOf, decideSwapStart, feedText, feedRoute, hasUnread, noteCharsLeft, NOTE_MAX_CHARS, pushUiState, togglePick } from "../src/swapFlow.ts";
import { reviewerView, rateBody } from "../src/reviews.ts";
import { parseLinkResult, canUnlink, loginMethodCount, linkErrorKey } from "../src/accountLinking.ts";

test("chat panel: locked states never show the composer; open chat does", () => {
  const waiting = chatPanel({ status: "pending", is_requester: true, chat: { state: "locked_waiting" } }, "me");
  assert.equal(waiting.composer, false);
  assert.equal(waiting.waiting, true);
  assert.equal(waiting.canPropose, true);
  const deciding = chatPanel({ status: "pending", chat: { state: "locked_needs_you" }, active_proposal: { proposer_id: "them" } }, "me");
  assert.equal(deciding.composer, false);
  assert.equal(deciding.decide, true);
  assert.equal(deciding.canAnswerProposal, true);
  assert.equal(deciding.canPropose, false);
  // A completed swap keeps its conversation.
  assert.equal(chatPanel({ status: "completed", chat: { state: "open" } }, "me").composer, true);
  assert.equal(chatPanel({ status: "declined", chat: { state: "closed" } }, "me").composer, false);
  // My own proposal is never mine to accept.
  assert.equal(chatPanel({ status: "pending", chat: { state: "open" }, active_proposal: { proposer_id: "me" } }, "me").canAnswerProposal, false);
});

test("chat state: older servers without `chat` fall back to the status rule", () => {
  assert.equal(chatStateOf({ status: "completed" }), "open");
  assert.equal(chatStateOf({ status: "cancelled" }), "closed");
  assert.equal(chatStateOf({ status: "pending", chat: { state: "bogus" } }), "open");
});

test("request swap: open swap first, not-exchanging blocks strangers, previous partners keep chatting", () => {
  assert.deepEqual(decideSwapStart({ open: { id: "s1", status: "pending" }, latest: null, can_propose: false }), { kind: "open", swapId: "s1" });
  assert.deepEqual(decideSwapStart({ open: null, latest: null, can_propose: false }), { kind: "notExchanging" });
  assert.deepEqual(decideSwapStart({ open: null, latest: { id: "old", status: "completed" }, can_propose: false, latest_chat_open: true }), { kind: "openPreviousOnly", swapId: "old" });
  assert.deepEqual(decideSwapStart({ open: null, latest: { id: "d", status: "declined" }, can_propose: false, latest_chat_open: false }), { kind: "notExchanging" });
  assert.equal(decideSwapStart({ open: null, latest: { id: "old", status: "completed" }, can_propose: true }).kind, "askPrevious");
  assert.deepEqual(decideSwapStart({ open: null, latest: null }), { kind: "compose" }); // older server: no can_propose
});

test("notification feed: every type has text in all languages; taps go to the conversation", () => {
  const types = ["message", "proposal_received", "proposal_accepted", "proposal_declined", "proposal_cancelled", "chat_accepted", "some_future_type"];
  for (const l of ["en", "uz", "ru", "it", "ar"]) {
    const dict = locale(l);
    for (const type of types) {
      const item = { id: "x", kind: type === "message" ? "message" : "event", type, swap_id: "s", actor: { user_id: "u", name: "Sara" }, read: false, unread_count: 3 };
      const { key, params } = feedText(item);
      const parts = key.split(".");
      const parent = parts.slice(0, -1).reduce((o, k) => o?.[k], dict);
      const leaf = parts[parts.length - 1];
      assert.ok(typeof (parent?.[leaf] ?? parent?.[`${leaf}_other`]) === "string", `${l}: ${key}`);
      assert.equal(params.name, "Sara");
    }
  }
  assert.equal(feedRoute({ swap_id: "swap_1" }), "/swap/swap_1");
  assert.equal(hasUnread({ unread_total: 0 }), false);
  assert.equal(hasUnread({ unread_total: 2 }), true);
  assert.equal(hasUnread(undefined), false);
});

test("proposal note counter matches the server (trimmed length, 280 max)", () => {
  assert.equal(NOTE_MAX_CHARS, 280);
  assert.equal(noteCharsLeft("  hi  "), 278);
  assert.ok(noteCharsLeft("x".repeat(281)) < 0);
});

test("notification toggle: ON only with server opt-in AND OS permission", () => {
  assert.equal(pushUiState({ enabledOnServer: true, permission: "granted", pushReady: true }), "on");
  assert.equal(pushUiState({ enabledOnServer: true, permission: "granted", pushReady: false }), "onNoPush");
  assert.equal(pushUiState({ enabledOnServer: true, permission: "denied", pushReady: false }), "denied");
  assert.equal(pushUiState({ enabledOnServer: false, permission: "granted", pushReady: true }), "off");
  assert.equal(pushUiState({ enabledOnServer: true, permission: "unsupported", pushReady: false }), "unsupported");
});

test("book picker (Z): any of 20+ books, including ones far past the first screen, can be picked and cleared", () => {
  const books = Array.from({ length: 22 }, (_, i) => `book_${i}`);
  let sel = null;
  sel = togglePick(sel, books[21]); // the last one, well beyond the ~5 covers visible at once
  assert.equal(sel, "book_21");
  sel = togglePick(sel, books[7]);
  assert.equal(sel, "book_7"); // switching keeps exactly one selection
  sel = togglePick(sel, books[7]);
  assert.equal(sel, null); // tapping the selected book clears it
  // The picker renders every book it gets (no first-page slicing) and scrolls inside bottom sheets.
  const src = readFileSync(new URL("../src/components/BookPicker.tsx", import.meta.url), "utf8");
  assert.match(src, /books\.map\(/);
  assert.doesNotMatch(src, /books\.slice\(/);
  assert.match(src, /from "react-native-gesture-handler"/);
});

test("reviews: anonymous shows nothing about the reviewer; default submission is identified", () => {
  assert.deepEqual(reviewerView({ id: "r", stars: 5, is_anonymous: true, rater_id: null, rater_name: "BookLoop member" }), { anonymous: true, name: null, avatar: null, profileId: null });
  assert.deepEqual(reviewerView({ id: "r", stars: 4, is_anonymous: false, rater_id: "u1", rater_name: "Sarah", rater_avatar: "a.png" }), { anonymous: false, name: "Sarah", avatar: "a.png", profileId: "u1" });
  // Legacy review (no flag) = identified, as it always was.
  assert.equal(reviewerView({ id: "r", stars: 4, rater_id: "u1", rater_name: "Old" }).anonymous, false);
  assert.deepEqual(rateBody(5, "  Great  "), { stars: 5, review: "Great", is_anonymous: false });
  assert.equal(rateBody(5, "", true).is_anonymous, true);
  assert.equal(rateBody(9, "").stars, 5);
  for (const l of ["en", "uz", "ru", "it", "ar"]) assert.ok(locale(l).reviews.bookLoopMember, l);
});

test("account linking: result parsing, and the last sign-in method is never unlinkable", () => {
  assert.deepEqual(parseLinkResult("bookloop://settings/account?linked=google"), { kind: "linked" });
  assert.deepEqual(parseLinkResult("bookloop://settings/account?link_error=identity_in_use"), { kind: "error", code: "identity_in_use" });
  assert.deepEqual(parseLinkResult("bookloop://settings/account"), { kind: "cancelled" });
  const googleOnly = { password: false, providers: { google: { linked: true }, apple: { linked: false } } };
  assert.equal(loginMethodCount(googleOnly), 1);
  assert.equal(canUnlink(googleOnly, "google"), false);
  assert.equal(canUnlink({ ...googleOnly, password: true }, "google"), true);
  assert.equal(canUnlink(googleOnly, "apple"), false); // not linked
  assert.equal(linkErrorKey("last_login_method"), "account.errorLastMethod");
  assert.equal(linkErrorKey("weird"), "account.linkFailed");
});

test("plurals use CLDR forms (i18next v4 ignores _plural)", () => {
  for (const l of ["en", "uz", "ru", "it", "ar"]) {
    const txt = readFileSync(new URL(`../src/i18n/locales/${l}.json`, import.meta.url), "utf8");
    assert.doesNotMatch(txt, /_plural"/, l);
  }
  assert.ok(locale("ru").common.reviewCount_few.includes("отзыва"));
});
