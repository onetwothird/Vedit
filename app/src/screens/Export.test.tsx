// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Job, Project } from "../engine/types";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const project: Project = {
  id: "p1", type: "clip", source: "C:/v/talk.mp4", language: "en", platform: "reels", created: "2026-11-22T00:00:00Z",
  clips: [{ highlight: { id: "h01", start: 0, end: 30, w0: 0, w1: 1, score: 90, title: "Clip one", features: {} }, kept: true, edl_file: "x" }],
  settings: {}, last_job: null,
};
const exportJob = (id: string, state: Job["state"]): Job => ({
  id, project_id: "p1", kind: "export", state, stage: "render 1/1", pct: 0, message: "", error: null, result: null,
});

const fake = vi.hoisted(() => ({ getProject: vi.fn(), activeJobs: vi.fn(), cancelJob: vi.fn(), exportClips: vi.fn(), getJob: vi.fn() }));
vi.mock("../engine/client", () => ({
  engine: fake, isTauri: () => false, isMock: () => true, fetchEngineInfo: vi.fn(), onJobResult: () => () => undefined,
}));

import { jobStore } from "../engine/events";
import Export from "./Export";

let host: HTMLElement;
let root: Root;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const button = (re: RegExp) => [...host.querySelectorAll("button")].find((b) => re.test(b.textContent ?? ""))!;

beforeEach(async () => {
  Object.values(fake).forEach((f) => f.mockReset());
  fake.getProject.mockResolvedValue(project);
  fake.cancelJob.mockResolvedValue({});
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={["/p/p1/export"]}>
        <Routes><Route path="/p/:id/export" element={<Export />} /></Routes>
      </MemoryRouter>,
    );
  });
  await flush();
});
afterEach(() => {
  for (const j of jobStore.getJobs().values()) jobStore.upsert({ ...j, state: "cancelled" }); // the store outlives a test
  act(() => root.unmount());
  host.remove();
});

describe("Export after a reload", () => {
  it("waits for queued jobs the live events never delivered, and Cancel stops all of them", async () => {
    // the job store is empty (fresh page); the engine still has two exports of this project queued
    fake.activeJobs.mockResolvedValue([exportJob("a", "running"), exportJob("b", "queued")]);
    // remount to run the load effect with the engine now reporting the jobs
    act(() => root.unmount());
    root = createRoot(host);
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={["/p/p1/export"]}>
          <Routes><Route path="/p/:id/export" element={<Export />} /></Routes>
        </MemoryRouter>,
      );
    });
    await flush();
    expect(host.textContent).toContain("An export of this video is still running.");
    expect((button(/^Export \d+ clips?$/) as HTMLButtonElement).disabled).toBe(true);

    await act(async () => { button(/Cancel export/).click(); });
    await flush();
    expect(fake.cancelJob.mock.calls.map((c) => c[0]).sort()).toEqual(["a", "b"]);
  });

  it("cancels a job that only the engine knew about when Cancel is pressed", async () => {
    fake.activeJobs.mockResolvedValueOnce([exportJob("c", "running")]); // what the load saw
    act(() => root.unmount());
    root = createRoot(host);
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={["/p/p1/export"]}>
          <Routes><Route path="/p/:id/export" element={<Export />} /></Routes>
        </MemoryRouter>,
      );
    });
    await flush();
    fake.activeJobs.mockResolvedValue([exportJob("c", "running"), exportJob("late", "queued")]); // queued since
    await act(async () => { button(/Cancel export/).click(); });
    await flush();
    expect(fake.cancelJob.mock.calls.map((c) => c[0]).sort()).toEqual(["c", "late"]);
  });
});
