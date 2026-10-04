import { useEffect, useRef, useState } from "react";
import { AppState, Pressable, View } from "react-native";
import { Bell } from "phosphor-react-native";

import { AppText, haptic, useToast } from "@/src/components/ui";
import { useAuth } from "@/src/auth";
import { openSettings } from "@/src/media";
import { checkPermission, disableNotifications, enableNotifications, refreshRegistrationSilently, usePushStatus } from "@/src/notifications";
import { pushUiState } from "@/src/swapFlow";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { makeStyles, useTheme } from "@/src/theme";

const CAPTION_KEYS = {
  off: "notificationSettings.offBody",
  on: "notificationSettings.onBody",
  onNoPush: "notificationSettings.noPushBody",
  denied: "notificationSettings.deniedBody",
  unsupported: "notificationSettings.unsupportedBody",
} as const;

/**
 * Profile -> Notifications. The switch is ON only when the account opted in AND the OS permission is
 * granted. The permission prompt appears only here, when the user turns it on — never at launch.
 */
export function NotificationsSetting() {
  const styles = useStyles();
  const { colors } = useTheme();
  const { t } = useLanguage();
  const toast = useToast();
  const { user, setUser } = useAuth();
  const push = usePushStatus();
  const [busy, setBusy] = useState(false);
  const [triedDenied, setTriedDenied] = useState(false);
  const lock = useRef(false);

  // Re-read the permission on mount and whenever the app comes back (e.g. from the system settings).
  useEffect(() => {
    checkPermission();
    const sub = AppState.addEventListener("change", async (s) => {
      if (s !== "active") return;
      const p = await checkPermission();
      if (p === "granted" && user?.notifications_enabled) refreshRegistrationSilently();
    });
    return () => sub.remove();
  }, [user?.notifications_enabled]);

  if (!user) return null;
  let ui = pushUiState({ enabledOnServer: !!user.notifications_enabled, permission: push.permission, pushReady: push.pushReady });
  if (ui === "off" && triedDenied && push.permission === "denied") ui = "denied";
  const isOn = ui === "on" || ui === "onNoPush";

  const toggle = async () => {
    if (lock.current || ui === "unsupported") return;
    lock.current = true;
    setBusy(true);
    haptic("selection");
    try {
      if (isOn) {
        await disableNotifications();
        setUser({ ...user, notifications_enabled: false });
        toast(t("notificationSettings.turnedOff"), "success");
      } else {
        const result = await enableNotifications();
        if (result === "denied") {
          setTriedDenied(true);
          toast(t("notificationSettings.deniedToast"), "error");
        } else if (result === "unsupported") {
          toast(t("notificationSettings.unsupportedBody"), "error");
        } else {
          setUser({ ...user, notifications_enabled: true });
          toast(result === "on" ? t("notificationSettings.turnedOn") : t("notificationSettings.turnedOnNoPush"), "success");
        }
      }
    } catch (e: any) {
      toast(e?.message || t("notificationSettings.couldNotUpdate"), "error");
    } finally {
      setBusy(false);
      lock.current = false;
    }
  };

  return (
    <View testID="notifications-setting" style={styles.card}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 14 }}>
        <View style={styles.icon}>
          <Bell size={22} color={colors.onBrandPrimary} weight="fill" />
        </View>
        <View style={{ flex: 1 }}>
          <AppText variant="heading">{t("notificationSettings.title")}</AppText>
          <AppText variant="caption" color={colors.muted}>
            {t(CAPTION_KEYS[ui])}
          </AppText>
        </View>
        <Pressable
          testID="notifications-toggle"
          accessibilityRole="switch"
          accessibilityState={{ checked: isOn, disabled: busy || ui === "unsupported" }}
          accessibilityLabel={t("notificationSettings.title")}
          onPress={toggle}
          disabled={busy || ui === "unsupported"}
          style={[styles.switch, isOn && styles.switchOn, (busy || ui === "unsupported") && { opacity: 0.5 }]}
        >
          <View style={[styles.knob, isOn && styles.knobOn]} />
        </Pressable>
      </View>
      {ui === "denied" && (
        <Pressable testID="notifications-open-settings" onPress={openSettings} style={{ alignSelf: "flex-start", paddingTop: 10 }}>
          <AppText variant="label" color={colors.brandPrimary}>
            {t("notificationSettings.openSettings")}
          </AppText>
        </Pressable>
      )}
    </View>
  );
}

// Same card, icon and switch styling as the Language / Appearance rows in Profile.
const useStyles = makeStyles((colors) => ({
  card: { marginHorizontal: 20, marginTop: 12, backgroundColor: colors.surfaceSecondary, borderRadius: 18, padding: 16, borderWidth: 1, borderColor: colors.border },
  icon: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.brandPrimary, alignItems: "center", justifyContent: "center" },
  switch: { width: 50, height: 30, borderRadius: 15, backgroundColor: colors.surfaceTertiary, padding: 3, justifyContent: "center" },
  switchOn: { backgroundColor: colors.brandSecondary },
  knob: { width: 24, height: 24, borderRadius: 12, backgroundColor: "#FFFFFF" },
  knobOn: { alignSelf: "flex-end" },
}));
