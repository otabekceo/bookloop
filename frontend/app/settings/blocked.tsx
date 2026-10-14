import { View, Pressable, FlatList, ActivityIndicator } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { X, Prohibit } from "phosphor-react-native";

import { AppText, Avatar, EmptyState, haptic, useToast } from "@/src/components/ui";
import { apiFetch } from "@/src/api";
import { displayName } from "@/src/people";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { makeStyles, useTheme } from "@/src/theme";

type Blocked = { user_id: string; name: string; avatar_url?: string | null; is_deleted?: boolean };

/** Profile -> Account -> Blocked readers: who I blocked, with Unblock. (Who blocked me is never shown.) */
export default function BlockedReaders() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { t } = useLanguage();
  const toast = useToast();
  const qc = useQueryClient();

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["blocks"],
    queryFn: () => apiFetch<{ blocked: Blocked[] }>("/api/blocks"),
  });

  const unblock = async (u: Blocked) => {
    try {
      await apiFetch(`/api/blocks/${u.user_id}`, { method: "DELETE" });
      haptic("success");
      toast(t("safety.unblockedToast", { name: displayName(t, u) }), "success");
      qc.invalidateQueries();
    } catch (e: any) {
      toast(e?.message || t("safety.blockFailed"), "error");
    } finally {
      refetch();
    }
  };

  return (
    <View style={styles.root}>
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}>
        <AppText variant="heading" style={{ flex: 1 }} numberOfLines={1} accessibilityRole="header">
          {t("safety.blockedReaders")}
        </AppText>
        <Pressable testID="blocked-close" onPress={() => router.back()} hitSlop={10} style={styles.closeBtn} accessibilityRole="button" accessibilityLabel={t("common.close")}>
          <X size={20} color={colors.onSurface} />
        </Pressable>
      </View>
      {isLoading ? (
        <ActivityIndicator color={colors.brandPrimary} style={{ marginTop: 40 }} />
      ) : (
        <FlatList
          data={data?.blocked || []}
          keyExtractor={(u) => u.user_id}
          contentContainerStyle={{ padding: 20, paddingBottom: insets.bottom + 32, gap: 12, flexGrow: 1 }}
          ListHeaderComponent={
            <AppText variant="body" color={colors.muted} style={{ marginBottom: 4 }}>
              {t("safety.blockedHint")}
            </AppText>
          }
          ListEmptyComponent={
            <EmptyState icon={<Prohibit size={44} color={colors.muted} weight="light" />} title={t("safety.noBlocked")} subtitle="" />
          }
          renderItem={({ item }) => (
            <View style={styles.row} testID={`blocked-${item.user_id}`}>
              <Avatar uri={item.avatar_url} name={displayName(t, item)} size={40} />
              <AppText variant="label" style={{ flex: 1 }} numberOfLines={1}>
                {displayName(t, item)}
              </AppText>
              <Pressable
                testID={`unblock-${item.user_id}`}
                onPress={() => unblock(item)}
                hitSlop={8}
                style={styles.action}
                accessibilityRole="button"
                accessibilityLabel={t("safety.unblockName", { name: displayName(t, item) })}
              >
                <AppText variant="label" color={colors.brandPrimary}>{t("safety.unblock")}</AppText>
              </Pressable>
            </View>
          )}
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  topBar: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 20, paddingBottom: 12 },
  closeBtn: { width: 44, height: 44, borderRadius: 22, alignItems: "center", justifyContent: "center", backgroundColor: colors.surfaceTertiary },
  row: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 12, paddingHorizontal: 14, borderRadius: 16, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border },
  action: { minHeight: 44, justifyContent: "center", paddingHorizontal: 6 },
}));
