import { ActivityIndicator, Modal, View } from "react-native";

import { AppText } from "@/src/components/ui";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { useTheme } from "@/src/theme";

/**
 * Full-screen "Switching language…" cover shown just before the reload that a left-to-right <-> right-to-left
 * change needs, so the reload doesn't look like a freeze or a crash.
 *
 * - The text comes from the active language, which is already the NEWLY selected one when this appears
 *   (the language is saved and applied before the overlay is shown).
 * - It is a native Modal so it sits above everything, including the Profile -> Language sheet, and it
 *   swallows touches, so nothing can be tapped while the switch is in progress.
 */
export function LanguageSwitchOverlay() {
  const { isSwitching, t } = useLanguage();
  const { colors } = useTheme();

  return (
    <Modal visible={isSwitching} transparent animationType="none" statusBarTranslucent onRequestClose={() => {}}>
      <View
        testID="language-switching-overlay"
        style={{
          flex: 1,
          alignItems: "center",
          justifyContent: "center",
          gap: 16,
          paddingHorizontal: 32,
          backgroundColor: colors.surface,
        }}
      >
        <ActivityIndicator size="large" color={colors.brandPrimary} />
        <AppText variant="body" color={colors.muted} style={{ textAlign: "center" }}>
          {t("languageSelect.switching")}
        </AppText>
      </View>
    </Modal>
  );
}
