import { View, Pressable, ScrollView } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Check, X, DeviceMobile, Sun, Moon } from "phosphor-react-native";

import { AppText, haptic } from "@/src/components/ui";
import { useAppearance, type AppearancePreference } from "@/src/appearance";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { makeStyles, useTheme } from "@/src/theme";

const OPTIONS: { value: AppearancePreference; icon: typeof Sun; labelKey: string; hintKey?: string }[] = [
  { value: "system", icon: DeviceMobile, labelKey: "appearance.system", hintKey: "appearance.systemHint" },
  { value: "light", icon: Sun, labelKey: "appearance.light" },
  { value: "dark", icon: Moon, labelKey: "appearance.dark" },
];

/**
 * Profile -> Appearance. Purely a re-render (no native reload, unlike language direction), so
 * selecting an option applies instantly with no overlay/loading state needed.
 */
export default function AppearanceSettings() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { t } = useLanguage();
  const { preference, setPreference } = useAppearance();

  const choose = async (value: AppearancePreference) => {
    if (value === preference) {
      router.back();
      return;
    }
    haptic("selection");
    await setPreference(value);
    router.back();
  };

  return (
    <View style={styles.root}>
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}>
        <AppText variant="heading">{t("appearance.title")}</AppText>
        <Pressable testID="appearance-close" onPress={() => router.back()} hitSlop={10} style={styles.closeBtn}>
          <X size={20} color={colors.onSurface} />
        </Pressable>
      </View>

      <ScrollView contentContainerStyle={{ padding: 20, paddingBottom: insets.bottom + 32, gap: 16 }} showsVerticalScrollIndicator={false}>
        <AppText variant="body" color={colors.muted}>
          {t("appearance.subtitle")}
        </AppText>

        <View style={styles.list}>
          {OPTIONS.map(({ value, icon: Icon, labelKey, hintKey }) => {
            const active = preference === value;
            return (
              <Pressable
                key={value}
                testID={`appearance-option-${value}`}
                accessibilityRole="button"
                accessibilityState={{ selected: active }}
                onPress={() => choose(value)}
                style={[styles.row, active && styles.rowActive]}
              >
                <View style={[styles.iconWrap, active && { backgroundColor: colors.brandTertiary }]}>
                  <Icon size={20} color={active ? colors.brandPrimary : colors.muted} weight={active ? "fill" : "regular"} />
                </View>
                <View style={styles.rowText}>
                  <AppText variant="label" style={styles.optionName}>
                    {t(labelKey)}
                  </AppText>
                  {hintKey && (
                    <AppText variant="caption" color={colors.muted}>
                      {t(hintKey)}
                    </AppText>
                  )}
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
  topBar: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 20, paddingBottom: 12 },
  closeBtn: { width: 36, height: 36, borderRadius: 18, alignItems: "center", justifyContent: "center", backgroundColor: colors.surfaceTertiary },
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
  iconWrap: { width: 40, height: 40, borderRadius: 20, alignItems: "center", justifyContent: "center", backgroundColor: colors.surfaceTertiary },
  rowText: { flex: 1, gap: 2 },
  optionName: { fontSize: 16 },
  check: { width: 24, height: 24, borderRadius: 12, backgroundColor: colors.brandPrimary, alignItems: "center", justifyContent: "center" },
}));
