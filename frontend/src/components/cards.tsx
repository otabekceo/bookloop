import { View, Pressable, ScrollView } from "react-native";
import { useRouter } from "expo-router";
import { MapPin } from "phosphor-react-native";

import { AppText, Avatar, BookCover, ExchangingDot, RatingPill, StatusBadge, haptic } from "@/src/components/ui";
import { useTheme } from "@/src/theme";

export type Person = {
  user_id: string;
  name: string;
  avatar_url?: string | null;
  neighborhood: string;
  distance_km: number;
  rating: number;
  rating_count: number;
  swaps_count: number;
  is_exchanging: boolean;
  genres: string[];
  books?: any[];
  available_count?: number;
};

export type Book = {
  id: string;
  owner_id: string;
  title: string;
  author: string;
  cover_url?: string | null;
  condition: string;
  language: string;
  genre: string;
  status: string;
  owner_name?: string;
  distance_km?: number;
};

export function PersonCard({ person }: { person: Person }) {
  const { colors } = useTheme();
  const router = useRouter();
  return (
    <Pressable
      testID={`person-card-${person.user_id}`}
      onPress={() => {
        haptic("light");
        router.push(`/person/${person.user_id}`);
      }}
      style={{
        backgroundColor: colors.surfaceSecondary,
        borderRadius: 20,
        padding: 16,
        borderWidth: 1,
        borderColor: colors.border,
        gap: 12,
      }}
    >
      <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
        <Avatar uri={person.avatar_url} name={person.name} size={52} />
        <View style={{ flex: 1, gap: 3 }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <AppText variant="heading">{person.name}</AppText>
            <ExchangingDot active={person.is_exchanging} />
          </View>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 3 }}>
              <MapPin size={13} color={colors.brandSecondary} weight="fill" />
              <AppText variant="caption" color={colors.muted}>
                {person.distance_km < 999 ? `${person.distance_km} km` : person.neighborhood}
              </AppText>
            </View>
            <RatingPill rating={person.rating} count={person.rating_count} />
            <AppText variant="caption" color={colors.muted}>
              {person.swaps_count} swaps
            </AppText>
          </View>
        </View>
      </View>

      {person.genres?.length > 0 && (
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6 }}>
          {person.genres.slice(0, 3).map((g) => (
            <View key={g} style={{ backgroundColor: colors.surfaceTertiary, paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999 }}>
              <AppText variant="caption" color={colors.onSurfaceTertiary}>
                {g}
              </AppText>
            </View>
          ))}
        </View>
      )}

      {person.books && person.books.length > 0 && (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}>
          {person.books.map((b) => (
            <BookCover key={b.id} uri={b.cover_url} width={48} />
          ))}
          {(person.available_count || 0) > person.books.length && (
            <View style={{ width: 48, height: 72, borderRadius: 8, backgroundColor: colors.surfaceTertiary, alignItems: "center", justifyContent: "center" }}>
              <AppText variant="caption" color={colors.muted}>
                +{(person.available_count || 0) - person.books.length}
              </AppText>
            </View>
          )}
        </ScrollView>
      )}
    </Pressable>
  );
}

export function BookTile({
  book,
  width,
  onPress,
  showStatus,
  showOwner,
}: {
  book: Book;
  width: number;
  onPress?: () => void;
  showStatus?: boolean;
  showOwner?: boolean;
}) {
  const { colors } = useTheme();
  return (
    <Pressable
      testID={`book-tile-${book.id}`}
      onPress={() => {
        if (onPress) {
          haptic("light");
          onPress();
        }
      }}
      style={{ width, gap: 8 }}
    >
      <BookCover uri={book.cover_url} width={width} />
      <View style={{ gap: 3 }}>
        <AppText variant="label" numberOfLines={1}>
          {book.title}
        </AppText>
        <AppText variant="caption" color={colors.muted} numberOfLines={1}>
          {showOwner && book.owner_name ? book.owner_name : book.author}
        </AppText>
        {showStatus && <StatusBadge status={book.status} />}
      </View>
    </Pressable>
  );
}
