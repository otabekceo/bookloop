import { View, Pressable } from "react-native";
import { useRouter } from "expo-router";
import { UserCircle } from "phosphor-react-native";

import { AppText, Avatar, Stars } from "@/src/components/ui";
import { ReportButton } from "@/src/components/Safety";
import { useAuth } from "@/src/auth";
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
  const { user: me } = useAuth();
  const who = reviewerView(review);
  const size = compact ? 32 : 38;
  // An identified review without a profile to open was written by an account that has since been deleted.
  const name = who.anonymous ? t("reviews.bookLoopMember") : who.profileId ? who.name : t("common.deletedReader");
  const canReport = !!review.id && (!me || review.rater_id !== me.user_id);

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
      <View style={styles.headRow}>
        {!who.anonymous && who.profileId ? (
          <Pressable onPress={() => router.push(`/person/${who.profileId}`)} style={[styles.head, { flex: 1 }]} accessibilityRole="button" accessibilityLabel={name}>
            {header}
          </Pressable>
        ) : (
          <View style={[styles.head, { flex: 1 }]}>{header}</View>
        )}
        {canReport ? <ReportButton target={{ type: "review", id: review.id }} size={16} style={styles.report} /> : null}
      </View>
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
  headRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  // 44 pt touch target around a small icon.
  report: { width: 44, height: 44, alignItems: "center", justifyContent: "center", marginEnd: -10 },
  anonAvatar: { backgroundColor: colors.surfaceTertiary, alignItems: "center", justifyContent: "center" },
}));
