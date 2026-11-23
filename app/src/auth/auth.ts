import { create } from "zustand";
import { isMock } from "../engine/client";
import { AuthError, checkCredentials, read, write, type Account, type AuthErrorCode, type AuthProvider } from "./core";
import { supabaseConfig, supabaseProvider } from "./supabase";

export * from "./core";

/* Accounts. The screens talk to one AuthProvider: the demo build (dev:mock, Playwright) uses a local stand-in,
   a build with a Supabase project (VITE_SUPABASE_URL + VITE_SUPABASE_ANON_KEY) uses Supabase, and a build with
   neither answers "not_configured" while the app stays usable without signing in. */

const SESSION = "vedit.account";
const USERS = "vedit.demo.users";
export const SKIPPED = "vedit.signin.skipped";

/** Demo stand-in: accounts live in localStorage on this machine, nothing leaves it. Passwords are not stored. */
export function demoProvider(): AuthProvider {
  const users = () => read<Record<string, Account>>(USERS, {});
  const start = (a: Account) => {
    write(SESSION, a);
    return a;
  };
  return {
    restore: () => read<Account | null>(SESSION, null),
    async social(provider) {
      const email = provider === "google" ? "demo.creator@gmail.com" : "demo.creator@facebook.com";
      return start({ id: `demo-${provider}`, name: "Demo Creator", email, provider, plan: "free" });
    },
    async signIn(email, password) {
      checkCredentials(email, password);
      const a = users()[email.trim().toLowerCase()];
      if (!a) throw new AuthError("no_account");
      return start(a);
    },
    async signUp(name, email, password) {
      checkCredentials(email, password);
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
  const store = create<AuthState>((set) => ({
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
        set({ busy: false, error: e instanceof AuthError ? e.code : "unknown" });
        return false;
      }
    },
    async signOut() {
      await provider.signOut();
      set({ account: null });
    },
    clearError: () => set({ error: null }),
  }));
  if (provider.resume) {
    store.setState({ busy: true });
    provider.resume().then(
      (account) => store.setState({ account, busy: false }),
      (e) => store.setState({ busy: false, error: e instanceof AuthError ? e.code : "unknown" }),
    );
  }
  return store;
}

function pickProvider(): AuthProvider {
  if (isMock()) return demoProvider();
  const config = supabaseConfig();
  return config ? supabaseProvider(config) : unconfiguredProvider();
}

export const useAuth = createAuthStore(pickProvider());

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
