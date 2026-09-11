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
import { makeStyles, useTheme } from "@/src/theme";

const FILTERS = ["All", "Available", "Reserved", "Swapped"];

export default function MyBooks() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
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
        <AppText variant="display">My Books</AppText>
        <AppText variant="body" color={colors.muted}>
          Your exchange inventory
        </AppText>
      </View>

      <ChipRow>
        {FILTERS.map((f) => (
          <Chip key={f} label={f} selected={filter === f} onPress={() => setFilter(f)} testID={`book-filter-${f}`} />
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
                    {wanted} {wanted === 1 ? "reader" : "readers"} nearby want books like yours
                  </AppText>
                  <AppText variant="caption" color={colors.muted}>
                    Based on the genres & languages they're hunting for
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
              title="No books yet"
              subtitle="Add books to your shelf so others can discover and swap with you."
              action={<Button title="Add your first book" onPress={() => router.push("/book/add")} testID="add-first-book" style={{ marginTop: 8, paddingHorizontal: 28 }} />}
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
