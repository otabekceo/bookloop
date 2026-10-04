import { View, FlatList, Pressable, ActivityIndicator } from "react-native";
import { useLocalSearchParams, useRouter, Stack } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Star } from "phosphor-react-native";

import { AppText, EmptyState, DirectionalIcon } from "@/src/components/ui";
import { ReviewCard } from "@/src/components/ReviewCard";
import { apiFetch } from "@/src/api";
import { makeStyles, useTheme } from "@/src/theme";
import { useLanguage } from "@/src/i18n/LanguageProvider";

export default function Reviews() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const styles = useStyles();
  const { colors } = useTheme();
  const { t } = useLanguage();
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
          <DirectionalIcon>
            <ArrowLeft size={22} color={colors.onSurface} />
          </DirectionalIcon>
        </Pressable>
        <AppText variant="heading">{t("reviews.title")}</AppText>
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
              <Star size={26} color={colors.rating} weight="fill" />
              <AppText variant="display">{data.user.rating > 0 ? data.user.rating.toFixed(1) : t("reviews.new")}</AppText>
              <AppText variant="body" color={colors.muted}>
                {t("reviews.summary", {
                  reviews: t("common.reviewCount", { count: data.user.rating_count }),
                  swaps: t("common.swapCount", { count: data.user.swaps_count }),
                })}
              </AppText>
            </View>
          }
          renderItem={({ item }) => <ReviewCard review={item} />}
          ListEmptyComponent={
            <EmptyState
              icon={<Star size={44} color={colors.muted} weight="light" />}
              title={t("reviews.emptyTitle")}
              subtitle={t("reviews.emptyBody")}
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
}));
