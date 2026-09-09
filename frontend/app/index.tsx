import { View } from "react-native";

import { useTheme } from "@/src/theme";

// Splash placeholder; RootNavigator handles routing based on auth state.
export default function Index() {
  const { colors } = useTheme();
  return <View style={{ flex: 1, backgroundColor: colors.surface }} testID="index-splash" />;
}
