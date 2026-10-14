import { useEffect, useState } from "react";
import { View, Pressable, ScrollView, ActivityIndicator, Alert, Platform } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { X, EnvelopeSimple, GoogleLogo, AppleLogo, Prohibit, CaretRight } from "phosphor-react-native";

import { AppText, Button, DirectionalIcon, haptic, useToast } from "@/src/components/ui";
import { useAuth } from "@/src/auth";
import { apiFetch } from "@/src/api";
import { appleAvailable, appleCredential, finishGoogleLink, googleNativeAvailable, googleNativeCredential, linkGoogle } from "@/src/socialAuth";
import { canUnlink, linkErrorKey, type Identities, type LinkResult, type Provider } from "@/src/accountLinking";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { makeStyles, useTheme } from "@/src/theme";

/**
 * Profile -> Account: how this account signs in (email/password, Google, Apple). Linking always
 * attaches the provider to THIS account (never creates another profile); the server refuses to link
 * an identity that belongs to someone else and to unlink the last way to sign in.
 */
export default function AccountSettings() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { t } = useLanguage();
  const toast = useToast();
  const qc = useQueryClient();
  const [busy, setBusy] = useState<Provider | null>(null);
  const [deleting, setDeleting] = useState(false);
  const { logout } = useAuth();

  // Permanent account deletion (store requirement: available inside the app). Two explicit steps:
  // the button, then a destructive confirmation that spells out what happens.
  const deleteAccount = async () => {
    setDeleting(true);
    try {
      await apiFetch("/api/users/me/delete", { method: "POST", body: { confirm: true } });
      haptic("success");
      toast(t("account.deleted"), "success");
      await logout();
    } catch (e: any) {
      toast(e?.message || t("account.deleteFailed"), "error");
      setDeleting(false);
    }
  };
  const confirmDelete = () => {
    if (Platform.OS === "web") {
      if (window.confirm(`${t("account.deleteConfirmTitle")}\n\n${t("account.deleteConfirmBody")}`)) deleteAccount();
      return;
    }
    Alert.alert(t("account.deleteConfirmTitle"), t("account.deleteConfirmBody"), [
      { text: t("common.cancel"), style: "cancel" },
      { text: t("account.deleteButton"), style: "destructive", onPress: deleteAccount },
    ]);
  };

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["identities"],
    queryFn: () => apiFetch<Identities>("/api/auth/identities"),
  });
  // Native Google linking (Android) when the backend has native sign-in on; the browser flow otherwise.
  const nativeQ = useQuery({
    queryKey: ["authProviders"],
    queryFn: () => apiFetch<{ google_native?: boolean }>("/api/auth/providers"),
    select: (p) => googleNativeAvailable(!!p.google_native),
  });
  const appleQ = useQuery({
    queryKey: ["appleAvailable", data?.available.apple],
    queryFn: () => appleAvailable(!!data?.available.apple),
    enabled: !!data,
  });

  const run = async (provider: Provider, fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(provider);
    try {
      await fn();
    } catch (e: any) {
      toast(e?.code ? t(linkErrorKey(e.code)) : e?.message || t("account.linkFailed"), "error");
    } finally {
      setBusy(null);
      refetch();
    }
  };

  const showGoogleLink = (r: LinkResult) => {
    if (r.kind === "linked") {
      haptic("success");
      toast(t("account.linked", { provider: "Google" }), "success");
    } else if (r.kind === "error") {
      toast(t(linkErrorKey(r.code)), "error");
    }
  };

  // Google's return reopened this screen (web, or the app was restarted while the browser was open):
  // finish that link here. finishGoogleLink ignores returns this app didn't start and de-duplicates
  // with linkGoogle() when both receive the same return.
  const { link_id, link_error, attempt } = useLocalSearchParams<{ link_id?: string; link_error?: string; attempt?: string }>();
  useEffect(() => {
    if (!attempt || (!link_id && !link_error)) return;
    const ret = link_id
      ? `?link_id=${encodeURIComponent(link_id)}&attempt=${encodeURIComponent(attempt)}`
      : `?link_error=${encodeURIComponent(link_error!)}&attempt=${encodeURIComponent(attempt)}`;
    router.setParams({ link_id: undefined, link_error: undefined, attempt: undefined });
    finishGoogleLink(ret).then((r) => {
      showGoogleLink(r);
      refetch();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once per distinct return
  }, [link_id, link_error, attempt]);

  const link = (provider: Provider) =>
    run(provider, async () => {
      if (provider === "google") {
        if (!nativeQ.data) {
          showGoogleLink(await linkGoogle());
          return;
        }
        const google = await googleNativeCredential();
        if (!google) return;
        // The backend verifies the token and its nonce, and refuses a Google account that belongs to
        // someone else (identity_in_use) or a second Google account on this one (provider_already_linked).
        const linked = await apiFetch<Identities>("/api/auth/google/native/link", { method: "POST", body: google });
        qc.setQueryData(["identities"], linked);
        haptic("success");
        toast(t("account.linked", { provider: "Google" }), "success");
        return;
      }
      const cred = await appleCredential();
      if (!cred) return;
      const next = await apiFetch<Identities>("/api/auth/apple/link", { method: "POST", body: cred });
      qc.setQueryData(["identities"], next);
      haptic("success");
      toast(t("account.linked", { provider: "Apple" }), "success");
    });

  const unlink = (provider: Provider, name: string) => {
    const go = () =>
      run(provider, async () => {
        const next = await apiFetch<Identities>(`/api/auth/identities/${provider}`, { method: "DELETE" });
        qc.setQueryData(["identities"], next);
        toast(t("account.unlinked", { provider: name }), "success");
      });
    if (Platform.OS === "web") {
      if (window.confirm(t("account.unlinkConfirm", { provider: name }))) go();
      return;
    }
    Alert.alert(t("account.unlinkTitle", { provider: name }), t("account.unlinkConfirm", { provider: name }), [
      { text: t("common.cancel"), style: "cancel" },
      { text: t("account.unlink"), style: "destructive", onPress: go },
    ]);
  };

  const rows: { provider: Provider; name: string; Icon: typeof GoogleLogo; show: boolean }[] = data
    ? [
        { provider: "google", name: "Google", Icon: GoogleLogo, show: data.available.google || !!nativeQ.data || data.providers.google.linked },
        // Linking Apple needs an Apple device; an already linked Apple ID is shown everywhere.
        { provider: "apple", name: "Apple", Icon: AppleLogo, show: !!appleQ.data || data.providers.apple.linked },
      ]
    : [];

  return (
    <View style={styles.root}>
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}>
        <AppText variant="heading" style={{ flex: 1 }} numberOfLines={1}>
          {t("account.title")}
        </AppText>
        <Pressable testID="account-close" onPress={() => router.back()} hitSlop={10} style={styles.closeBtn} accessibilityRole="button" accessibilityLabel={t("common.close")}>
          <X size={20} color={colors.onSurface} />
        </Pressable>
      </View>

      {isLoading || !data ? (
        <ActivityIndicator color={colors.brandPrimary} style={{ marginTop: 40 }} />
      ) : (
        <ScrollView contentContainerStyle={{ padding: 20, paddingBottom: insets.bottom + 32, gap: 12 }} showsVerticalScrollIndicator={false}>
          <AppText variant="body" color={colors.muted}>
            {t("account.subtitle")}
          </AppText>

          <View testID="account-email" style={styles.row}>
            <View style={styles.iconWrap}>
              <EnvelopeSimple size={20} color={colors.brandPrimary} />
            </View>
            <View style={styles.rowText}>
              <AppText variant="label">{t("account.emailPassword")}</AppText>
              <AppText variant="caption" color={colors.muted} numberOfLines={1}>
                {data.email}
              </AppText>
            </View>
            <AppText variant="caption" color={data.password ? colors.brandSecondaryText : colors.muted} style={styles.status}>
              {data.password ? t("account.statusLinked") : t("account.notSet")}
            </AppText>
          </View>

          {rows
            .filter((r) => r.show)
            .map(({ provider, name, Icon }) => {
              const p = data.providers[provider];
              return (
                <View key={provider} testID={`account-${provider}`} style={styles.row}>
                  <View style={styles.iconWrap}>
                    <Icon size={20} color={colors.brandPrimary} weight="bold" />
                  </View>
                  <View style={styles.rowText}>
                    <AppText variant="label">{name}</AppText>
                    <AppText variant="caption" color={colors.muted} numberOfLines={1}>
                      {p.linked ? (p.private_email ? t("account.privateEmail") : p.email || t("account.statusLinked")) : t("account.notLinked")}
                    </AppText>
                  </View>
                  {busy === provider ? (
                    <ActivityIndicator color={colors.brandPrimary} />
                  ) : p.linked ? (
                    canUnlink(data, provider) ? (
                      <Pressable testID={`unlink-${provider}`} onPress={() => unlink(provider, name)} hitSlop={8} style={styles.action}>
                        <AppText variant="label" color={colors.error}>{t("account.unlink")}</AppText>
                      </Pressable>
                    ) : (
                      <AppText variant="caption" color={colors.brandSecondaryText} style={styles.status}>{t("account.statusLinked")}</AppText>
                    )
                  ) : (
                    <Pressable testID={`link-${provider}`} onPress={() => link(provider)} hitSlop={8} style={styles.action}>
                      <AppText variant="label" color={colors.brandPrimary}>{t("account.link")}</AppText>
                    </Pressable>
                  )}
                </View>
              );
            })}

          <AppText variant="caption" color={colors.muted}>
            {t("account.lastMethodHint")}
          </AppText>

          <Pressable
            testID="blocked-readers"
            onPress={() => router.push("/settings/blocked")}
            style={[styles.row, { marginTop: 12 }]}
            accessibilityRole="button"
          >
            <View style={styles.iconWrap}>
              <Prohibit size={20} color={colors.onSurface} />
            </View>
            <View style={styles.rowText}>
              <AppText variant="label">{t("safety.blockedReaders")}</AppText>
            </View>
            <DirectionalIcon>
              <CaretRight size={18} color={colors.muted} />
            </DirectionalIcon>
          </Pressable>

          <View style={styles.danger} testID="delete-account-section">
            <AppText variant="heading" accessibilityRole="header">{t("account.deleteTitle")}</AppText>
            <AppText variant="body" color={colors.muted}>
              {t("account.deleteBody")}
            </AppText>
            <Button testID="delete-account" title={t("account.deleteButton")} variant="outline" onPress={confirmDelete} loading={deleting} />
          </View>
        </ScrollView>
      )}
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  topBar: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 20, paddingBottom: 12 },
  closeBtn: { width: 36, height: 36, borderRadius: 18, alignItems: "center", justifyContent: "center", backgroundColor: colors.surfaceTertiary },
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
  iconWrap: { width: 40, height: 40, borderRadius: 20, alignItems: "center", justifyContent: "center", backgroundColor: colors.surfaceTertiary },
  rowText: { flex: 1, minWidth: 0, gap: 2 },
  status: { flexShrink: 0, maxWidth: "40%", textAlign: "right" },
  action: { flexShrink: 0, paddingVertical: 6, paddingHorizontal: 4 },
  danger: { marginTop: 28, gap: 10, padding: 16, borderRadius: 16, borderWidth: 1.5, borderColor: colors.error, backgroundColor: colors.surfaceSecondary },
}));
