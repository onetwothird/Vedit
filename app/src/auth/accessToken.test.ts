import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const now = () => Math.floor(Date.now() / 1000);
const save = (access_token: string, expires_at: number) =>
  localStorage.setItem("vedit.session", JSON.stringify({
    access_token, refresh_token: "r1", expires_at,
    account: { id: "u", name: "U", email: "u@x.io", provider: "email", plan: "free" },
  }));

beforeEach(() => {
  localStorage.clear();
  vi.stubEnv("VITE_SUPABASE_URL", "https://proj.supabase.co");
  vi.stubEnv("VITE_SUPABASE_PUBLISHABLE_KEY", "sb_publishable_x");
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("getAccessToken", () => {
  it("is null with no session", async () => {
    const { getAccessToken } = await import("./accessToken");
    expect(await getAccessToken()).toBeNull();
  });

  it("returns a token that is not near expiry without any network call", async () => {
    save("live", now() + 3600);
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    const { getAccessToken } = await import("./accessToken");
    expect(await getAccessToken()).toBe("live");
    expect(f).not.toHaveBeenCalled();
  });

  it("refreshes a token that is about to expire", async () => {
    save("old", now() + 10);
    const f = vi.fn(async () => new Response(JSON.stringify({
      access_token: "new", refresh_token: "r2", expires_in: 3600,
      user: { id: "u", email: "u@x.io" },
    }), { status: 200 }));
    vi.stubGlobal("fetch", f);
    const { getAccessToken } = await import("./accessToken");
    expect(await getAccessToken()).toBe("new");
    expect(f).toHaveBeenCalledTimes(1);
  });

  it("is null when the refresh is refused", async () => {
    save("old", now() - 5);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: "invalid_grant" }), { status: 400 })));
    const { getAccessToken } = await import("./accessToken");
    expect(await getAccessToken()).toBeNull();
  });
});

describe("getSyncSession and signedInUserId", () => {
  it("give the token with the id of the account it belongs to, from the same saved session", async () => {
    const { getSyncSession, signedInUserId } = await import("./accessToken");
    expect(await getSyncSession()).toBeNull();
    expect(signedInUserId()).toBeNull();
    save("live", now() + 3600);
    expect(await getSyncSession()).toEqual({ token: "live", uid: "u" });
    expect(signedInUserId()).toBe("u");
    localStorage.setItem("vedit.session", JSON.stringify({ access_token: "x", expires_at: now() + 3600 }));
    expect(await getSyncSession()).toBeNull(); // no account id: not a session sync can act as
  });
});
