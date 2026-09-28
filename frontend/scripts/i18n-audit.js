#!/usr/bin/env node
/**
 * Localization audit. Run from frontend/:  node scripts/i18n-audit.js   (exit code 1 on problems)
 *
 * Reports:
 *  1. keys missing from any locale (compared with the union of all five), and keys present in
 *     only some locales;
 *  2. values identical to English in a non-English locale (likely untranslated), minus a small
 *     allowlist of words that are legitimately the same (brand names, "OK", "Email"…);
 *  3. static t("…") keys used in app/ and src/ that no locale defines (they would render the key);
 *  4. hardcoded user-facing English in .tsx: JSX text, and literal title/label/placeholder/
 *     accessibilityLabel props, toast("…") and Alert.alert("…").
 *
 * Dynamic user content (book titles, names, messages) never appears as literals, so it's out of scope.
 */
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const LOCALES_DIR = path.join(ROOT, "src", "i18n", "locales");
const LANGS = ["en", "uz", "ru", "it", "ar"];
// Same in every language on purpose (brand, universal abbreviations, proper nouns).
const SAME_AS_ENGLISH_OK = new Set([
  "BookLoop", "OK", "Email", "Google", "km", "ISBN", "Loop Legend", "{{count}}", "",
]);
const SAME_AS_ENGLISH_OK_KEYS = [/^common\.appName$/, /^common\.km$/, /\.emailPlaceholder$/];
// Loanwords that really are the local word ("Password" and "Account" are standard Italian UI terms).
const SAME_AS_ENGLISH_OK_BY_LANG = { it: new Set(["Password", "Account", "Fantasy"]) };

const flatten = (o, p = "") =>
  Object.entries(o).flatMap(([k, v]) => (v && typeof v === "object" ? flatten(v, `${p}${k}.`) : [[`${p}${k}`, v]]));

const locales = Object.fromEntries(
  LANGS.map((l) => [l, Object.fromEntries(flatten(JSON.parse(fs.readFileSync(path.join(LOCALES_DIR, `${l}.json`), "utf8"))))]),
);

let problems = 0;
const report = (title, lines) => {
  console.log(`\n== ${title}: ${lines.length}`);
  lines.slice(0, 200).forEach((l) => console.log(`  ${l}`));
  if (lines.length > 200) console.log(`  … ${lines.length - 200} more`);
};

// 1. Missing keys
const allKeys = new Set(LANGS.flatMap((l) => Object.keys(locales[l])));
const missing = [];
for (const l of LANGS) for (const k of allKeys) if (!(k in locales[l])) missing.push(`${l}: ${k}`);
report("Missing translation keys", missing);
problems += missing.length;

// 2. Same as English
const same = [];
for (const l of LANGS.filter((x) => x !== "en")) {
  for (const [k, v] of Object.entries(locales[l])) {
    const en = locales.en[k];
    if (typeof v !== "string" || v !== en) continue;
    if (SAME_AS_ENGLISH_OK.has(v) || SAME_AS_ENGLISH_OK_KEYS.some((re) => re.test(k))) continue;
    if (SAME_AS_ENGLISH_OK_BY_LANG[l]?.has(v)) continue;
    if (!/[A-Za-z]{3,}/.test(v.replace(/\{\{[^}]+\}\}/g, ""))) continue; // numbers/placeholders only
    same.push(`${l}: ${k} = ${JSON.stringify(v)}`);
  }
}
report("Values identical to English (possible fallbacks)", same);
problems += same.length;

// 3 + 4. Source scan
const files = [];
const walk = (d) => {
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, e.name);
    if (e.isDirectory()) walk(p);
    else if (/\.(tsx?|jsx?)$/.test(e.name)) files.push(p);
  }
};
walk(path.join(ROOT, "app"));
walk(path.join(ROOT, "src"));

