import i18n from "@/src/i18n";

/**
 * The backend answers errors with an English `detail` string. The app shows that text in toasts, so
 * without this every server error appeared in English whatever the app language. Known messages are
 * mapped to translation keys here; anything unrecognised falls back to a translated generic message
 * (never raw English). Errors that carry a machine `code` (registration/OTP) are handled by their
 * screens and are left untouched.
 */
const EXACT: Record<string, string> = {
  "Invalid session": "apiErrors.sessionExpired",
  "Session expired": "apiErrors.sessionExpired",
  "Not authenticated": "apiErrors.sessionExpired",
  "Invalid email or password": "apiErrors.invalidCredentials",
  "Email already registered": "apiErrors.emailRegistered",
  "Password is too long (maximum 72 bytes)": "apiErrors.passwordTooLong",
  "Invalid or expired sign-in attempt": "apiErrors.invalidSignIn",
  "Google sign-in is not configured": "apiErrors.googleNotConfigured",
  "Could not send the verification email — please try again": "apiErrors.emailSendFailed",
  "Not found": "apiErrors.notFound",
  "User not found": "apiErrors.userNotFound",
  "Book not found": "apiErrors.bookNotFound",
  "Swap not found": "apiErrors.swapNotFound",
  "Unsupported language": "apiErrors.unsupportedLanguage",
  "Title cannot be empty": "apiErrors.titleEmpty",
  "Invalid status": "apiErrors.invalidStatus",
  "This book is part of an active swap; cancel the swap first": "apiErrors.bookInActiveSwap",
  "Cannot swap with yourself": "apiErrors.cannotSwapSelf",
  "Swap is no longer pending": "apiErrors.swapNotPending",
  "Swap can no longer be cancelled": "apiErrors.swapCannotCancel",
  "Offered book must be one of your available books": "apiErrors.offeredBookNotYours",
  "Requested book must be an available book of the other reader": "apiErrors.requestedBookUnavailable",
  "One of the proposed books is no longer available": "apiErrors.bookNoLongerAvailable",
  "No proposal to accept": "apiErrors.noProposal",
  "You cannot accept your own proposal": "apiErrors.cannotAcceptOwn",
  "Use cancel to withdraw your own proposal": "apiErrors.useCancelInstead",
  "Swap not completed yet": "apiErrors.swapNotCompleted",
  "Already rated": "apiErrors.alreadyRated",
  "Empty message": "apiErrors.emptyMessage",
  "Unsupported file type; upload a JPEG, PNG, WebP or HEIC image": "apiErrors.unsupportedFileType",
  "Image too large (max 8 MB)": "apiErrors.imageTooLarge",
  "Could not store the image": "apiErrors.imageStoreFailed",
  "Invalid or expired file link": "apiErrors.invalidFileLink",
};

const SWAP_STATUS_KEYS: Record<string, string> = {
  pending: "swaps.statusPending",
  active: "swaps.statusActive",
  accepted: "swaps.statusActive",
  completed: "swaps.statusCompleted",
  declined: "swaps.statusDeclined",
  cancelled: "swaps.statusCancelled",
};

/** Translated text for a failed request. `status` 0 means the request never reached the server. */
export function localizeApiError(detail: string | undefined, status: number): string {
  const t = i18n.t.bind(i18n);
  if (status === 0) return t("errors.network");
  if (detail && EXACT[detail]) return t(EXACT[detail]);
  const notAllowed = detail?.match(/^Swap is (\w+); action not allowed$/);
  if (notAllowed) {
    const key = SWAP_STATUS_KEYS[notAllowed[1]];
    return t("apiErrors.swapActionNotAllowed", { status: key ? t(key) : notAllowed[1] });
  }
  if (status === 422) return t("apiErrors.invalidInput");
  if (status === 401) return t("apiErrors.sessionExpired");
  if (status === 413) return t("apiErrors.imageTooLarge");
  return t("errors.generic");
}
