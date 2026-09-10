import React, { useEffect } from "react";

import { buildLeafletHTML } from "@/src/components/leaflet";

export default function DensityMap({
  clusters,
  onSelect,
}: {
  clusters: any[];
  onSelect: (neighborhood: string) => void;
}) {
  const html = buildLeafletHTML(clusters);
  useEffect(() => {
    const handler = (e: MessageEvent) => {
      try {
        const d = typeof e.data === "string" ? JSON.parse(e.data) : e.data;
        if (d?.neighborhood) onSelect(d.neighborhood);
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
