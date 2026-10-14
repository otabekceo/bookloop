import { useState } from "react";
import { View, Pressable, Modal, ScrollView, Alert, Platform, StyleProp, StyleSheet, ViewStyle, TextInput } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQueryClient } from "@tanstack/react-query";
import { DotsThreeVertical, Flag, Prohibit, X, CheckCircle } from "phosphor-react-native";

import { AppText, Button, haptic, useToast } from "@/src/components/ui";
import { apiFetch } from "@/src/api";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { makeStyles, useTheme } from "@/src/theme";

/** What a report is about. Mirrors POST /api/reports `target_type`. */
export type ReportTarget = { type: "user" | "book" | "review" | "message" | "swap"; id: string };

const REASONS = ["spam", "harassment", "inappropriate", "fraud", "safety", "other"] as const;

/** Native confirm on iOS/Android; window.confirm on web (react-native-web has no Alert buttons). */
function confirmAction(title: string, body: string, confirmText: string, cancelText: string, onConfirm: () => void) {
  if (Platform.OS === "web") {
    if (window.confirm(`${title}\n\n${body}`)) onConfirm();
    return;
  }
  Alert.alert(title, body, [
    { text: cancelText, style: "cancel" },
    { text: confirmText, style: "destructive", onPress: onConfirm },
  ]);
}

/**
 * Reason picker + optional details for a report. The reporter is never revealed to the reported
 * reader; the server keeps the report (with a snapshot of the content) for moderation.
 */
