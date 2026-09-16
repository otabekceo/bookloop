import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleProp,
  Text,
  TextProps,
  TextStyle,
  View,
  ViewStyle,
} from "react-native";
import { Image } from "expo-image";
import Animated, { FadeInDown, FadeOutUp } from "react-native-reanimated";
import { LinearGradient } from "expo-linear-gradient";
import * as Haptics from "expo-haptics";
import { BookOpen, Star } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { FONTS, fontsForLanguage, type FontRole } from "@/src/typography";
import { resolveImage } from "@/src/api";
import { useLanguage } from "@/src/i18n/LanguageProvider";

export function haptic(kind: "light" | "success" | "selection" = "light") {
  try {
    if (kind === "success") Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    else if (kind === "selection") Haptics.selectionAsync();
    else Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
  } catch {}
}

/**
 * True when the active language renders right-to-left (Arabic). Use this to
 * mirror directional chrome (back arrows, chevrons, progress) while leaving
 * non-directional icons untouched.
 */
export function useRTL(): boolean {
  return useLanguage().isRTL;
}

/**
 * Wraps a directional icon and mirrors it horizontally under RTL. Only use for
 * icons whose meaning is tied to reading direction (back/forward arrows,
 * chevrons, next/previous). Do NOT wrap icons like stars, hearts, cameras.
 */
export function DirectionalIcon({ children, mirror = true }: { children: React.ReactNode; mirror?: boolean }) {
  const isRTL = useRTL();
  if (!isRTL || !mirror) return <>{children}</>;
  return <View style={{ transform: [{ scaleX: -1 }] }}>{children}</View>;
}

// ---------------------------------------------------------------------------
// AppText
// ---------------------------------------------------------------------------
type Variant = "display" | "title" | "heading" | "body" | "label" | "caption" | "button";

// Font role per variant — resolved against the active language's font map so
// Arabic swaps in Noto Sans Arabic while other languages keep Fraunces + DM Sans.
const variantFontRole: Record<Variant, FontRole> = {
  display: "display",
  title: "displaySemi",
  heading: "bold",
  body: "regular",
  label: "medium",
  caption: "regular",
  button: "bold",
};

const variantMetrics: Record<Variant, { fontSize: number; lineHeight: number }> = {
  display: { fontSize: 30, lineHeight: 36 },
  title: { fontSize: 22, lineHeight: 28 },
  heading: { fontSize: 16, lineHeight: 22 },
  body: { fontSize: 14, lineHeight: 20 },
  label: { fontSize: 13, lineHeight: 18 },
  caption: { fontSize: 12, lineHeight: 16 },
  button: { fontSize: 15, lineHeight: 20 },
};

export function AppText({
  variant = "body",
  color,
  style,
  children,
  ...rest
}: TextProps & { variant?: Variant; color?: string }) {
  const { colors } = useTheme();
  const { language, isRTL } = useLanguage();
  const fonts = fontsForLanguage(language);
  const fontFamily = fonts[variantFontRole[variant]];
  return (
    <Text
      {...rest}
      style={[
        variantMetrics[variant],
        { fontFamily, color: color || colors.onSurface },
        // Arabic reads right-to-left: default text alignment follows the script
        // unless a caller explicitly overrides it.
        isRTL ? { textAlign: "right", writingDirection: "rtl" } : null,
        style as StyleProp<TextStyle>,
      ]}
    >
      {children}
    </Text>
  );
}

