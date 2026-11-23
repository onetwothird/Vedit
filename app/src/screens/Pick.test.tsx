// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Job, Project, ProjectClip } from "../engine/types";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const clip = (id: string, start: number, score: number, kept = true): ProjectClip => ({
  highlight: { id, start, end: start + 32, w0: 0, w1: 1, score, title: `Clip ${id}`, features: {} },
  kept,
  edl_file: `clip-${id}.edl.json`,
});
const project = (clips: ProjectClip[], last_job: Job | null = null): Project => ({
  id: "p1", type: "clip", source: "C:/v/talk.mp4", language: "en", platform: "reels",
  created: "2026-11-22T00:00:00Z", clips, settings: {}, last_job,
});
const job = (over: Partial<Job>): Job => ({
  id: "j1", project_id: "p1", kind: "analyze", state: "running", stage: "highlights", pct: 40, message: "", error: null,
  result: null, ...over,
});

const fake = vi.hoisted(() => ({
  getProject: vi.fn(), setKept: vi.fn(), analyze: vi.fn(), getJob: vi.fn(), filmstrip: vi.fn(),
  transcript: vi.fn(async () => { throw new Error("no transcript in these tests"); }),
}));
vi.mock("../engine/client", () => ({
  engine: fake, isTauri: () => false, isMock: () => true, fetchEngineInfo: vi.fn(), onJobResult: () => () => undefined,
}));
vi.mock("../lib/native", async (orig) => ({ ...(await orig<typeof import("../lib/native")>()), analyzeBody: async () => ({}) }));

import { jobStore } from "../engine/events";
import { EngineError } from "../engine/types";
import Pick from "./Pick";

function Probe() {
  const loc = useLocation();
  return <output data-testid="loc">{loc.pathname}</output>;
}

