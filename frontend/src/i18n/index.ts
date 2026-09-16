import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { getLocales } from "expo-localization";

import { storage } from "@/src/utils/storage";
import {
  DEFAULT_LANGUAGE,
  LANGUAGE_CODES,
  isLanguageCode,
  matchDeviceLocale,
  type LanguageCode,
} from "./languages";

import en from "./locales/en.json";
import uz from "./locales/uz.json";
import ru from "./locales/ru.json";
import it from "./locales/it.json";
import ar from "./locales/ar.json";

/** AsyncStorage key holding the user's chosen language. */
export const LANGUAGE_STORAGE_KEY = "bookloop_language";

export const resources = {
  en: { translation: en },
  uz: { translation: uz },
  ru: { translation: ru },
  it: { translation: it },
  ar: { translation: ar },
} as const;

/**
 * Reads the persisted language. Returns `null` when the user has never picked
 * one, which is what drives the first-run language selection screen.
 */
export async function loadStoredLanguage(): Promise<LanguageCode | null> {
  const value = await storage.getItem<string | null>(LANGUAGE_STORAGE_KEY, null);
  return isLanguageCode(value) ? value : null;
}

export async function persistLanguage(code: LanguageCode): Promise<void> {
  await storage.setItem(LANGUAGE_STORAGE_KEY, code);
}

/**
 * Best-effort guess at the user's language from the device locale. Only used
 * to pre-highlight an option on the first-run picker — never to silently
 * choose a language on the user's behalf.
 */
export function detectDeviceLanguage(): LanguageCode | null {
  try {
    const locales = getLocales();
    for (const locale of locales) {
      const match = matchDeviceLocale(locale.languageTag) ?? matchDeviceLocale(locale.languageCode);
      if (match) return match;
    }
  } catch {
    // expo-localization is unavailable (e.g. during SSR) — fall through.
  }
  return null;
}

if (!i18n.isInitialized) {
  i18n.use(initReactI18next).init({
    resources,
    lng: DEFAULT_LANGUAGE,
    fallbackLng: DEFAULT_LANGUAGE,
    supportedLngs: LANGUAGE_CODES,
    defaultNS: "translation",
    ns: ["translation"],
    interpolation: {
      // React already escapes interpolated values.
      escapeValue: false,
    },
    returnNull: false,
    compatibilityJSON: "v4",
  });
}

/**
 * BCP-47 locale tags used for `Intl`/`toLocale*` formatting. Arabic maps to
 * `ar` so dates and numbers render with Arabic-Indic digits and Arabic month
 * names; the rest use their plain language tag.
 */
const LOCALE_TAGS: Record<LanguageCode, string> = {
  uz: "uz-UZ",
  en: "en-US",
  ru: "ru-RU",
  it: "it-IT",
  ar: "ar",
};

/** Returns the BCP-47 locale tag for a supported language code. */
export function localeFor(language: string | null | undefined): string {
  return isLanguageCode(language) ? LOCALE_TAGS[language] : LOCALE_TAGS[DEFAULT_LANGUAGE];
}

/**
 * Formats an ISO date string using the active language's locale. Returns an
 * empty string for missing/invalid input so callers can render safely.
 */
export function formatDate(
  iso: string | null | undefined,
  language: string | null | undefined,
  options: Intl.DateTimeFormatOptions = { month: "short", day: "numeric", year: "numeric" },
): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString(localeFor(language), options);
  } catch {
    return "";
  }
}

/** Formats a number using the active language's locale (e.g. Arabic digits). */
export function formatNumber(value: number, language: string | null | undefined): string {
  try {
    return value.toLocaleString(localeFor(language));
  } catch {
    return String(value);
  }
}

export default i18n;
export { DEFAULT_LANGUAGE, LANGUAGE_CODES, isLanguageCode };
export type { LanguageCode };
