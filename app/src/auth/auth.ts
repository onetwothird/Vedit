import { create } from "zustand";
import { isMock } from "../engine/client";

/* Accounts. The screens talk to one AuthProvider; the demo build (dev:mock, Playwright) uses a local stand-in,
   and the desktop app gets the real provider once the account service is chosen. Until then the real provider
   answers "not_configured" and the app stays usable without signing in. */

export type SocialProvider = "google" | "facebook";
export type Plan = "free" | "lifetime";

export interface Account {
  id: string;
  name: string;
  email: string;
  provider: SocialProvider | "email";
  plan: Plan;
}

export type AuthErrorCode = "not_configured" | "invalid_email" | "weak_password" | "wrong_password" | "email_taken" | "no_account";

export class AuthError extends Error {
  constructor(public code: AuthErrorCode) {
    super(code);
    this.name = "AuthError";
  }
}

export interface AuthProvider {
  restore(): Account | null;
  social(provider: SocialProvider): Promise<Account>;
  signIn(email: string, password: string): Promise<Account>;
  signUp(name: string, email: string, password: string): Promise<Account>;
  signOut(): Promise<void>;
}

const SESSION = "vedit.account";
const USERS = "vedit.demo.users";
export const SKIPPED = "vedit.signin.skipped";
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
export const MIN_PASSWORD = 8;

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}
function write(key: string, value: unknown): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // storage unavailable: the session lasts until the window closes
  }
}

/** Demo stand-in: accounts live in localStorage on this machine, nothing leaves it. Passwords are not stored. */
export function demoProvider(): AuthProvider {
  const users = () => read<Record<string, Account>>(USERS, {});
  const start = (a: Account) => {
    write(SESSION, a);
    return a;
  };
  const check = (email: string, password: string) => {
    if (!EMAIL.test(email.trim())) throw new AuthError("invalid_email");
    if (password.length < MIN_PASSWORD) throw new AuthError("weak_password");
  };
  return {
    restore: () => read<Account | null>(SESSION, null),
    async social(provider) {
      const email = provider === "google" ? "demo.creator@gmail.com" : "demo.creator@facebook.com";
      return start({ id: `demo-${provider}`, name: "Demo Creator", email, provider, plan: "free" });
    },
    async signIn(email, password) {
      check(email, password);
      const a = users()[email.trim().toLowerCase()];
      if (!a) throw new AuthError("no_account");
      return start(a);
    },
    async signUp(name, email, password) {
      check(email, password);
      const key = email.trim().toLowerCase();
      const all = users();
      if (all[key]) throw new AuthError("email_taken");
      const a: Account = { id: `demo-${Date.now()}`, name: name.trim() || key.split("@")[0], email: key, provider: "email", plan: "free" };
      write(USERS, { ...all, [key]: a });
      return start(a);
    },
    async signOut() {
      write(SESSION, null);
    },
  };
}

/** The desktop app's provider until an account service is connected. */
export function unconfiguredProvider(): AuthProvider {
  const no = async (): Promise<never> => {
    throw new AuthError("not_configured");
  };
  return { restore: () => null, social: no, signIn: no, signUp: no, signOut: async () => undefined };
}

interface AuthState {
  account: Account | null;
  busy: boolean;
  error: AuthErrorCode | null;
  run(task: (p: AuthProvider) => Promise<Account>): Promise<boolean>;
  signOut(): Promise<void>;
  clearError(): void;
}

export function createAuthStore(provider: AuthProvider) {
  return create<AuthState>((set) => ({
    account: provider.restore(),
    busy: false,
    error: null,
    async run(task) {
      set({ busy: true, error: null });
      try {
        const account = await task(provider);
        set({ account, busy: false });
        return true;
      } catch (e) {
        set({ busy: false, error: e instanceof AuthError ? e.code : "not_configured" });
        return false;
      }
    },
    async signOut() {
      await provider.signOut();
      set({ account: null });
    },
    clearError: () => set({ error: null }),
  }));
}

export const useAuth = createAuthStore(isMock() ? demoProvider() : unconfiguredProvider());

/** Whether the first-run sign-in screen should show: nobody signed in and "Skip for now" not chosen yet. */
export function shouldOfferSignIn(account: Account | null): boolean {
  return !account && read<boolean>(SKIPPED, false) !== true;
}

export function skipSignIn(): void {
  write(SKIPPED, true);
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase() || "V";
}
