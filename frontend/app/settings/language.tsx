import { useState } from "react";
import { View, Pressable, ScrollView } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Check, X } from "phosphor-react-native";

import { AppText, haptic } from "@/src/components/ui";
import { useAuth } from "@/src/auth";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { LANGUAGES, type LanguageCode } from "@/src/i18n/languages";
import { makeStyles, useTheme } from "@/src/theme";

/**
 * Language settings modal.
 *
 * Reachable from the Profile tab. Changing the language takes effect app-wide
 * immediately, is persisted locally, and is synced to the signed-in user's
 * server profile.
 */
export default function LanguageSettings() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { language, setLanguage, isSwitching, t } = useLanguage();
  const { setPreferredLanguage } = useAuth();

  // True from the tap until the switch finished: covers the server sync below, so a second tap during a
  // slow request can't start another switch.
  const [busy, setBusy] = useState(false);
  const locked = busy || isSwitching;

  const choose = async (code: LanguageCode) => {
    if (locked) return;
    if (code === language) {
      router.back();
      return;
    }
    setBusy(true);
    haptic("selection");
    try {
      // Best-effort server sync FIRST: switching between LTR and RTL reloads the app, which would
      // otherwise cut this request off. (It also self-heals on the next start if it fails.)
      await setPreferredLanguage(code);
      await setLanguage(code);
      router.back();
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={styles.root}>
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}>
        <AppText variant="heading">{t("languageSelect.changeTitle")}</AppText>
        <Pressable
          testID="language-close"
          onPress={() => router.back()}
          hitSlop={10}
          style={styles.closeBtn}
        >
          <X size={20} color={colors.onSurface} />
        </Pressable>
      </View>

      <ScrollView
        contentContainerStyle={{ padding: 20, paddingBottom: insets.bottom + 32, gap: 16 }}
        showsVerticalScrollIndicator={false}
      >
        <AppText variant="body" color={colors.muted}>
          {t("languageSelect.changeSubtitle")}
        </AppText>

        <View style={styles.list}>
          {LANGUAGES.map((lang) => {
            const active = language === lang.code;
            return (
              <Pressable
                key={lang.code}
                testID={`language-option-${lang.code}`}
                accessibilityRole="button"
                accessibilityState={{ selected: active, disabled: locked }}
                disabled={locked}
                onPress={() => choose(lang.code)}
                style={[styles.row, active && styles.rowActive]}
              >
                <AppText style={styles.flag}>{lang.flag}</AppText>
                <View style={styles.rowText}>
                  <AppText variant="label" style={styles.nativeName}>
                    {lang.nativeName}
                  </AppText>
                  <AppText variant="caption" color={colors.muted}>
                    {lang.englishName}
                  </AppText>
                </View>
                {active ? (
                  <View style={styles.check}>
                    <Check size={16} color={colors.onBrandPrimary} weight="bold" />
                  </View>
                ) : null}
              </Pressable>
            );
          })}
        </View>
      </ScrollView>
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  topBar: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 20,
    paddingBottom: 12,
  },
  closeBtn: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.surfaceTertiary,
  },
  list: { gap: 12 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    paddingVertical: 14,
    paddingHorizontal: 16,
    borderRadius: 16,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1.5,
    borderColor: colors.border,
  },
  rowActive: { borderColor: colors.brandPrimary, backgroundColor: colors.brandTertiary },
  flag: { fontSize: 24, lineHeight: 30 },
  rowText: { flex: 1, gap: 2 },
  nativeName: { fontSize: 16 },
  check: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: colors.brandPrimary,
    alignItems: "center",
    justifyContent: "center",
  },
}));
