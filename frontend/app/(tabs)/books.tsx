import { useState } from "react";
import { View, FlatList, useWindowDimensions, ActivityIndicator, RefreshControl } from "react-native";
import { useRouter } from "expo-router";
import { useFocusEffect } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery } from "@tanstack/react-query";
import { useCallback } from "react";
import { BookOpen } from "phosphor-react-native";

import { AppText, Chip, ChipRow, Button, EmptyState } from "@/src/components/ui";
import { BookTile, Book } from "@/src/components/cards";
import { apiFetch } from "@/src/api";
import { makeStyles, useTheme } from "@/src/theme";
import { FONTS } from "@/src/typography";

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

  useFocusEffect(
    useCallback(() => {
      refetch();
    }, [refetch]),
  );

  const books = data?.books || [];

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
          renderItem={({ item }) => (
            <BookTile book={item} width={col} showStatus onPress={() => router.push(`/book/${item.id}`)} />
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
}));
