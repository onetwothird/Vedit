// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const session = vi.hoisted(() => ({ token: "tok-1", calls: 0 }));
vi.mock("../auth/accessToken", () => ({
  getAccessToken: async () => {
    session.calls++;
    return session.token;
  },
}));

let host: HTMLElement;
let root: Root;

beforeEach(() => {
  session.token = "tok-1";
  session.calls = 0;
  vi.stubEnv("VITE_ENGINE", "cloud");
  vi.stubEnv("VITE_ENGINE_URL", "https://api.vedit.example");
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllEnvs();
  vi.resetModules();
});

async function setup() {
  const client = await import("../engine/client");
  const { useMediaUrl } = await import("./useMediaUrl");
  const seen: { url: string | null; onError(): void }[] = [];
  function Probe() {
    const m = useMediaUrl(() => client.engineMediaUrl("/projects/p1/media"));
    seen.push(m);
    return <video src={m.url ?? undefined} onError={m.onError} />;
  }
  await client.fetchEngineInfo();
  act(() => root.render(<Probe />));
  return { client, seen, last: () => seen[seen.length - 1] };
}

describe("useMediaUrl", () => {
  it("builds the URL from the current token", async () => {
    const { last } = await setup();
    expect(last().url).toBe("https://api.vedit.example/projects/p1/media?token=tok-1");
  });

  it("re-derives the URL when the token changes", async () => {
    const { client, last } = await setup();
    session.token = "tok-2";
    await act(async () => {
      await client.fetchEngineInfo();
    });
    expect(last().url).toBe("https://api.vedit.example/projects/p1/media?token=tok-2");
  });

  it("on an error asks for a fresh token once and retries with it", async () => {
    const { last } = await setup();
    session.token = "tok-2";
    await act(async () => last().onError());
    expect(last().url).toBe("https://api.vedit.example/projects/p1/media?token=tok-2");
    // a second failure of that same URL does not ask again: the token is not the problem
    const asked = session.calls;
    session.token = "tok-3";
    await act(async () => last().onError());
    expect(session.calls).toBe(asked);
    expect(last().url).toBe("https://api.vedit.example/projects/p1/media?token=tok-2");
  });

  it("does nothing on the desktop build", async () => {
    vi.stubEnv("VITE_ENGINE", "");
    vi.stubEnv("VITE_ENGINE_URL", "http://127.0.0.1:8765");
    vi.stubEnv("VITE_ENGINE_TOKEN", "t0k");
    const { last } = await setup();
    expect(last().url).toBe("http://127.0.0.1:8765/projects/p1/media?token=t0k");
    session.token = "tok-9";
    await act(async () => last().onError());
    expect(last().url).toBe("http://127.0.0.1:8765/projects/p1/media?token=t0k");
  });
});
