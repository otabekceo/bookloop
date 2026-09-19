import { View, Pressable } from "react-native";
import { Tabs, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQuery } from "@tanstack/react-query";
import { House, MapTrifold, Books, ArrowsClockwise, User, Plus } from "phosphor-react-native";

import { AppText, haptic, useDirectionalStyle } from "@/src/components/ui";
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

function CustomTabBar({ state, navigation }: any) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { t } = useLanguage();
  // The badge is absolutely positioned; mirror its anchor under RTL so it sits
  // on the leading corner of the icon in Arabic.
  const badgeStyle = useDirectionalStyle({
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
  });

  const { data: notif } = useQuery({
    queryKey: ["notifications"],
    queryFn: () => apiFetch<{ incoming_requests: number; total: number }>("/api/notifications"),
    refetchInterval: 15000,
  });

  const routes = state.routes.filter((r: any) => LABEL_KEYS[r.name]);

  const renderTab = (route: any) => {
    const index = state.routes.indexOf(route);
    const focused = state.index === index;
    const Icon = ICONS[route.name];
    const badge = route.name === "swaps" ? notif?.incoming_requests || 0 : 0;
    return (
      <Pressable
        key={route.key}
        testID={`tab-${route.name}`}
        onPress={() => {
          haptic("selection");
          if (!focused) navigation.navigate(route.name);
        }}
        style={{ flex: 1, alignItems: "center", justifyContent: "center", gap: 3, paddingTop: 8 }}
      >
        <View>
          <Icon size={24} color={focused ? colors.brandPrimary : colors.muted} weight={focused ? "fill" : "regular"} />
          {badge > 0 && (
            <View style={badgeStyle}>
              <AppText variant="caption" color={colors.onBrandPrimary} style={{ fontSize: 10 }}>
                {badge}
              </AppText>
            </View>
          )}
        </View>
        <AppText variant="caption" color={focused ? colors.brandPrimary : colors.muted} style={{ fontSize: 10 }}>
          {t(LABEL_KEYS[route.name])}
        </AppText>
      </Pressable>
    );
  };

  return (
    <View
      style={{
        flexDirection: "row",
        backgroundColor: colors.surfaceSecondary,
        borderTopWidth: 1,
        borderTopColor: colors.border,
        paddingBottom: insets.bottom,
        height: 64 + insets.bottom,
      }}
    >
      {renderTab(routes[0])}
      {renderTab(routes[1])}
      {/* Center FAB slot */}
      <View style={{ width: 72, alignItems: "center" }}>
        <Pressable
          testID="add-book-fab"
          onPress={() => {
            haptic("light");
            router.push("/book/add");
          }}
          style={{
            position: "absolute",
            top: -18,
            width: 58,
            height: 58,
            borderRadius: 29,
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
      {renderTab(routes[2])}
      {renderTab(routes[3])}
      {renderTab(routes[4])}
    </View>
  );
}

export default function TabsLayout() {
  return (
    <Tabs tabBar={(props) => <CustomTabBar {...props} />} screenOptions={{ headerShown: false }}>
      <Tabs.Screen name="discover" />
      <Tabs.Screen name="map" />
      <Tabs.Screen name="books" />
      <Tabs.Screen name="swaps" />
      <Tabs.Screen name="profile" />
    </Tabs>
  );
}
