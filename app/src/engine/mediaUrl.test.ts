import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("media URLs from the engine", () => {
  it("are null until the engine is reached, then carry the token in the query", async () => {
    vi.stubEnv("VITE_ENGINE", "");
    vi.stubEnv("VITE_ENGINE_URL", "http://127.0.0.1:8765");
    vi.stubEnv("VITE_ENGINE_TOKEN", "t0k");
    const { engineMediaUrl, fetchEngineInfo } = await import("./client");
    expect(engineMediaUrl("/projects/p1/media")).toBeNull();
    await fetchEngineInfo();
    expect(engineMediaUrl("/projects/p1/media")).toBe("http://127.0.0.1:8765/projects/p1/media?token=t0k");
    expect(engineMediaUrl("/file", { path: "C:/b roll/shot.png" })).toBe(
      "http://127.0.0.1:8765/file?path=C%3A%2Fb+roll%2Fshot.png&token=t0k",
    );
    const { previewSrc } = await import("../preview/media");
    expect(previewSrc({ id: "a/b" })).toBe("http://127.0.0.1:8765/projects/a%2Fb/media?token=t0k");
  });
});
