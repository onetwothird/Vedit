// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Usage } from "../engine/types";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const fake = vi.hoisted(() => ({ usage: vi.fn() }));
vi.mock("../engine/client", () => ({ engine: { usage: fake.usage } }));

import UsageMeter, { usageText } from "./UsageMeter";

const GB = 1024 ** 3;
const usage = (over: Partial<Usage> = {}): Usage => ({ seconds_used: 23 * 60 + 30, seconds_limit: 3600, storage_bytes: 1.2 * GB, storage_limit: 10 * GB, ...over });

let root: Root | undefined;
let host: HTMLElement | undefined;
afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  fake.usage.mockReset();
});

async function mount() {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root!.render(<UsageMeter />));
  return host;
}

describe("usageText", () => {
  it("words the month's minutes and the stored gigabytes", () => {
    expect(usageText(usage())).toEqual({ minutes: "23 of 60 minutes used this month", storage: "1.2 of 10 GB of storage used" });
  });
  it("shows no decimal for whole or empty amounts", () => {
    expect(usageText(usage({ seconds_used: 0, storage_bytes: 0 }))).toEqual({ minutes: "0 of 60 minutes used this month", storage: "0 of 10 GB of storage used" });
  });
});

describe("UsageMeter", () => {
  it("shows two named meters with the values", async () => {
    fake.usage.mockResolvedValue(usage());
    const el = await mount();
    const meters = el.querySelectorAll('[role="meter"]');
    expect(meters).toHaveLength(2);
    expect(meters[0].getAttribute("aria-label")).toBe("Analysis time this month");
    expect(meters[0].getAttribute("aria-valuenow")).toBe(String(23 * 60 + 30));
    expect(meters[1].getAttribute("aria-label")).toBe("Storage used");
    expect(el.textContent).toContain("23 of 60 minutes used this month");
    expect(el.textContent).toContain("1.2 of 10 GB");
  });

  it("says so when usage cannot be read", async () => {
    fake.usage.mockRejectedValue(new Error("down"));
    const el = await mount();
    expect(el.textContent).toContain("Usage is not available right now.");
    expect(el.querySelector('[role="meter"]')).toBeNull();
  });
});
