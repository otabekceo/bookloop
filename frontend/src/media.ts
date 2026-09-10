import { Platform, Linking } from "react-native";
import * as ImagePicker from "expo-image-picker";

import { getToken } from "@/src/api";

const BASE = process.env.EXPO_PUBLIC_BACKEND_URL as string;

export type PickResult = { uri: string } | null;

// Ensure permission for library or camera. Returns true if granted.
async function ensurePermission(kind: "library" | "camera", onDenied: (blocked: boolean) => void): Promise<boolean> {
  const get = kind === "camera" ? ImagePicker.getCameraPermissionsAsync : ImagePicker.getMediaLibraryPermissionsAsync;
  const req = kind === "camera" ? ImagePicker.requestCameraPermissionsAsync : ImagePicker.requestMediaLibraryPermissionsAsync;
  const perm = await get();
  let status = perm.status;
  let canAskAgain = perm.canAskAgain;
  if (status !== "granted") {
    if (canAskAgain) {
      const r = await req();
      status = r.status;
      canAskAgain = r.canAskAgain;
    }
  }
  if (status !== "granted") {
    onDenied(!canAskAgain);
    return false;
  }
  return true;
}

export async function pickImage(
  source: "library" | "camera",
  onDenied: (blocked: boolean) => void,
): Promise<PickResult> {
  const ok = await ensurePermission(source, onDenied);
  if (!ok) return null;
  const opts: ImagePicker.ImagePickerOptions = {
    mediaTypes: ["images"],
    allowsEditing: true,
    quality: 0.7,
  };
  const result =
    source === "camera"
      ? await ImagePicker.launchCameraAsync(opts)
      : await ImagePicker.launchImageLibraryAsync(opts);
  if (result.canceled || !result.assets?.[0]) return null;
  return { uri: result.assets[0].uri };
}

export function openSettings() {
  Linking.openSettings();
}

// Upload with progress via XHR (works on native + web, reports real errors).
export function uploadWithProgress(
  uri: string,
  onProgress?: (fraction: number) => void,
): Promise<{ path: string; url: string }> {
  return new Promise(async (resolve, reject) => {
    try {
      const token = await getToken();
      const name = `photo_${Date.now()}.jpg`;
      const form = new FormData();
      if (Platform.OS === "web") {
        const blob = await (await fetch(uri)).blob();
        form.append("file", blob, name);
      } else {
        form.append("file", { uri, name, type: "image/jpeg" } as any);
      }
      const xhr = new XMLHttpRequest();
      xhr.open("POST", BASE + "/api/upload");
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      if (xhr.upload) {
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
        };
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText));
          } catch {
            reject(new Error("Bad server response"));
          }
        } else {
          let detail = `Upload failed (${xhr.status})`;
          try {
            detail = JSON.parse(xhr.responseText).detail || detail;
          } catch {}
          reject(new Error(detail));
        }
      };
      xhr.onerror = () => reject(new Error("Network error during upload"));
      xhr.send(form);
    } catch (e: any) {
      reject(new Error(e?.message || "Upload failed"));
    }
  });
}
