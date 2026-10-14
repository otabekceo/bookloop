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

test("distances of 1 km or less are only ever 'within 1 km' (no finer proximity is shown)", () => {
  for (const l of ["en", "uz", "ru", "it", "ar"]) {
    const t = makeT(locale(l));
    const within = locale(l).location.within1km;
    assert.ok(within && !within.includes("100"), l);
    for (const km of [0.01, 0.1, 0.5, 1]) assert.equal(distanceLabel(t, km), within);
    assert.notEqual(distanceLabel(t, 2), within);
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
import {
  base64UrlEncode, base64ToBase64Url, attemptOf, parseOAuthReturn, pendingFor, parsePending, serializePending,
  PENDING_OAUTH_MAX_AGE_MS,
} from "../src/pkce.ts";
import { createHash, randomBytes } from "node:crypto";

// RFC 7636 Appendix B: the verifier octets, the verifier and its S256 challenge.
const RFC_OCTETS = [116, 24, 223, 180, 151, 153, 224, 37, 79, 250, 96, 125, 216, 173, 187, 186, 22, 212, 37, 77,
  105, 214, 191, 240, 91, 88, 5, 88, 83, 132, 141, 121];
const RFC_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
const RFC_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM";

test("pkce: verifier and challenge encoding match RFC 7636 (and so the backend)", () => {
  assert.equal(base64UrlEncode(new Uint8Array(RFC_OCTETS)), RFC_VERIFIER);
  // The app hashes with expo-crypto (base64 output) and converts; same as Node's sha256 here.
  const b64 = createHash("sha256").update(RFC_VERIFIER).digest("base64");
  assert.equal(base64ToBase64Url(b64), RFC_CHALLENGE);
  for (let n = 0; n < 40; n++) {
    const bytes = randomBytes(n);
    assert.equal(base64UrlEncode(new Uint8Array(bytes)), bytes.toString("base64url"), `length ${n}`);
  }
  assert.equal(base64UrlEncode(new Uint8Array(randomBytes(32))).length, 43); // RFC minimum verifier length
  assert.equal(attemptOf(RFC_CHALLENGE), "E9Melhoa2OwvFrEM");
});

test("pkce: a redirect is only paired with the pending attempt of the same kind, while fresh", () => {
  const now = 1_000_000_000;
  const p = { kind: "login", verifier: RFC_VERIFIER, attempt: attemptOf(RFC_CHALLENGE), createdAt: now };
  assert.equal(pendingFor(p, "login", p.attempt, now + 1000), p);
  assert.equal(pendingFor(p, "login", "AAAAAAAAAAAAAAAA", now), null); // another attempt (stale tab, crafted link)
  assert.equal(pendingFor(p, "login", null, now), null); // no attempt at all
  assert.equal(pendingFor(p, "link", p.attempt, now), null); // a sign-in verifier never confirms a link
  assert.equal(pendingFor(null, "login", p.attempt, now), null); // nothing pending (completed / cancelled)
  assert.equal(pendingFor(p, "login", p.attempt, now + PENDING_OAUTH_MAX_AGE_MS + 1), null); // expired
  assert.equal(pendingFor(p, "login", p.attempt, now - 120_000), null); // clock moved back: treated as expired
});

test("pkce: stored attempts survive a restart, corrupted ones are dropped", () => {
  const p = { kind: "link", verifier: RFC_VERIFIER, attempt: attemptOf(RFC_CHALLENGE), createdAt: 123 };
  assert.deepEqual(parsePending(serializePending(p)), p);
  assert.deepEqual(parsePending(JSON.stringify({ ...p, extra: "x" })), p); // unknown fields stripped
  for (const bad of [null, "", "{", "[]", 42, JSON.stringify({ ...p, kind: "other" }),
    JSON.stringify({ ...p, verifier: "short" }), JSON.stringify({ ...p, attempt: "x" }),
    JSON.stringify({ ...p, createdAt: "123" })]) {
    assert.equal(parsePending(bad), null, String(bad));
  }
});

test("pkce: parsing the backend's redirects back to the app", () => {
  const a = attemptOf(RFC_CHALLENGE);
  assert.deepEqual(parseOAuthReturn(`bookloop://?session_id=S1&attempt=${a}`),
    { attempt: a, sessionId: "S1", googleError: null, linkId: null, linkError: null });
  assert.deepEqual(parseOAuthReturn(`?google_error=cancelled&attempt=${a}`),
    { attempt: a, sessionId: null, googleError: "cancelled", linkId: null, linkError: null });
  assert.equal(parseOAuthReturn("bookloop://?session_id=S1").attempt, null);
  assert.equal(parseOAuthReturn("bookloop://?session_id=S1&attempt=../../x").attempt, null); // wrong shape
  assert.equal(parseOAuthReturn("bookloop://?session_id=%E0%A4%A").sessionId, null); // malformed escape
  assert.deepEqual(parseOAuthReturn("bookloop://settings/account"),
    { attempt: null, sessionId: null, googleError: null, linkId: null, linkError: null });
});

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
  const A = "E9Melhoa2OwvFrEM";
  assert.deepEqual(parseLinkResult(`bookloop://settings/account?link_id=L1&attempt=${A}`), { kind: "confirm", linkId: "L1", attempt: A });
  assert.deepEqual(parseLinkResult(`bookloop://settings/account?link_error=failed&attempt=${A}`), { kind: "error", code: "failed", attempt: A });
  // A URL alone never means "linked" (only the server's answer to the confirmation does).
  assert.deepEqual(parseLinkResult("bookloop://settings/account?linked=google"), { kind: "cancelled" });
  assert.deepEqual(parseLinkResult("bookloop://settings/account?link_id=L1"), { kind: "cancelled" }); // no attempt
  assert.deepEqual(parseLinkResult("bookloop://settings/account"), { kind: "cancelled" });
  assert.deepEqual(parseLinkResult("bookloop://settings/account?link_id=%E0%A4%A"), { kind: "cancelled" }); // malformed
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

// ---------------------------------------------------------------- Discover distance + map clusters
import { DEFAULT_DISTANCE, DISTANCE_ALL, DISTANCE_OPTIONS, DISTANCES, normalizeDistance, areaScopeFromParams, peopleQueryString } from "../src/discoverFilters.ts";

const qs = (f) => Object.fromEntries(new URLSearchParams(peopleQueryString({ search: "", genre: "All", language: "All", exchangingOnly: true, scope: null, maxDistance: DEFAULT_DISTANCE, ...f })));

test("distance filter: 'All' is offered last, is not a number, and is never the default", () => {
  assert.equal(DEFAULT_DISTANCE, 25);
  assert.notEqual(DEFAULT_DISTANCE, DISTANCE_ALL);
  assert.deepEqual(DISTANCE_OPTIONS, [...DISTANCES, DISTANCE_ALL]);
  assert.equal(DISTANCE_OPTIONS.filter((d) => d === DISTANCE_ALL).length, 1);
  assert.equal(typeof DISTANCE_ALL, "string");
  assert.ok(Math.max(...DISTANCES) <= 50); // no fake huge radius was added
});

test("distance filter: query semantics for radius, All, and All -> radius", () => {
  assert.equal(qs({}).max_distance, "25");
  assert.equal(qs({ maxDistance: DISTANCE_ALL }).max_distance, "all");
  assert.equal(qs({ maxDistance: 10 }).max_distance, "10"); // back from All: a radius again
  assert.equal(qs({}).cluster, undefined);
});

test("distance filter: older/unknown stored values stay compatible", () => {
  for (const d of DISTANCES) assert.equal(normalizeDistance(d), d);
  assert.equal(normalizeDistance("10"), 10);
  assert.equal(normalizeDistance("all"), DISTANCE_ALL);
  for (const bad of [undefined, null, "", 7, 9999, "ALL?", {}, NaN]) assert.equal(normalizeDistance(bad), DEFAULT_DISTANCE, String(bad));
});

test("map cluster -> Discover: the area replaces the distance filter", () => {
  const scope = areaScopeFromParams({ cluster: "abc123def456", area: "Chilonzor, Tashkent" });
  assert.deepEqual(scope, { cluster: "abc123def456", area: "Chilonzor, Tashkent" });
  for (const maxDistance of [1, 25, DISTANCE_ALL]) {
    const q = qs({ scope, maxDistance });
    assert.equal(q.cluster, "abc123def456");
    assert.equal(q.max_distance, undefined, "the normal radius must not leak into the cluster query");
  }
  assert.equal(areaScopeFromParams({}), null);
  assert.equal(areaScopeFromParams({ cluster: "abc" }), null);
  assert.equal(areaScopeFromParams({ cluster: " ", area: "X" }), null);
  assert.deepEqual(areaScopeFromParams({ cluster: ["id1", "id2"], area: ["A"] }), { cluster: "id1", area: "A" });
  // The map screen really sends the cluster to Discover (the original bug: it sent nothing).
  const map = readFileSync(new URL("../app/(tabs)/map.tsx", import.meta.url), "utf8");
  assert.match(map, /pathname: "\/\(tabs\)\/discover",\s*params: \{ cluster: selected\.id, area: areaName\(selected\) \}/);
});

test("area scope and 'All' are translated in all five languages", () => {
  for (const l of ["en", "uz", "ru", "it", "ar"]) {
    const d = locale(l);
    assert.ok(d.common.all && d.common.all !== "common.all", l);
    for (const k of ["inArea", "areaDistanceHint"]) assert.match(d.discover[k], /\{\{area\}\}/, `${l} ${k}`);
    assert.ok(d.discover.clearArea, l);
    if (l !== "en") assert.notEqual(d.discover.inArea, locale("en").discover.inArea, l);
  }
  assert.equal(makeT(locale("ar"))("discover.inArea", { area: "طشقند" }), "القرّاء في طشقند");
});

// theme.ts imports react-native, so its token tables are read from source.
const themeSrc = readFileSync(new URL("../src/theme.ts", import.meta.url), "utf8");
const tokens = (name) => {
  const body = themeSrc.match(new RegExp(String.raw`const ${name} = \{([\s\S]*?)\n\};`))[1];
  return Object.fromEntries([...body.matchAll(/^\s*(\w+):\s*"([^"]+)"/gm)].map((m) => [m[1], m[2]]));
};

test("theme: light primary is BookLoop green; Terracotta only as an accent; dark unchanged", () => {
  const light = tokens("light");
  const dark = tokens("dark");
  const INK = "#17211F";
  const TERRACOTTA = "#D96C4A";
  assert.equal(light.brandPrimary, INK);
  assert.equal(light.brand, INK);
  assert.equal(light.onBrandTertiary, INK);
  for (const k of ["brand", "brandPrimary", "brandSecondary", "brandTertiary", "onBrandTertiary"]) assert.notEqual(light[k].toUpperCase(), TERRACOTTA, k);
  assert.equal(light.accent, TERRACOTTA);
  // Dark keeps its one brand green, and its accents resolve to that green (no Terracotta in dark).
  assert.equal(dark.brandPrimary, "#4E9E76");
  assert.equal(dark.brand, "#4E9E76");
  assert.equal(dark.accent, "#4E9E76");
  assert.ok(!Object.values(dark).some((v) => v.toUpperCase() === TERRACOTTA));
  // Both themes define the same tokens (dark adds its map-only colors).
  for (const k of Object.keys(light)) assert.ok(k in dark, `dark is missing ${k}`);
});

test("safety: every report target and reason, block/delete texts and server system lines exist in all five languages", () => {
  // Report keys are built at runtime (`safety.reportTitle.${type}`), so the i18n audit can't see them.
  const safety = readFileSync(new URL("../src/components/Safety.tsx", import.meta.url), "utf8");
  const types = safety.match(/type: ("[a-z]+"(?: \| "[a-z]+")*)/)[1].match(/[a-z]+/g);
  const reasons = safety.match(/const REASONS = \[([^\]]+)\]/)[1].match(/[a-z]+/g);
  assert.deepEqual(types, ["user", "book", "review", "message", "swap"]);
  for (const l of ["en", "uz", "ru", "it", "ar"]) {
    const d = locale(l);
    for (const ty of types) assert.ok(d.safety.reportTitle[ty], `${l} reportTitle.${ty}`);
    for (const r of reasons) assert.ok(d.safety.reasons[r], `${l} reasons.${r}`);
    for (const k of ["blockTitle", "blockedToast", "unblockName"]) assert.ok(d.safety[k].includes("{{name}}"), `${l} ${k}`);
    for (const k of ["deleteTitle", "deleteBody", "deleteButton", "deleteConfirmTitle", "deleteConfirmBody"]) assert.ok(d.account[k], `${l} ${k}`);
    for (const k of ["closed", "accountDeleted"]) assert.ok(d.swapChat.sys[k], `${l} sys.${k}`);
    for (const k of ["blocked", "invalidImageUrl", "unsupportedImage", "tooManySignInAttempts"]) assert.ok(d.apiErrors[k], `${l} apiErrors.${k}`);
  }
});

// ---------------------------------------------------------------- Profile stats + invite sharing
import { loadFont, wrapLines } from "./fontMetrics.mjs";
import { statLabelWidth, STAT_LABEL_FONT_SIZE } from "../src/profileStats.ts";
import { INVITE_URL, TEMPORARY_INVITE_URL, inviteMessage } from "../src/invite.ts";

const LANGS = ["en", "uz", "ru", "it", "ar"];
const WIDTHS = [320, 360, 375, 390, 412, 430];
const latinFont = loadFont(new URL("../assets/fonts/DMSans-Regular.ttf", import.meta.url));
const arabicFont = loadFont(new URL("../assets/fonts/NotoSansArabic-Regular.ttf", import.meta.url));
const fontFor = (l) => (l === "ar" ? arabicFont : latinFont); // as src/typography.ts fontsForLanguage
/** makeT plus i18next's CLDR plural lookup (key_one / key_few / … chosen by Intl.PluralRules). */
const makePluralT = (l) => {
  const dict = locale(l), t = makeT(dict), rules = new Intl.PluralRules(l);
  return (key, opts = {}) => {
    if (opts.count === undefined) return t(key, opts);
    const exact = t(`${key}_${rules.select(opts.count)}`, opts);
    return exact.startsWith(key) ? t(`${key}_other`, opts) : exact;
  };
};
// The three labels exactly as app/(tabs)/profile.tsx renders them.
const statLabels = (t, n) => [t("profile.reviewsCount", { count: n }), t("profile.swapsDone", { count: n }), t("profile.booksCount", { count: n })];
const COUNTS = [0, 1, 2, 3, 5, 11, 21, 101, 1234];

test("profile stats: every label in all five languages fits in at most 2 lines at 320–430 dp (and at 1.3× text)", () => {
  for (const l of LANGS) {
    const t = makePluralT(l);
    for (const n of COUNTS) for (const label of statLabels(t, n)) {
      assert.ok(!label.includes("profile."), `${l}: missing translation ${label}`);
      for (const scale of [1, 1.3]) for (const w of WIDTHS) {
        const lines = wrapLines(fontFor(l), label, STAT_LABEL_FONT_SIZE * scale, statLabelWidth(w));
        assert.ok(lines.length <= 2, `${l} "${label}" @${w}dp ×${scale}: ${lines.length} lines`);
        // No single word wider than the cell: it would be cut mid-word instead of wrapping.
        for (const word of label.split(/\s+/)) assert.ok(fontFor(l).width(word, STAT_LABEL_FONT_SIZE * scale) <= statLabelWidth(w), `${l} "${word}" @${w}`);
      }
    }
  }
});

test("profile stats: Russian swaps label is short, grammatical for every count, and one line even at 320 dp", () => {
  const t = makePluralT("ru");
  assert.equal(t("profile.swapsDone", { count: 1 }), "обмен");
  assert.equal(t("profile.swapsDone", { count: 3 }), "обмена");
  assert.equal(t("profile.swapsDone", { count: 5 }), "обменов");
  assert.equal(t("profile.booksCount", { count: 21 }), "книга");
  for (const w of WIDTHS) for (const n of COUNTS)
    assert.equal(wrapLines(latinFont, t("profile.swapsDone", { count: n }), STAT_LABEL_FONT_SIZE, statLabelWidth(w)).length, 1, `@${w}`);
  // The old label wrapped at the most common Android width — the bug this guards against.
  assert.equal(wrapLines(latinFont, "обменов завершено", STAT_LABEL_FONT_SIZE, statLabelWidth(360)).length, 2);
});

test("profile stats: numbers stay level (top-aligned cells, equal value boxes) and nothing is positioned left/right (RTL-safe)", () => {
  const src = readFileSync(new URL("../app/(tabs)/profile.tsx", import.meta.url), "utf8");
  const style = (name) => src.match(new RegExp(`\n  ${name}: \{([^}]*)\}`))[1];
  assert.match(style("stats"), /alignItems: "stretch"/);
  assert.match(style("statCell"), /justifyContent: "flex-start"/);
  assert.equal(src.match(/style=\{\[styles\.statValue, statValueHeight\]\}/g).length, 3); // all three values
  for (const n of ["stats", "statCell", "statValue", "statLabel", "statDivider"])
    assert.doesNotMatch(style(n), /\b(left|right|marginLeft|marginRight|paddingLeft|paddingRight)\b/, n);
  assert.doesNotMatch(src, /numberOfLines=\{1\}[^>]*profile\.(swapsDone|booksCount)/); // never truncated
  assert.ok(statLabelWidth(320) > 70); // labels keep real room on the narrowest phones
});

test("invite: shared text is in the selected app language, then the temporary Expo build URL on its own line", () => {
  assert.equal(INVITE_URL, "https://expo.dev/accounts/otabekceo/projects/bookloop/builds/5028139b-8384-4bf3-9806-0d1479f534cc");
  assert.equal(INVITE_URL, TEMPORARY_INVITE_URL);
  const texts = new Set();
  for (const l of LANGS) {
    const msg = inviteMessage(makeT(locale(l)));
    const [body, url, ...rest] = msg.split("\n\n");
    assert.equal(body, locale(l).profile.inviteMessage, l);
    assert.equal(url, INVITE_URL, l);
    assert.equal(rest.length, 0, l);
    assert.doesNotMatch(msg, /appbookloop|\?ref=/, l);
    texts.add(body);
  }
  assert.equal(texts.size, 5); // each language has its own text
  // Switching the app language switches the text; the link never changes.
  const en = inviteMessage(makeT(locale("en"))), ru = inviteMessage(makeT(locale("ru")));
  assert.notEqual(en, ru);
  assert.ok(ru.includes("BookLoop") && /[а-я]/.test(ru) && ru.endsWith(INVITE_URL));
  assert.ok(/[Ѐ-ӿ]/.test(locale("ru").profile.inviteMessage) && /[؀-ۿ]/.test(locale("ar").profile.inviteMessage));
});

test("invite: Arabic text reads right-to-left while the URL stays intact; the old ref link is gone from Profile", () => {
  const msg = inviteMessage(makeT(locale("ar")));
  const firstStrong = [...msg].find((c) => /[\p{L}]/u.test(c));
  assert.match(firstStrong, /[؀-ۿ]/); // the share sheet / chat app picks RTL for the message
  assert.doesNotMatch(msg, /[‎‏‪-‮⁦-⁩]/); // no bidi controls that break link detection
  const src = readFileSync(new URL("../app/(tabs)/profile.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(src, /\?ref=|EXPO_PUBLIC_BACKEND_URL|Join me on BookLoop/);
  assert.match(src, /Share\.share\(\{ message: inviteMessage\(t\) \}\)/); // t = the selected BookLoop language
  assert.match(src, /setStringAsync\(INVITE_URL\)/);
});

// ---------------------------------------------------------------- First-run language picker: live preview
import { createLanguagePreview, createLanguageSwitcher } from "../src/i18n/languageSwitch.ts";

/** LanguageProvider's language plumbing with fakes for storage, i18n and the native direction, wired the
 * same way (preview = i18n + state only; Continue = the switcher). `screen()` is what the picker shows. */
function pickerHarness({ applyDelay = () => 0 } = {}) {
  const s = { stored: null, active: "en", needsSelection: true, nativeRTL: false, persisted: [], applied: [], direction: [], reloads: 0 };
  const changeLanguage = async (code) => {
    const ms = applyDelay(code);
    if (ms) await new Promise((r) => setTimeout(r, ms));
    s.active = code; // i18n.changeLanguage + setLanguageState(code)
    s.applied.push(code);
  };
  const previewLanguage = createLanguagePreview(changeLanguage);
  const setLanguage = createLanguageSwitcher({
    persist: async (code) => { s.stored = code; s.persisted.push(code); },
    applyLanguage: async (code) => { await changeLanguage(code); s.needsSelection = false; },
    syncDirection: async (code, beforeReload) => {
      const rtl = code === "ar";
      s.direction.push(code);
      if (rtl === s.nativeRTL) return "in-sync";
      await beforeReload();
      s.reloads++;
      return "reloading";
    },
    setSwitching: () => {},
    waitForPaint: async () => {},
    schedule: () => {},
  });
  const screen = () => {
    const d = locale(s.active).languageSelect;
    return { title: d.title, subtitle: d.subtitle, continue: d.continue, selected: s.active, rtl: s.active === "ar" };
  };
  return { s, previewLanguage, setLanguage, screen };
}

test("language picker: each tap re-renders title, subtitle and Continue in that language at once, without saving", async () => {
  const h = pickerHarness();
  let prev = h.screen();
  for (const code of ["uz", "ru", "it", "ar", "en"]) { // en->uz, uz->ru, ru->it, it->ar, ar->en
    await h.previewLanguage(code);
    const now = h.screen();
    const d = locale(code).languageSelect;
    assert.equal(now.selected, code); // the highlighted card is the active language
    assert.deepEqual([now.title, now.subtitle, now.continue], [d.title, d.subtitle, d.continue], code);
    for (const k of ["title", "subtitle", "continue"]) assert.notEqual(now[k], prev[k], `${prev.selected}->${code} ${k} stale`);
    assert.equal(now.rtl, code === "ar"); // root `direction` follows the active language
    prev = now;
  }
  assert.equal(h.s.stored, null); // previews never persist ...
  assert.equal(h.s.needsSelection, true); // ... nor finish the first-run selection
  assert.deepEqual(h.s.direction, []); // ... nor touch the native direction / reload
  assert.equal(locale("uz").languageSelect.continue, "Davom etish");
});

test("language picker: Continue saves the already-active language and changes nothing on screen (no flash)", async () => {
  const h = pickerHarness();
  await h.previewLanguage("uz");
  const before = h.screen();
  await h.setLanguage("uz");
  assert.deepEqual(h.screen(), before); // the moment the selection completes, the text is unchanged
  assert.equal(h.s.stored, "uz");
  assert.equal(h.s.needsSelection, false);
  assert.deepEqual(h.s.persisted, ["uz"]);
  assert.equal(h.s.reloads, 0); // LTR -> LTR never reloads
});

test("language picker: rapid taps — the last one wins, even when applies finish out of order", async () => {
  const delays = { en: 0, ru: 30, uz: 5, ar: 20, it: 1 };
  const h = pickerHarness({ applyDelay: (c) => delays[c] });
  const taps = ["en", "ru", "uz", "ar", "it"];
  let latest;
  const pending = taps.map((c) => { latest = c; return h.previewLanguage(c); }); // no awaiting between taps
  await Promise.all(pending);
  assert.equal(h.s.active, "it");
  assert.equal(h.s.applied.at(-1), "it");
  assert.deepEqual(h.screen().continue, locale("it").languageSelect.continue);
  await h.setLanguage(latest); // Continue confirms the newest tap
  assert.equal(h.s.stored, "it");
  assert.equal(h.s.active, "it");
});

test("language picker: Continue right after a tap (preview still applying) confirms that tap", async () => {
  const h = pickerHarness({ applyDelay: (c) => (c === "ru" ? 25 : 0) });
  h.previewLanguage("ru"); // not awaited
  await h.setLanguage("ru");
  await new Promise((r) => setTimeout(r, 40));
  assert.equal(h.s.active, "ru");
  assert.equal(h.s.stored, "ru");
});

test("language picker: Arabic previews RTL without a reload; Continue applies it natively; back to LTR restores", async () => {
  const h = pickerHarness();
  await h.previewLanguage("ar");
  assert.equal(h.screen().rtl, true);
  assert.equal(h.screen().title, locale("ar").languageSelect.title);
  assert.equal(h.s.reloads, 0); // previewing never reloads
  await h.previewLanguage("en");
  assert.equal(h.screen().rtl, false);
  await h.previewLanguage("ar");
  const before = h.screen();
  await h.setLanguage("ar"); // fresh install: native LTR -> one reload to make native RTL, text already Arabic
  assert.deepEqual(h.screen(), before);
  assert.equal(h.s.stored, "ar");
  assert.equal(h.s.reloads, 1);
});

test("language picker: all five languages have their own title, subtitle and Continue; screen uses the active language", () => {
  const seen = { title: new Set(), subtitle: new Set(), continue: new Set() };
  for (const l of LANGS) for (const k of Object.keys(seen)) {
    const v = locale(l).languageSelect[k];
    assert.ok(v, `${l} ${k}`);
    seen[k].add(v);
  }
  for (const k of Object.keys(seen)) assert.equal(seen[k].size, 5, k);
  const src = readFileSync(new URL("../app/(auth)/language.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(src, /useState/); // no separate draft that can disagree with the active language
  assert.match(src, /const active = language === lang\.code/);
  assert.match(src, /previewLanguage\(code\)/);
  assert.match(src, /setLanguage\(latest\.current\)/);
  assert.doesNotMatch(src, /setTimeout/);
});

// ---------------------------------------------------------------- Native Google sign-in (Android)
import { googleErrorKey, isGoogleClientId, nativeGoogleUsable, nativeOutcome } from "../src/googleNative.ts";

const WEB_ID = "123456789012-abcdefghijklmnop0123456789abcdef.apps.googleusercontent.com";

test("nativeGoogleUsable: Android + server switch + a real client ID, otherwise the browser flow", () => {
  assert.equal(nativeGoogleUsable({ platform: "android", serverNative: true, webClientId: WEB_ID }), true);
  assert.equal(nativeGoogleUsable({ platform: "ios", serverNative: true, webClientId: WEB_ID }), false);
  assert.equal(nativeGoogleUsable({ platform: "web", serverNative: true, webClientId: WEB_ID }), false);
  assert.equal(nativeGoogleUsable({ platform: "android", serverNative: false, webClientId: WEB_ID }), false);
  for (const id of ["", undefined, null, "test-client-id", "GOCSPX-not-an-id", `${WEB_ID} extra`, "abc.apps.googleusercontent.com"]) {
    assert.equal(nativeGoogleUsable({ platform: "android", serverNative: true, webClientId: id }), false, String(id));
  }
  assert.equal(isGoogleClientId(` ${WEB_ID} `), true);
});

test("nativeOutcome: only a success carrying an ID token continues to the backend", () => {
  assert.deepEqual(nativeOutcome({ type: "success", data: { idToken: "eyJ.x.y" } }), { kind: "success", idToken: "eyJ.x.y" });
  for (const bad of [{ type: "success", data: null }, { type: "success", data: { idToken: "" } }, { type: "success", data: { idToken: 42 } }, null, undefined, {}, { type: "weird" }]) {
    assert.deepEqual(nativeOutcome(bad), { kind: "failed" }, JSON.stringify(bad));
  }
  assert.deepEqual(nativeOutcome({ type: "cancelled", data: null }), { kind: "cancelled" });
  assert.deepEqual(nativeOutcome({ type: "noSavedCredentialFound", data: null }), { kind: "unavailable" });
  assert.deepEqual(nativeOutcome(undefined, { code: "SIGN_IN_CANCELLED" }), { kind: "cancelled" });
  assert.deepEqual(nativeOutcome(undefined, { code: "PLAY_SERVICES_NOT_AVAILABLE" }), { kind: "unavailable" });
  assert.deepEqual(nativeOutcome(undefined, { code: "DEVELOPER_ERROR" }), { kind: "misconfigured" });
  for (const err of [new Error("network"), { code: "ONE_TAP_START_FAILED" }, null, "boom"]) {
    assert.deepEqual(nativeOutcome(undefined, err), { kind: "failed" });
  }
});

test("googleErrorKey: actionable refusals get their own translated message, in every language", () => {
  assert.equal(googleErrorKey("link_required"), "auth.googleLinkRequired");
  assert.equal(googleErrorKey("account_mismatch"), "auth.googleAccountMismatch");
  assert.equal(googleErrorKey("google_unavailable"), "auth.googleUnavailable");
  for (const c of ["google_failed", "rate_limited", undefined, null, ""]) assert.equal(googleErrorKey(c), "auth.googleFailed");
  assert.equal(linkErrorKey("google_unavailable"), "auth.googleUnavailable");
  for (const l of ["en", "uz", "ru", "it", "ar"]) {
    const t = makeT(locale(l));
    for (const key of ["auth.googleLinkRequired", "auth.googleAccountMismatch", "auth.googleUnavailable", "auth.googleFailed"]) {
      assert.notEqual(t(key), key, `${l}: ${key}`);
    }
  }
});

test("native Google sign-in code: server nonce passed through, no secrets, browser flow kept", () => {
  const src = readFileSync(new URL("../src/socialAuth.ts", import.meta.url), "utf8");
  // The nonce Google signs is exactly the one the server issued for this attempt; its id goes back with the token.
  assert.match(src, /\/api\/auth\/google\/nonce/);
  assert.match(src, /configure\(\{ webClientId: GOOGLE_WEB_CLIENT_ID, nonce, offlineAccess: false \}\)/);
  assert.match(src, /return \{ id_token: outcome\.idToken, nonce_id \}/);
  assert.doesNotMatch(src, /clientSecret|client_secret|GOCSPX|offlineAccess: true/);
  assert.match(src, /export async function linkGoogle\(/); // legacy browser linking still there
  const auth = readFileSync(new URL("../src/auth.tsx", import.meta.url), "utf8");
  assert.match(auth, /"\/api\/auth\/google\/native"/);
  assert.match(auth, /\/api\/auth\/google\/start\?redirect=/); // legacy browser sign-in still there
  const env = readFileSync(new URL("../.env.example", import.meta.url), "utf8");
  assert.doesNotMatch(env, /^EXPO_PUBLIC_[A-Z_]*SECRET/m);
});
