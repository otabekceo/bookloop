import { Image } from "expo-image";

import { useTheme } from "@/src/theme";

// Same artwork, two color variants (see frontend/scripts/generate-dark-logo.py): the original
// dark-ink logo for light backgrounds, and a recolored cream/green variant for dark ones — same
// geometry, alpha (and therefore anti-aliasing) preserved exactly. Never black-on-black.
const FULL_LIGHT = require("../../assets/images/bookloop-logo.png");
const FULL_DARK = require("../../assets/images/bookloop-logo-dark.png");
const WORDMARK_LIGHT = require("../../assets/images/bookloop-wordmark.png");
const WORDMARK_DARK = require("../../assets/images/bookloop-wordmark-dark.png");

// Aspect ratios of the processed assets.
const FULL_RATIO = 723 / 430;
const WORD_RATIO = 694 / 210;

export function Logo({ variant = "wordmark", height }: { variant?: "full" | "wordmark"; height: number }) {
  const { scheme } = useTheme();
  const ratio = variant === "full" ? FULL_RATIO : WORD_RATIO;
  const source =
    variant === "full" ? (scheme === "dark" ? FULL_DARK : FULL_LIGHT) : scheme === "dark" ? WORDMARK_DARK : WORDMARK_LIGHT;
  return (
    <Image
      source={source}
      style={{ height, width: height * ratio }}
      contentFit="contain"
    />
  );
}
