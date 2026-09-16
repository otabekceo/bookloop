import { View } from "react-native";
import { Medal, Fire, Books, Crown } from "phosphor-react-native";

import { AppText } from "@/src/components/ui";
import { makeStyles, useTheme } from "@/src/theme";
import { useLanguage } from "@/src/i18n/LanguageProvider";

export type Badge = { id: string; label: string; threshold: number; blurb: string; earned: boolean };

const ICONS: Record<string, any> = { first_loop: Medal, regular: Fire, bookworm: Books, legend: Crown };

export function BadgeIcon({ id, size = 16, color, weight = "fill" }: { id: string; size?: number; color: string; weight?: "fill" | "regular" | "light" }) {
  const Icon = ICONS[id] || Medal;
  return <Icon size={size} color={color} weight={weight} />;
}

export function topBadge(badges?: Badge[]): Badge | undefined {
  return (badges || []).filter((b) => b.earned).sort((a, b) => b.threshold - a.threshold)[0];
}

/** Compact earned-badge pills — for other readers' profiles. */
export function BadgeChips({ badges }: { badges?: Badge[] }) {
  const styles = useStyles();
  const { colors } = useTheme();
  const earned = (badges || []).filter((b) => b.earned);
  if (earned.length === 0) return null;
  return (
    <View style={styles.chipWrap}>
      {earned.map((b) => (
        <View key={b.id} testID={`badge-chip-${b.id}`} style={styles.chip}>
          <BadgeIcon id={b.id} size={14} color={colors.onBrandTertiary} />
          <AppText variant="caption" color={colors.onBrandTertiary}>
            {b.label}
          </AppText>
        </View>
      ))}
    </View>
  );
}

/** Full badge grid with locked/unlocked state — for the owner's profile. */
export function BadgeGrid({ badges, swapsCount }: { badges: Badge[]; swapsCount: number }) {
  const styles = useStyles();
  const { colors } = useTheme();
  const { t } = useLanguage();
  const next = badges.find((b) => !b.earned);
  return (
    <View style={{ gap: 12 }}>
      <View style={styles.grid}>
        {badges.map((b) => (
          <View key={b.id} testID={`badge-${b.id}-${b.earned ? "earned" : "locked"}`} style={styles.cell}>
            <View style={[styles.circle, b.earned ? styles.circleOn : styles.circleOff]}>
              <BadgeIcon id={b.id} size={22} color={b.earned ? colors.onBrandPrimary : colors.muted} weight={b.earned ? "fill" : "regular"} />
            </View>
            <AppText variant="caption" color={b.earned ? colors.onSurface : colors.muted} style={{ textAlign: "center" }} numberOfLines={2}>
              {b.label}
            </AppText>
          </View>
        ))}
      </View>
      <AppText variant="caption" color={colors.muted} testID="badge-progress">
        {next
          ? t("badges.progress", { count: next.threshold - swapsCount, label: next.label })
          : t("badges.allUnlocked")}
      </AppText>
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  chipWrap: { flexDirection: "row", flexWrap: "wrap", gap: 6, justifyContent: "center" },
  chip: { flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: colors.brandTertiary, paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999 },
  grid: { flexDirection: "row", gap: 10 },
  cell: { flex: 1, alignItems: "center", gap: 6 },
  circle: { width: 52, height: 52, borderRadius: 26, alignItems: "center", justifyContent: "center" },
  circleOn: { backgroundColor: colors.brandPrimary },
  circleOff: { backgroundColor: colors.surfaceTertiary, borderWidth: 1, borderColor: colors.border },
}));
