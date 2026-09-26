import { View } from "react-native";

import { AppText } from "@/src/components/ui";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { useTheme } from "@/src/theme";

const STEPS = ["account", "verify", "password"] as const;
type Step = (typeof STEPS)[number];

const LABEL_KEYS: Record<Step, string> = {
  account: "auth.stepAccount",
  verify: "auth.stepVerify",
  password: "auth.stepPassword",
};

/**
 * The subtle "Account -> Verify -> Password" progress indicator shown across the Sign Up, Verify
 * Email, and Create Password screens — three dots connected by a line, the current (and any
 * completed) step filled in Terracotta, the rest a muted outline. Deliberately restrained: a
 * wayfinding cue, not a stepper widget.
 */
export function AuthProgress({ step }: { step: Step }) {
  const { colors } = useTheme();
  const { t, isRTL } = useLanguage();
  const activeIndex = STEPS.indexOf(step);

  return (
    <View
      testID="auth-progress"
      style={{ flexDirection: isRTL ? "row-reverse" : "row", alignItems: "center", alignSelf: "center", gap: 6 }}
    >
      {STEPS.map((s, i) => {
        const state = i < activeIndex ? "done" : i === activeIndex ? "active" : "upcoming";
        return (
          <View key={s} style={{ flexDirection: isRTL ? "row-reverse" : "row", alignItems: "center" }}>
            <View style={{ alignItems: "center", gap: 4 }}>
              <View
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: 4,
                  backgroundColor: state === "upcoming" ? "transparent" : colors.brand,
                  borderWidth: 1.5,
                  borderColor: state === "upcoming" ? colors.borderStrong : colors.brand,
                }}
              />
              <AppText variant="caption" color={state === "upcoming" ? colors.muted : colors.onSurface} style={{ fontSize: 11 }}>
                {t(LABEL_KEYS[s])}
              </AppText>
            </View>
            {i < STEPS.length - 1 && (
              <View
                style={{
                  width: 24,
                  height: 1.5,
                  marginHorizontal: 6,
                  marginBottom: 14,
                  backgroundColor: state === "done" ? colors.brand : colors.borderStrong,
                }}
              />
            )}
          </View>
        );
      })}
    </View>
  );
}
