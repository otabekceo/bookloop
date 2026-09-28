/**
 * Display labels for the fixed values BookLoop stores in English (genres, book conditions, book
 * languages, reading interests, book statuses). The stored value never changes — it is what the API
 * filters and matches on — only its on-screen label is translated. An unknown value (e.g. an older
 * language no longer offered in pickers) falls back to the stored text rather than a raw key.
 */
type Translate = (key: string, options?: Record<string, unknown>) => string;

export type EnumKind = "genre" | "condition" | "bookLanguage" | "interest" | "bookStatus";

/** "Self-development" -> "self_development", "Like New" -> "like_new". */
export function enumKey(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

export function enumLabel(t: Translate, kind: EnumKind, value: string | null | undefined): string {
  if (!value) return "";
  // Book statuses already had translations under `status.*` before this helper existed.
  const key = kind === "bookStatus" ? `status.${enumKey(value)}` : `enums.${kind}.${enumKey(value)}`;
  return t(key, { defaultValue: value });
}

/** For lists that mix genres and reading interests (e.g. "shared with you" summaries). */
export function genreOrInterestLabel(t: Translate, value: string): string {
  const genre = t(`enums.genre.${enumKey(value)}`, { defaultValue: "" });
  return genre || enumLabel(t, "interest", value);
}
