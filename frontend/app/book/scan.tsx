import { useRef, useState } from "react";
import { View, Pressable, ActivityIndicator, Linking } from "react-native";
import { useRouter, Stack } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { CameraView, useCameraPermissions } from "expo-camera";
import { X, Barcode } from "phosphor-react-native";

import { AppText, Button, haptic, useToast } from "@/src/components/ui";
import { lookupIsbn } from "@/src/googlebooks";
import { makeStyles, useTheme } from "@/src/theme";
import { useLanguage } from "@/src/i18n/LanguageProvider";

export default function ScanBook() {
  const styles = useStyles();
  const { colors } = useTheme();
  const { t } = useLanguage();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const toast = useToast();
  const [permission, requestPermission] = useCameraPermissions();
  const [busy, setBusy] = useState(false);
  const scanned = useRef(false);

  const onScan = async ({ data }: { data: string }) => {
    if (scanned.current || busy) return;
    scanned.current = true;
    setBusy(true);
    haptic("success");
    try {
      const result = await lookupIsbn(data.trim());
      const prefill = result || { isbn: data.trim(), title: "", author: "", language: "English" };
      router.replace({ pathname: "/book/add", params: { prefill: JSON.stringify(prefill) } });
    } catch {
      toast(t("scan.lookupFailed"), "error");
      router.replace({ pathname: "/book/add", params: { prefill: JSON.stringify({ isbn: data.trim() }) } });
    }
  };

  return (
    <View style={styles.root}>
      <Stack.Screen options={{ headerShown: false }} />

      {permission?.granted ? (
        <CameraView
          style={{ flex: 1 }}
          facing="back"
          barcodeScannerSettings={{ barcodeTypes: ["ean13", "ean8", "upc_a", "upc_e"] }}
          onBarcodeScanned={busy ? undefined : onScan}
        />
      ) : (
        <View style={styles.perm}>
          <Barcode size={56} color={colors.brandSecondary} weight="light" />
          <AppText variant="title" style={{ textAlign: "center" }}>
            {t("scan.title")}
          </AppText>
          <AppText variant="body" color={colors.muted} style={{ textAlign: "center" }}>
            {t("scan.subtitle")}
          </AppText>
          {permission && !permission.canAskAgain ? (
            <Button title={t("scan.openSettings")} variant="outline" onPress={() => Linking.openSettings()} testID="scan-open-settings" />
          ) : (
            <Button title={t("scan.enableCamera")} onPress={requestPermission} testID="scan-enable-camera" />
          )}
        </View>
      )}

      {/* Overlay */}
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]} pointerEvents="box-none">
        <Pressable testID="close-scan" onPress={() => router.back()} style={styles.iconBtn}>
          <X size={22} color="#FFFFFF" />
        </Pressable>
      </View>

      {permission?.granted && (
        <View pointerEvents="none" style={styles.frameWrap}>
          <View style={styles.frame} />
          <AppText variant="label" color="#FFFFFF" style={{ marginTop: 16, textAlign: "center" }}>
            {t("scan.alignFrame")}
          </AppText>
        </View>
      )}

      {busy && (
        <View style={styles.busy}>
          <ActivityIndicator color="#FFFFFF" />
          <AppText variant="label" color="#FFFFFF">
            {t("scan.lookingUp")}
          </AppText>
        </View>
      )}
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: "#000" },
  perm: { flex: 1, alignItems: "center", justifyContent: "center", gap: 14, padding: 30, backgroundColor: colors.surface },
  topBar: { position: "absolute", top: 0, left: 0, right: 0, paddingHorizontal: 20, paddingBottom: 10 },
  iconBtn: { width: 42, height: 42, borderRadius: 21, backgroundColor: "rgba(0,0,0,0.4)", alignItems: "center", justifyContent: "center" },
  frameWrap: { position: "absolute", top: 0, bottom: 0, left: 0, right: 0, alignItems: "center", justifyContent: "center" },
  frame: { width: 260, height: 160, borderRadius: 18, borderWidth: 3, borderColor: "#FFFFFF" },
  busy: { position: "absolute", bottom: 60, left: 0, right: 0, flexDirection: "row", gap: 10, alignItems: "center", justifyContent: "center" },
}));