// ---------------------------------------------------------------------------
// Button
// ---------------------------------------------------------------------------
export function Button({
  title,
  onPress,
  variant = "primary",
  loading,
  disabled,
  icon,
  style,
  testID,
}: {
  title: string;
  onPress: () => void;
  variant?: "primary" | "secondary" | "ghost" | "outline";
  loading?: boolean;
  disabled?: boolean;
  icon?: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  testID?: string;
}) {
  const { colors } = useTheme();
  const bg =
    variant === "primary"
      ? colors.brandPrimary
      : variant === "secondary"
        ? colors.brandSecondary
        : variant === "outline"
          ? "transparent"
          : "transparent";
  const fg =
    variant === "primary"
      ? colors.onBrandPrimary
      : variant === "secondary"
        ? colors.onBrandSecondary
        : colors.brandPrimary;
  return (
    <Pressable
      testID={testID}
      disabled={disabled || loading}
      onPress={() => {
        haptic("light");
        onPress();
      }}
      style={({ pressed }) => [
        {
          backgroundColor: bg,
          borderWidth: variant === "outline" ? 1.5 : 0,
          borderColor: colors.brandPrimary,
          borderRadius: 14,
          height: 52,
          alignItems: "center",
          justifyContent: "center",
          flexDirection: "row",
          gap: 8,
          opacity: disabled ? 0.5 : pressed ? 0.85 : 1,
          paddingHorizontal: 20,
        },
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={fg} />
      ) : (
        <>
          {icon}
          <AppText variant="button" color={fg}>
            {title}
          </AppText>
        </>
      )}
    </Pressable>
  );
}

// ---------------------------------------------------------------------------
// Chip + ChipRow (horizontal scroller, chrome)
// ---------------------------------------------------------------------------
export function Chip({
  label,
  selected,
  onPress,
  testID,
  leading,
}: {
  label: string;
  selected?: boolean;
  onPress?: () => void;
  testID?: string;
  leading?: React.ReactNode;
}) {
  const { colors } = useTheme();
  return (
    <Pressable
      testID={testID}
      onPress={() => {
        if (onPress) {
          haptic("selection");
          onPress();
        }
      }}
      style={{
        height: 36,
        flexShrink: 0,
        paddingHorizontal: 14,
        borderRadius: 999,
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        backgroundColor: selected ? colors.surfaceInverse : colors.surfaceSecondary,
        borderWidth: 1,
        borderColor: selected ? colors.surfaceInverse : colors.border,
      }}
    >
      {leading}
      <AppText variant="label" color={selected ? colors.onSurfaceInverse : colors.onSurfaceSecondary}>
        {label}
      </AppText>
    </Pressable>
  );
}

export function ChipRow({ children, style }: { children: React.ReactNode; style?: StyleProp<ViewStyle> }) {
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      style={[{ height: 56, flexGrow: 0, flexShrink: 0 }, style]}
      contentContainerStyle={{ gap: 8, paddingHorizontal: 20, alignItems: "center" }}
    >
      {children}
    </ScrollView>
  );
}

// ---------------------------------------------------------------------------
// Avatar
// ---------------------------------------------------------------------------
export function Avatar({ uri, name, size = 48 }: { uri?: string | null; name?: string; size?: number }) {
  const { colors } = useTheme();
  const src = resolveImage(uri);
  const initials = (name || "?")
    .split(" ")
    .map((s) => s[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
  if (src) {
    return (
      <Image
        source={{ uri: src }}
        style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: colors.surfaceTertiary }}
        contentFit="cover"
        transition={200}
      />
    );
  }
  return (
    <View
      style={{
        width: size,
        height: size,
        borderRadius: size / 2,
        backgroundColor: colors.brandSecondary,
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <AppText variant="heading" color={colors.onBrandSecondary} style={{ fontSize: size * 0.36 }}>
        {initials}
      </AppText>
    </View>
  );
}

// ---------------------------------------------------------------------------
// Stars
// ---------------------------------------------------------------------------
export function Stars({ value, size = 14 }: { value: number; size?: number }) {
  const { colors } = useTheme();
  return (
    <View style={{ flexDirection: "row", gap: 1 }}>
      {[1, 2, 3, 4, 5].map((i) => (
        <Star
          key={i}
          size={size}
          color={colors.star}
          weight={i <= Math.round(value) ? "fill" : "regular"}
        />
      ))}
    </View>
  );
}

export function RatingPill({ rating, count }: { rating: number; count?: number }) {
  const { colors } = useTheme();
  const { t } = useLanguage();
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
      <Star size={13} color={colors.star} weight="fill" />
      <AppText variant="label" color={colors.onSurface}>
        {rating > 0 ? rating.toFixed(1) : t("common.new")}
        {count ? ` (${count})` : ""}
      </AppText>
    </View>
  );
}

// ---------------------------------------------------------------------------
// BookCover (with generated placeholder + remote error fallback)
// ---------------------------------------------------------------------------
const COVER_PALETTES: [string, string][] = [
  ["#879B7A", "#6E8368"], // sage
  ["#D96C4A", "#C0552F"], // terracotta
  ["#17211F", "#2C3D39"], // ink
  ["#4A6D8C", "#375169"], // blue
  ["#B08968", "#8C6A4E"], // beige/brown
  ["#9C4A4A", "#7E3838"], // red
  ["#5B7A6B", "#456051"], // deep green
];

function paletteFor(seed: string): [string, string] {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) & 0xffff;
  return COVER_PALETTES[h % COVER_PALETTES.length];
}

