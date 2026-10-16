import { describe, expect, it } from "vitest";

import { SseParser } from "./sse";

describe("SseParser", () => {
  it("parses a complete message", () => {
    const [m] = new SseParser().push('event: step\nid: 1-0\ndata: {"a":1}\n\n');
    expect(m).toEqual({ event: "step", id: "1-0", data: '{"a":1}', retry: undefined });
  });

  it("assembles a message split across chunks at any point", () => {
    const wire = 'event: run.started\r\nid: 17-3\r\nretry: 2000\r\ndata: {"x":"y"}\r\n\r\n';
    for (let cut = 1; cut < wire.length; cut++) {
      const p = new SseParser();
      const out = [...p.push(wire.slice(0, cut)), ...p.push(wire.slice(cut))];
      expect(out).toEqual([{ event: "run.started", id: "17-3", data: '{"x":"y"}', retry: 2000 }]);
    }
  });

  it("does not treat a CR at the end of a chunk as a line on its own", () => {
    const p = new SseParser();
    expect(p.push("data: a\r")).toEqual([]);
    expect(p.push("\n\r\n")).toEqual([{ event: "message", id: undefined, data: "a", retry: undefined }]);
  });

  it("ignores comments (keep-alive pings) and unknown fields", () => {
    const out = new SseParser().push(": ping\n\nfoo: bar\ndata: x\n\n");
    expect(out).toEqual([{ event: "message", id: undefined, data: "x", retry: undefined }]);
  });

  it("joins multi-line data and keeps one leading space only", () => {
    const [m] = new SseParser().push("data:  two spaces\ndata:none\n\n");
    expect(m?.data).toBe(" two spaces\nnone");
  });

  it("ignores a non-numeric retry", () => {
    const [m] = new SseParser().push("retry: soon\ndata: x\n\n");
    expect(m?.retry).toBeUndefined();
  });

  it("returns several messages from one chunk, in order", () => {
    const out = new SseParser().push("id: 1-0\ndata: a\n\nid: 2-0\ndata: b\n\n");
    expect(out.map((m) => [m.id, m.data])).toEqual([
      ["1-0", "a"],
      ["2-0", "b"],
    ]);
  });
});