export function ReportSheet({ target, visible, onClose }: { target: ReportTarget; visible: boolean; onClose: () => void }) {
  const styles = useStyles();
  const { colors } = useTheme();
  const { t } = useLanguage();
  const insets = useSafeAreaInsets();
  const toast = useToast();
  const [reason, setReason] = useState<(typeof REASONS)[number] | null>(null);
  const [details, setDetails] = useState("");
  const [sending, setSending] = useState(false);

  const close = () => {
    setReason(null);
    setDetails("");
    onClose();
  };

  const send = async () => {
    if (!reason || sending) return;
    setSending(true);
    try {
      await apiFetch("/api/reports", {
        method: "POST",
        body: { target_type: target.type, target_id: target.id, reason, details: details.trim() || undefined },
      });
      haptic("success");
      toast(t("safety.reportSent"), "success");
      close();
    } catch (e: any) {
      toast(e?.message || t("safety.reportFailed"), "error");
    } finally {
      setSending(false);
    }
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={close}>
      <View style={styles.backdrop}>
        <View style={[styles.sheet, { paddingBottom: insets.bottom + 16 }]} accessibilityViewIsModal>
          <View style={styles.sheetHead}>
            <AppText variant="title" style={{ flex: 1 }} accessibilityRole="header">
              {t(`safety.reportTitle.${target.type}`)}
            </AppText>
            <Pressable onPress={close} hitSlop={10} style={styles.iconBtn} accessibilityRole="button" accessibilityLabel={t("common.close")}>
              <X size={20} color={colors.onSurface} />
            </Pressable>
          </View>
          <ScrollView contentContainerStyle={{ gap: 8 }} keyboardShouldPersistTaps="handled">
            <AppText variant="body" color={colors.muted}>{t("safety.reasonPrompt")}</AppText>
            {REASONS.map((r) => {
              const on = reason === r;
              return (
                <Pressable
                  key={r}
                  testID={`report-reason-${r}`}
                  onPress={() => { haptic("selection"); setReason(r); }}
                  style={[styles.reason, on && styles.reasonOn]}
                  accessibilityRole="radio"
                  accessibilityState={{ selected: on, checked: on }}
                >
                  <AppText variant="label" style={{ flex: 1 }}>{t(`safety.reasons.${r}`)}</AppText>
                  {on ? <CheckCircle size={20} color={colors.brandPrimary} weight="fill" /> : null}
                </Pressable>
              );
            })}
            <TextInput
              testID="report-details"
              value={details}
              onChangeText={setDetails}
              placeholder={t("safety.detailsPlaceholder")}
              placeholderTextColor={colors.muted}
              accessibilityLabel={t("safety.detailsPlaceholder")}
              multiline
              maxLength={1000}
              style={styles.details}
            />
            <AppText variant="caption" color={colors.muted}>{t("safety.reportPrivacy")}</AppText>
            <Button testID="report-send" title={t("safety.sendReport")} onPress={send} disabled={!reason} loading={sending} />
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

/** A labelled "Report" icon button that opens the report sheet (book listings, reviews). */
export function ReportButton({ target, style, size = 18 }: { target: ReportTarget; style?: StyleProp<ViewStyle>; size?: number }) {
  const { colors } = useTheme();
  const { t } = useLanguage();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Pressable
        testID={`report-${target.type}`}
        onPress={() => setOpen(true)}
        hitSlop={10}
        style={style}
        accessibilityRole="button"
        accessibilityLabel={t(`safety.reportTitle.${target.type}`)}
      >
        <Flag size={size} color={colors.muted} />
      </Pressable>
      <ReportSheet target={target} visible={open} onClose={() => setOpen(false)} />
    </>
  );
}

/**
 * "More actions" for another reader (their profile, a chat): report them (or the given target, e.g.
 * the conversation) and block them. Blocking is enforced by the server; the blocked reader is not
 * told, and open swaps between the two are closed.
 */
export function SafetyMenuButton({
  userId,
  name,
  reportTarget,
  onBlocked,
  style,
}: {
  userId: string;
  name: string;
  reportTarget?: ReportTarget;
  onBlocked?: () => void;
  style?: StyleProp<ViewStyle>;
}) {
  const styles = useStyles();
  const { colors } = useTheme();
  const { t } = useLanguage();
  const insets = useSafeAreaInsets();
  const toast = useToast();
  const qc = useQueryClient();
  const [menu, setMenu] = useState(false);
  const [reporting, setReporting] = useState(false);
  const target = reportTarget ?? { type: "user" as const, id: userId };

  const block = () => {
    setMenu(false);
    confirmAction(t("safety.blockTitle", { name }), t("safety.blockBody"), t("safety.block"), t("common.cancel"), async () => {
      try {
        await apiFetch(`/api/blocks/${userId}`, { method: "PUT" });
        haptic("success");
        toast(t("safety.blockedToast", { name }), "success");
        // Everything that could show this reader is refreshed (the server now hides them).
        qc.invalidateQueries();
        onBlocked?.();
      } catch (e: any) {
        toast(e?.message || t("safety.blockFailed"), "error");
      }
    });
  };

  return (
    <>
      <Pressable
        testID="safety-menu"
        onPress={() => setMenu(true)}
        hitSlop={8}
        style={style}
        accessibilityRole="button"
        accessibilityLabel={t("safety.moreActions")}
      >
        <DotsThreeVertical size={22} color={colors.onSurface} weight="bold" />
      </Pressable>
      <Modal visible={menu} transparent animationType="fade" onRequestClose={() => setMenu(false)}>
        <View style={styles.backdrop}>
          <Pressable style={StyleSheet.absoluteFill} onPress={() => setMenu(false)} accessibilityLabel={t("common.close")} accessibilityRole="button" />
          <View style={[styles.sheet, { paddingBottom: insets.bottom + 16 }]} accessibilityViewIsModal>
            <Pressable
              testID="safety-report"
              onPress={() => { setMenu(false); setReporting(true); }}
              style={styles.menuRow}
              accessibilityRole="button"
            >
              <Flag size={20} color={colors.onSurface} />
              <AppText variant="label">{t(`safety.reportTitle.${target.type}`)}</AppText>
            </Pressable>
            <Pressable testID="safety-block" onPress={block} style={styles.menuRow} accessibilityRole="button">
              <Prohibit size={20} color={colors.error} />
              <AppText variant="label" color={colors.error}>{t("safety.blockName", { name })}</AppText>
            </Pressable>
            <Pressable onPress={() => setMenu(false)} style={styles.menuRow} accessibilityRole="button">
              <AppText variant="label" color={colors.muted}>{t("common.cancel")}</AppText>
            </Pressable>
          </View>
        </View>
      </Modal>
      <ReportSheet target={target} visible={reporting} onClose={() => setReporting(false)} />
    </>
  );
}

const useStyles = makeStyles((colors) => ({
  backdrop: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(0,0,0,0.4)" },
  sheet: { backgroundColor: colors.surfaceSecondary, borderTopLeftRadius: 22, borderTopRightRadius: 22, padding: 20, gap: 12, maxHeight: "90%" },
  sheetHead: { flexDirection: "row", alignItems: "center", gap: 12 },
  iconBtn: { width: 44, height: 44, borderRadius: 22, alignItems: "center", justifyContent: "center", backgroundColor: colors.surfaceTertiary },
  reason: { flexDirection: "row", alignItems: "center", minHeight: 48, paddingHorizontal: 14, borderRadius: 14, borderWidth: 1.5, borderColor: colors.border, backgroundColor: colors.surface },
  reasonOn: { borderColor: colors.brandPrimary, backgroundColor: colors.brandTertiary },
  details: { minHeight: 90, borderRadius: 14, borderWidth: 1, borderColor: colors.borderStrong, padding: 12, color: colors.onSurface, backgroundColor: colors.surface, textAlignVertical: "top", fontSize: 15 },
  menuRow: { flexDirection: "row", alignItems: "center", gap: 12, minHeight: 52, paddingHorizontal: 6 },
}));
