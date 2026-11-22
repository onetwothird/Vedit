import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { EngineError, type ClipOp, type ClipView, type Edl, type Transcript } from "../engine/types";
import { createClipStore } from "./edlStore";
import { applyOp, keptRanges, parseTime, pyRound, type Op } from "./timeline";

interface FixtureCase {
  name: string;
  edl: Edl;
  ops: Op[];
  expected: { clips: unknown[]; kept_ranges: [number, number][] }[];
}
const fixture = JSON.parse(readFileSync(resolve(__dirname, "../../../tests/fixtures/edl_ops_cases.json"), "utf8")) as {
  transcript: Transcript;
  cases: FixtureCase[];
};
const TR = fixture.transcript;

const near = (got: number[][], want: number[][]) => {
  expect(got).toHaveLength(want.length);
  got.forEach((r, i) => r.forEach((v, j) => expect(Math.abs(v - want[i][j])).toBeLessThan(1e-9)));
};

/** A fake engine whose PATCH replies are held until the test settles them, one at a time or all at once. */
function fakeServer(start: Edl) {
  let edl: Edl = JSON.parse(JSON.stringify(start));
  const sent: ClipOp[] = [];
  const held: { op: ClipOp; ok: () => void; fail: (m: string) => void }[] = [];
  let auto = true;
  const view = (): ClipView => ({ edl, kept_ranges: keptRanges(edl, TR), captions: { events: [] } as unknown as ClipView["captions"] });
  const engine = {
    clip: vi.fn(async () => view()),
    transcript: vi.fn(async () => TR),
    patchClip: vi.fn((_p: string, _h: string, op: ClipOp) => {
      sent.push(op);
      return new Promise<ClipView>((res, rej) => {
        const ok = () => {
          edl = applyOp(edl, op as Op);
          res(view());
        };
        const fail = (m: string) => rej(new EngineError(400, "bad_request", m));
        if (auto) queueMicrotask(ok);
        else held.push({ op, ok, fail });
      });
    }),
  };
  return {
    engine, sent, held, view,
    manual: () => { auto = false; },
    edl: () => edl,
  };
}

async function setup(edl: Edl, manual = false) {
  const srv = fakeServer(edl);
  const store = createClipStore(srv.engine as never);
  if (manual) srv.manual();
  await store.getState().load("p", "h");
  const idle = () => vi.waitFor(() => expect(store.getState().saving).toBe(false));
  return { srv, store, s: () => store.getState(), idle };
}

const E0: Edl = { clips: [{ src: "main", words: [0, 13] }] };

describe("timeline port", () => {
  it("rounds half to even like Python", () => {
    expect([0.5, 1.5, 2.5, 3.5, -0.4, 2.4, 2.6].map(pyRound)).toEqual([0, 2, 2, 4, 0, 2, 3]);
  });
  it("parses times like common.parse_time", () => {
    expect([parseTime(1.5), parseTime("0:03.2"), parseTime("1:02:03.25")]).toEqual([1.5, 3.2, 3723.25]);
  });
  for (const c of fixture.cases) {
    it(`matches the Python fixture: ${c.name}`, () => {
      expect(c.expected).toHaveLength(c.ops.length + 1);
      let edl = c.edl;
      near(keptRanges(edl, TR), c.expected[0].kept_ranges);
      c.ops.forEach((op, i) => {
        edl = applyOp(edl, op);
        expect(edl.clips).toEqual(c.expected[i + 1].clips);
        near(keptRanges(edl, TR), c.expected[i + 1].kept_ranges);
      });
    });
  }
});

