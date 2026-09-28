import { useRef, useState } from "react";
import { View, Pressable, ActivityIndicator, Linking } from "react-native";
import { useRouter, Stack } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { CameraView, useCameraPermissions } from "expo-camera";
import { X, Barcode } from "phosphor-react-native";

import { AppText, Button, haptic } from "@/src/components/ui";
import { lookupIsbn } from "@/src/googlebooks";
import { deliverScanResult, type ScanPrefill } from "@/src/scanResult";
import { makeStyles, useTheme } from "@/src/theme";
import { useLanguage } from "@/src/i18n/LanguageProvider";

export default function ScanBook() {
  const styles = useStyles();
  const { colors } = useTheme();
  const { t } = useLanguage();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [permission, requestPermission] = useCameraPermissions();
  const [busy, setBusy] = useState(false);
  // A failed lookup stays on THIS screen with "Scan again" / "Add manually" — retrying never
  // navigates, so repeated attempts can't pile scanner or Add Book screens onto the stack.
  const [failure, setFailure] = useState<{ kind: "notFound" | "error"; isbn: string } | null>(null);
  const scanned = useRef(false);
  const leaving = useRef(false);

  /** Hands the result to the Add Book screen that opened the scanner and returns to it. */
  const finish = (result: ScanPrefill) => {
    if (leaving.current) return;
    leaving.current = true;
    deliverScanResult(result);
    if (router.canGoBack()) router.back();
    else router.replace("/book/add"); // opened directly (e.g. a deep link): nothing to go back to
  };

  const onScan = async ({ data }: { data: string }) => {
    if (scanned.current || busy || failure) return;
    scanned.current = true;
    const isbn = data.trim();
    setBusy(true);
    haptic("success");
    try {
      const result = await lookupIsbn(isbn);
      if (result?.title) finish({ ...result, isbn: result.isbn || isbn });
      else setFailure({ kind: "notFound", isbn });
    } catch {
      setFailure({ kind: "error", isbn });
    } finally {
      setBusy(false);
    }
  };

  const scanAgain = () => {
    haptic("light");
    setFailure(null);
    scanned.current = false; // re-arms the same camera view; no navigation
  };

  return (
    <View style={styles.root}>
      <Stack.Screen options={{ headerShown: false }} />

      {permission?.granted ? (
        <CameraView
          style={{ flex: 1 }}
          facing="back"
          barcodeScannerSettings={{ barcodeTypes: ["ean13", "ean8", "upc_a", "upc_e"] }}
          onBarcodeScanned={busy || failure ? undefined : onScan}
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

      {permission?.granted && !failure && (
        <View pointerEvents="none" style={styles.frameWrap}>
          <View style={styles.frame} />
          <AppText variant="label" color="#FFFFFF" style={{ marginTop: 16, textAlign: "center" }}>
            {t("scan.alignFrame")}
          </AppText>
        </View>
      )}

      {failure && (
        <View style={[styles.failCard, { marginBottom: insets.bottom + 24 }]} testID="scan-failure">
          <AppText variant="title">{t("scan.notFoundTitle")}</AppText>
          <AppText variant="body" color={colors.muted}>
            {failure.kind === "notFound" ? t("scan.notFoundBody", { isbn: failure.isbn }) : t("scan.lookupErrorBody")}
          </AppText>
          <View style={{ flexDirection: "row", gap: 10 }}>
            <Button testID="scan-again" title={t("scan.scanAgain")} variant="outline" style={{ flex: 1 }} onPress={scanAgain} />
            <Button testID="scan-add-manually" title={t("scan.addManually")} style={{ flex: 1 }} onPress={() => finish({ isbn: failure.isbn })} />
          </View>
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
  failCard: { position: "absolute", left: 16, right: 16, bottom: 0, gap: 10, padding: 18, borderRadius: 20, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border },
}));
