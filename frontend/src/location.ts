import { useCallback, useRef, useState } from "react";
import { Linking } from "react-native";
import * as Location from "expo-location";
import { useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/src/api";
import { useAuth, type User } from "@/src/auth";

/**
 * Where the device-location flow stands:
 * - unknown      permission never asked (or not yet checked)
 * - locating     reading the position / saving it
 * - ready        the device position is saved on the backend
 * - denied       permission refused, but Android/iOS will still show the prompt again
 * - blocked      permission refused for good: only the system Settings can change it
 * - servicesOff  permission granted, but location services are switched off on the device
 * - error        no position could be read (timeout, no fix, network)
 */
export type LocationState = "unknown" | "locating" | "ready" | "denied" | "blocked" | "servicesOff" | "error";

// Everything whose contents depend on where the user is.
const LOCATION_QUERY_KEYS = ["people", "discoverBooks", "clusters", "wishlist", "bookDemand", "person", "book"];

const POSITION_TIMEOUT_MS = 15000;
const LAST_KNOWN_MAX_AGE_MS = 10 * 60 * 1000;

function permissionState(p: Location.LocationPermissionResponse): LocationState | null {
  if (p.status === "granted") return null;
  if (p.status === "undetermined") return "unknown";
  return p.canAskAgain ? "denied" : "blocked";
}

async function currentPosition(): Promise<Location.LocationObject | null> {
  const timeout = new Promise<null>((resolve) => setTimeout(() => resolve(null), POSITION_TIMEOUT_MS));
  try {
    const fix = await Promise.race([Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced }), timeout]);
    if (fix) return fix;
  } catch {
    // fall through to the last known position
  }
  try {
    return await Location.getLastKnownPositionAsync({ maxAge: LAST_KNOWN_MAX_AGE_MS });
  } catch {
    return null;
  }
}

/** Area names for the position, from the device's own geocoder. Neighborhood only when the geocoder
 * actually knows one (district); otherwise just the city — never an invented neighborhood. */
async function areaFor(latitude: number, longitude: number) {
  try {
    const [a] = await Location.reverseGeocodeAsync({ latitude, longitude });
    if (!a) return {};
    return {
      city: a.city || a.subregion || a.region || null,
      neighborhood: a.district || null,
      country: a.country || null,
    };
  } catch {
    return {}; // no geocoder (e.g. web, or no Play services): coordinates alone still give distances
  }
}

/**
 * Reads the device position and saves it (with its area names) on the backend.
 * `prompt` decides whether the system permission dialog may be shown: only true in direct response
 * to a location feature the user opened — never at app start.
 */
export async function syncDeviceLocation(prompt: boolean): Promise<{ state: LocationState; user?: User }> {
  let perm = await Location.getForegroundPermissionsAsync();
  if (perm.status !== "granted" && prompt && perm.canAskAgain) {
    perm = await Location.requestForegroundPermissionsAsync();
  }
  const refused = permissionState(perm);
  if (refused) return { state: refused };
  if (!(await Location.hasServicesEnabledAsync())) return { state: "servicesOff" };

  const fix = await currentPosition();
  if (!fix) return { state: "error" };
  const { latitude, longitude } = fix.coords;
  const area = await areaFor(latitude, longitude);
  const data = await apiFetch<{ user: User }>("/api/users/me/location", {
    method: "PUT",
    body: { lat: latitude, lng: longitude, ...area },
  });
  return { state: "ready", user: data.user };
}

export function userHasLocation(user: Pick<User, "lat" | "lng"> | null | undefined): boolean {
  return user?.lat != null && user?.lng != null;
}

/** Location flow for a screen: `request()` for a user action (may show the permission dialog),
 * `refreshSilently()` to update an already-granted location without ever prompting. */
export function useDeviceLocation() {
  const { user, setUser } = useAuth();
  const qc = useQueryClient();
  const [state, setState] = useState<LocationState>("unknown");
  const busy = useRef(false);

  const run = useCallback(
    async (prompt: boolean) => {
      if (busy.current) return;
      busy.current = true;
      setState("locating");
      try {
        const r = await syncDeviceLocation(prompt);
        setState(r.state);
        if (r.user) {
          setUser(r.user);
          qc.invalidateQueries({ predicate: (q) => LOCATION_QUERY_KEYS.includes(String(q.queryKey[0])) });
        }
      } catch {
        setState("error");
      } finally {
        busy.current = false;
      }
    },
    [qc, setUser],
  );

  const request = useCallback(() => run(true), [run]);
  const refreshSilently = useCallback(() => run(false), [run]);

  return { state, request, refreshSilently, openSettings: Linking.openSettings, hasLocation: userHasLocation(user) };
}
