// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ClipView, Edl, Project, Transcript } from "../engine/types";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const WORDS = Array.from({ length: 6 }, (_, i) => ({ text: `w${i}`, start: i, end: i + 0.9 }));
const TR: Transcript = { words: WORDS, duration: 6 };
const PROJECT: Project = {
  id: "p1", type: "clip", source: "C:/v/talk.mp4", language: "en", platform: "reels", created: "2026-11-22T00:00:00Z",
  settings: {}, last_job: null,
  clips: [{ highlight: { id: "h1", start: 0, end: 5.9, w0: 0, w1: 5, score: 90, title: "Clip one", features: {} }, kept: true, edl_file: "e" }],
};
const view = (edl: Edl): ClipView => ({
  edl, kept_ranges: [[0, 5.9]],
  captions: { events: [], style: "pop", uppercase: true, speaker_colors: {} } as unknown as ClipView["captions"],
});

const fake = vi.hoisted(() => ({
  getProject: vi.fn(), transcript: vi.fn(), clip: vi.fn(), patchClip: vi.fn(), filmstrip: vi.fn(), setKept: vi.fn(),
}));
vi.mock("../engine/client", () => ({ engine: fake, isTauri: () => false, isMock: () => true }));

import { useClipStore } from "../edit/edlStore";
import Edit from "./Edit";

let host: HTMLElement;
let root: Root;
beforeEach(() => {
  vi.clearAllMocks();
  fake.getProject.mockResolvedValue(PROJECT);
  fake.transcript.mockResolvedValue(TR);
  fake.clip.mockResolvedValue(view({ clips: [{ src: "main", words: [0, 5] }], output: { aspect: "9:16" } }));
  fake.filmstrip.mockRejectedValue(new Error("none"));
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  // the store is a module singleton: point it at a clip no test uses, so the next test loads fresh
  await act(async () => useClipStore.getState().load("none", "none").catch(() => undefined));
});

async function mount() {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={["/p/p1/edit/h1"]}>
        <Routes>
          <Route path="/p/:id/edit/:hid" element={<Edit />} />
        </Routes>
      </MemoryRouter>,
    );
  });
  await settle();
}
const settle = () => act(async () => {
  for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
});
const alerts = () => [...host.querySelectorAll("[role=alert]")].map((e) => e.textContent ?? "");
const retry = () => [...host.querySelectorAll("button")].find((b) => b.textContent === "Try again");

describe("Edit load failures", () => {
  it("shows a transcript failure with Try again, which recovers", async () => {
    fake.transcript.mockRejectedValueOnce(new Error("socket closed"));
    await mount();
    expect(alerts().join(" ")).toContain("Couldn't load the transcript");
    expect(host.querySelectorAll(".tx [data-w]")).toHaveLength(0);
    await act(async () => retry()!.click());
    await settle();
    expect(alerts()).toEqual([]);
    expect(host.querySelectorAll(".tx [data-w]")).toHaveLength(6);
    expect(host.querySelector(".tl-content")).not.toBeNull();
  });

  it("reports a clip that fails to load as a load failure, not a failed save, and Try again recovers", async () => {
    fake.clip.mockRejectedValueOnce(new Error("engine down"));
    await mount();
    const text = alerts().join(" ");
    expect(text).toContain("Couldn't load this clip");
    expect(text).not.toContain("Couldn't save");
    await act(async () => retry()!.click());
    await settle();
    expect(alerts()).toEqual([]);
    expect(host.querySelectorAll(".tx [data-w]")).toHaveLength(6);
  });

  it("shows an edit that does not fit its transcript instead of hiding it", async () => {
    fake.clip.mockResolvedValue(view({ clips: [{ src: "main", words: [0, 40] }] }));
    await mount();
    expect(alerts().join(" ")).toContain("doesn't match its transcript");
  });
});
