import { useState, useCallback } from "react";
import { View, FlatList, Pressable, ActivityIndicator, RefreshControl } from "react-native";
import { useRouter, useFocusEffect } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery } from "@tanstack/react-query";
import { ArrowsClockwise, CaretRight } from "phosphor-react-native";

import { AppText, Avatar, EmptyState, ExchangingDot, DirectionalIcon, haptic } from "@/src/components/ui";
import { apiFetch } from "@/src/api";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { renderKeyed } from "@/src/i18n/messageKeys";
import { makeStyles, useTheme } from "@/src/theme";

type SwapMeta = {
  id: string;
  status: string;
  last_message: string;
  last_message_key?: string | null;
  last_message_params?: Record<string, any> | null;
  other_user: { name: string; avatar_url?: string | null };
  is_requester: boolean;
};

type Buckets = { incoming: SwapMeta[]; outgoing: SwapMeta[]; active: SwapMeta[]; completed: SwapMeta[] };

const TABS = [
  { key: "incoming", labelKey: "swaps.tabIncoming" },
  { key: "outgoing", labelKey: "swaps.tabOutgoing" },
  { key: "active", labelKey: "swaps.tabActive" },
  { key: "completed", labelKey: "swaps.tabDone" },
] as const;

const STATUS_KEYS: Record<string, string> = {
  pending: "swaps.statusPending",
  active: "swaps.statusActive",
  accepted: "swaps.statusActive",
  completed: "swaps.statusCompleted",
  declined: "swaps.statusDeclined",
  cancelled: "swaps.statusCancelled",
};

export default function Swaps() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { t } = useLanguage();
  const [tab, setTab] = useState<keyof Buckets>("incoming");

  const { data, isLoading, isRefetching, refetch } = useQuery({
    queryKey: ["swaps"],
    queryFn: () => apiFetch<Buckets>("/api/swaps"),
  });

  useFocusEffect(
    useCallback(() => {
      refetch();
    }, [refetch]),
  );

  const items = data?.[tab] || [];

  const statusColor = (s: string) =>
    s === "active" || s === "accepted"
      ? colors.brandSecondary
      : s === "completed"
        ? colors.muted
        : colors.brandPrimary;

  return (
    <View style={styles.root}>
      <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
        <AppText variant="display">{t("swaps.title")}</AppText>
      </View>

      <View style={styles.tabs}>
        {TABS.map((tabDef) => {
          const count = data?.[tabDef.key]?.length || 0;
          const active = tab === tabDef.key;
          return (
            <Pressable
              key={tabDef.key}
              testID={`swap-tab-${tabDef.key}`}
              onPress={() => {
                haptic("selection");
                setTab(tabDef.key);
              }}
              style={[styles.tabItem, active && styles.tabItemActive]}
            >
              <AppText variant="label" color={active ? colors.onSurfaceInverse : colors.muted}>
                {t(tabDef.labelKey)}
                {count > 0 ? ` ${count}` : ""}
              </AppText>
            </Pressable>
          );
        })}
      </View>

      {isLoading ? (
        <ActivityIndicator color={colors.brandPrimary} style={{ marginTop: 40 }} />
      ) : (
        <FlatList
          data={items}
          keyExtractor={(s) => s.id}
          contentContainerStyle={{ padding: 20, gap: 12, paddingBottom: 24 }}
          showsVerticalScrollIndicator={false}
          refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={colors.brandPrimary} />}
          renderItem={({ item }) => (
            <Pressable
              testID={`swap-row-${item.id}`}
              onPress={() => {
                haptic("light");
                router.push(`/swap/${item.id}`);
              }}
              style={styles.row}
            >
              <Avatar uri={item.other_user.avatar_url} name={item.other_user.name} size={50} />
              <View style={{ flex: 1, gap: 3 }}>
                <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
                  <AppText variant="heading">{item.other_user.name}</AppText>
                  <View style={[styles.statusPill, { backgroundColor: statusColor(item.status) + "22" }]}>
                    <AppText variant="caption" color={statusColor(item.status)}>
                      {STATUS_KEYS[item.status] ? t(STATUS_KEYS[item.status]) : item.status}
                    </AppText>
                  </View>
                </View>
                <AppText variant="body" color={colors.muted} numberOfLines={1}>
                  {renderKeyed(t, { key: item.last_message_key, params: item.last_message_params, text: item.last_message }) ||
                    t("swaps.tapToOpen")}
                </AppText>
              </View>
              <DirectionalIcon>
                <CaretRight size={18} color={colors.muted} />
              </DirectionalIcon>
            </Pressable>
          )}
          ListEmptyComponent={
            <EmptyState
              icon={<ArrowsClockwise size={48} color={colors.muted} weight="light" />}
              title={t("swaps.emptyTitle")}
              subtitle={t("swaps.emptyBody")}
            />
          }
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { paddingHorizontal: 20, paddingBottom: 12 },
  tabs: { flexDirection: "row", gap: 8, paddingHorizontal: 20, marginBottom: 8 },
  tabItem: { flex: 1, alignItems: "center", paddingVertical: 9, borderRadius: 10, backgroundColor: colors.surfaceTertiary },
  tabItemActive: { backgroundColor: colors.surfaceInverse },
  row: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: colors.surfaceSecondary, borderRadius: 18, padding: 14, borderWidth: 1, borderColor: colors.border },
  statusPill: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999 },
}));
