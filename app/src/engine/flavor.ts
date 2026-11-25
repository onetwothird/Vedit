// Which build this is. Kept apart from client.ts so modules that only need the flavor do not pull in the engine.

export const isTauri = (): boolean => typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/** The website build: the engine is the cloud API, signed with the user's Supabase access token. */
export const isCloud = (): boolean => !isTauri() && import.meta.env.VITE_ENGINE === "cloud";

/** The website: the cloud engine, or the mock engine run as the website (`dev:mock:web`). Every screen gate for web
    behaviour (uploads instead of paths, download links instead of folders) uses this, never isCloud alone. */
export const isWeb = (): boolean => isCloud() || (!isTauri() && import.meta.env.VITE_FLAVOR === "web");
