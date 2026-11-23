import { beforeEach, describe, expect, it, vi } from "vitest";
import { createAuthStore } from "./auth";
import { supabaseConfig, supabaseProvider } from "./supabase";

const URL_ = "https://abcd.supabase.co";
const KEY = ["anon", "test", "key"].join("-");

type Call = { url: string; init: RequestInit };

function fakeFetch(...responses: Array<{ status?: number; body: unknown } | Error>) {
  const calls: Call[] = [];
  const impl = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), init: init ?? {} });
    const next = responses.shift();
    if (!next) throw new Error("unexpected request " + String(url));
    if (next instanceof Error) throw next;
    return new Response(JSON.stringify(next.body), { status: next.status ?? 200 });
  });
  return { impl: impl as unknown as typeof fetch, calls };
}

const user = (over: Record<string, unknown> = {}) => ({
  id: "u1",
  email: "ana@example.com",
  user_metadata: { full_name: "Ana Reyes" },
  app_metadata: {},
  ...over,
});
const session = (over: Record<string, unknown> = {}) => ({
  access_token: "at-1",
  refresh_token: "rt-1",
  expires_in: 3600,
  expires_at: Math.floor(Date.now() / 1000) + 3600,
  user: user(),
  ...over,
});

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  history.replaceState(null, "", "/");
});

describe("email", () => {
  it("signs in with the password grant, sends the anon key, and keeps the session but not the password", async () => {
    const f = fakeFetch({ body: session({ user: user({ app_metadata: { plan: "lifetime" } }) }) });
    const p = supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: f.impl });
    const a = await p.signIn(" Ana@Example.com ", "test-pass-123");
    expect(a).toEqual({ id: "u1", name: "Ana Reyes", email: "ana@example.com", provider: "email", plan: "lifetime" });
    expect(f.calls[0].url).toBe(`${URL_}/auth/v1/token?grant_type=password`);
    expect((f.calls[0].init.headers as Record<string, string>).apikey).toBe(KEY);
    expect(JSON.parse(String(f.calls[0].init.body))).toEqual({ email: "ana@example.com", password: "test-pass-123" });
    expect(JSON.stringify({ ...localStorage })).not.toContain("test-pass-123");
    expect(supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: f.impl }).restore()).toEqual(a);
  });

  it("checks the email and password locally before calling Supabase", async () => {
    const f = fakeFetch();
    const p = supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: f.impl });
    await expect(p.signIn("ana@", "test-pass-123")).rejects.toMatchObject({ code: "invalid_email" });
    await expect(p.signUp("Ana", "ana@example.com", "short")).rejects.toMatchObject({ code: "weak_password" });
    expect(f.calls).toHaveLength(0);
  });

  it("maps Supabase errors to the app's codes", async () => {
    const f = fakeFetch(
      { status: 400, body: { error_code: "invalid_credentials", msg: "Invalid login credentials" } },
      { status: 422, body: { error_code: "user_already_exists", msg: "User already registered" } },
      { status: 400, body: { error_code: "email_not_confirmed" } },
      { status: 429, body: { error_code: "over_request_rate_limit" } },
      new TypeError("Failed to fetch"),
    );
    const p = supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: f.impl });
    await expect(p.signIn("ana@example.com", "test-pass-123")).rejects.toMatchObject({ code: "wrong_password" });
    await expect(p.signUp("Ana", "ana@example.com", "test-pass-123")).rejects.toMatchObject({ code: "email_taken" });
    await expect(p.signIn("ana@example.com", "test-pass-123")).rejects.toMatchObject({ code: "check_email" });
    await expect(p.signIn("ana@example.com", "test-pass-123")).rejects.toMatchObject({ code: "rate_limited" });
    await expect(p.signIn("ana@example.com", "test-pass-123")).rejects.toMatchObject({ code: "network" });
  });

  it("asks the person to confirm their email when sign up returns no session", async () => {
    const f = fakeFetch({ body: user({ confirmation_sent_at: "2026-11-23T00:00:00Z" }) });
    const p = supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: f.impl });
    await expect(p.signUp("Ana Reyes", "ana@example.com", "test-pass-123")).rejects.toMatchObject({ code: "check_email" });
    expect(f.calls[0].url).toBe(`${URL_}/auth/v1/signup`);
    expect(JSON.parse(String(f.calls[0].init.body))).toMatchObject({ data: { full_name: "Ana Reyes" } });
    expect(p.restore()).toBeNull();
  });
});

