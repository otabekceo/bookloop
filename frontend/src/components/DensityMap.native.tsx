import { WebView } from "react-native-webview";

import { buildLeafletHTML } from "@/src/components/leaflet";

export default function DensityMap({
  clusters,
  onSelect,
}: {
  clusters: any[];
  onSelect: (neighborhood: string) => void;
}) {
  const html = buildLeafletHTML(clusters);
  return (
    <WebView
      originWhitelist={["*"]}
      source={{ html }}
      style={{ flex: 1, backgroundColor: "transparent" }}
      onMessage={(e) => {
        try {
          const d = JSON.parse(e.nativeEvent.data);
          if (d?.neighborhood) onSelect(d.neighborhood);
        } catch {}
      }}
      javaScriptEnabled
      domStorageEnabled
      scrollEnabled={false}
      startInLoadingState
    />
  );
}
