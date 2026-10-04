import { useState } from "react";
import { View, Pressable, ActivityIndicator, TextInput } from "react-native";
import { useLocalSearchParams, useRouter, Stack } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowsClockwise, LockSimple } from "phosphor-react-native";

import { AppText, Avatar, Button, DirectionalIcon, haptic, useToast } from "@/src/components/ui";
import { BookPicker } from "@/src/components/BookPicker";
import type { Book } from "@/src/components/cards";
import { apiFetch } from "@/src/api";
import { NOTE_MAX_CHARS, noteCharsLeft } from "@/src/swapFlow";
import { makeStyles, useTheme } from "@/src/theme";
import { useLanguage } from "@/src/i18n/LanguageProvider";

/**
 * A new swap proposal: one of my books for one of theirs (or none — then it's a plain request) plus an
 * optional note. Sending it does NOT open a chat: the other reader first sees the proposal, the note
 * and my profile, then accepts the swap or accepts to continue chatting.
 */
export default function NewProposal() {
  const { userId, bookId } = useLocalSearchParams<{ userId: string; bookId?: string }>();
  const styles = useStyles();
  const { colors } = useTheme();
  const { t } = useLanguage();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const toast = useToast();
  const qc = useQueryClient();

  const [giveId, setGiveId] = useState<string | null>(null);
  const [getId, setGetId] = useState<string | null>(bookId || null);
  const [note, setNote] = useState("");
  const [sending, setSending] = useState(false);

  const personQ = useQuery({
    queryKey: ["person", userId],
    queryFn: () => apiFetch<{ user: any; books: Book[] }>(`/api/users/${userId}`),
  });
  const mineQ = useQuery({
    queryKey: ["myBooks", "Available"],
    queryFn: () => apiFetch<{ books: Book[] }>(`/api/books?status=Available`),
  });

  const person = personQ.data?.user;
  const first = person?.name?.split(" ")[0] || "";
  const theirBooks = (personQ.data?.books || []).filter((b) => b.status === "Available");
  const myBooks = mineQ.data?.books || [];
  const left = noteCharsLeft(note);
  const halfPicked = !!giveId !== !!getId;
  const canSend = !sending && left >= 0 && !halfPicked && person?.is_exchanging !== false;

  const send = async () => {
    if (!canSend) return;
    haptic("light");
    setSending(true);
    try {
      const res = await apiFetch<{ swap: { id: string }; existing: boolean }>("/api/swaps", {
        method: "POST",
        body: {
          receiver_id: userId,
          note: note.trim() || undefined,
          ...(giveId && getId ? { offered_book_id: giveId, requested_book_id: getId } : {}),
        },
      });
      haptic("success");
      qc.invalidateQueries({ queryKey: ["swaps"] });
      toast(res.existing ? t("proposal.alreadyOpen", { name: first }) : t("proposal.sent"), res.existing ? "info" : "success");
      router.replace(`/swap/${res.swap.id}`);
    } catch (e: any) {
      toast(e.message || t("proposal.failed"), "error");
    } finally {
      setSending(false);
    }
  };

  const loading = personQ.isLoading || mineQ.isLoading || !person;

  return (
    <View style={styles.root}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}>
        <Pressable testID="back-button" onPress={() => router.back()} style={styles.backBtn}>
          <DirectionalIcon>
            <ArrowLeft size={22} color={colors.onSurface} />
          </DirectionalIcon>
        </Pressable>
        <AppText variant="heading">{t("proposal.title")}</AppText>
        <View style={{ width: 42 }} />
      </View>

      {loading ? (
        <ActivityIndicator color={colors.brandPrimary} style={{ marginTop: 60 }} />
      ) : (
        <>
          <KeyboardAwareScrollView contentContainerStyle={{ padding: 20, gap: 18, paddingBottom: 40 }} bottomOffset={100} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
              <Avatar uri={person.avatar_url} name={person.name} size={48} />
              <View style={{ flex: 1 }}>
                <AppText variant="title">{t("proposal.withName", { name: first })}</AppText>
                <AppText variant="caption" color={colors.muted}>
                  {t("proposal.booksHint")}
                </AppText>
              </View>
            </View>

            {person.is_exchanging === false && (
              <View testID="not-accepting" style={styles.warn}>
                <AppText variant="label">{t("person.notAccepting")}</AppText>
              </View>
            )}

            <View style={{ gap: 10 }}>
              <AppText variant="label" color={colors.muted}>{t("swapChat.youGive")}</AppText>
              <BookPicker books={myBooks} selectedId={giveId} onChange={setGiveId} testIDPrefix="give" emptyText={t("swapChat.addBooksFirst")} />
            </View>

            <View style={{ alignItems: "center" }}>
              <ArrowsClockwise size={22} color={colors.muted} />
            </View>

            <View style={{ gap: 10 }}>
              <AppText variant="label" color={colors.muted}>{t("swapChat.youReceive")}</AppText>
              <BookPicker books={theirBooks} selectedId={getId} onChange={setGetId} testIDPrefix="get" emptyText={t("swapChat.theyHaveNoBooks")} />
              {halfPicked && (
                <AppText variant="caption" color={colors.error}>
                  {t("proposal.pickBoth")}
                </AppText>
              )}
            </View>

            <View style={{ gap: 8 }}>
              <AppText variant="label">{t("proposal.noteLabel")}</AppText>
              <TextInput
                testID="proposal-note-input"
                value={note}
                onChangeText={setNote}
                placeholder={t("proposal.notePlaceholder")}
                placeholderTextColor={colors.muted}
                style={styles.noteInput}
                multiline
                // A little slack over the limit so pasted text isn't silently cut; the counter turns red.
                maxLength={NOTE_MAX_CHARS + 40}
              />
              <AppText variant="caption" color={left < 0 ? colors.error : colors.muted} style={{ alignSelf: "flex-end" }}>
                {left < 0 ? t("proposal.noteTooLong") : t("proposal.charsLeft", { count: left })}
              </AppText>
            </View>

            <View style={styles.infoRow}>
              <LockSimple size={16} color={colors.muted} weight="bold" />
              <AppText variant="caption" color={colors.muted} style={{ flex: 1 }}>
                {t("proposal.chatAfterAccept", { name: first })}
              </AppText>
            </View>
          </KeyboardAwareScrollView>

          <View style={[styles.footer, { paddingBottom: insets.bottom + 12 }]}>
            <Button
              testID="send-new-proposal"
              title={sending ? t("swapChat.sending") : t("swapChat.sendProposal")}
              icon={<ArrowsClockwise size={18} color={colors.onBrandPrimary} weight="bold" />}
              onPress={send}
              disabled={!canSend}
              loading={sending}
            />
          </View>
        </>
      )}
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.surface },
  topBar: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 20, paddingBottom: 10, backgroundColor: colors.surface, borderBottomWidth: 1, borderBottomColor: colors.border },
  backBtn: { width: 42, height: 42, borderRadius: 21, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  noteInput: { backgroundColor: colors.surfaceTertiary, borderRadius: 12, padding: 14, minHeight: 90, color: colors.onSurface, fontFamily: "DMSans-Regular", fontSize: 15, textAlignVertical: "top" },
  infoRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  warn: { backgroundColor: colors.surfaceTertiary, borderRadius: 12, padding: 12 },
  footer: { paddingHorizontal: 20, paddingTop: 12, backgroundColor: colors.surface, borderTopWidth: 1, borderTopColor: colors.border },
}));