export function BookCover({
  uri,
  width,
  title,
  style,
}: {
  uri?: string | null;
  width: number;
  title?: string;
  style?: StyleProp<ViewStyle>;
}) {
  const [failed, setFailed] = useState(false);
  const { language } = useLanguage();
  const fonts = fontsForLanguage(language);
  const src = resolveImage(uri);
  const height = width * 1.5;

  if (src && !failed) {
    return (
      <Image
        source={{ uri: src }}
        style={[{ width, height, borderRadius: 8, backgroundColor: "#00000010" }, style]}
        contentFit="cover"
        transition={200}
        onError={() => setFailed(true)}
      />
    );
  }

  const [c1, c2] = paletteFor(title || uri || "book");
  const showTitle = width >= 70 && !!title;
  return (
    <LinearGradient
      colors={[c1, c2]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={[
        { width, height, borderRadius: 8, padding: width * 0.1, justifyContent: "space-between", overflow: "hidden" },
        style,
      ]}
    >
      {showTitle ? (
        <Text
          numberOfLines={4}
          style={{ color: "#FFFFFF", fontFamily: fonts.displaySemi, fontSize: Math.max(11, width * 0.11), lineHeight: Math.max(14, width * 0.14) }}
        >
          {title}
        </Text>
      ) : (
        <BookOpen size={width * 0.32} color="rgba(255,255,255,0.85)" weight="light" />
      )}
      <View style={{ flexDirection: "row", alignItems: "center", gap: 3, opacity: 0.85 }}>
        <View style={{ width: width * 0.09, height: width * 0.09, borderRadius: width * 0.045, borderWidth: 1.5, borderColor: "#FFFFFF" }} />
        <View style={{ width: width * 0.09, height: width * 0.09, borderRadius: width * 0.045, borderWidth: 1.5, borderColor: "#FFFFFF" }} />
      </View>
    </LinearGradient>
  );
}

// ---------------------------------------------------------------------------
// StatusBadge
// ---------------------------------------------------------------------------
export function StatusBadge({ status }: { status: string }) {
  const { colors } = useTheme();
  const { language } = useLanguage();
  const fonts = fontsForLanguage(language);
  const map: Record<string, { bg: string; fg: string }> = {
    Available: { bg: colors.sageSoft, fg: colors.brandSecondary },
    Reserved: { bg: colors.brandTertiary, fg: colors.brandPrimary },
    Swapped: { bg: colors.surfaceTertiary, fg: colors.muted },
  };
  const c = map[status] || map.Available;
  return (
    <View style={{ backgroundColor: c.bg, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6, alignSelf: "flex-start" }}>
      <AppText variant="caption" color={c.fg} style={{ fontFamily: fonts.bold }}>
        {status}
      </AppText>
    </View>
  );
}

export function ExchangingDot({ active, label }: { active: boolean; label?: boolean }) {
  const { colors } = useTheme();
  const { t } = useLanguage();
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 5 }}>
      <View
        style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: active ? colors.brandSecondary : colors.muted }}
      />
      {label && (
        <AppText variant="caption" color={active ? colors.brandSecondary : colors.muted}>
          {active ? t("status.exchanging") : t("status.paused")}
        </AppText>
      )}
    </View>
  );
}

