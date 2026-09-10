import { Image } from "expo-image";

const FULL = require("../../assets/images/bookloop-logo.png");
const WORDMARK = require("../../assets/images/bookloop-wordmark.png");

// Aspect ratios of the processed assets.
const FULL_RATIO = 723 / 430;
const WORD_RATIO = 694 / 210;

export function Logo({ variant = "wordmark", height }: { variant?: "full" | "wordmark"; height: number }) {
  const ratio = variant === "full" ? FULL_RATIO : WORD_RATIO;
  return (
    <Image
      source={variant === "full" ? FULL : WORDMARK}
      style={{ height, width: height * ratio }}
      contentFit="contain"
    />
  );
}
