import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { I18nManager, Platform } from "react-native";
import { useTranslation } from "react-i18next";

import i18n, {
  DEFAULT_LANGUAGE,
  loadStoredLanguage,
  persistLanguage,
} from "./index";
import {
  getLanguageMeta,
  isLanguageCode,
  isRTLLanguage,
  type LanguageCode,
} from "./languages";

type LanguageContextType = {
  /** Active language code. */
  language: LanguageCode;
  /** True when the active language uses a right-to-left script. */
  isRTL: boolean;
  /** True until the persisted language has been read from storage. */
  isReady: boolean;
  /** True when the user has never explicitly chosen a language. */
  needsSelection: boolean;
  /** Change the app language. Persists locally and (when authed) server-side. */
  setLanguage: (code: LanguageCode) => Promise<void>;
  /**
   * Adopt a language coming from the signed-in user's server profile.
   * Unlike `setLanguage`, this does not mark the user as having made an
   * explicit choice and does not write back to the server.
   */
  syncFromServer: (code: string | null | undefined) => Promise<void>;
  /** i18next translate function bound to the active language. */
  t: ReturnType<typeof useTranslation>["t"];
};

const LanguageContext = createContext<LanguageContextType | null>(null);

/**
 * Applies the RTL/LTR layout direction for a language.
 *
 * React Native only reads `I18nManager.isRTL` at startup, so a direction change
 * requires an app reload to take full effect. We still flip the flag so that
 * the next launch renders correctly, and expose `isRTL` so components can
 * mirror individual styles immediately.
 */
function applyLayoutDirection(code: LanguageCode) {
  const shouldBeRTL = isRTLLanguage(code);
  I18nManager.allowRTL(true);
  if (I18nManager.isRTL !== shouldBeRTL) {
    I18nManager.forceRTL(shouldBeRTL);
  }
}

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation();
  const [language, setLanguageState] = useState<LanguageCode>(DEFAULT_LANGUAGE);
  const [isReady, setIsReady] = useState(false);
  const [needsSelection, setNeedsSelection] = useState(false);
  // Guards against writing the language back to the server before the user
  // has actually made a choice.
  const userChoseRef = useRef(false);

  // Bootstrap: read the persisted language before the first meaningful render.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const stored = await loadStoredLanguage();
      if (cancelled) return;
      if (stored) {
        setLanguageState(stored);
        await i18n.changeLanguage(stored);
        applyLayoutDirection(stored);
        setNeedsSelection(false);
      } else {
        // Brand-new user: keep the default rendering language but flag that we
        // must show the language picker before login/signup.
        setNeedsSelection(true);
      }
      setIsReady(true);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const setLanguage = useCallback(async (code: LanguageCode) => {
    userChoseRef.current = true;
    setLanguageState(code);
    setNeedsSelection(false);
    await i18n.changeLanguage(code);
    applyLayoutDirection(code);
    await persistLanguage(code);
  }, []);

  const syncFromServer = useCallback(
    async (code: string | null | undefined) => {
      if (!isLanguageCode(code)) return;
      const next: LanguageCode = code;
      // A local explicit choice always wins over a stale server value.
      if (userChoseRef.current) return;
      const stored = await loadStoredLanguage();
      if (stored) return;
      setLanguageState(next);
      setNeedsSelection(false);
      await i18n.changeLanguage(next);
      applyLayoutDirection(next);
      await persistLanguage(next);
    },
    [],
  );

  // Web only: `I18nManager.forceRTL` is a no-op in the browser, so mirror the
  // document itself. Setting `dir`/`lang` on <html> lets the browser's bidi
  // algorithm lay out mixed Arabic + Latin text correctly and flips native
  // scrollbars/overflow. Native platforms rely on I18nManager instead.
  useEffect(() => {
    if (Platform.OS !== "web" || typeof document === "undefined") return;
    const el = document.documentElement;
    el.dir = isRTLLanguage(language) ? "rtl" : "ltr";
    el.lang = language;
  }, [language]);

  const value = useMemo<LanguageContextType>(
    () => ({
      language,
      isRTL: isRTLLanguage(language),
      isReady,
      needsSelection,
      setLanguage,
      syncFromServer,
      t,
    }),
    [language, isReady, needsSelection, setLanguage, syncFromServer, t],
  );

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage(): LanguageContextType {
  const ctx = useContext(LanguageContext);
  if (!ctx) throw new Error("useLanguage must be used within a LanguageProvider");
  return ctx;
}

/** Convenience hook returning the active language metadata (name, flag, rtl). */
export function useLanguageMeta() {
  const { language } = useLanguage();
  return getLanguageMeta(language);
}

export { isRTLLanguage };
export type { LanguageCode };
