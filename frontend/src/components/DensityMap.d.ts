// Types for the platform-specific DensityMap.native.tsx / DensityMap.web.tsx. Metro picks the right
// file at build time; TypeScript does not know platform suffixes, so it reads this declaration.
import type { JSX } from "react";

export default function DensityMap(props: {
  clusters: any[];
  /** The viewer's own location; the map opens centered there. */
  center?: { lat: number; lng: number } | null;
  onSelect: (clusterId: string) => void;
}): JSX.Element;