let host: HTMLElement;
let root: Root;
async function mount(state?: unknown) {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[{ pathname: "/p/p1/pick", state }]}>
        <Routes>
          <Route path="/p/:id/pick" element={<Pick />} />
          <Route path="*" element={<div />} />
        </Routes>
        <Probe />
      </MemoryRouter>,
    );
  });
  await act(async () => undefined);
}
const text = () => host.textContent ?? "";
const loc = () => host.querySelector("[data-testid=loc]")!.textContent;
const button = (name: string) => [...host.querySelectorAll("button")].find((b) => b.textContent?.includes(name))!;
const boxes = () => [...host.querySelectorAll<HTMLInputElement>("input[type=checkbox]")];

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  Object.values(fake).forEach((f) => f.mockReset());
  fake.filmstrip.mockRejectedValue(new EngineError(404, "no_filmstrip", "none"));
  fake.getJob.mockResolvedValue(null);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("Pick", () => {
  it("shows the counts and the clips best first", async () => {
    fake.getProject.mockResolvedValue(project([clip("a", 0, 50), clip("b", 40, 90, false), clip("c", 80, 70)]));
    await mount({ analyze: { clips: 3, new: 3, reason: null } });
    expect(text()).toContain("3 clips found, 2 kept");
    expect([...host.querySelectorAll(".clip-title")].map((e) => e.textContent)).toEqual(["Clip b", "Clip c", "Clip a"]);
  });

  it("shows the no-speech state and goes home from it", async () => {
    fake.getProject.mockResolvedValue(project([]));
    await mount({ analyze: { clips: 0, new: 0, reason: "no_speech" } });
    expect(text()).toContain("No speech found in this video. Vedit finds clips from what people say.");
    await act(async () => (host.querySelector("a.secondary[href='/']") as HTMLElement).click());
    expect(loc()).toBe("/");
  });

  it("falls back to the project's last job when the router state is gone", async () => {
    const last = job({ state: "done", result: { clips: 0, new: 0, reason: "no_speech" } });
    fake.getProject.mockResolvedValue(project([clip("a", 0, 50)], last));
    await mount();
    expect(text()).toContain("No speech found in this video.");
  });

  it("shows the empty state for zero clips even without a reason", async () => {
    fake.getProject.mockResolvedValue(project([]));
    await mount({ analyze: { clips: 0, new: 0, reason: null } });
    expect(text()).toContain("No speech found in this video.");
  });

  it("updates the count right away and calls setKept, then takes the returned project", async () => {
    fake.getProject.mockResolvedValue(project([clip("a", 0, 50), clip("b", 40, 90)]));
    let resolve!: (p: Project) => void;
    fake.setKept.mockReturnValue(new Promise<Project>((r) => (resolve = r)));
    await mount({ analyze: { clips: 2, new: 2 } });
    expect(text()).toContain("2 clips found, 2 kept");
    await act(async () => boxes()[0].click()); // b, the best clip
    expect(fake.setKept).toHaveBeenCalledWith("p1", "b", false);
    expect(text()).toContain("2 clips found, 1 kept"); // before the engine answered
    await act(async () => resolve(project([clip("a", 0, 50), clip("b", 40, 90, false)])));
    expect(text()).toContain("2 clips found, 1 kept");
  });

  it("reverts the checkbox and shows an error when setKept fails", async () => {
    fake.getProject.mockResolvedValue(project([clip("a", 0, 50)]));
    fake.setKept.mockRejectedValue(new EngineError(0, "network", "could not reach the engine"));
    await mount({ analyze: { clips: 1, new: 1 } });
    await act(async () => boxes()[0].click());
    expect(boxes()[0].checked).toBe(true);
    expect(text()).toContain("1 clip found, 1 kept");
    expect(host.querySelector("[role=alert]")!.textContent).toMatch(/couldn.t save/i);
  });

  it("opens the editor for the selected clip", async () => {
    fake.getProject.mockResolvedValue(project([clip("a", 0, 50), clip("b", 40, 90)]));
    await mount({ analyze: { clips: 2, new: 2 } });
    await act(async () => host.querySelectorAll<HTMLButtonElement>(".clip-main")[1].click()); // a
    await act(async () => button("Edit this clip").click());
    expect(loc()).toBe("/p/p1/edit/a");
  });

  it("selects the best clip first", async () => {
    fake.getProject.mockResolvedValue(project([clip("a", 0, 50), clip("b", 40, 90)]));
    await mount({ analyze: { clips: 2, new: 2 } });
    await act(async () => button("Edit this clip").click());
    expect(loc()).toBe("/p/p1/edit/b");
  });

  it("finds more clips with max_clips = count + 6, shows progress, then reloads", async () => {
    const two = project([clip("a", 0, 50), clip("b", 40, 90)]);
    const eight = project([clip("a", 0, 50), clip("b", 40, 90), ...["c", "d", "e", "f", "g", "h"].map((id, i) => clip(id, 100 + i, 40))]);
    fake.getProject.mockResolvedValueOnce(two).mockResolvedValue(eight);
    fake.analyze.mockResolvedValue(job({ id: "j-more", pct: 0, state: "queued" }));
    await mount({ analyze: { clips: 2, new: 2 } });
    await act(async () => button("Find more clips").click());
    expect(fake.analyze).toHaveBeenCalledWith("p1", { max_clips: 8 });
    await act(async () => jobStore.upsert(job({ id: "j-more", pct: 42 })));
    const more = button("Finding") as HTMLButtonElement;
    expect(more.textContent).toContain("42%");
    expect(more.disabled).toBe(true);
    await act(async () => jobStore.upsert(job({ id: "j-more", state: "done", pct: 100, result: { clips: 8, new: 6 } })));
    await act(async () => undefined);
    expect(text()).toContain("8 clips found");
    expect((button("Find more clips") as HTMLButtonElement).disabled).toBe(false);
  });

  it("never asks for more than 60 clips", async () => {
    const many = (n: number) => Array.from({ length: n }, (_, i) => clip(`x${i}`, i * 100, 50 + (i % 40)));
    fake.getProject.mockResolvedValue(project(many(57)));
    fake.analyze.mockResolvedValue(job({ id: "j-cap", pct: 0, state: "queued" }));
    await mount({ analyze: { clips: 57, new: 57 } });
    await act(async () => button("Find more clips").click());
    expect(fake.analyze).toHaveBeenCalledWith("p1", { max_clips: 60 });
  });

  it("disables Find more clips at 60 clips and says why", async () => {
    fake.getProject.mockResolvedValue(project(Array.from({ length: 60 }, (_, i) => clip(`x${i}`, i * 100, 50))));
    await mount({ analyze: { clips: 60, new: 60 } });
    expect((button("Find more clips") as HTMLButtonElement).disabled).toBe(true);
    expect(text()).toContain("You have the most clips Vedit can find (60).");
  });
});
