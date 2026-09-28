import React, { useEffect } from "react";

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
  useEffect(() => {
    const handler = (e: MessageEvent) => {
      try {
        const d = typeof e.data === "string" ? JSON.parse(e.data) : e.data;
        if (d?.cluster) onSelect(d.cluster);
      } catch {}
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [onSelect]);

  return React.createElement("iframe", {
    srcDoc: html,
    title: "map",
    style: { border: "none", width: "100%", height: "100%", display: "block" },
  });
}
