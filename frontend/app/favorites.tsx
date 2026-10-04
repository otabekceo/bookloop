import { useCallback } from "react";
import { View, FlatList, Pressable, ActivityIndicator, RefreshControl } from "react-native";
import { useRouter, Stack, useFocusEffect } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Heart, MapPin } from "phosphor-react-native";

import { AppText, Avatar, EmptyState, ExchangingDot, RatingPill, DirectionalIcon, haptic } from "@/src/components/ui";
import { FavoriteButton } from "@/src/components/FavoriteButton";
import type { Person } from "@/src/components/cards";
import { apiFetch } from "@/src/api";
import { distanceOrArea } from "@/src/distance";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { makeStyles, useTheme } from "@/src/theme";

/** Readers the user hearted. Private to them: nobody is notified and it grants no chat or swap. */
export default function Favorites() {
  const styles = useStyles();
  const { colors } = useTheme();
  const { t } = useLanguage();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const qc = useQueryClient();

  const { data, isLoading, isRefetching, refetch } = useQuery({
    queryKey: ["favorites"],
    queryFn: () => apiFetch<{ favorites: Person[] }>("/api/favorites"),
  });

  useFocusEffect(
    useCallback(() => {
      refetch();
    }, [refetch]),
  );

  // Unfavoriting here removes the row right away (the request itself is made by FavoriteButton).
  const dropRow = (userId: string) =>
    qc.setQueryData<{ favorites: Person[] }>(["favorites"], (d) => (d ? { favorites: d.favorites.filter((p) => p.user_id !== userId) } : d));

  return (
    <View style={styles.root}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}>
        <Pressable testID="back-button" onPress={() => router.back()} style={styles.iconBtn} accessibilityRole="button" accessibilityLabel={t("common.back")}>
          <DirectionalIcon>
            <ArrowLeft size={22} color={colors.onSurface} />
          </DirectionalIcon>
        </Pressable>
        <AppText variant="heading">{t("favorites.title")}</AppText>
        <View style={{ width: 42 }} />
      </View>

      {isLoading ? (
        <ActivityIndicator color={colors.brandPrimary} style={{ marginTop: 40 }} />
      ) : (
        <FlatList
          data={data?.favorites || []}
          keyExtractor={(p) => p.user_id}
          contentContainerStyle={{ padding: 20, gap: 12, paddingBottom: insets.bottom + 24, flexGrow: 1 }}
          refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={colors.brandPrimary} />}
          renderItem={({ item }) => (
            <Pressable
              testID={`favorite-row-${item.user_id}`}
              onPress={() => {
                haptic("light");
                router.push(`/person/${item.user_id}`);
              }}
              style={styles.row}
            >
              <Avatar uri={item.avatar_url} name={item.name} size={52} />
              <View style={{ flex: 1, minWidth: 0, gap: 3 }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                  <AppText variant="heading" numberOfLines={1} style={{ flexShrink: 1 }}>
                    {item.name}
                  </AppText>
                  <ExchangingDot active={item.is_exchanging} />
                </View>
                <View style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", columnGap: 10, rowGap: 2 }}>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 3, flexShrink: 1 }}>
                    <MapPin size={13} color={colors.brandSecondary} weight="fill" />
                    <AppText variant="caption" color={colors.muted} style={{ flexShrink: 1 }}>
                      {distanceOrArea(t, item)}
                    </AppText>
                  </View>
                  <RatingPill rating={item.rating} count={item.rating_count} />
                </View>
                <AppText variant="caption" color={colors.muted}>
                  {item.is_exchanging ? t("favorites.booksAvailable", { count: item.available_count || 0 }) : t("person.notAccepting")}
                </AppText>
              </View>
              <FavoriteButton userId={item.user_id} isFavorite onChange={(on) => !on && dropRow(item.user_id)} />
            </Pressable>
          )}
          ListEmptyComponent={
            <EmptyState
              icon={<Heart size={48} color={colors.muted} weight="light" />}
              title={t("favorites.emptyTitle")}
              subtitle={t("favorites.emptyBody")}
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
  row: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: colors.glassFill, borderRadius: 18, padding: 14, borderWidth: 1, borderColor: colors.glassBorder },
}));
