export function exactNodeEventIds(event, rootId) {
  const data = event?.data || {};
  const ids = [];

  if (
    [
      "session.created",
      "session.finished",
      "search.start",
      "search.complete",
      "search.candidates",
      "runner.finished",
      "runner.error",
      "error",
    ].includes(event?.type)
  ) {
    ids.push(rootId);
  }

  if (event?.type === "search.turn_done") {
    ids.push(`turn:${data.turn || 1}`);
  }

  if (data.url) {
    ids.push(`page:${data.url}`);
  }
  if (data.start_url) {
    ids.push(`page:${data.start_url}`);
  }
  if (data.stream_url) {
    ids.push(`stream:${data.stream_url}`);
  }
  if (data.source_url) {
    ids.push(`page:${data.source_url}`);
  }

  return [...new Set(ids)];
}

export function fitViewport(bounds, size) {
  const paddingX = 48;
  const paddingTop = 28;
  const paddingBottom = 72;
  const scale = clamp(
    Math.min(
      (size.width - paddingX * 2) / bounds.width,
      (size.height - paddingTop - paddingBottom) / bounds.height,
    ),
    0.35,
    1,
  );

  return {
    scale,
    tx: (size.width - bounds.width * scale) / 2 - bounds.minX * scale,
    ty: paddingTop - bounds.minY * scale,
  };
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}
