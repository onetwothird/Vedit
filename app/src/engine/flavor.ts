// Which build this is. Kept apart from client.ts so modules that only need the flavor do not pull in the engine.

export const isTauri = (): boolean => typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/** The website build: the engine is the cloud API, signed with the user's Supabase access token. */
export const isCloud = (): boolean => !isTauri() && import.meta.env.VITE_ENGINE === "cloud";
