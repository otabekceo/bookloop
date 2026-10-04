import { View, Pressable } from "react-native";
// gesture-handler's ScrollView, not React Native's: inside @gorhom/bottom-sheet a plain RN horizontal
// ScrollView loses the swipe to the sheet's own pan gesture on Android, so only the covers that fit on
// screen (~5 on a phone) could ever be picked. This one scrolls in sheets and on normal screens alike,
// and a press that turns into a scroll is cancelled instead of selecting a book.
import { ScrollView } from "react-native-gesture-handler";
import { CheckCircle } from "phosphor-react-native";

import { AppText, BookCover, haptic } from "@/src/components/ui";
import { togglePick } from "@/src/swapFlow";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { makeStyles, useTheme } from "@/src/theme";

type PickerBook = { id: string; title: string; cover_url?: string | null };

/** A horizontal, fully scrollable row of books; tap selects, tap again clears. Every book is reachable. */
export function BookPicker({
  books,
  selectedId,
  onChange,
  testIDPrefix,
  emptyText,
  coverWidth = 64,
}: {
  books: PickerBook[];
  selectedId: string | null;
  onChange: (id: string | null) => void;
  testIDPrefix: string;
  emptyText: string;
  coverWidth?: number;
}) {
  const styles = useStyles();
  const { colors } = useTheme();
  const { t } = useLanguage();

  if (books.length === 0) {
    return (
      <AppText variant="body" color={colors.muted}>
        {emptyText}
      </AppText>
    );
  }
  return (
    <View style={{ gap: 6 }}>
      <ScrollView
        testID={`${testIDPrefix}-list`}
        horizontal
        showsHorizontalScrollIndicator={books.length > 3}
        contentContainerStyle={styles.row}
        keyboardShouldPersistTaps="handled"
      >
        {books.map((b) => {
          const on = selectedId === b.id;
          return (
            <Pressable
              key={b.id}
              testID={`${testIDPrefix}-${b.id}`}
              accessibilityRole="button"
              accessibilityState={{ selected: on }}
              accessibilityLabel={b.title}
              onPress={() => {
                haptic("selection");
                onChange(togglePick(selectedId, b.id));
              }}
              style={[styles.pick, on && styles.pickOn, { width: coverWidth + 16 }]}
            >
              <View>
                <BookCover uri={b.cover_url} width={coverWidth} />
                {on && (
                  <View style={styles.check}>
                    <CheckCircle size={20} color={colors.brandPrimary} weight="fill" />
                  </View>
                )}
              </View>
              <AppText variant="caption" numberOfLines={2} style={{ width: coverWidth, textAlign: "center" }}>
                {b.title}
              </AppText>
            </Pressable>
          );
        })}
      </ScrollView>
      {books.length > 3 && (
        <AppText variant="caption" color={colors.muted}>
          {t("proposal.bookCount", { count: books.length })}
        </AppText>
      )}
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  row: { gap: 8, paddingVertical: 2 },
  pick: { alignItems: "center", gap: 4, padding: 6, borderRadius: 12, borderWidth: 2, borderColor: "transparent" },
  pickOn: { borderColor: colors.brandPrimary, backgroundColor: colors.brandTertiary },
  check: { position: "absolute", top: -6, end: -6, backgroundColor: colors.surface, borderRadius: 12 },
}));