describe("Google and Facebook", () => {
  it("opens the authorize page with a PKCE challenge, then exchanges the returned code", async () => {
    const opened: string[] = [];
    const f = fakeFetch({ body: session({ user: user({ app_metadata: { provider: "google" } }) }) });
    const p = supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: f.impl, open: (u) => opened.push(u) });
    void p.social("google");
    await vi.waitFor(() => expect(opened).toHaveLength(1));
    const auth = new URL(opened[0]);
    expect(auth.origin + auth.pathname).toBe(`${URL_}/auth/v1/authorize`);
    expect(auth.searchParams.get("provider")).toBe("google");
    expect(auth.searchParams.get("code_challenge_method")).toBe("s256");
    expect(auth.searchParams.get("code_challenge")).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(auth.searchParams.get("redirect_to")).toBe(`${location.origin}/`);

    history.replaceState(null, "", "/?code=abc123#/signin");
    const back = supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: f.impl });
    const a = await back.resume!();
    expect(a).toMatchObject({ id: "u1", provider: "google" });
    expect(f.calls[0].url).toBe(`${URL_}/auth/v1/token?grant_type=pkce`);
    const body = JSON.parse(String(f.calls[0].init.body));
    expect(body.auth_code).toBe("abc123");
    expect(body.code_verifier).toMatch(/^[A-Za-z0-9_-]{43,128}$/);
    expect(location.search).toBe("");
    expect(location.hash).toBe("#/signin");
  });

  it("reports a provider error that comes back in the address", async () => {
    history.replaceState(null, "", "/?error=access_denied&error_description=denied#/signin");
    const p = supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: fakeFetch().impl });
    await expect(p.resume!()).rejects.toMatchObject({ code: "social_cancelled" });
    expect(location.search).toBe("");
  });

  it("is unavailable inside the desktop window, where Google blocks embedded sign in", async () => {
    const p = supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: fakeFetch().impl, embedded: true });
    await expect(p.social("facebook")).rejects.toMatchObject({ code: "social_unavailable" });
  });
});

describe("session", () => {
  it("refreshes an expired session on start, and drops it when the refresh is refused", async () => {
    const f = fakeFetch(
      { body: session() },
      { body: session({ access_token: "at-2", refresh_token: "rt-2" }) },
      { status: 400, body: { error_code: "refresh_token_not_found" } },
    );
    const p = supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: f.impl });
    await p.signIn("ana@example.com", "test-pass-123");
    const stored = JSON.parse(localStorage.getItem("vedit.session")!);
    localStorage.setItem("vedit.session", JSON.stringify({ ...stored, expires_at: 1 }));

    expect(await p.resume!()).toMatchObject({ id: "u1" });
    expect(f.calls[1].url).toBe(`${URL_}/auth/v1/token?grant_type=refresh_token`);
    expect(JSON.parse(String(f.calls[1].init.body))).toEqual({ refresh_token: "rt-1" });

    const again = JSON.parse(localStorage.getItem("vedit.session")!);
    localStorage.setItem("vedit.session", JSON.stringify({ ...again, expires_at: 1 }));
    expect(await p.resume!()).toBeNull();
    expect(localStorage.getItem("vedit.session")).toBeNull();
  });

  it("signs out on the server and forgets the session even if the server call fails", async () => {
    const f = fakeFetch({ body: session() }, new TypeError("offline"));
    const p = supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: f.impl });
    await p.signIn("ana@example.com", "test-pass-123");
    await p.signOut();
    expect(f.calls[1].url).toBe(`${URL_}/auth/v1/logout`);
    expect((f.calls[1].init.headers as Record<string, string>).Authorization).toBe("Bearer at-1");
    expect(p.restore()).toBeNull();
  });

  it("the store picks up the account that resume finds", async () => {
    history.replaceState(null, "", "/?code=xyz");
    sessionStorage.setItem("vedit.pkce", "v".repeat(43));
    const f = fakeFetch({ body: session() });
    const use = createAuthStore(supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: f.impl }));
    await vi.waitFor(() => expect(use.getState().account).toMatchObject({ id: "u1" }));
    expect(use.getState().busy).toBe(false);
  });
});

it("reads the project address and anon key from the build, or reports none", () => {
  expect(supabaseConfig({})).toBeNull();
  expect(supabaseConfig({ VITE_SUPABASE_URL: URL_ + "/", VITE_SUPABASE_ANON_KEY: KEY })).toEqual({ url: URL_, anonKey: KEY });
});