// ---------------------------------------------------------------------------
// EmptyState
// ---------------------------------------------------------------------------
export function EmptyState({
  icon,
  title,
  subtitle,
  action,
}: {
  icon?: React.ReactNode;
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
}) {
  const { colors } = useTheme();
  return (
    <View style={{ alignItems: "center", justifyContent: "center", padding: 32, gap: 10 }}>
      {icon}
      <AppText variant="title" style={{ textAlign: "center" }}>
        {title}
      </AppText>
      {subtitle && (
        <AppText variant="body" color={colors.muted} style={{ textAlign: "center", maxWidth: 280 }}>
          {subtitle}
        </AppText>
      )}
      {action}
    </View>
  );
}

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------
type ToastKind = "success" | "error" | "info";
const ToastContext = createContext<(msg: string, kind?: ToastKind) => void>(() => {});

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toast, setToast] = useState<{ msg: string; kind: ToastKind } | null>(null);
  const timer = useRef<any>(null);
  const styles = useToastStyles();
  const show = useCallback((msg: string, kind: ToastKind = "info") => {
    setToast({ msg, kind });
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setToast(null), 2600);
  }, []);
  useEffect(() => () => timer.current && clearTimeout(timer.current), []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      {toast && (
        <Animated.View
          entering={FadeInDown} exiting={FadeOutUp}
          pointerEvents="none"
          style={[styles.toast, toast.kind === "error" && styles.error, toast.kind === "success" && styles.success]}
        >
          <AppText variant="label" color="#FFFFFF" style={{ textAlign: "center" }}>
            {toast.msg}
          </AppText>
        </Animated.View>
      )}
    </ToastContext.Provider>
  );
}

const useToastStyles = makeStyles((colors) => ({
  toast: {
    position: "absolute",
    bottom: 110,
    alignSelf: "center",
    left: 24,
    right: 24,
    backgroundColor: colors.surfaceInverse,
    paddingVertical: 12,
    paddingHorizontal: 18,
    borderRadius: 14,
    zIndex: 9999,
  },
  error: { backgroundColor: colors.error },
  success: { backgroundColor: colors.brandSecondary },
}));

export function useToast() {
  return useContext(ToastContext);
}

// ---------------------------------------------------------------------------
// Input field
// ---------------------------------------------------------------------------
import { TextInput, TextInputProps } from "react-native";

export function Field({
  label,
  style,
  onSurface,
  ...rest
}: TextInputProps & { label?: string; onSurface?: boolean }) {
  const { colors } = useTheme();
  const { language, isRTL } = useLanguage();
  const fonts = fontsForLanguage(language);
  return (
    <View style={{ gap: 6 }}>
      {label && (
        <AppText variant="label" color={colors.onSurface}>
          {label}
        </AppText>
      )}
      <TextInput
        placeholderTextColor={colors.muted}
        {...rest}
        style={[
          {
            backgroundColor: onSurface ? colors.surfaceTertiary : colors.surfaceSecondary,
            borderRadius: 12,
            borderWidth: 1,
            borderColor: colors.border,
            paddingHorizontal: 14,
            paddingVertical: 14,
            fontFamily: fonts.regular,
            fontSize: 15,
            color: colors.onSurface,
            textAlign: isRTL ? "right" : "left",
            writingDirection: isRTL ? "rtl" : "ltr",
          },
          style,
        ]}
      />
    </View>
  );
}