const undefinedKeys = [];
const hardcoded = [];
// Plural lookups use key_plural / key_one etc.; a base key counts as defined if any plural form is.
const defined = (k) =>
  k in locales.en || `${k}_plural` in locales.en || `${k}_one` in locales.en || `${k}_other` in locales.en ||
  Object.keys(locales.en).some((x) => x.startsWith(`${k}.`)); // a whole namespace passed around
for (const f of files) {
  const rel = path.relative(ROOT, f).replace(/\\/g, "/");
  const src = fs.readFileSync(f, "utf8");
  for (const m of src.matchAll(/\bt\(\s*["'`]([a-zA-Z0-9_.]+)["'`]/g)) {
    if (!defined(m[1])) undefinedKeys.push(`${rel}: ${m[1]}`);
  }
  if (!rel.endsWith(".tsx") || rel.includes("+html")) continue;
  const lines = src.split(/\r?\n/);
  lines.forEach((line, i) => {
    if (/^\s*(\/\/|\*|\/\*)/.test(line)) return;
    const loc = `${rel}:${i + 1}`;
    // JSX text between tags: >Some words<   (not "=> fn<Generic>" / "-> x<")
    for (const m of line.matchAll(/(?<![=\-])>\s*([A-Za-z][A-Za-z ,.'!?&-]{2,})\s*</g)) {
      if (/^(View|Text|Pressable)$/.test(m[1])) continue;
      hardcoded.push(`${loc}  JSX text "${m[1].trim()}"`);
    }
    for (const m of line.matchAll(/\b(title|label|placeholder|accessibilityLabel|subtitle)=["']([^"']*[A-Za-z]{2,}[^"']*)["']/g)) {
      hardcoded.push(`${loc}  ${m[1]}="${m[2]}"`);
    }
    for (const m of line.matchAll(/\b(toast|Alert\.alert)\(\s*["']([^"']*[A-Za-z]{2,}[^"']*)["']/g)) {
      hardcoded.push(`${loc}  ${m[1]}("${m[2]}")`);
    }
  });
}
// 5. Every stored enum value (src/constants.ts) needs a label in every language, and every
//    "apiErrors.*" key referenced from code (the server-error map) must exist.
const slug = (v) => v.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
const constants = fs.readFileSync(path.join(ROOT, "src", "constants.ts"), "utf8");
const arrayOf = (name) => {
  const m = constants.match(new RegExp(`export const ${name} = \\[([\\s\\S]*?)\\];`));
  return m ? [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]) : [];
};
const ENUMS = { genre: arrayOf("GENRES"), condition: arrayOf("CONDITIONS"), bookLanguage: arrayOf("LANGUAGES"), interest: arrayOf("READING_INTERESTS") };
const enumMissing = [];
for (const [kind, values] of Object.entries(ENUMS)) {
  if (!values.length) enumMissing.push(`could not read ${kind} values from src/constants.ts`);
  for (const v of values) for (const l of LANGS) if (!(`enums.${kind}.${slug(v)}` in locales[l])) enumMissing.push(`${l}: enums.${kind}.${slug(v)} ("${v}")`);
}
for (const v of arrayOf("BOOK_STATUSES")) for (const l of LANGS) if (!(`status.${slug(v)}` in locales[l])) enumMissing.push(`${l}: status.${slug(v)} ("${v}")`);
for (const f of files) {
  for (const m of fs.readFileSync(f, "utf8").matchAll(/["'](apiErrors\.[a-zA-Z]+)["']/g)) {
    if (!(m[1] in locales.en)) undefinedKeys.push(`${path.relative(ROOT, f).replace(/\\/g, "/")}: ${m[1]}`);
  }
}
report("Stored enum values without a translation", enumMissing);
problems += enumMissing.length;

report("t() keys used in code but not defined in en.json", undefinedKeys);
report("Hardcoded user-facing English in .tsx", hardcoded);
problems += undefinedKeys.length + hardcoded.length;

console.log(`\n${problems === 0 ? "OK" : `${problems} problem(s)`} — ${allKeys.size} keys × ${LANGS.length} languages`);
process.exit(problems === 0 ? 0 : 1);
