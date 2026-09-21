// Design tokens for BookLoop — "Warm Editorial" light theme.
// Keys match the "color" block of docs/design_guidelines.json.

import { useMemo } from "react";
import { Appearance, StyleSheet, useColorScheme } from "react-native";

export type ColorScheme = "light" | "dark";

const light = {
  // Surfaces
  surface: "#F7F4EE", // Warm Paper — main background
  onSurface: "#17211F", // Deep Ink
  surfaceSecondary: "#FFFFFF", // cards, panels, book sections
  onSurfaceSecondary: "#17211F",
  surfaceTertiary: "#F0ECE1", // inputs, chips, deepest nesting
  onSurfaceTertiary: "#17211F",
  surfaceInverse: "#17211F", // Deep Ink surfaces, tooltips
  onSurfaceInverse: "#F7F4EE",
  muted: "#707775", // captions, timestamps, placeholders

  // Brand
  brand: "#D96C4A", // Terracotta
  onBrand: "#FFFFFF",
  brandPrimary: "#D96C4A", // primary CTA, swap actions, active states
  onBrandPrimary: "#FFFFFF",
  brandSecondary: "#879B7A", // Sage — available/exchanging, positive, location
  onBrandSecondary: "#FFFFFF",
  brandTertiary: "#F2E8E3", // soft terracotta wash for tags/badges
  onBrandTertiary: "#D96C4A",

  // Status
  success: "#879B7A",
  onSuccess: "#FFFFFF",
  warning: "#E8A365",
  onWarning: "#17211F",
  error: "#B33939",
  onError: "#FFFFFF",
  info: "#707775",
  onInfo: "#FFFFFF",

  // Lines
  border: "#E8E5E1",
  borderStrong: "#D6D1C9",
  divider: "#E8E5E1",

  // Extra
  sage: "#879B7A",
  sageSoft: "#E7ECE3",
  star: "#E8A365",
};

export type ThemeColors = typeof light;

export const defaultScheme = "light" satisfies ColorScheme;

export const themes: { light: ThemeColors; dark?: ThemeColors } = { light };

export function setColorScheme(scheme: ColorScheme | null) {
  Appearance.setColorScheme?.(scheme);
}

setColorScheme?.(themes.dark ? null : defaultScheme);

export function useTheme(): { scheme: ColorScheme; colors: ThemeColors } {
  const system = useColorScheme();
  const scheme: ColorScheme = system && themes[system] ? system : defaultScheme;
  return { scheme, colors: themes[scheme] ?? themes.light };
}

export function makeStyles<T extends StyleSheet.NamedStyles<T> | StyleSheet.NamedStyles<any>>(
  factory: (colors: ThemeColors) => T & StyleSheet.NamedStyles<any>,
): () => T {
  return function useStyles(): T {
    const { colors } = useTheme();
    return useMemo(() => StyleSheet.create(factory(colors)), [colors]);
  };
}
