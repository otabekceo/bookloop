import { WebView } from "react-native-webview";

import { buildLeafletHTML } from "@/src/components/leaflet";
import { useLanguage } from "@/src/i18n/LanguageProvider";

export default function DensityMap({
  clusters,
  center,
  onSelect,
}: {
  clusters: any[];
  /** The viewer's own location; the map opens centered there. */
  center?: { lat: number; lng: number } | null;
  onSelect: (clusterId: string) => void;
}) {
  const { isRTL } = useLanguage();
  const html = buildLeafletHTML(clusters, isRTL, center ?? null);
  return (
    <WebView
      originWhitelist={["*"]}
      source={{ html }}
      style={{ flex: 1, backgroundColor: "transparent" }}
      onMessage={(e) => {
        try {
          const d = JSON.parse(e.nativeEvent.data);
          if (d?.cluster) onSelect(d.cluster);
        } catch {}
      }}
      javaScriptEnabled
      domStorageEnabled
      scrollEnabled={false}
      startInLoadingState
    />
  );
}
