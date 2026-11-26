/* The signed-in user's Supabase access token, for the cloud engine. supabase.ts keeps the session in localStorage and
   refreshes it from resume(); this reads it through that public surface, so a token that is near expiry is
   refreshed first and the engine client always sends a live one. */

import { read } from "./core";
import { supabaseConfig, supabaseProvider } from "./supabase";

const SESSION = "vedit.session"; // the key supabase.ts saves the session under
const SKEW_MS = 60_000; // refresh a minute before the token runs out

interface Saved {
  access_token?: string;
  expires_at?: number; // unix seconds
  account?: { id?: unknown }; // the signed-in account, saved with its token
}

const saved = (): Saved | null => read<Saved | null>(SESSION, null);

const usable = (s: Saved | null): s is Saved & { access_token: string } =>
  !!s && typeof s.access_token === "string" && s.access_token !== "";

const fresh = (s: Saved): boolean => typeof s.expires_at === "number" && s.expires_at * 1000 > Date.now() + SKEW_MS;

let refreshing: Promise<void> | null = null;

function refresh(): Promise<void> {
  if (!refreshing) {
    const config = supabaseConfig();
    const p = (config ? supabaseProvider({ ...config, embedded: false }).resume!() : Promise.resolve(null))
      .then(() => undefined, () => undefined)
      .finally(() => {
        if (refreshing === p) refreshing = null;
      });
    refreshing = p;
  }
  return refreshing;
}

/** A current access token, refreshing it when it is about to expire; null when nobody is signed in. */
export async function getAccessToken(): Promise<string | null> {
  let s = saved();
  if (!usable(s)) return null;
  if (!fresh(s)) {
    await refresh(); // a failed refresh clears the session, so the re-read below then finds nothing
    s = saved();
    if (!usable(s)) return null;
  }
  return s.access_token;
}

const uidOf = (s: Saved | null): string | null =>
  usable(s) && typeof s.account?.id === "string" && s.account.id !== "" ? s.account.id : null;

/** The signed-in account's id as saved now (no refresh); null when nobody is signed in. */
export function signedInUserId(): string | null {
  return uidOf(saved());
}

/** A current access token and the id of the account it belongs to, read from the same saved session; null when
    nobody is signed in. */
export async function getSyncSession(): Promise<{ token: string; uid: string } | null> {
  const token = await getAccessToken();
  if (!token) return null;
  const s = saved();
  const uid = uidOf(s);
  return uid && s?.access_token === token ? { token, uid } : null;
}
