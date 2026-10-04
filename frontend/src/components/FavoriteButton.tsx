import { useRef, useState } from "react";
import { Pressable, type StyleProp, type ViewStyle } from "react-native";
import { useQueryClient } from "@tanstack/react-query";
import { Heart } from "phosphor-react-native";

import { haptic, useToast } from "@/src/components/ui";
import { apiFetch } from "@/src/api";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { useTheme } from "@/src/theme";

/**
 * Heart toggle for favoriting a reader. Private: the other reader is never notified, and it grants
 * nothing (no chat, no swap). Flips at once, reverts if the server says no.
 */
export function FavoriteButton({
  userId,
  isFavorite,
  size = 22,
  style,
  onChange,
}: {
  userId: string;
  isFavorite: boolean;
  size?: number;
  style?: StyleProp<ViewStyle>;
  onChange?: (next: boolean) => void;
}) {
  const { colors } = useTheme();
  const { t } = useLanguage();
  const toast = useToast();
  const qc = useQueryClient();
  const [on, setOn] = useState(isFavorite);
  const busy = useRef(false);

  // A refetch (another screen changed it) wins over the local state — adjusted while rendering, the
  // React-recommended way to follow a prop, instead of an extra effect pass.
  const [seen, setSeen] = useState(isFavorite);
  if (seen !== isFavorite) {
    setSeen(isFavorite);
    setOn(isFavorite);
  }

  const toggle = async () => {
    if (busy.current) return;
    busy.current = true;
    const next = !on;
    setOn(next);
    haptic(next ? "success" : "selection");
    try {
      await apiFetch(`/api/favorites/${encodeURIComponent(userId)}`, { method: next ? "PUT" : "DELETE" });
      onChange?.(next);
      qc.invalidateQueries({ queryKey: ["favorites"] });
      qc.invalidateQueries({ queryKey: ["person", userId] });
      // Lists that show hearts refresh the next time they are shown, not right now (no list jump).
      qc.invalidateQueries({ queryKey: ["people"], refetchType: "none" });
    } catch (e: any) {
      setOn(!next);
      toast(e?.message || t("favorites.couldNotUpdate"), "error");
    } finally {
      busy.current = false;
    }
  };

  return (
    <Pressable
      testID={`favorite-${userId}`}
      onPress={toggle}
      hitSlop={10}
      accessibilityRole="button"
      accessibilityState={{ selected: on }}
      accessibilityLabel={on ? t("favorites.remove") : t("favorites.add")}
      style={style}
    >
      <Heart size={size} color={on ? colors.notification : colors.muted} weight={on ? "fill" : "regular"} />
    </Pressable>
  );
}
