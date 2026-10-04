import { useState } from "react";
import { View, Pressable, ScrollView, ActivityIndicator, Alert, Platform } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { X, EnvelopeSimple, GoogleLogo, AppleLogo } from "phosphor-react-native";

import { AppText, haptic, useToast } from "@/src/components/ui";
import { apiFetch } from "@/src/api";
import { appleAvailable, appleCredential, linkGoogle } from "@/src/socialAuth";
import { canUnlink, linkErrorKey, type Identities, type Provider } from "@/src/accountLinking";
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

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["identities"],
    queryFn: () => apiFetch<Identities>("/api/auth/identities"),
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

  const link = (provider: Provider) =>
    run(provider, async () => {
      if (provider === "google") {
        const r = await linkGoogle();
        if (r.kind === "linked") {
          haptic("success");
          toast(t("account.linked", { provider: "Google" }), "success");
        } else if (r.kind === "error") {
          toast(t(linkErrorKey(r.code)), "error");
        }
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
        { provider: "google", name: "Google", Icon: GoogleLogo, show: data.available.google || data.providers.google.linked },
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
            <AppText variant="caption" color={data.password ? colors.brandSecondary : colors.muted} style={styles.status}>
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
                      <AppText variant="caption" color={colors.brandSecondary} style={styles.status}>{t("account.statusLinked")}</AppText>
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
}));