describe("clip store", () => {
  it("loads the view and the transcript", async () => {
    const { s } = await setup(E0);
    expect(s().view?.edl).toEqual(E0);
    expect(s().past).toEqual([]);
  });

  it("applies optimistically, then undo restores kept_ranges exactly", async () => {
    const { s, idle } = await setup(E0);
    const before = s().view!;
    s().apply({ op: "cut", w0: 3, w1: 5 });
    expect(s().view!.kept_ranges).toHaveLength(2); // before the server answers
    expect(s().saving).toBe(true);
    await idle();
    s().undo();
    await idle();
    expect(s().view!.edl).toEqual(before.edl);
    expect(s().view!.kept_ranges).toEqual(before.kept_ranges);
    expect(s().future).toHaveLength(1);
  });

  it("redo re-applies and a new op clears the future", async () => {
    const { s, idle } = await setup(E0);
    s().apply({ op: "cut", w0: 3, w1: 5 });
    await idle();
    const cutView = s().view!;
    s().undo();
    await idle();
    s().redo();
    await idle();
    expect(s().view!.kept_ranges).toEqual(cutView.kept_ranges);
    expect(s().future).toHaveLength(0);
    s().undo();
    await idle();
    s().apply({ op: "cut", w0: 9, w1: 10 });
    await idle();
    expect(s().future).toHaveLength(0);
    expect(s().past).toHaveLength(1);
  });

  it("undoing a cut over a partly cut range brings back only what that cut removed", async () => {
    const { s, srv, idle } = await setup({ clips: [{ src: "main", words: [0, 3] }, { src: "main", words: [8, 13] }] });
    s().apply({ op: "cut", w0: 2, w1: 10 });
    await idle();
    const afterCut = s().view!;
    s().apply({ op: "cut", w0: 0, w1: 13 }); // nothing left to cut but the rest
    s().undo();
    await idle();
    expect(s().view!.kept_ranges).toEqual(afterCut.kept_ranges);
    s().undo();
    await idle();
    expect(s().view!.edl.clips).toEqual([{ src: "main", words: [0, 3] }, { src: "main", words: [8, 13] }]);
    expect(srv.edl().clips).toEqual(s().view!.edl.clips);
  });

  it("undoing a restore over a range with kept words does not cut the kept words", async () => {
    const start: Edl = { clips: [{ src: "main", words: [0, 3] }, { src: "main", words: [8, 13] }] };
    const { s, idle } = await setup(start);
    const before = s().view!;
    s().apply({ op: "restore", w0: 2, w1: 9 });
    await idle();
    s().undo();
    await idle();
    expect(s().view!.edl.clips).toEqual(before.edl.clips);
    near(s().view!.kept_ranges, before.kept_ranges);
  });

  it("undoing a trim restores every dropped clip and word", async () => {
    const start: Edl = { clips: [{ src: "main", words: [0, 5] }, { src: "main", words: [7, 13] }] };
    const { s, idle } = await setup(start);
    const before = s().view!;
    s().apply({ op: "trim", w_first: 3, w_last: 9 });
    await idle();
    expect(s().view!.edl.clips).toEqual([{ src: "main", words: [3, 5] }, { src: "main", words: [7, 9] }]);
    s().undo();
    await idle();
    expect(s().view!.edl.clips).toEqual(before.edl.clips);
    near(s().view!.kept_ranges, before.kept_ranges);
  });

  it("undoes a patch of an absent key by removing it", async () => {
    const { s, srv, idle } = await setup({ ...E0, captions: { style: "pop" } });
    s().apply({ op: "patch", section: "captions", patch: { style: "box", size: 40 } });
    await idle();
    expect(s().view!.edl.captions).toEqual({ style: "box", size: 40 });
    expect(s().past[0].op).toMatchObject({ prev: { style: "pop", size: null } });
    s().undo();
    await idle();
    expect(s().view!.edl.captions).toEqual({ style: "pop" });
    expect(srv.edl().captions).toEqual({ style: "pop" });
    s().redo();
    await idle();
    expect(s().view!.edl.captions).toEqual({ style: "box", size: 40 });
  });

  it("undoes framing patches, including an absent fit", async () => {
    const { s, idle } = await setup({ clips: [{ src: "main", words: [0, 13], crop_x: 0.4 }] });
    s().apply({ op: "patch", section: "framing", patch: { crop_x: 0.7, fit: "blur" } });
    await idle();
    s().undo();
    await idle();
    expect(s().view!.edl).toEqual({ clips: [{ src: "main", words: [0, 13], crop_x: 0.4 }] });
  });

  it("undoes fix_word to the previous replacement, or removes the key when there was none", async () => {
    const { s, srv, idle } = await setup({ ...E0, captions: { replace: { teh: "the" } } });
    s().apply({ op: "fix_word", wrong: "teh", right: "THE" });
    s().apply({ op: "fix_word", wrong: "recieve", right: "receive" });
    await idle();
    expect((s().view!.edl.captions as { replace: object }).replace).toEqual({ teh: "THE", recieve: "receive" });
    s().undo();
    await idle();
    expect(srv.sent.at(-1)).toEqual({ op: "fix_word", wrong: "recieve", right: "recieve" });
    expect((srv.edl().captions as { replace: object }).replace).toEqual({ teh: "THE" });
    s().undo();
    await idle();
    expect(srv.sent.at(-1)).toEqual({ op: "fix_word", wrong: "teh", right: "the" });
    expect((s().view!.edl.captions as { replace: object }).replace).toEqual({ teh: "the" });
  });

  it("sends the op without the store's prev bookkeeping", async () => {
    const { s, srv, idle } = await setup(E0);
    s().apply({ op: "fix_word", wrong: "a", right: "b" });
    await idle();
    expect(srv.sent[0]).toEqual({ op: "fix_word", wrong: "a", right: "b" });
  });

  it("sends one op at a time and queues the rest, updating the view at once", async () => {
    const { s, srv, idle } = await setup(E0, true);
    s().apply({ op: "cut", w0: 0, w1: 1 });
    s().apply({ op: "cut", w0: 5, w1: 6 });
    s().apply({ op: "cut", w0: 9, w1: 9 });
    expect(s().view!.edl.clips).toHaveLength(3); // all three applied locally
    await vi.waitFor(() => expect(srv.held).toHaveLength(1));
    expect(srv.sent).toEqual([{ op: "cut", w0: 0, w1: 1 }]);
    srv.held.shift()!.ok();
    await vi.waitFor(() => expect(srv.held).toHaveLength(1));
    expect(srv.sent).toHaveLength(2);
    expect(s().view!.edl.clips).toHaveLength(3); // the stale reply does not clobber the optimistic view
    srv.held.shift()!.ok();
    await vi.waitFor(() => expect(srv.held).toHaveLength(1));
    srv.held.shift()!.ok();
    await idle();
    expect(srv.sent.map((o) => (o as { w0: number }).w0)).toEqual([0, 5, 9]);
    expect(s().view!.edl).toEqual(srv.view().edl);
  });

  it("runs undo and redo through the same queue", async () => {
    const { s, srv, idle } = await setup(E0, true);
    s().apply({ op: "cut", w0: 3, w1: 5 });
    s().undo();
    s().redo();
    await vi.waitFor(() => expect(srv.held).toHaveLength(1));
    for (let i = 0; i < 3; i++) {
      srv.held.shift()!.ok();
      if (i < 2) await vi.waitFor(() => expect(srv.held).toHaveLength(1));
    }
    await idle();
    expect(srv.sent).toEqual([
      { op: "cut", w0: 3, w1: 5 }, { op: "restore", w0: 3, w1: 5 }, { op: "cut", w0: 3, w1: 5 },
    ]);
    expect(s().past).toHaveLength(1);
    expect(s().future).toHaveLength(0);
  });

  it("rolls back to the last confirmed view on a server error and drops pending ops", async () => {
    const { s, srv, idle } = await setup(E0, true);
    s().apply({ op: "cut", w0: 0, w1: 1 });
    s().apply({ op: "cut", w0: 5, w1: 6 });
    s().apply({ op: "cut", w0: 9, w1: 9 });
    await vi.waitFor(() => expect(srv.held).toHaveLength(1));
    srv.held.shift()!.ok(); // first confirmed
    await vi.waitFor(() => expect(srv.held).toHaveLength(1));
    srv.held.shift()!.fail("no speech left");
    await idle();
    expect(s().error).toBe("no speech left");
    expect(s().view!.edl.clips).toEqual([{ src: "main", words: [2, 13] }]);
    expect(s().past).toHaveLength(1);
    expect(srv.sent).toHaveLength(2); // the third never went out
    expect(srv.held).toHaveLength(0);
    s().apply({ op: "cut", w0: 12, w1: 13 }); // usable again, and the error clears
    expect(s().error).toBeUndefined();
  });

  it("rolls back a failed undo to the pre-undo history", async () => {
    const { s, srv, idle } = await setup(E0);
    s().apply({ op: "cut", w0: 3, w1: 5 });
    await idle();
    srv.engine.patchClip.mockImplementationOnce(() => Promise.reject(new EngineError(0, "network", "could not reach the engine")));
    s().undo();
    await idle();
    expect(s().error).toBe("could not reach the engine");
    expect(s().past).toHaveLength(1);
    expect(s().future).toHaveLength(0);
    expect(s().view!.edl.clips).toHaveLength(2);
  });

  it("does not queue an op the local port rejects", async () => {
    const { s, srv } = await setup(E0);
    s().apply({ op: "cut", w0: 5, w1: 2 });
    expect(s().error).toMatch(/invalid word range/);
    expect(srv.sent).toHaveLength(0);
    expect(s().past).toHaveLength(0);
  });

  for (const c of fixture.cases) {
    it(`optimistic kept_ranges equal the Python result: ${c.name}`, async () => {
      const { s, idle } = await setup(c.edl);
      for (let i = 0; i < c.ops.length; i++) {
        s().apply(c.ops[i]);
        expect(s().view!.edl.clips).toEqual(c.expected[i + 1].clips);
        near(s().view!.kept_ranges, c.expected[i + 1].kept_ranges);
      }
      await idle();
      near(s().view!.kept_ranges, c.expected[c.ops.length].kept_ranges);
      for (let i = c.ops.length - 1; i >= 0; i--) s().undo();
      await idle();
      near(s().view!.kept_ranges, c.expected[0].kept_ranges);
    });
  }
});
