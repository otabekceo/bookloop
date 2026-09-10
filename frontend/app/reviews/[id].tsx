import { View, FlatList, Pressable, ActivityIndicator } from "react-native";
import { useLocalSearchParams, useRouter, Stack } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Star } from "phosphor-react-native";

import { AppText, Avatar, Stars, EmptyState } from "@/src/components/ui";
import { apiFetch } from "@/src/api";
import { makeStyles, useTheme } from "@/src/theme";

function formatDate(iso?: string) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  } catch {
    return "";
  }
}

export default function Reviews() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const { data, isLoading } = useQuery({
    queryKey: ["person", id],
    queryFn: () => apiFetch<{ user: any; reviews: any[] }>(`/api/users/${id}`),
  });

  return (
    <View style={styles.root}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}>
        <Pressable testID="back-button" onPress={() => router.back()} style={styles.backBtn}>
          <ArrowLeft size={22} color={colors.onSurface} />
        </Pressable>
        <AppText variant="heading">Reviews</AppText>
        <View style={{ width: 42 }} />
      </View>

      {isLoading || !data ? (
        <ActivityIndicator color={colors.brandPrimary} style={{ marginTop: 60 }} />
      ) : (
        <FlatList
          data={data.reviews}
          keyExtractor={(r) => r.id}
          contentContainerStyle={{ padding: 20, gap: 12, paddingBottom: insets.bottom + 24 }}
          showsVerticalScrollIndicator={false}
          ListHeaderComponent={
            <View style={styles.summary}>
              <Star size={26} color={colors.star} weight="fill" />
              <AppText variant="display">{data.user.rating > 0 ? data.user.rating.toFixed(1) : "New"}</AppText>
              <AppText variant="body" color={colors.muted}>
                {data.user.rating_count} {data.user.rating_count === 1 ? "review" : "reviews"} · {data.user.swaps_count} swaps
              </AppText>
            </View>
          }
          renderItem={({ item }) => (
            <View style={styles.card}>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 10, marginBottom: 8 }}>
                <Avatar uri={item.rater_avatar} name={item.rater_name} size={38} />
                <View style={{ flex: 1 }}>
                  <AppText variant="label">{item.rater_name}</AppText>
                  <AppText variant="caption" color={colors.muted}>
                    {formatDate(item.created_at)}
                  </AppText>
                </View>
                <Stars value={item.stars} />
              </View>
              {item.review ? <AppText variant="body" color={colors.onSurface}>{item.review}</AppText> : null}
            </View>
          )}
          ListEmptyComponent={
            <EmptyState
              icon={<Star size={44} color={colors.muted} weight="light" />}
              title="No reviews yet"
              subtitle="Reviews appear here after completed swaps."
            />
          }
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  topBar: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 20, paddingBottom: 10, borderBottomWidth: 1, borderBottomColor: colors.border, backgroundColor: colors.surface },
  backBtn: { width: 42, height: 42, borderRadius: 21, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  summary: { alignItems: "center", gap: 4, paddingVertical: 16, marginBottom: 6 },
  card: { backgroundColor: colors.surfaceSecondary, borderRadius: 16, padding: 14, borderWidth: 1, borderColor: colors.border },
}));
