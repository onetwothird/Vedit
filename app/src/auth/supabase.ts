import { AuthError, checkCredentials, read, write, type Account, type AuthErrorCode, type AuthProvider, type SocialProvider } from "./core";

/* Supabase accounts, spoken to over Supabase Auth's HTTP API with fetch (no SDK). Email and password, plus
   Google and Facebook through Supabase's PKCE redirect. The session (tokens + account) lives in localStorage;
   the password never does. The plan comes from app_metadata.plan, which only the server can set. */

export interface SupabaseConfig {
  url: string;
  anonKey: string;
}

export interface SupabaseOptions extends SupabaseConfig {
  fetchImpl?: typeof fetch;
  /** Sends the window to the provider's sign-in page. */
  open?: (url: string) => void;
  /** Inside the desktop window: Google refuses sign in from embedded web views, so social sign in waits for the
      system-browser hand-off that ships with the installer. */
  embedded?: boolean;
}

interface Session {
  access_token: string;
  refresh_token: string;
  expires_at: number; // unix seconds
  account: Account;
}

interface SupabaseUser {
  id: string;
  email?: string;
  user_metadata?: {
    full_name?: string;
    name?: string;
    nickname?: string;
    slug?: string;
    avatar_url?: string;
    picture?: string;
  };
  app_metadata?: { plan?: string; provider?: string };
}

interface TokenResponse {
  access_token?: string;
  refresh_token?: string;
  expires_in?: number;
  expires_at?: number;
  user?: SupabaseUser;
}

const SESSION = "vedit.session";
const PKCE = "vedit.pkce";

/** The project address and publishable key baked into the build, or null when this build has none. */
export function supabaseConfig(env: Record<string, unknown> = import.meta.env): SupabaseConfig | null {
  const url = typeof env.VITE_SUPABASE_URL === "string" ? env.VITE_SUPABASE_URL.trim().replace(/\/+$/, "") : "";
  const raw = env.VITE_SUPABASE_PUBLISHABLE_KEY ?? env.VITE_SUPABASE_ANON_KEY; // publishable (sb_publishable_…) or a legacy anon key
  const anonKey = typeof raw === "string" ? raw.trim() : "";
  return url && anonKey ? { url, anonKey } : null;
}

function errorCode(status: number, body: Record<string, unknown>): AuthErrorCode {
  const code = String(body.error_code ?? body.code ?? body.error ?? "");
  const text = String(body.msg ?? body.error_description ?? body.message ?? "").toLowerCase();
  if (code === "invalid_credentials" || text.includes("invalid login")) return "wrong_password";
  if (code === "user_already_exists" || code === "email_exists" || text.includes("already registered")) return "email_taken";
  if (code === "email_not_confirmed" || text.includes("not confirmed")) return "check_email";
  if (code === "weak_password") return "weak_password";
  if (code === "email_address_invalid" || code === "validation_failed") return "invalid_email";
  if (status === 429 || code.startsWith("over_")) return "rate_limited";
  if (code === "provider_disabled" || code === "email_provider_disabled" || code === "signup_disabled") return "not_configured";
  return "unknown";
}

/** The fullest name the provider shares. Facebook's `name` can be short ("Angelito III") while `nickname` or `slug`
    carries the whole one ("Angelito Decatoria III"); take the longest candidate that starts like the given name. */
export function displayName(meta: { full_name?: string; name?: string; nickname?: string; slug?: string }): string {
  const clean = (v?: string) => (typeof v === "string" ? v.trim().replace(/\s+/g, " ") : "");
  const given = clean(meta.full_name) || clean(meta.name);
  const first = given.split(" ")[0]?.toLowerCase();
  const pool = [given, clean(meta.name), clean(meta.nickname), clean(meta.slug)].filter(
    (n) => n && (!first || n.toLowerCase().split(" ")[0] === first),
  );
  return pool.reduce((best, n) => (n.split(" ").length > best.split(" ").length ? n : best), given);
}

function base64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function pkcePair(): Promise<{ verifier: string; challenge: string }> {
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)));
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return { verifier, challenge: base64url(new Uint8Array(digest)) };
}

