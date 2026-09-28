import { View, StyleProp, ViewStyle } from "react-native";
import { MapPin } from "phosphor-react-native";

import { AppText, Button } from "@/src/components/ui";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import type { LocationState } from "@/src/location";
import { makeStyles, useTheme } from "@/src/theme";

/**
 * Explains why BookLoop wants the device location and offers the right next step for the current
 * permission state: ask (again), or open the system Settings once the permission is blocked.
 * Same card treatment as the Discover "see your best matches" setup card.
 */
export function LocationPrompt({
  state,
  onRequest,
  onOpenSettings,
  style,
  testID = "location-prompt",
}: {
  state: LocationState;
  onRequest: () => void;
  onOpenSettings: () => void;
  style?: StyleProp<ViewStyle>;
  testID?: string;
}) {
  const styles = useStyles();
  const { colors } = useTheme();
  const { t } = useLanguage();

  const body =
    state === "denied"
      ? t("location.deniedBody")
      : state === "blocked"
        ? t("location.blockedBody")
        : state === "servicesOff"
          ? t("location.servicesOffBody")
          : state === "error"
            ? t("location.errorBody")
            : t("location.enableBody");
  const blocked = state === "blocked";
  const retry = state === "denied" || state === "servicesOff" || state === "error";

  return (
    <View testID={testID} style={[styles.card, style]}>
      <View style={styles.head}>
        <MapPin size={20} color={colors.brandPrimary} weight="fill" />
        <View style={{ flex: 1, gap: 2 }}>
          <AppText variant="label">{t("location.enableTitle")}</AppText>
          <AppText variant="caption" color={colors.muted}>
            {body}
          </AppText>
        </View>
      </View>
      <Button
        testID={`${testID}-${blocked ? "settings" : "request"}`}
        variant="outline"
        title={blocked ? t("location.openSettings") : retry ? t("common.retry") : t("location.useMyLocation")}
        loading={state === "locating"}
        onPress={blocked ? onOpenSettings : onRequest}
      />
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  card: { gap: 12, backgroundColor: colors.brandTertiary, borderRadius: 16, padding: 14 },
  head: { flexDirection: "row", alignItems: "flex-start", gap: 12 },
}));
