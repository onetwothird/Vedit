import { beforeEach, describe, expect, it } from "vitest";
import { AuthError, createAuthStore, demoProvider, initials, shouldOfferSignIn, skipSignIn, unconfiguredProvider } from "./auth";

beforeEach(() => localStorage.clear());

describe("demo provider", () => {
  it("creates an account, restores the session, and refuses a taken email", async () => {
    const p = demoProvider();
    const a = await p.signUp("Ana Reyes", "Ana@Example.com", "test-pass-123");
    expect(a).toMatchObject({ name: "Ana Reyes", email: "ana@example.com", provider: "email", plan: "free" });
    expect(demoProvider().restore()).toEqual(a);
    await expect(p.signUp("Ana", "ana@example.com", "test-pass-123")).rejects.toMatchObject({ code: "email_taken" });
  });

  it("checks the email and password before anything else", async () => {
    const p = demoProvider();
    await expect(p.signIn("ana@", "test-pass-123")).rejects.toMatchObject({ code: "invalid_email" });
    await expect(p.signIn("ana@example.com", "short")).rejects.toMatchObject({ code: "weak_password" });
    await expect(p.signIn("ana@example.com", "test-pass-123")).rejects.toMatchObject({ code: "no_account" });
  });

  it("never writes the password to storage", async () => {
    await demoProvider().signUp("Ana", "ana@example.com", "test-pass-123");
    expect(JSON.stringify({ ...localStorage })).not.toContain("test-pass-123");
  });
});

describe("auth store", () => {
  it("signs in, keeps the error code on failure, and signs out", async () => {
    const use = createAuthStore(demoProvider());
    expect(await use.getState().run((p) => p.signIn("x@example.com", "test-pass-123"))).toBe(false);
    expect(use.getState().error).toBe("no_account");
    expect(await use.getState().run((p) => p.social("google"))).toBe(true);
    expect(use.getState()).toMatchObject({ error: null, busy: false, account: { provider: "google" } });
    await use.getState().signOut();
    expect(use.getState().account).toBeNull();
  });

  it("reports not_configured from the desktop build's provider", async () => {
    const use = createAuthStore(unconfiguredProvider());
    expect(await use.getState().run((p) => p.social("facebook"))).toBe(false);
    expect(use.getState().error).toBe("not_configured");
    expect(new AuthError("not_configured").code).toBe("not_configured");
  });
});

it("offers sign in once, until skipped or signed in", () => {
  expect(shouldOfferSignIn(null)).toBe(true);
  skipSignIn();
  expect(shouldOfferSignIn(null)).toBe(false);
  localStorage.clear();
  expect(shouldOfferSignIn({ id: "1", name: "A", email: "a@b.co", provider: "email", plan: "free" })).toBe(false);
});

it("makes initials from a name", () => {
  expect(initials("Ana Reyes")).toBe("AR");
  expect(initials("demo")).toBe("D");
  expect(initials("  ")).toBe("V");
});
