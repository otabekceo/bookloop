import { View, Pressable, ScrollView } from "react-native";
import { useRouter } from "expo-router";
import { MapPin, Sparkle, Fire } from "phosphor-react-native";

import { AppText, Avatar, BookCover, ExchangingDot, RatingPill, StatusBadge, haptic, useDirectionalStyle } from "@/src/components/ui";
import { Badge, BadgeIcon, topBadge } from "@/src/components/badges";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { badgeLabel } from "@/src/i18n/messageKeys";
import { bidiIsolate } from "@/src/i18n";
import { distanceOrArea } from "@/src/distance";
import { enumLabel } from "@/src/i18n/enums";
import { useTheme } from "@/src/theme";

export type Person = {
  user_id: string;
  name: string;
  avatar_url?: string | null;
  neighborhood?: string | null;
  city?: string | null;
  /** 999 = unknown (either reader has no location yet); never below 0.1 ("Within 100 m"). */
  distance_km: number;
  rating: number;
  rating_count: number;
  swaps_count: number;
  is_exchanging: boolean;
  genres: string[];
  books?: any[];
  available_count?: number;
  shared_genres?: string[];
  shared_languages?: string[];
  shared_interests?: string[];
  match_score?: number;
  badges?: Badge[];
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
  const { t, language } = useLanguage();
  const router = useRouter();
  // Shared-genres pill carries a trailing gap; mirror it under RTL.
  const sharedPillStyle = useDirectionalStyle({ flexDirection: "row", alignItems: "center", gap: 4, marginRight: 2 });
  const shared = new Set(person.shared_genres || []);
  const best = topBadge(person.badges);
  // Show shared genres first so the match is glanceable.
  const genres = [...(person.genres || [])].sort((a, b) => Number(shared.has(b)) - Number(shared.has(a)));
  return (
    <Pressable
      testID={`person-card-${person.user_id}`}
      onPress={() => {
        haptic("light");
        router.push(`/person/${person.user_id}`);
      }}
      style={{
        backgroundColor: colors.glassFill,
        borderRadius: 20,
        padding: 16,
        borderWidth: 1,
        borderColor: colors.glassBorder,
        gap: 12,
      }}
    >
      <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
        <Avatar uri={person.avatar_url} name={person.name} size={52} />
        {/* minWidth 0 lets this column shrink to the card instead of pushing past its edge; both rows
            wrap, so a long name, badge or translated label moves to the next line rather than
            overflowing (nothing is truncated away). */}
        <View style={{ flex: 1, minWidth: 0, gap: 3 }}>
          <View style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", columnGap: 8, rowGap: 4 }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 8, flexShrink: 1, maxWidth: "100%" }}>
              <AppText variant="heading" style={{ flexShrink: 1 }}>
                {person.name}
              </AppText>
              <ExchangingDot active={person.is_exchanging} />
            </View>
            {best && (
              <View testID={`person-badge-${best.id}`} style={{ flexDirection: "row", alignItems: "center", gap: 3, flexShrink: 1, maxWidth: "100%", backgroundColor: colors.brandTertiary, paddingHorizontal: 7, paddingVertical: 2, borderRadius: 999 }}>
                <BadgeIcon id={best.id} size={12} color={colors.onBrandTertiary} />
                <AppText variant="caption" color={colors.onBrandTertiary} style={{ fontSize: 11, flexShrink: 1 }}>
                  {badgeLabel(t, best)}
                </AppText>
              </View>
            )}
          </View>
          <View style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", columnGap: 10, rowGap: 2 }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 3, flexShrink: 1, maxWidth: "100%" }}>
              <MapPin size={13} color={colors.brandSecondary} weight="fill" />
              <AppText variant="caption" color={colors.muted} style={{ flexShrink: 1 }}>
                {distanceOrArea(t, person)}
              </AppText>
            </View>
            <RatingPill rating={person.rating} count={person.rating_count} />
            <AppText variant="caption" color={colors.muted} style={{ flexShrink: 1 }}>
              {t("common.swapCount", { count: person.swaps_count })}
            </AppText>
          </View>
        </View>
      </View>

      {genres.length > 0 && (
        <View style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 6 }}>
          {shared.size > 0 && (
            <View testID="shared-genres-pill" style={sharedPillStyle}>
              <Sparkle size={13} color={colors.brandPrimary} weight="fill" />
              <AppText variant="caption" color={colors.brandPrimary}>
                {t("cards.inCommon", { count: shared.size })}
              </AppText>
            </View>
          )}
          {genres.slice(0, 3).map((g) => {
            const hit = shared.has(g);
            return (
              <View key={g} style={{ backgroundColor: hit ? colors.brandTertiary : colors.surfaceTertiary, paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999 }}>
                <AppText variant="caption" color={hit ? colors.onBrandTertiary : colors.onSurfaceTertiary}>
                  {bidiIsolate(enumLabel(t, "genre", g), language)}
                </AppText>
              </View>
            );
          })}
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

/** Compact card for the "Great matches" strip on Discover. */
export function MatchCard({ person }: { person: Person }) {
  const { colors } = useTheme();
  const { t, language } = useLanguage();
  const router = useRouter();
  const shared = person.shared_genres || [];
  return (
    <Pressable
      testID={`match-card-${person.user_id}`}
      onPress={() => {
        haptic("light");
        router.push(`/person/${person.user_id}`);
      }}
      style={{
        width: 156,
        backgroundColor: colors.glassFill,
        borderRadius: 18,
        padding: 14,
        borderWidth: 1,
        borderColor: colors.brandTertiary,
        alignItems: "center",
        gap: 8,
      }}
    >
      <Avatar uri={person.avatar_url} name={person.name} size={56} />
      <AppText variant="label" numberOfLines={1} style={{ textAlign: "center" }}>
        {person.name}
      </AppText>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
        <Sparkle size={12} color={colors.brandPrimary} weight="fill" />
        <AppText variant="caption" color={colors.brandPrimary}>
          {t("cards.genresInCommon", { count: shared.length })}
        </AppText>
      </View>
      <View style={{ flexDirection: "row", flexWrap: "wrap", justifyContent: "center", gap: 4 }}>
        {shared.slice(0, 2).map((g) => (
          <View key={g} style={{ backgroundColor: colors.brandTertiary, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999 }}>
            <AppText variant="caption" color={colors.onBrandTertiary} style={{ fontSize: 11 }}>
              {bidiIsolate(enumLabel(t, "genre", g), language)}
            </AppText>
          </View>
        ))}
      </View>
      <AppText variant="caption" color={colors.muted} style={{ textAlign: "center" }}>
        {distanceOrArea(t, person)}
      </AppText>
    </Pressable>
  );
}

export function BookTile({
  book,
  width,
  onPress,
  showStatus,
  showOwner,
  wantedBy,
}: {
  book: Book;
  width: number;
  onPress?: () => void;
  showStatus?: boolean;
  showOwner?: boolean;
  wantedBy?: number;
}) {
  const { colors } = useTheme();
  const { t } = useLanguage();
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
      <BookCover uri={book.cover_url} width={width} title={book.title} />
      <View style={{ gap: 3 }}>
        <AppText variant="label" numberOfLines={1}>
          {book.title}
        </AppText>
        <AppText variant="caption" color={colors.muted} numberOfLines={1}>
          {showOwner && book.owner_name ? book.owner_name : book.author}
        </AppText>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          {showStatus && <StatusBadge status={book.status} />}
          {(wantedBy || 0) > 0 && (
            <View testID={`wanted-pill-${book.id}`} style={{ flexDirection: "row", alignItems: "center", gap: 3, backgroundColor: colors.brandTertiary, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999 }}>
              <Fire size={11} color={colors.onBrandTertiary} weight="fill" />
              <AppText variant="caption" color={colors.onBrandTertiary} style={{ fontSize: 11 }}>
                {t("cards.wantsThis", { count: wantedBy })}
              </AppText>
            </View>
          )}
        </View>
      </View>
    </Pressable>
  );
}