export function supabaseProvider(opts: SupabaseOptions): AuthProvider {
  const doFetch: typeof fetch = opts.fetchImpl ?? ((i, init) => globalThis.fetch(i, init));
  const open = opts.open ?? ((u: string) => window.location.assign(u));
  const embedded = opts.embedded ?? (typeof window !== "undefined" && "__TAURI_INTERNALS__" in window);

  async function call(path: string, body: unknown, token?: string): Promise<TokenResponse> {
    let res: Response;
    try {
      res = await doFetch(`${opts.url}/auth/v1${path}`, {
        method: "POST",
        // the publishable key goes in apikey only; Authorization carries a user's own token, never the project key
        headers: { apikey: opts.anonKey, "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify(body),
      });
    } catch {
      throw new AuthError("network");
    }
    let json: Record<string, unknown> = {};
    try {
      const text = await res.text();
      if (text) json = JSON.parse(text);
    } catch {
      // empty or non-JSON body: fall through on the status alone
    }
    if (!res.ok) throw new AuthError(errorCode(res.status, json));
    return json as unknown as TokenResponse;
  }

  function toAccount(u: SupabaseUser, provider?: Account["provider"]): Account {
    const email = u.email ?? "";
    const meta = u.user_metadata ?? {};
    const via = provider ?? (u.app_metadata?.provider === "google" || u.app_metadata?.provider === "facebook" ? u.app_metadata.provider : "email");
    const photo = [meta.avatar_url, meta.picture].find((p) => typeof p === "string" && p.startsWith("https://"));
    return {
      id: u.id,
      name: displayName(meta) || email.split("@")[0] || "vedit",
      email,
      provider: via,
      plan: u.app_metadata?.plan === "lifetime" ? "lifetime" : "free",
      ...(photo ? { avatar: photo } : {}),
    };
  }

  function start(t: TokenResponse, provider?: Account["provider"]): Account {
    if (!t.access_token || !t.refresh_token || !t.user) throw new AuthError("check_email");
    const account = toAccount(t.user, provider);
    const expires_at = t.expires_at ?? Math.floor(Date.now() / 1000) + (t.expires_in ?? 3600);
    write(SESSION, { access_token: t.access_token, refresh_token: t.refresh_token, expires_at, account } satisfies Session);
    return account;
  }

  const saved = () => read<Session | null>(SESSION, null);

  return {
    restore: () => saved()?.account ?? null,

    async resume() {
      const here = new URL(window.location.href);
      const code = here.searchParams.get("code");
      const failed = here.searchParams.get("error");
      if (code || failed) {
        // the provider's return trip: take the code out of the address before anything else
        window.history.replaceState(null, "", window.location.pathname + window.location.hash);
        if (failed) {
          const why = `${failed} ${here.searchParams.get("error_code") ?? ""}`;
          throw new AuthError(/access_denied|user_cancel/.test(why) ? "social_cancelled" : "social_failed");
        }
        const verifier = sessionStorage.getItem(PKCE);
        sessionStorage.removeItem(PKCE);
        if (!verifier) throw new AuthError("unknown");
        return start(await call("/token?grant_type=pkce", { auth_code: code, code_verifier: verifier }));
      }
      const s = saved();
      if (!s) return null;
      if (s.expires_at * 1000 > Date.now() + 60_000) return s.account;
      try {
        return start(await call("/token?grant_type=refresh_token", { refresh_token: s.refresh_token }), s.account.provider);
      } catch (e) {
        if (e instanceof AuthError && e.code === "network") return s.account; // offline: keep working, retry next start
        write(SESSION, null);
        return null;
      }
    },

    async social(provider: SocialProvider) {
      if (embedded) throw new AuthError("social_unavailable");
      const { verifier, challenge } = await pkcePair();
      try {
        sessionStorage.setItem(PKCE, verifier);
      } catch {
        throw new AuthError("unknown");
      }
      const q = new URLSearchParams({
        provider,
        redirect_to: window.location.origin + window.location.pathname,
        code_challenge: challenge,
        code_challenge_method: "s256",
      });
      open(`${opts.url}/auth/v1/authorize?${q}`);
      return new Promise<Account>(() => undefined); // the window is leaving for the provider's page
    },

    async signIn(email, password) {
      checkCredentials(email, password);
      return start(await call("/token?grant_type=password", { email: email.trim().toLowerCase(), password }), "email");
    },

    async signUp(name, email, password) {
      checkCredentials(email, password);
      const t = await call("/signup", { email: email.trim().toLowerCase(), password, data: { full_name: name.trim() } });
      return start(t, "email"); // no session yet means Supabase sent a confirmation email
    },

    async signOut() {
      const s = saved();
      write(SESSION, null);
      if (s) await call("/logout", {}, s.access_token).catch(() => undefined);
    },
  };
}
