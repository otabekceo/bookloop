import { Alert, Platform } from "react-native";

import { apiFetch } from "@/src/api";
import { decideSwapStart, type SwapWith } from "@/src/swapFlow";

type Translate = (key: string, options?: Record<string, unknown>) => string;

const STATUS_KEYS: Record<string, string> = {
  completed: "swaps.statusCompleted",
  declined: "swaps.statusDeclined",
  cancelled: "swaps.statusCancelled",
};

/**
 * "Request swap" from a profile or a book (the decision itself is decideSwapStart in swapFlow.ts):
 *
 * - An open swap with this reader (pending / active) -> open that conversation.
 * - The reader is not accepting exchanges -> their previous conversation if it is still open to chat,
 *   otherwise a "not accepting exchanges" message. The server enforces this too.
 * - Only finished swaps -> ask: open the previous conversation, or compose a NEW proposal.
 * - No swap yet -> compose a proposal (books + optional note). Nothing is created until it is sent.
 */
export async function openOrRequestSwap(opts: {
  userId: string;
  name: string;
  /** The book the request started from (Book detail): preselected as the book to receive. */
  bookId?: string;
  t: Translate;
  open: (swapId: string) => void;
  compose: (userId: string, bookId?: string) => void;
  onError: (message: string) => void;
}): Promise<void> {
  const { userId, name, bookId, t, open, compose, onError } = opts;
  const w = await apiFetch<SwapWith>(`/api/swaps/with/${encodeURIComponent(userId)}`);
  const d = decideSwapStart(w);
  switch (d.kind) {
    case "open":
    case "openPreviousOnly":
      open(d.swapId);
      return;
    case "notExchanging":
      onError(t("person.notAcceptingNamed", { name }));
      return;
    case "compose":
      compose(userId, bookId);
      return;
    case "askPrevious": {
      const status = STATUS_KEYS[d.status] ? t(STATUS_KEYS[d.status]) : d.status;
      const body = t("swaps.previousBody", { name, status });
      if (Platform.OS === "web") {
        // react-native-web has no multi-button Alert.
        if (window.confirm(`${body}\n\n${t("swaps.newRequest")}?`)) compose(userId, bookId);
        else open(d.swapId);
        return;
      }
      Alert.alert(t("swaps.previousTitle", { name }), body, [
        { text: t("common.cancel"), style: "cancel" },
        { text: t("swaps.openPrevious"), onPress: () => open(d.swapId) },
        { text: t("swaps.newRequest"), onPress: () => compose(userId, bookId) },
      ]);
    }
  }
}
