import { View, Pressable, StyleSheet, useWindowDimensions } from "react-native";
import { Tabs, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery } from "@tanstack/react-query";
import { House, MapTrifold, Books, ArrowsClockwise, User, Plus } from "phosphor-react-native";

import { AppText, haptic } from "@/src/components/ui";
import { apiFetch } from "@/src/api";
import { useLanguage } from "@/src/i18n/LanguageProvider";
import { useTheme } from "@/src/theme";

const ICONS: Record<string, any> = {
  discover: House,
  map: MapTrifold,
  books: Books,
  swaps: ArrowsClockwise,
  profile: User,
};

// Translation keys for each tab; resolved at render time so the bar re-renders
// when the language changes.
const LABEL_KEYS: Record<string, string> = {
  discover: "tabs.discover",
  map: "tabs.map",
  books: "tabs.books",
  swaps: "tabs.swaps",
  profile: "tabs.profile",
};

// The physical order of the bar, left to right, in EVERY language (Arabic included).
// Only the labels are translated; the positions never change.
const TAB_ORDER = ["discover", "map", "books", "swaps", "profile"] as const;

const TAB_BAR_HEIGHT = 64; // excludes the bottom safe-area inset
const FAB_SIZE = 58;
const FAB_GAP = 12; // clear space between the floating button and the top of the bar

function CustomTabBar({ state, navigation }: any) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const { t } = useLanguage();

  const { data: notif } = useQuery({
    queryKey: ["notifications"],
    queryFn: () => apiFetch<{ incoming_requests: number; total: number }>("/api/notifications"),
    refetchInterval: 15000,
  });

  // Look routes up by name so the on-screen order is TAB_ORDER whatever order the navigator lists them in.
  const routes = TAB_ORDER.map((name) => state.routes.find((r: any) => r.name === name)).filter(Boolean);

  const renderTab = (route: any) => {
    const index = state.routes.indexOf(route);
    const focused = state.index === index;
    const Icon = ICONS[route.name];
    const badge = route.name === "swaps" ? notif?.incoming_requests || 0 : 0;
    return (
      <Pressable
        key={route.key}
        testID={`tab-${route.name}`}
        accessibilityRole="tab"
        accessibilityState={{ selected: focused }}
        onPress={() => {
          haptic("selection");
          if (!focused) navigation.navigate(route.name);
        }}
        style={{ flex: 1, alignItems: "center", justifyContent: "center", gap: 3, paddingTop: 8, paddingHorizontal: 2 }}
      >
        <View>
          <Icon size={24} color={focused ? colors.brandPrimary : colors.muted} weight={focused ? "fill" : "regular"} />
          {badge > 0 && (
            // Physical top-right corner of the icon in every language: this bar is pinned to LTR below.
            <View
              style={{
                position: "absolute",
                top: -4,
                right: -8,
                minWidth: 16,
                height: 16,
                borderRadius: 8,
                backgroundColor: colors.brandPrimary,
                alignItems: "center",
                justifyContent: "center",
                paddingHorizontal: 4,
              }}
            >
              <AppText variant="caption" color={colors.onBrandPrimary} style={{ fontSize: 10 }}>
                {badge}
              </AppText>
            </View>
          )}
        </View>
        <AppText
          variant="caption"
          color={focused ? colors.brandPrimary : colors.muted}
          numberOfLines={1}
          adjustsFontSizeToFit
          minimumFontScale={0.8}
          style={{ fontSize: 10, textAlign: "center" }}
        >
          {t(LABEL_KEYS[route.name])}
        </AppText>
      </Pressable>
    );
  };

  return (
    <View
      style={{
        // Pin the bar to left-to-right. Without this, Arabic (RTL) makes `flexDirection: "row"` lay the
        // tabs out right-to-left and reverses the whole bar. Setting `direction` here (not globally) keeps
        // RTL working for all the screen content.
        direction: "ltr",
        flexDirection: "row",
        backgroundColor: colors.surfaceSecondary,
        borderTopWidth: 1,
        borderTopColor: colors.border,
        paddingBottom: insets.bottom,
        height: TAB_BAR_HEIGHT + insets.bottom,
      }}
    >
      {routes.map(renderTab)}
    </View>
  );
}

/**
 * The orange "+" button. It is NOT a tab: it floats above the bar, over the Profile column on the physical
 * right, and opens the existing Add Book flow. It lives in a full-screen, touch-transparent overlay (rather
 * than sticking out of the bar) so it stays tappable on Android, and that overlay is pinned to LTR so
 * `right` always means the physical right edge, in every language.
 */
function AddBookFab() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { width } = useWindowDimensions();
  const { t } = useLanguage();

  // Centre the button over the Profile tab (the last of the equal-width columns), but keep it at least
  // 8px from the screen edge on very narrow phones.
  const right = Math.max(8, (width / TAB_ORDER.length - FAB_SIZE) / 2);

  return (
    <View pointerEvents="box-none" style={[StyleSheet.absoluteFill, { direction: "ltr" }]}>
      <Pressable
        testID="add-book-fab"
        accessibilityRole="button"
        accessibilityLabel={t("profile.addBook")}
        onPress={() => {
          haptic("light");
          router.push("/book/add");
        }}
        style={{
          position: "absolute",
          right,
          bottom: insets.bottom + TAB_BAR_HEIGHT + FAB_GAP,
          width: FAB_SIZE,
          height: FAB_SIZE,
          borderRadius: FAB_SIZE / 2,
          backgroundColor: colors.brandPrimary,
          alignItems: "center",
          justifyContent: "center",
          shadowColor: "#000",
          shadowOpacity: 0.18,
          shadowRadius: 8,
          shadowOffset: { width: 0, height: 4 },
          elevation: 6,
        }}
      >
        <Plus size={28} color={colors.onBrandPrimary} weight="bold" />
      </Pressable>
    </View>
  );
}

export default function TabsLayout() {
  return (
    <View style={{ flex: 1 }}>
      <Tabs tabBar={(props) => <CustomTabBar {...props} />} screenOptions={{ headerShown: false }}>
        <Tabs.Screen name="discover" />
        <Tabs.Screen name="map" />
        <Tabs.Screen name="books" />
        <Tabs.Screen name="swaps" />
        <Tabs.Screen name="profile" />
      </Tabs>
      <AddBookFab />
    </View>
  );
}
