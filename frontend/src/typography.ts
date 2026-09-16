// Font families and the asset map for expo-font's useFonts.
export const FONTS = {
  display: "Fraunces-Bold", // major editorial headings
  displaySemi: "Fraunces-SemiBold",
  regular: "DMSans-Regular",
  medium: "DMSans-Medium",
  bold: "DMSans-Bold",
};

// Arabic script needs a font with proper Arabic glyph coverage. Fraunces and
// DM Sans have none, so we swap in Noto Sans Arabic when the active language
// is Arabic (see `fontsForLanguage`).
export const ARABIC_FONTS = {
  display: "NotoSansArabic-Bold",
  displaySemi: "NotoSansArabic-Bold",
  regular: "NotoSansArabic-Regular",
  medium: "NotoSansArabic-Medium",
  bold: "NotoSansArabic-Bold",
};

export const fontAssets = {
  "Fraunces-Bold": require("../assets/fonts/Fraunces-Bold.ttf"),
  "Fraunces-SemiBold": require("../assets/fonts/Fraunces-SemiBold.ttf"),
  "DMSans-Regular": require("../assets/fonts/DMSans-Regular.ttf"),
  "DMSans-Medium": require("../assets/fonts/DMSans-Medium.ttf"),
  "DMSans-Bold": require("../assets/fonts/DMSans-Bold.ttf"),
  "NotoSansArabic-Regular": require("../assets/fonts/NotoSansArabic-Regular.ttf"),
  "NotoSansArabic-Medium": require("../assets/fonts/NotoSansArabic-Medium.ttf"),
  "NotoSansArabic-Bold": require("../assets/fonts/NotoSansArabic-Bold.ttf"),
};

export type FontRole = keyof typeof FONTS;

/**
 * Returns the font-family map for a given language. Arabic uses Noto Sans
 * Arabic for every role so that headings, body copy and buttons all render
 * Arabic script correctly; every other language keeps the BookLoop editorial
 * pairing of Fraunces + DM Sans.
 */
export function fontsForLanguage(language?: string | null): typeof FONTS {
  return language === "ar" ? ARABIC_FONTS : FONTS;
}
