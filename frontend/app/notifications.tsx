import { useCallback } from "react";
import { View, FlatList, Pressable, ActivityIndicator, RefreshControl } from "react-native";
import { useRouter, Stack, useFocusEffect } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, BellSimple, ChatCircleDots, ArrowsClockwise, CheckCircle, XCircle, Checks } from "phosphor-react-native";

import { AppText, Avatar, EmptyState, DirectionalIcon, haptic, useToast } from "@/src/components/ui";
import { displayName } from "@/src/people";
import { apiFetch } from "@/src/api";
import { formatDate } from "@/src/i18n";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { renderKeyed } from "@/src/i18n/messageKeys";
import { feedRoute, feedText, type FeedItem } from "@/src/swapFlow";
import { makeStyles, useTheme } from "@/src/theme";

type Feed = { items: (FeedItem & { last_message?: string; last_message_key?: string | null; last_message_params?: any })[]; unread_total: number };

/**
 * Notification center: conversations with unread messages (completed swaps included) and swap
 * events — new proposal, accepted, declined, cancelled, chat accepted. Opening this screen does not
 * mark anything read; tapping a row opens its conversation, which marks that conversation read.
 */
export default function Notifications() {
  const styles = useStyles();
  const { colors } = useTheme();
  const { t, language } = useLanguage();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const toast = useToast();
  const qc = useQueryClient();

  const { data, isLoading, isRefetching, refetch } = useQuery({
    queryKey: ["notificationFeed"],
    queryFn: () => apiFetch<Feed>("/api/notifications/feed"),
    refetchInterval: 15000,
  });

  useFocusEffect(
    useCallback(() => {
      refetch();
    }, [refetch]),
  );

  const refreshCounts = () => {
    qc.invalidateQueries({ queryKey: ["notifications"] });
    qc.invalidateQueries({ queryKey: ["notificationFeed"] });
  };

  const openItem = async (item: FeedItem) => {
    haptic("light");
    if (item.kind === "event" && !item.read) {
      apiFetch(`/api/notifications/${encodeURIComponent(item.id)}/read`, { method: "POST" }).then(refreshCounts, () => {});
    }
    router.push(feedRoute(item) as any);
  };

  const markAll = async () => {
    haptic("selection");
    try {
      await apiFetch("/api/notifications/read-all", { method: "POST" });
      refreshCounts();
      qc.invalidateQueries({ queryKey: ["swaps"] });
    } catch (e: any) {
      toast(e.message || t("errors.generic"), "error");
    }
  };

  const iconFor = (item: FeedItem) => {
    const c = item.read ? colors.muted : colors.brandPrimary;
    switch (item.type) {
      case "message":
      case "chat_accepted":
        return <ChatCircleDots size={16} color={c} weight="fill" />;
      case "proposal_accepted":
        return <CheckCircle size={16} color={item.read ? colors.muted : colors.brandSecondary} weight="fill" />;
      case "proposal_declined":
      case "proposal_cancelled":
        return <XCircle size={16} color={colors.muted} weight="fill" />;
      default:
        return <ArrowsClockwise size={16} color={c} weight="bold" />;
    }
  };

  const items = data?.items || [];

  return (
    <View style={styles.root}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}>
        <Pressable testID="back-button" onPress={() => router.back()} style={styles.iconBtn} accessibilityRole="button" accessibilityLabel={t("common.back")}>
          <DirectionalIcon>
            <ArrowLeft size={22} color={colors.onSurface} />
          </DirectionalIcon>
        </Pressable>
        <AppText variant="heading">{t("notifications.title")}</AppText>
        {(data?.unread_total || 0) > 0 ? (
          <Pressable testID="mark-all-read" onPress={markAll} style={styles.iconBtn} accessibilityRole="button" accessibilityLabel={t("notifications.markAllRead")}>
            <Checks size={20} color={colors.brandPrimary} weight="bold" />
          </Pressable>
        ) : (
          <View style={{ width: 42 }} />
        )}
      </View>

      {isLoading ? (
        <ActivityIndicator color={colors.brandPrimary} style={{ marginTop: 40 }} />
      ) : (
        <FlatList
          data={items}
          keyExtractor={(i) => i.id}
          contentContainerStyle={{ padding: 20, gap: 10, paddingBottom: insets.bottom + 24, flexGrow: 1 }}
          refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={colors.brandPrimary} />}
          renderItem={({ item }) => {
            const { key, params } = feedText(item);
            const unread = !item.read;
            const preview = item.kind === "message" ? renderKeyed(t, { key: item.last_message_key, params: item.last_message_params, text: item.last_message }) : "";
            return (
              <Pressable testID={`notification-${item.id}`} onPress={() => openItem(item)} style={[styles.row, unread && styles.rowUnread]}>
                <Avatar uri={item.actor?.avatar_url} name={displayName(t, item.actor)} size={44} />
                <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                    {iconFor(item)}
                    <AppText variant={unread ? "label" : "body"} style={{ flex: 1 }} numberOfLines={2}>
                      {t(key, { ...params, name: displayName(t, item.actor) })}
                    </AppText>
                  </View>
                  {preview ? (
                    <AppText variant="caption" color={colors.muted} numberOfLines={1}>
                      {preview}
                    </AppText>
                  ) : null}
                  <AppText variant="caption" color={colors.muted}>
                    {formatDate(item.created_at, language, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                  </AppText>
                </View>
                {unread &&
                  (item.kind === "message" ? (
                    <View style={styles.unreadPill}>
                      <AppText variant="caption" color={colors.onError} style={{ fontSize: 11 }}>
                        {item.unread_count}
                      </AppText>
                    </View>
                  ) : (
                    <View style={styles.unreadDot} />
                  ))}
              </Pressable>
            );
          }}
          ListEmptyComponent={
            <EmptyState
              icon={<BellSimple size={48} color={colors.muted} weight="light" />}
              title={t("notifications.emptyTitle")}
              subtitle={t("notifications.emptyBody")}
            />
          }
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  topBar: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 20, paddingBottom: 10, backgroundColor: colors.surface, borderBottomWidth: 1, borderBottomColor: colors.border },
  iconBtn: { width: 42, height: 42, borderRadius: 21, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  row: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: colors.surfaceSecondary, borderRadius: 16, padding: 12, borderWidth: 1, borderColor: colors.border },
  rowUnread: { borderColor: colors.brandPrimary },
  unreadPill: { minWidth: 20, height: 20, borderRadius: 10, paddingHorizontal: 5, backgroundColor: colors.notification, alignItems: "center", justifyContent: "center" },
  unreadDot: { width: 9, height: 9, borderRadius: 5, backgroundColor: colors.notification },
}));
