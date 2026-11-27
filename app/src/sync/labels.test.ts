import { describe, expect, it } from "vitest";
import type { SyncState } from "../engine/types";
import { projectSyncLabel, sizeText } from "./labels";

const row = (over: Partial<SyncState> = {}): SyncState => ({
  id: "p1", title: "Talk", cloud_id: null, cloud_rev: 0, cloud_hash: null, cloud_owner: null, hash: "h1", web_video: false,
  has_source: true, source_name: "talk.mp4", size: 10, content_type: "video/mp4", ...over,
});
const on = { available: true, active: true, states: [] as SyncState[], cloudIds: [] as string[], busy: [] as string[] };

describe("projectSyncLabel", () => {
  it("shows nothing where sync does not exist (the website, a build without a cloud)", () => {
    expect(projectSyncLabel("p1", {}, { ...on, available: false })).toBeNull();
  });

  it("signed out or switched off: on this computer, not synced", () => {
    const linked = { states: [row({ cloud_id: "c1", cloud_hash: "h1" })], cloudIds: ["c1"] };
    expect(projectSyncLabel("p1", { cloud_id: "c1" }, { ...on, ...linked, active: false }))
      .toEqual({ where: "local", status: "signedout" });
  });

  it("a project not in the cloud yet is on this computer", () => {
    expect(projectSyncLabel("p1", {}, { ...on, states: [row()] })).toEqual({ where: "local", status: "syncing" });
  });

  it("linked and listed by the cloud is in both places; synced when the hash matches the last synced one", () => {
    const states = [row({ cloud_id: "c1", cloud_hash: "h1" })];
    expect(projectSyncLabel("p1", {}, { ...on, states, cloudIds: ["c1"] })).toEqual({ where: "both", status: "synced" });
  });

  it("a push or pull in flight is syncing", () => {
    const states = [row({ cloud_id: "c1", cloud_hash: "h1" })];
    expect(projectSyncLabel("p1", {}, { ...on, states, cloudIds: ["c1"], busy: ["p1"] })).toEqual({ where: "both", status: "syncing" });
  });

  it("a link the cloud no longer lists is only on this computer", () => {
    const states = [row({ cloud_id: "gone", cloud_hash: "h1" })];
    expect(projectSyncLabel("p1", {}, { ...on, states, cloudIds: ["c1"] })?.where).toBe("local");
  });

  it("before the first cycle: the project's own link decides, with no status yet", () => {
    expect(projectSyncLabel("p1", { cloud_id: "c1" }, on)).toEqual({ where: "local", status: null });
  });

  it("a project that cannot be exported has no status", () => {
    expect(projectSyncLabel("p1", {}, { ...on, states: [row({ hash: null })] })).toEqual({ where: "local", status: null });
  });
});

describe("sizeText", () => {
  it("megabytes under a gigabyte, one decimal of gigabytes above", () => {
    expect(sizeText(120 * 1024 ** 2)).toBe("120 MB");
    expect(sizeText(0.4 * 1024 ** 2)).toBe("1 MB");
    expect(sizeText(1.25 * 1024 ** 3)).toBe("1.3 GB");
    expect(sizeText(2 * 1024 ** 3)).toBe("2 GB");
    expect(sizeText(0)).toBe("0 MB");
  });
});
