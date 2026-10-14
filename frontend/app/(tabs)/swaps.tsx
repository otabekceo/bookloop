import { useState, useCallback } from "react";
import { View, FlatList, Pressable, ActivityIndicator, RefreshControl } from "react-native";
import { useRouter, useFocusEffect } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery } from "@tanstack/react-query";
import { ArrowsClockwise, CaretRight } from "phosphor-react-native";

import { AppText, Avatar, EmptyState, ExchangingDot, DirectionalIcon, haptic } from "@/src/components/ui";
import { displayName } from "@/src/people";
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
  /** Chat messages from the other reader not read yet (also for completed swaps). */
  unread_count?: number;
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
      ? colors.brandSecondaryText
      : s === "completed"
        ? colors.muted
        : colors.onAccentSoft; // pending/closed status — a semantic accent (its text-safe shade), not a primary action

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
              <Avatar uri={item.other_user.avatar_url} name={displayName(t, item.other_user)} size={50} />
              <View style={{ flex: 1, minWidth: 0, gap: 3 }}>
                <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                  <AppText variant="heading" numberOfLines={1} style={{ flexShrink: 1 }}>{displayName(t, item.other_user)}</AppText>
                  <View style={[styles.statusPill, { backgroundColor: statusColor(item.status) + "22", flexShrink: 0 }]}>
                    <AppText variant="caption" color={statusColor(item.status)}>
                      {STATUS_KEYS[item.status] ? t(STATUS_KEYS[item.status]) : item.status}
                    </AppText>
                  </View>
                </View>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                  <AppText
                    variant={(item.unread_count || 0) > 0 ? "label" : "body"}
                    color={(item.unread_count || 0) > 0 ? colors.onSurface : colors.muted}
                    numberOfLines={1}
                    style={{ flex: 1 }}
                  >
                    {renderKeyed(t, { key: item.last_message_key, params: item.last_message_params, text: item.last_message }) ||
                      t("swaps.tapToOpen")}
                  </AppText>
                  {(item.unread_count || 0) > 0 && (
                    <View testID={`swap-unread-${item.id}`} style={styles.unreadPill} accessibilityLabel={t("swaps.unreadCount", { count: item.unread_count })}>
                      <AppText variant="caption" color={colors.onError} style={{ fontSize: 11 }}>
                        {item.unread_count}
                      </AppText>
                    </View>
                  )}
                </View>
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
  unreadPill: { minWidth: 20, height: 20, borderRadius: 10, paddingHorizontal: 5, backgroundColor: colors.notification, alignItems: "center", justifyContent: "center" },
}));
