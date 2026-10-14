// "Invite friends" on the Profile screen: what gets shared. Kept free of React/Expo imports so it is
// unit-tested (scripts/logic.test.mjs).

/**
 * TEMPORARY invite destination: the current Android preview build on Expo, until BookLoop is in the
 * stores. Replace INVITE_URL with the production Play Store / App Store / deep-link URL at launch;
 * every share and copy action reads it from here.
 */
export const TEMPORARY_INVITE_URL =
  "https://expo.dev/accounts/otabekceo/projects/bookloop/builds/5028139b-8384-4bf3-9806-0d1479f534cc";

export const INVITE_URL = TEMPORARY_INVITE_URL;

type Translate = (key: string, options?: Record<string, unknown>) => string;

/**
 * The invitation text in the app's CURRENT interface language (`t` from useLanguage, which follows the
 * language the reader chose in BookLoop, not the device language), then the link on its own line.
 * The link stays a separate paragraph with no direction marks around it, so in Arabic the message
 * reads right-to-left while the URL stays intact and tappable in the share sheet.
 */
export function inviteMessage(t: Translate): string {
  return `${t("profile.inviteMessage")}\n\n${INVITE_URL}`;
}
