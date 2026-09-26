import React from "react";
import { Platform, StyleProp, View, ViewStyle } from "react-native";
import { BlurView } from "expo-blur";

import { useTheme } from "@/src/theme";

const RADII = { sm: 12, control: 16, card: 20, panel: 24 } as const;
export type GlassRadius = keyof typeof RADII;

/**
 * BookLoop's one glass surface: a translucent fill (warm/light in Light mode, deep green-black in
 * Dark), a thin low-contrast border, and (where it's safe to) a soft blur behind it, tinted to match
 * the active theme. Every glass field/card/pill/panel in the app should render through this component
 * rather than hand-rolling `BlurView` + colors per screen.
 *
 * `blur`:
 *  - true (default) uses a real BlurView. Reserve this for STATIC surfaces — search bars, modals,
 *    auth/welcome backgrounds, action sheets — where there's no scrolling list compositing behind it
 *    every frame.
 *  - false renders the same visual result (fill + border) without BlurView, for anything that lives
 *    inside a FlatList/ScrollView (list rows, chips in a horizontal scroller) where blur-per-item is
 *    the actual perf risk on mid/low-end Android. It still looks like glass — just not blurring
 *    whatever happens to scroll underneath it, which is rarely visible in a dense list anyway.
 *
 * Web has no native BlurView; it always falls back to the flat translucent fill there.
 */
export function GlassSurface({
  children,
  radius = "card",
  blur = true,
  intensity = 28,
  style,
  testID,
}: {
  children?: React.ReactNode;
  radius?: GlassRadius | number;
  blur?: boolean;
  /** BlurView intensity (0-100). Kept low by default — subtle, not a frosted-glass showpiece. */
  intensity?: number;
  style?: StyleProp<ViewStyle>;
  testID?: string;
}) {
  const { colors, scheme } = useTheme();
  const borderRadius = typeof radius === "number" ? radius : RADII[radius];
  const base: ViewStyle = {
    borderRadius,
    borderWidth: 1,
    borderColor: colors.glassBorder,
    overflow: "hidden",
  };

  if (blur && Platform.OS !== "web") {
    return (
      <BlurView testID={testID} intensity={intensity} tint={scheme} style={[base, style]}>
        <View style={{ flex: 1, backgroundColor: colors.glassFill }}>{children}</View>
      </BlurView>
    );
  }

  return (
    <View testID={testID} style={[base, { backgroundColor: colors.glassFill }, style]}>
      {children}
    </View>
  );
}
