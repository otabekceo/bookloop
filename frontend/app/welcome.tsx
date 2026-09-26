import { useState } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import Animated, { FadeIn, FadeInDown } from "react-native-reanimated";

import { AppText, Button, haptic } from "@/src/components/ui";
import { Logo } from "@/src/components/Logo";
import { useAuth } from "@/src/auth";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { makeStyles, useTheme, withOpacity } from "@/src/theme";

/**
 * The ONE-TIME welcome moment right after a brand-new registration (email/password or first-time
 * Google) completes. Never shown again — see completeOnboarding()/onboarding_completed on the user.
 * Deliberately quiet: no confetti, no badges, just the beginning of the reader's BookLoop shelf.
 */
export default function Welcome() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { t } = useLanguage();
  const { user, completeOnboarding } = useAuth();
  const [starting, setStarting] = useState(false);

  const firstName = (user?.name || "").trim().split(/\s+/)[0] || "";

  const start = async () => {
    if (starting) return;
    setStarting(true);
    try {
      await completeOnboarding();
    } catch {
      // Non-fatal: don't strand the user on the welcome screen over a transient network hiccup —
      // they still get into the app now, and completeOnboarding is safe to have partially failed
      // (worst case: onboarding_completed didn't persist and they'd see this screen once more).
    }
    haptic("success");
    router.replace("/(tabs)/discover");
  };

  return (
    <View style={styles.root}>
      {/* Atmospheric depth without a stock photo: a soft radial glow, derived from the active theme's
          own brand colors (Terracotta+Sage in light, the single brand green in dark) rather than a
          hardcoded hex — so dark mode never gets an orange-tinted glow. */}
      <LinearGradient
        colors={[withOpacity(colors.brand, 0.14), "transparent"]}
        style={[styles.glow, { top: -80, right: -60 }]}
      />
      <LinearGradient
        colors={[withOpacity(colors.brandSecondary, 0.12), "transparent"]}
        style={[styles.glow, { bottom: -100, left: -80 }]}
      />

      <Animated.View
        entering={FadeIn.duration(500)}
        style={[styles.content, { paddingTop: insets.top + 40, paddingBottom: insets.bottom + 24 }]}
      >
        <Animated.View entering={FadeInDown.duration(600).delay(80)} style={styles.brandWrap}>
          <Logo variant="full" height={40} />
          <AppText variant="caption" color={colors.muted} style={{ letterSpacing: 1.5, marginTop: 10 }}>
            {t("welcome.brandLine")}
          </AppText>
        </Animated.View>

        <View style={styles.body}>
          <Animated.View entering={FadeInDown.duration(600).delay(160)}>
            <AppText variant="display" style={styles.heading}>
              {t("welcome.heading", { name: firstName })}
            </AppText>
          </Animated.View>

          <Animated.View entering={FadeInDown.duration(600).delay(240)} style={{ gap: 6 }}>
            {/* "title" -> Fraunces (serif): this is an emotional/literary moment, not a label. */}
            <AppText variant="title" style={styles.literary}>
              {t("welcome.line1")}
            </AppText>
            <AppText variant="body" color={colors.muted} style={styles.literary}>
              {t("welcome.line2")}
            </AppText>
          </Animated.View>

          <Animated.View entering={FadeInDown.duration(600).delay(320)}>
            <AppText variant="body" color={colors.muted} style={styles.supporting}>
              {t("welcome.supporting")}
            </AppText>
          </Animated.View>
        </View>

        <Animated.View entering={FadeInDown.duration(600).delay(400)}>
          <Button testID="welcome-start" title={t("welcome.cta")} onPress={start} loading={starting} />
        </Animated.View>
      </Animated.View>
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface, overflow: "hidden" },
  glow: { position: "absolute", width: 280, height: 280, borderRadius: 140 },
  content: { flex: 1, paddingHorizontal: 28, justifyContent: "space-between" },
  brandWrap: { alignItems: "center" },
  body: { gap: 28 },
  heading: { textAlign: "center" },
  literary: { textAlign: "center" },
  supporting: { textAlign: "center", lineHeight: 22, marginTop: 4 },
}));
