import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useColorScheme as useSystemColorScheme } from "react-native";

import { storage } from "@/src/utils/storage";
import { setColorScheme as forceNativeColorScheme, type ColorScheme } from "@/src/theme";

export type AppearancePreference = "system" | "light" | "dark";

const STORAGE_KEY = "bookloop_appearance_preference";

type AppearanceContextType = {
  /** What the user picked in Settings — "system" by default for anyone who never chose. */
  preference: AppearancePreference;
  /** The theme actually in effect right now (resolves "system" against the live OS appearance). */
  resolvedScheme: ColorScheme;
  setPreference: (p: AppearancePreference) => Promise<void>;
};

const AppearanceContext = createContext<AppearanceContextType | null>(null);

function isPreference(v: unknown): v is AppearancePreference {
  return v === "system" || v === "light" || v === "dark";
}

/**
 * Owns the appearance preference (System/Light/Dark) and is the ONLY place that calls
 * `setColorScheme` (theme.ts): forcing "light"/"dark" makes `useColorScheme()` — and therefore
 * `useTheme()`, used everywhere — report that value everywhere; releasing it with `null` for
 * "system" lets `useColorScheme()` track the OS live, including while the app is open (RN's
 * `Appearance` module fires change events `useColorScheme()` already subscribes to — this is what
 * was broken before: the old code forced a scheme unconditionally at module load and never released
 * it, so "system" never actually tracked the OS).
 *
 * Deliberately non-blocking: unlike language (which needs a reload to apply and so gates first
 * render), a theme preference is a plain re-render, so this never adds a startup gate. Worst case on
 * a fresh boot is a single frame at the "system" default before an explicit stored choice applies.
 */
export function AppearanceProvider({ children }: { children: React.ReactNode }) {
  const systemScheme = useSystemColorScheme();
  const [preference, setPreferenceState] = useState<AppearancePreference>("system");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const stored = await storage.getItem<string | null>(STORAGE_KEY, null);
      if (cancelled || !isPreference(stored)) return;
      setPreferenceState(stored);
      forceNativeColorScheme(stored === "system" ? null : stored);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const setPreference = useCallback(async (p: AppearancePreference) => {
    setPreferenceState(p);
    forceNativeColorScheme(p === "system" ? null : p);
    await storage.setItem(STORAGE_KEY, p);
  }, []);

  const resolvedScheme: ColorScheme = preference === "system" ? (systemScheme === "dark" ? "dark" : "light") : preference;

  return (
    <AppearanceContext.Provider value={{ preference, resolvedScheme, setPreference }}>
      {children}
    </AppearanceContext.Provider>
  );
}

export function useAppearance(): AppearanceContextType {
  const ctx = useContext(AppearanceContext);
  if (!ctx) throw new Error("useAppearance must be used within AppearanceProvider");
  return ctx;
}
