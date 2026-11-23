/* What every account provider shares: the account shape, the error codes the sign-in screen knows how to word,
   the provider interface, and small storage helpers. */

export type SocialProvider = "google" | "facebook";
export type Plan = "free" | "lifetime";

export interface Account {
  id: string;
  name: string;
  email: string;
  provider: SocialProvider | "email";
  plan: Plan;
  /** The profile photo Google or Facebook shares (https only); absent for email accounts. */
  avatar?: string;
}

export type AuthErrorCode =
  | "not_configured"
  | "invalid_email"
  | "weak_password"
  | "wrong_password"
  | "email_taken"
  | "no_account"
  | "check_email"
  | "rate_limited"
  | "network"
  | "social_cancelled"
  | "social_failed"
  | "social_unavailable"
  | "unknown";

export class AuthError extends Error {
  constructor(public code: AuthErrorCode) {
    super(code);
    this.name = "AuthError";
  }
}

export interface AuthProvider {
  /** The account saved from the last session, read synchronously so the first paint knows who is signed in. */
  restore(): Account | null;
  /** Optional async start-up: finish a Google/Facebook return trip, refresh an expired session. */
  resume?(): Promise<Account | null>;
  social(provider: SocialProvider): Promise<Account>;
  signIn(email: string, password: string): Promise<Account>;
  signUp(name: string, email: string, password: string): Promise<Account>;
  signOut(): Promise<void>;
}

export const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
export const MIN_PASSWORD = 8;

/** Same checks for every provider, before anything leaves the machine. */
export function checkCredentials(email: string, password: string): void {
  if (!EMAIL.test(email.trim())) throw new AuthError("invalid_email");
  if (password.length < MIN_PASSWORD) throw new AuthError("weak_password");
}

export function read<T>(key: string, fallback: T, store: Storage = localStorage): T {
  try {
    const raw = store.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function write(key: string, value: unknown, store: Storage = localStorage): void {
  try {
    if (value === null) store.removeItem(key);
    else store.setItem(key, JSON.stringify(value));
  } catch {
    // storage unavailable: the session lasts until the window closes
  }
}
