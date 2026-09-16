// Canonical list of languages BookLoop supports. This is the single source of
// truth used by the language picker, the settings screen and the i18n core.
export type LanguageCode = "uz" | "en" | "ru" | "it" | "ar";

export type LanguageMeta = {
  code: LanguageCode;
  /** Endonym — the language's name written in that language. */
  nativeName: string;
  /** English name, used for accessibility labels and fallbacks. */
  englishName: string;
  flag: string;
  /** Right-to-left script (Arabic). */
  rtl: boolean;
};

export const LANGUAGES: LanguageMeta[] = [
  { code: "uz", nativeName: "O'zbek", englishName: "Uzbek", flag: "🇺🇿", rtl: false },
  { code: "en", nativeName: "English", englishName: "English", flag: "🇬🇧", rtl: false },
  { code: "ru", nativeName: "Русский", englishName: "Russian", flag: "🇷🇺", rtl: false },
  { code: "it", nativeName: "Italiano", englishName: "Italian", flag: "🇮🇹", rtl: false },
  { code: "ar", nativeName: "العربية", englishName: "Arabic", flag: "🇸🇦", rtl: true },
];

export const DEFAULT_LANGUAGE: LanguageCode = "en";

export const LANGUAGE_CODES: LanguageCode[] = LANGUAGES.map((l) => l.code);

export function isLanguageCode(value: unknown): value is LanguageCode {
  return typeof value === "string" && (LANGUAGE_CODES as string[]).includes(value);
}

export function getLanguageMeta(code: string | null | undefined): LanguageMeta {
  return LANGUAGES.find((l) => l.code === code) ?? LANGUAGES.find((l) => l.code === DEFAULT_LANGUAGE)!;
}

export function isRTLLanguage(code: string | null | undefined): boolean {
  return getLanguageMeta(code).rtl;
}

/**
 * Maps an arbitrary device locale (e.g. "ar-SA", "uz_Cyrl") onto one of our
 * supported languages. Falls back to `null` so callers can decide whether to
 * apply a default.
 */
export function matchDeviceLocale(locale: string | null | undefined): LanguageCode | null {
  if (!locale) return null;
  const base = locale.toLowerCase().split(/[-_]/)[0];
  return isLanguageCode(base) ? base : null;
}
