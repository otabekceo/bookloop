import { View, Pressable } from "react-native";
import { useRouter } from "expo-router";
import { UserCircle } from "phosphor-react-native";

import { AppText, Avatar, Stars } from "@/src/components/ui";
import { reviewerView, type PublicReview } from "@/src/reviews";
import { formatDate } from "@/src/i18n";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { makeStyles, useTheme } from "@/src/theme";

/**
 * One received review. Identified: the reviewer's public name + avatar, tapping opens their profile.
 * Anonymous: a generic "BookLoop member" with a neutral icon — nothing to tap, nothing to identify.
 */
export function ReviewCard({ review, compact = false }: { review: PublicReview; compact?: boolean }) {
  const styles = useStyles();
  const { colors } = useTheme();
  const { t, language } = useLanguage();
  const router = useRouter();
  const who = reviewerView(review);
  const size = compact ? 32 : 38;
  const name = who.anonymous ? t("reviews.bookLoopMember") : who.name;

  const header = (
    <>
      {who.anonymous ? (
        <View style={[styles.anonAvatar, { width: size, height: size, borderRadius: size / 2 }]}>
          <UserCircle size={size - 8} color={colors.muted} weight="regular" />
        </View>
      ) : (
        <Avatar uri={who.avatar} name={who.name} size={size} />
      )}
      <View style={{ flex: 1, minWidth: 0 }}>
        <AppText variant="label" numberOfLines={1}>
          {name}
        </AppText>
        {review.created_at ? (
          <AppText variant="caption" color={colors.muted} numberOfLines={1}>
            {formatDate(review.created_at, language)}
          </AppText>
        ) : null}
      </View>
      <Stars value={review.stars} />
    </>
  );

  return (
    <View testID={who.anonymous ? "review-anonymous" : "review-identified"} style={styles.card}>
      {!who.anonymous && who.profileId ? (
        <Pressable onPress={() => router.push(`/person/${who.profileId}`)} style={styles.head} accessibilityRole="button" accessibilityLabel={name}>
          {header}
        </Pressable>
      ) : (
        <View style={styles.head}>{header}</View>
      )}
      {review.review ? (
        <AppText variant="body" color={compact ? colors.muted : colors.onSurface}>
          {review.review}
        </AppText>
      ) : null}
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  card: { backgroundColor: colors.surfaceSecondary, borderRadius: 16, padding: 14, borderWidth: 1, borderColor: colors.border, gap: 8 },
  head: { flexDirection: "row", alignItems: "center", gap: 10 },
  anonAvatar: { backgroundColor: colors.surfaceTertiary, alignItems: "center", justifyContent: "center" },
}));
