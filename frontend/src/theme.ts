// Design tokens for BookLoop. Two full themes — "Warm Editorial" (light) and "Deep Botanical" (dark)
// — sharing one design language (modernism + subtle glassmorphism + editorial book design). Screens
// and shared components only ever read `colors.<token>`; which theme those tokens resolve to is
// decided centrally here and in src/appearance.tsx, never with `isDark ? "#.." : "#.."` in a screen.
//
// Light keeps BookLoop's warm palette with Deep Ink green (#17211F) as the one primary interactive
// color: buttons, active/selected states, links, nav, focus. Terracotta is a light-mode ACCENT only
// (`accent*` tokens: status/demand/achievement badges, decorative glow) — never a primary action.
// Dark uses the authoritative palette below — a deliberately DIFFERENT color language (deep botanical
// green, not a black/orange restyle of light): Terracotta is not forced into dark mode.

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
  muted: "#646B69", // captions, timestamps, placeholders — ≥4.5:1 on every light surface (WCAG AA)

  // Brand
  brand: "#17211F", // Deep Ink — BookLoop's primary brand green (focus rings, active borders)
  onBrand: "#FFFFFF",
  brandPrimary: "#17211F", // primary CTA, swap actions, links, active nav/states
  onBrandPrimary: "#FFFFFF",
  brandSecondary: "#879B7A", // Sage — available/exchanging, positive, location (fills, icons, dots)
  onBrandSecondary: "#17211F", // Deep Ink on Sage: 5.5:1 (white on Sage is only 3.0:1)
  // Sage as TEXT (e.g. "Linked", "Available"): Sage itself is 2.7–3.0:1 on light surfaces, too faint
  // for text. Same hue, darkened to ≥5:1. Fills and icons keep brandSecondary.
  brandSecondaryText: "#56684B",
  brandTertiary: "#E7ECE3", // soft sage wash — selected chips/rows/filters, tags
  onBrandTertiary: "#17211F",

  // Accent — Terracotta, for semantic/decorative emphasis only (Reserved status, "wanted" demand,
  // earned badges, welcome glow). Never a primary action or selected state.
  accent: "#D96C4A",
  onAccent: "#FFFFFF",
  accentSoft: "#F2E8E3",
  onAccentSoft: "#A84A2C", // Terracotta, darkened for TEXT on the soft wash: 4.7:1 (#D96C4A is 2.8:1)

  // Status
  success: "#879B7A",
  onSuccess: "#17211F",
  warning: "#E8A365",
  onWarning: "#17211F",
  error: "#B33939",
  onError: "#FFFFFF",
  info: "#707775",
  onInfo: "#FFFFFF",
  rating: "#B8722A", // rating stars — 3.8:1 on white (non-text minimum is 3:1; #E8A365 was 2.1:1)
  notification: "#B33939", // notification dots/alert indicators — distinct from generic `error`

  // Lines
  border: "#E8E5E1",
  borderStrong: "#D6D1C9",
  divider: "#E8E5E1",

  // Extra
  sage: "#879B7A",
  sageSoft: "#E7ECE3",

  // Glass — subtle even in light: warm translucent surfaces, not plain white, per the design system.
  glassFill: "rgba(255,255,255,0.6)",
  glassBorder: "rgba(23,33,31,0.08)",
};

