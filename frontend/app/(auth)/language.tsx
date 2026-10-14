import { useRef } from "react";
import { View, Pressable, ScrollView } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Check } from "phosphor-react-native";

import { AppText, Button, haptic } from "@/src/components/ui";
import { Logo } from "@/src/components/Logo";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { LANGUAGES, type LanguageCode } from "@/src/i18n/languages";
import { makeStyles, useTheme } from "@/src/theme";

/**
 * First-run language picker.
 *
 * Shown before sign-in/sign-up for brand-new users so the rest of the app
 * (including the auth screens) renders in their chosen language. The selection
 * is persisted locally and, once the user authenticates, synced to their
 * server profile.
 *
 * One source of truth: the ACTIVE language. Tapping a card makes it active at once (previewLanguage), so
 * the title, subtitle and Continue re-render in it and the highlighted card always matches the text.
 * Continue only saves that already-active language and finishes the selection; it never changes what is
 * on screen, so there is no flash of new text before the app moves on.
 */
export default function LanguageSelect() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const { language, setLanguage, previewLanguage, isSwitching, t } = useLanguage();
  // The newest tap, synchronously: Continue right after a tap confirms that tap, even if its preview
  // is still being applied.
  const latest = useRef<LanguageCode>(language);

  const choose = (code: LanguageCode) => {
    if (isSwitching) return;
    haptic("selection");
    latest.current = code;
    previewLanguage(code);
  };

  const confirm = async () => {
    if (isSwitching) return;
    haptic("success");
    await setLanguage(latest.current);
  };

  return (
    <View style={styles.root}>
      <ScrollView
        contentContainerStyle={[
          styles.content,
          { paddingTop: insets.top + 48, paddingBottom: insets.bottom + 24 },
        ]}
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.brandWrap}>
          <Logo variant="full" height={44} />
        </View>

        <View style={styles.head}>
          <AppText variant="title" style={styles.title}>
            {t("languageSelect.title")}
          </AppText>
          <AppText variant="body" color={colors.muted} style={styles.subtitle}>
            {t("languageSelect.subtitle")}
          </AppText>
        </View>

        <View style={styles.list}>
          {LANGUAGES.map((lang) => {
            const active = language === lang.code;
            return (
              <Pressable
                key={lang.code}
                testID={`language-${lang.code}`}
                accessibilityRole="button"
                accessibilityState={{ selected: active, disabled: isSwitching }}
                disabled={isSwitching}
                onPress={() => choose(lang.code)}
                style={[styles.card, active && styles.cardActive]}
              >
                <AppText style={styles.flag}>{lang.flag}</AppText>
                <View style={styles.cardText}>
                  <AppText variant="label" style={styles.nativeName}>
                    {lang.nativeName}
                  </AppText>
                  <AppText variant="caption" color={colors.muted}>
                    {lang.englishName}
                  </AppText>
                </View>
                <View style={[styles.check, active && styles.checkActive]}>
                  {active ? <Check size={16} color={colors.onBrandPrimary} weight="bold" /> : null}
                </View>
              </Pressable>
            );
          })}
        </View>

        <Button
          title={t("languageSelect.continue")}
          onPress={confirm}
          disabled={isSwitching}
          testID="language-continue"
          style={styles.cta}
        />
      </ScrollView>
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  content: { paddingHorizontal: 24, gap: 28 },
  brandWrap: { alignItems: "center" },
  head: { gap: 8, alignItems: "center" },
  title: { textAlign: "center" },
  subtitle: { textAlign: "center", maxWidth: 320 },
  list: { gap: 12 },
  card: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    paddingVertical: 16,
    paddingHorizontal: 18,
    borderRadius: 16,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1.5,
    borderColor: colors.border,
  },
  cardActive: { borderColor: colors.brandPrimary, backgroundColor: colors.brandTertiary },
  flag: { fontSize: 26, lineHeight: 32 },
  cardText: { flex: 1, gap: 2 },
  nativeName: { fontSize: 16 },
  check: {
    width: 24,
    height: 24,
    borderRadius: 12,
    borderWidth: 1.5,
    borderColor: colors.borderStrong,
    alignItems: "center",
    justifyContent: "center",
  },
  checkActive: { backgroundColor: colors.brandPrimary, borderColor: colors.brandPrimary },
  cta: { marginTop: 4 },
}));
