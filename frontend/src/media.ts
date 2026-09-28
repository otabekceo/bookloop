import { Alert, Platform, Linking } from "react-native";
import * as ImagePicker from "expo-image-picker";

import { API_BASE as BASE, getToken } from "@/src/api";
import i18n from "@/src/i18n";
import { localizeApiError } from "@/src/i18n/apiErrors";

export type PickResult = { uri: string } | null;

// Camera permission, asked only when the user taps a camera action. Returns true if granted.
// The photo library needs no permission at all: launchImageLibraryAsync opens the system photo
// picker, which only hands the app the photo the user picks (a permission is needed on iOS 10 only).
async function ensureCameraPermission(onDenied: (blocked: boolean) => void): Promise<boolean> {
  const perm = await ImagePicker.getCameraPermissionsAsync();
  let status = perm.status;
  let canAskAgain = perm.canAskAgain;
  if (status !== "granted") {
    if (canAskAgain) {
      const r = await ImagePicker.requestCameraPermissionsAsync();
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
  if (source === "camera" && !(await ensureCameraPermission(onDenied))) return null;
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

type Translate = (key: string, options?: Record<string, unknown>) => string;

/** Explains a refused camera permission in the app's language and offers the useful next step:
 * try again while the system can still ask, otherwise open the system Settings. */
export function cameraDeniedAlert(t: Translate, blocked: boolean, retry: () => void) {
  Alert.alert(t("permissions.cameraTitle"), blocked ? t("permissions.cameraBlocked") : t("permissions.cameraDenied"), [
    { text: t("common.cancel"), style: "cancel" },
    blocked
      ? { text: t("location.openSettings"), onPress: openSettings }
      : { text: t("common.retry"), onPress: retry },
  ]);
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
            reject(new Error(i18n.t("errors.uploadFailed")));
          }
        } else {
          let detail: string | undefined;
          try {
            detail = JSON.parse(xhr.responseText).detail;
          } catch {}
          reject(new Error(localizeApiError(typeof detail === "string" ? detail : undefined, xhr.status)));
        }
      };
      xhr.onerror = () => reject(new Error(localizeApiError(undefined, 0)));
      xhr.send(form);
    } catch (e: any) {
      reject(new Error(e?.message || i18n.t("errors.uploadFailed")));
    }
  });
}
