import { beforeEach, describe, expect, it, vi } from "vitest";
import { createAuthStore } from "./auth";
import { DESKTOP_WAIT_MS, displayName, linkParams, supabaseConfig, supabaseProvider } from "./supabase";
import { initials } from "./auth";

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
    expect((f.calls[0].init.headers as Record<string, string>).Authorization).toBeUndefined();
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

  it("tells a failed return trip apart from a cancelled one", async () => {
    history.replaceState(null, "", "/?error=server_error&error_code=unexpected_failure&error_description=Error+getting+user+email#/signin");
    const p = supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: fakeFetch().impl });
    await expect(p.resume!()).rejects.toMatchObject({ code: "social_failed" });
    expect(location.search).toBe("");
  });

  it("keeps the fullest name and the profile photo the provider shares", async () => {
    const fb = user({
      email: undefined,
      user_metadata: {
        full_name: "Angelito III", name: "Angelito III", nickname: "Angelito Decatoria III", slug: "Angelito Decatoria III",
        avatar_url: "https://platform-lookaside.fbsbx.com/p.jpg", picture: "https://platform-lookaside.fbsbx.com/p.jpg",
      },
      app_metadata: { provider: "facebook" },
    });
    const f = fakeFetch({ body: session({ user: fb }) });
    const a = await supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: f.impl }).signIn("ana@example.com", "longenough");
    expect(a).toMatchObject({ name: "Angelito Decatoria III", email: "", avatar: "https://platform-lookaside.fbsbx.com/p.jpg" });
  });

  it("never uses a non-https photo, and leaves email accounts without one", async () => {
    const f = fakeFetch({ body: session({ user: user({ user_metadata: { full_name: "Ana Reyes", avatar_url: "http://x/p.jpg" } }) }) });
    const a = await supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: f.impl }).signIn("ana@example.com", "longenough");
    expect(a.avatar).toBeUndefined();
  });

  it("is unavailable inside the desktop window without the browser hand-off", async () => {
    const p = supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: fakeFetch().impl, embedded: true });
    await expect(p.social("facebook")).rejects.toMatchObject({ code: "social_unavailable" });
  });
});

describe("desktop sign in through the browser", () => {
  function handoff(link: string | Promise<string>) {
    const opened: string[] = [];
    return {
      opened,
      desktop: {
        openExternal: vi.fn(async (u: string) => void opened.push(u)),
        nextAuthLink: vi.fn(() => Promise.resolve(link)),
      },
    };
  }

  it("opens the provider in the browser, back to vedit://auth, and exchanges the code it brings", async () => {
    const h = handoff("vedit://auth?code=c-42");
    const f = fakeFetch({ body: session() });
    const p = supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: f.impl, embedded: true, desktop: h.desktop });
    const a = await p.social("google");
    expect(a).toMatchObject({ id: "u1", provider: "google" });
    const page = new URL(h.opened[0]);
    expect(page.origin + page.pathname).toBe(`${URL_}/auth/v1/authorize`);
    expect(page.searchParams.get("provider")).toBe("google");
    expect(page.searchParams.get("redirect_to")).toBe("vedit://auth");
    const body = JSON.parse(String(f.calls[0].init.body));
    expect(f.calls[0].url).toBe(`${URL_}/auth/v1/token?grant_type=pkce`);
    expect(body.auth_code).toBe("c-42");
    expect(body.code_verifier).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(p.restore()).toMatchObject({ id: "u1" });
    expect(sessionStorage.length).toBe(0);
  });

  it("listens for the link before the browser opens", async () => {
    const order: string[] = [];
    const desktop = {
      openExternal: vi.fn(async () => void order.push("open")),
      nextAuthLink: vi.fn(() => { order.push("listen"); return Promise.resolve("vedit://auth?code=c"); }),
    };
    await supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: fakeFetch({ body: session() }).impl, embedded: true, desktop }).social("google");
    expect(order).toEqual(["listen", "open"]);
  });

  it("reports a cancel from the provider, in the query or the fragment", async () => {
    for (const link of ["vedit://auth?error=access_denied", "vedit://auth#error=access_denied&error_code=user_cancelled"]) {
      const p = supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: fakeFetch().impl, embedded: true, desktop: handoff(link).desktop });
      await expect(p.social("facebook")).rejects.toMatchObject({ code: "social_cancelled" });
    }
  });

  it("fails cleanly when the browser cannot be opened", async () => {
    const desktop = { openExternal: vi.fn(async () => { throw new Error("no browser"); }), nextAuthLink: vi.fn(() => new Promise<string>(() => undefined)) };
    const p = supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: fakeFetch().impl, embedded: true, desktop });
    await expect(p.social("google")).rejects.toMatchObject({ code: "social_failed" });
    expect(sessionStorage.length).toBe(0);
  });

  it("stops waiting when nobody comes back from the browser", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      let opened!: () => void;
      const wasOpened = new Promise<void>((r) => (opened = r));
      const desktop = { openExternal: vi.fn(async () => opened()), nextAuthLink: vi.fn(() => new Promise<string>(() => undefined)) };
      const p = supabaseProvider({ url: URL_, anonKey: KEY, fetchImpl: fakeFetch().impl, embedded: true, desktop });
      const done = expect(p.social("google")).rejects.toMatchObject({ code: "social_cancelled" });
      await wasOpened;
      for (let i = 0; i < 3; i++) await Promise.resolve(); // let social() reach its timer
      await vi.advanceTimersByTimeAsync(DESKTOP_WAIT_MS - 1000);
      expect(sessionStorage.length).toBe(1); // still waiting
      await vi.advanceTimersByTimeAsync(2000);
      await done;
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("linkParams", () => {
  it("reads the query and the fragment, the query first", () => {
    const q = linkParams("vedit://auth?code=a#code=b&error=x");
    expect([q.get("code"), q.get("error")]).toEqual(["a", "x"]);
    expect(linkParams("vedit://auth").toString()).toBe("");
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
  expect(supabaseConfig({ VITE_SUPABASE_URL: URL_, VITE_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_x" })).toEqual({ url: URL_, anonKey: "sb_publishable_x" });
});

describe("names", () => {
  it("prefers the longest name that starts like the given one", () => {
    expect(displayName({ full_name: "Angelito III", nickname: "Angelito Decatoria III" })).toBe("Angelito Decatoria III");
    expect(displayName({ full_name: "Ana Reyes", nickname: "anarey99" })).toBe("Ana Reyes");
    expect(displayName({ name: "  Ana   Reyes " })).toBe("Ana Reyes");
    expect(displayName({})).toBe("");
  });
  it("skips suffixes in initials", () => {
    expect(initials("Angelito Decatoria III")).toBe("AD");
    expect(initials("Juan Cruz Jr.")).toBe("JC");
    expect(initials("III")).toBe("I");
  });
});
