import { useState } from "react";
import { View, FlatList, useWindowDimensions, ActivityIndicator, RefreshControl } from "react-native";
import { useRouter } from "expo-router";
import { useFocusEffect } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery } from "@tanstack/react-query";
import { useCallback } from "react";
import { BookOpen, Fire } from "phosphor-react-native";

import { AppText, Chip, ChipRow, Button, EmptyState } from "@/src/components/ui";
import { BookTile, Book } from "@/src/components/cards";
import { apiFetch } from "@/src/api";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { makeStyles, useTheme } from "@/src/theme";

const FILTERS = ["All", "Available", "Reserved", "Swapped"] as const;
const FILTER_KEYS: Record<(typeof FILTERS)[number], string> = {
  All: "books.filterAll",
  Available: "books.filterAvailable",
  Reserved: "books.filterReserved",
  Swapped: "books.filterSwapped",
};

export default function MyBooks() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { t } = useLanguage();
  const { width } = useWindowDimensions();
  const col = (width - 40 - 14) / 2;
  const [filter, setFilter] = useState("All");

  const { data, isLoading, isRefetching, refetch } = useQuery({
    queryKey: ["myBooks", filter],
    queryFn: () => apiFetch<{ books: Book[] }>(`/api/books?status=${filter}`),
  });

  const { data: demand, refetch: refetchDemand } = useQuery({
    queryKey: ["bookDemand"],
    queryFn: () => apiFetch<{ books: Record<string, number>; total_readers: number }>("/api/books/demand"),
  });

  useFocusEffect(
    useCallback(() => {
      refetch();
      refetchDemand();
    }, [refetch, refetchDemand]),
  );

  const books = data?.books || [];
  const wanted = demand?.total_readers || 0;

  return (
    <View style={styles.root}>
      <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
        <AppText variant="display">{t("books.title")}</AppText>
        <AppText variant="body" color={colors.muted}>
          {t("books.subtitle")}
        </AppText>
      </View>

      <ChipRow>
        {FILTERS.map((f) => (
          <Chip key={f} label={t(FILTER_KEYS[f])} selected={filter === f} onPress={() => setFilter(f)} testID={`book-filter-${f}`} />
        ))}
      </ChipRow>

      {isLoading ? (
        <ActivityIndicator color={colors.brandPrimary} style={{ marginTop: 40 }} />
      ) : (
        <FlatList
          data={books}
          keyExtractor={(b) => b.id}
          numColumns={2}
          columnWrapperStyle={{ gap: 14 }}
          ListHeaderComponent={
            wanted > 0 ? (
              <View testID="demand-banner" style={styles.banner}>
                <View style={styles.bannerIcon}>
                  <Fire size={18} color={colors.onBrandPrimary} weight="fill" />
                </View>
                <View style={{ flex: 1 }}>
                  <AppText variant="label">
                    {t("books.demandBanner", { count: wanted })}
                  </AppText>
                  <AppText variant="caption" color={colors.muted}>
                    {t("books.demandHint")}
                  </AppText>
                </View>
              </View>
            ) : null
          }
          renderItem={({ item }) => (
            <BookTile book={item} width={col} showStatus wantedBy={demand?.books?.[item.id]} onPress={() => router.push(`/book/${item.id}`)} />
          )}
          contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 18, paddingBottom: 24 }}
          showsVerticalScrollIndicator={false}
          refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={colors.brandPrimary} />}
          ListEmptyComponent={
            <EmptyState
              icon={<BookOpen size={48} color={colors.muted} weight="light" />}
              title={t("books.emptyTitle")}
              subtitle={t("books.emptyBody")}
              action={<Button title={t("books.emptyCta")} onPress={() => router.push("/book/add")} testID="add-first-book" style={{ marginTop: 8, paddingHorizontal: 28 }} />}
            />
          }
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { paddingHorizontal: 20, paddingBottom: 8, gap: 2 },
  banner: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: colors.brandTertiary, borderRadius: 16, padding: 14, marginBottom: 4 },
  bannerIcon: { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.brandPrimary, alignItems: "center", justifyContent: "center" },
}));
