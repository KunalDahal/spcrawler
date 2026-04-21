import assert from "node:assert/strict";
import { exactNodeEventIds, fitViewport } from "../src/graph-logic.js";

const viewport = fitViewport(
  { minX: 0, minY: 0, width: 1200, height: 900 },
  { width: 1000, height: 700 },
);

assert.equal(viewport.ty, 28, "root graph should align to the top padding");
assert.ok(viewport.scale <= 1 && viewport.scale >= 0.35, "scale should stay clamped");

const pageEvent = {
  type: "crawl.page_done",
  data: {
    url: "https://example.com/page",
    source_url: "https://example.com/source",
    stream_url: "https://cdn.example.com/live.m3u8",
  },
};

assert.deepEqual(
  exactNodeEventIds(pageEvent, "session:abc"),
  [
    "page:https://example.com/page",
    "stream:https://cdn.example.com/live.m3u8",
    "page:https://example.com/source",
  ],
  "page and stream events should attach to their exact nodes",
);

const rootEvent = {
  type: "session.created",
  data: {},
};

assert.deepEqual(
  exactNodeEventIds(rootEvent, "session:abc"),
  ["session:abc"],
  "session-wide events should attach to the root node",
);

console.log("graph logic checks passed");
