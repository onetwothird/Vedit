// The app version and the updater (desktop app only; both plugins are loaded on demand).

import { isTauri } from "../engine/client";

export async function appVersion(): Promise<string> {
  if (isTauri()) {
    try {
      const { getVersion } = await import("@tauri-apps/api/app");
      return await getVersion();
    } catch {
      // fall through to the build's own version
    }
  }
  return __APP_VERSION__;
}

export interface FoundUpdate {
  version: string;
  /** Downloads and installs it. */
  install(): Promise<void>;
}

/** The newer version if there is one, else null. Throws when the update server cannot be reached. */
export async function checkForUpdate(): Promise<FoundUpdate | null> {
  const { check } = await import("@tauri-apps/plugin-updater");
  const update = await check();
  if (!update) return null;
  return { version: update.version, install: () => update.downloadAndInstall() };
}

export async function restartApp(): Promise<void> {
  const { relaunch } = await import("@tauri-apps/plugin-process");
  await relaunch();
}