// ----------------------------------------------------------------------------
// Dark — "Deep Botanical". Authoritative palette (do not substitute generic black/arbitrary greens):
//   #0B1512 background · #16211D surface/card · #25332C border · #4E9E76 primary brand green
//   #1E3229 active/selected tint (paired with #4E9E76) · #F0B93E rating ONLY · #E8503A alert/notification ONLY
//   #F5F6F3 primary text · #8FA098 secondary text · #35473B/#1D3336 decorative map terrain ONLY
// Terracotta is intentionally ABSENT here: dark mode's one interactive color is the brand green, used
// for primary actions, active nav, status dots, map pins, and exchanging/success state alike — not a
// black-background reskin of the light (terracotta) button system.
// ----------------------------------------------------------------------------
const dark = {
  // Surfaces
  surface: "#0B1512", // DARK BACKGROUND — BASE
  onSurface: "#F5F6F3", // PRIMARY TEXT
  surfaceSecondary: "#16211D", // DARK SURFACE / CARD
  onSurfaceSecondary: "#F5F6F3",
  surfaceTertiary: "#16211D", // inputs/chips share the one card surface tier this palette defines
  onSurfaceTertiary: "#F5F6F3",
  surfaceInverse: "#16211D", // sheets/modals — same tier; separation comes from the border, not a 4th shade
  onSurfaceInverse: "#F5F6F3",
  muted: "#8FA098", // SECONDARY TEXT

  // Brand — one interactive green does the work Terracotta+Sage split in light mode.
  brand: "#4E9E76", // PRIMARY BRAND GREEN
  onBrand: "#0B1512", // dark ink on the mid-bright green reads far better than white (~5.7:1 vs ~3.2:1)
  brandPrimary: "#4E9E76",
  onBrandPrimary: "#0B1512",
  brandSecondary: "#4E9E76", // exchanging/positive/location — same green, not a second hue
  onBrandSecondary: "#0B1512",
  brandSecondaryText: "#4E9E76", // 5.1–5.7:1 on dark surfaces already
  brandTertiary: "#1E3229", // GREEN TINT — ACTIVE BACKGROUND (selected chips/filters/rows)
  onBrandTertiary: "#4E9E76", // exact pairing specified for selected/active state text

  // Accent — no Terracotta in dark: the same green/tint pairing the brand tokens use.
  accent: "#4E9E76",
  onAccent: "#0B1512",
  accentSoft: "#1E3229",
  onAccentSoft: "#4E9E76",

  // Status
  success: "#4E9E76",
  onSuccess: "#0B1512",
  warning: "#F0B93E",
  onWarning: "#0B1512",
  error: "#E8503A",
  onError: "#0B1512", // dark ink on the alert red: 5.0:1 (white was 3.7:1)
  info: "#8FA098",
  onInfo: "#0B1512",
  rating: "#F0B93E", // GOLD — rating stars ONLY, never a generic accent
  notification: "#E8503A", // ALERT/NOTIFICATION — badge dots only, never decorative or a CTA

  // Lines
  border: "#25332C",
  borderStrong: "#33463C",
  divider: "#25332C",

  // Extra
  sage: "#4E9E76",
  sageSoft: "#1E3229",
  // Decorative map terrain ONLY (src/components — map screen). Never used elsewhere in the UI.
  mapLand: "#35473B",
  mapWater: "#1D3336",

  // Glass — translucent surface derived from the card color, thin border derived from the line color.
  glassFill: "rgba(22,33,29,0.6)",
  glassBorder: "rgba(37,51,44,0.8)",
};

export type ThemeColors = typeof light;

export const defaultScheme = "light" satisfies ColorScheme;

export const themes: { light: ThemeColors; dark: ThemeColors } = { light, dark };

/** Forces the native-reported color scheme (`null` releases the override and lets it track the OS
 * live). Only src/appearance.tsx should call this — see AppearanceProvider for why. */
export function setColorScheme(scheme: ColorScheme | null) {
  // React Native 0.86 releases the override with "unspecified" (null is no longer the reset value).
  Appearance.setColorScheme?.(scheme ?? "unspecified");
}

export function useTheme(): { scheme: ColorScheme; colors: ThemeColors } {
  const system = useColorScheme();
  // useColorScheme() can also report "unspecified" (or null) — both mean "use the default theme".
  const scheme: ColorScheme = system === "light" || system === "dark" ? system : defaultScheme;
  return { scheme, colors: themes[scheme] };
}

export function makeStyles<T extends StyleSheet.NamedStyles<T> | StyleSheet.NamedStyles<any>>(
  factory: (colors: ThemeColors) => T & StyleSheet.NamedStyles<any>,
): () => T {
  return function useStyles(): T {
    const { colors } = useTheme();
    return useMemo(() => StyleSheet.create(factory(colors)), [colors]);
  };
}

/** `colors.brand` + a fixed alpha, for one-off decorative washes (e.g. Welcome's background glow)
 * that should follow the active theme's brand color rather than a hardcoded rgba literal. */
export function withOpacity(hex: string, alpha: number): string {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (!m) return hex;
  const [r, g, b] = [m[1], m[2], m[3]].map((h) => parseInt(h, 16));
  return `rgba(${r},${g},${b},${alpha})`;
}
