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
  const { language, setLanguage, t } = useLanguage();
  const { setPreferredLanguage } = useAuth();

  const choose = async (code: LanguageCode) => {
    if (code === language) {
      router.back();
      return;
    }
    haptic("selection");
    await setLanguage(code);
    // Best-effort server sync; local persistence already succeeded.
    await setPreferredLanguage(code);
    router.back();
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
                accessibilityState={{ selected: active }}
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
