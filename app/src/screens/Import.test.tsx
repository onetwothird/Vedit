// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Job, Project } from "../engine/types";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const project = (last_job: Job | null): Project => ({
  id: "p1", type: "clip", source: "C:/v/talk.mp4", language: "en", platform: "reels",
  created: "2026-11-22T00:00:00Z", clips: [], settings: {}, last_job,
});
const job = (over: Partial<Job>): Job => ({
  id: "j1", project_id: "p1", kind: "analyze", state: "running", stage: "proxy", pct: 0, message: "", error: null,
  result: null, ...over,
});

const fake = vi.hoisted(() => ({ getProject: vi.fn(), drop: null as null | { onOver(o: boolean): void; onDrop(p: string[]): void } }));

vi.mock("../engine/client", () => ({
  engine: { getProject: fake.getProject, getJob: vi.fn().mockResolvedValue(null), cancelJob: vi.fn(), createProject: vi.fn(), analyze: vi.fn() },
  isTauri: () => false,
  isMock: () => true,
  fetchEngineInfo: vi.fn(),
  onJobResult: () => () => undefined,
}));
vi.mock("../lib/native", async (orig) => ({
  ...(await orig<typeof import("../lib/native")>()),
  listenNativeDrop: (h: { onOver(o: boolean): void; onDrop(p: string[]): void }) => {
    fake.drop = h;
    return () => undefined;
  },
  analyzeBody: async () => ({}),
}));

import { jobStore } from "../engine/events";
import Import from "./Import";

function Probe() {
  const loc = useLocation();
  return <output data-testid="loc">{loc.pathname}|{JSON.stringify(loc.state)}</output>;
}

let host: HTMLElement;
let root: Root;

async function mount(path: string) {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/p/:id/import" element={<Import />} />
          <Route path="*" element={<div />} />
        </Routes>
        <Probe />
      </MemoryRouter>,
    );
  });
  await act(async () => undefined);
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  sessionStorage.clear();
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("Import", () => {
  it("keeps the model row and its scaled percentages after leaving and coming back mid-run", async () => {
    // the model step ran while no Import screen was mounted
    act(() => jobStore.upsert(job({ id: "j-model", stage: "model", pct: 10 })));
    act(() => jobStore.upsert(job({ id: "j-model", stage: "analyze", pct: 50 })));
    fake.getProject.mockResolvedValue(project(job({ id: "j-model", stage: "analyze", pct: 50 })));
    await mount("/p/p1/import");
    expect(host.textContent).toContain("Downloading the language model");
    // analyze spans 0.3+0.07 .. 0.79 of the scaled table: (50% - 37%) / 42% = 31%
    expect(host.textContent).toContain("31%");
  });

  it("ignores a file dropped while an analysis is running", async () => {
    act(() => jobStore.upsert(job({ id: "j-run", stage: "analyze", pct: 40 })));
    fake.getProject.mockResolvedValue(project(job({ id: "j-run", stage: "analyze", pct: 40 })));
    await mount("/p/p1/import");
    await act(async () => fake.drop!.onDrop(["C:/other/new.mp4"]));
    expect(host.querySelector("[data-testid=loc]")!.textContent).toMatch(/^\/p\/p1\/import/);
    expect(host.querySelector("button.secondary")!.textContent).toBe("Cancel");
  });

  it("passes the analyze result to Pick clips in the navigation state", async () => {
    const result = { clips: 3, new: 3, reason: null };
    act(() => jobStore.upsert(job({ id: "j-done", stage: "framing", pct: 90 })));
    fake.getProject.mockResolvedValue(project(job({ id: "j-done", stage: "framing", pct: 90 })));
    await mount("/p/p1/import");
    await act(async () => jobStore.upsert(job({ id: "j-done", state: "done", pct: 100, result })));
    const out = host.querySelector("[data-testid=loc]")!.textContent!;
    expect(out.startsWith("/p/p1/pick|")).toBe(true);
    expect(JSON.parse(out.split("|")[1])).toEqual({ analyze: result });
  });
});
