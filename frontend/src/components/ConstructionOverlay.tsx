import { useEffect, useState } from "react";
import { StyleSheet, Text, View, useWindowDimensions } from "react-native";
import Svg, { Defs, Pattern, Rect } from "react-native-svg";
import Animated, {
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withSequence,
  withTiming,
} from "react-native-reanimated";

import { AppText } from "@/src/components/ui";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { useTheme } from "@/src/theme";

// Module-level, not persisted anywhere (AsyncStorage or otherwise): this is exactly what makes it
// "once per app PROCESS" rather than "once ever". A true cold start (fresh install, force-close ->
// reopen, or the OS killing and relaunching the process) re-evaluates this whole JS module, resetting
// it to false. Backgrounding/foregrounding, tab switches, re-renders and query refetches never
// re-evaluate the module, so they never see it as false again — no persistence layer needed or wanted.
let shownThisProcess = false;

// Semantic construction colors — fixed in both Light and Dark, not theme tokens.
const TAPE_YELLOW = "#FFC629";
const TAPE_BLACK = "#111111";

const TOTAL_MS = 2000;
const ENTER_MS = 300;
const EXIT_START_MS = 1600;
const EXIT_MS = TOTAL_MS - EXIT_START_MS;

/** A thin diagonal hazard-chevron accent strip (the classic 45°-rotated black/yellow pattern). */
function HazardEdge({ width, height }: { width: number; height: number }) {
  return (
    <Svg width={width} height={height}>
      <Defs>
        <Pattern id="hazard" patternUnits="userSpaceOnUse" width={16} height={height} patternTransform="rotate(45)">
          <Rect width={16} height={height} fill={TAPE_YELLOW} />
          <Rect width={8} height={height} fill={TAPE_BLACK} />
        </Pattern>
      </Defs>
      <Rect width={width} height={height} fill="url(#hazard)" />
    </Svg>
  );
}

function TapeStrip({ angle, fromSide, top }: { angle: number; fromSide: "left" | "right"; top: number }) {
  const { width: screenW } = useWindowDimensions();
  const stripW = screenW * 1.7;
  const stripH = 46;
  const startX = fromSide === "left" ? -stripW * 0.6 : stripW * 0.6;

  const tx = useSharedValue(startX);
  const opacity = useSharedValue(0);

  useEffect(() => {
    // Entrance (0-300ms): slide + fade in. `tx` simply holds at 0 once this completes — no need to
    // explicitly re-anchor it before the hold-phase drift kicks in below.
    opacity.value = withTiming(1, { duration: ENTER_MS });
    tx.value = withTiming(0, { duration: ENTER_MS, easing: Easing.out(Easing.cubic) });
    // Hold (300-1600ms): a very subtle, slow drift — "not chaotic", just barely alive.
    const driftTimer = setTimeout(() => {
      tx.value = withRepeat(withSequence(withTiming(5, { duration: 700 }), withTiming(-5, { duration: 700 })), -1, true);
    }, ENTER_MS);
    // Exit (1600-2000ms): fade + continue off in the direction it came from.
    const exitTimer = setTimeout(() => {
      opacity.value = withTiming(0, { duration: EXIT_MS });
      tx.value = withTiming(fromSide === "left" ? -stripW * 0.25 : stripW * 0.25, { duration: EXIT_MS });
    }, EXIT_START_MS);
    return () => {
      clearTimeout(driftTimer);
      clearTimeout(exitTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs its own fixed timeline once on mount
  }, []);

  const animStyle = useAnimatedStyle(() => ({
    opacity: opacity.value,
    transform: [{ translateX: tx.value }, { rotate: `${angle}deg` }],
  }));

  return (
    <Animated.View
      style={[
        { position: "absolute", top, left: (screenW - stripW) / 2, width: stripW, height: stripH },
        animStyle,
      ]}
    >
      <View style={{ flex: 1, backgroundColor: TAPE_YELLOW, overflow: "hidden" }}>
        <HazardEdge width={stripW} height={7} />
        <View style={styles.tapeTextRow}>
          {Array.from({ length: 6 }).map((_, i) => (
            <Text key={i} style={styles.tapeText} numberOfLines={1}>
              UNDER CONSTRUCTION
            </Text>
          ))}
        </View>
        <View style={{ position: "absolute", bottom: 0, left: 0, right: 0 }}>
          <HazardEdge width={stripW} height={7} />
        </View>
      </View>
    </Animated.View>
  );
}

/**
 * ~2s cold-launch-only construction-tape moment. Purely a visual overlay: it never gates auth,
 * language, or routing initialization — all of that proceeds completely independently underneath it
 * the whole time, exactly as if this component didn't exist. Dismissal is a plain `setTimeout`, not
 * dependent on any animation callback firing, so it can never become a startup blocker even if the
 * animation itself misbehaves.
 */
export function ConstructionOverlay() {
  const { colors } = useTheme();
  const { t } = useLanguage();
  const { height: screenH } = useWindowDimensions();
  const [visible] = useState(() => {
    if (shownThisProcess) return false;
    shownThisProcess = true;
    return true;
  });
  const [mounted, setMounted] = useState(visible);

  const messageOpacity = useSharedValue(0);

  useEffect(() => {
    if (!visible) return;
    messageOpacity.value = withTiming(1, { duration: ENTER_MS });
    const exitTimer = setTimeout(() => {
      messageOpacity.value = withTiming(0, { duration: EXIT_MS });
    }, EXIT_START_MS);
    // Hard safety net: dismiss on a fixed timer regardless of animation state. A small buffer past
    // TOTAL_MS lets the exit fade actually finish being visible rather than cutting it off.
    const doneTimer = setTimeout(() => setMounted(false), TOTAL_MS + 150);
    return () => {
      clearTimeout(exitTimer);
      clearTimeout(doneTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  // useAnimatedStyle must run unconditionally (Rules of Hooks) — the `mounted` bail-out happens after.
  const messageStyle = useAnimatedStyle(() => ({ opacity: messageOpacity.value }));

  if (!mounted) return null;

  return (
    <View style={[StyleSheet.absoluteFill, { backgroundColor: colors.surface, zIndex: 9999 }]} pointerEvents="auto">
      <Animated.View style={[styles.messageWrap, { top: screenH * 0.28 }, messageStyle]}>
        <AppText variant="heading" style={{ textAlign: "center" }}>
          {t("constructionOverlay.message")}
        </AppText>
      </Animated.View>
      <TapeStrip angle={-12} fromSide="left" top={screenH * 0.48} />
      <TapeStrip angle={12} fromSide="right" top={screenH * 0.56} />
    </View>
  );
}

const styles = StyleSheet.create({
  tapeTextRow: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-around",
    paddingHorizontal: 8,
  },
  tapeText: {
    color: TAPE_BLACK,
    fontWeight: "800",
    fontSize: 12,
    letterSpacing: 1,
  },
  messageWrap: {
    position: "absolute",
    left: 32,
    right: 32,
  },
});
