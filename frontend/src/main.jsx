import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { exactNodeEventIds, fitViewport } from "./graph-logic.js";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8080";
const PIRACY_THRESHOLD = 70;
const NODE_W = 220;
const NODE_H = 108;
const H_GAP = 46;
const V_GAP = 88;
const PAD_X = 80;
const PAD_Y = 80;
const EVENT_HISTORY_LIMIT = 20000;

const emptyForm = {
  keyword: "",
  api_key: "",
  db_name: "sports_scraper",
  mongo_uri: "mongodb://localhost:27017",
  proxy_url: "",
};

const eventLabels = {
  "session.created": "Session created",
  "session.finished": "Session finished",
  "search.start": "Search started",
  "search.turn_done": "Search turn",
  "search.complete": "Search complete",
  "search.candidates": "Candidates queued",
  "crawl.tree_start": "Tree started",
  "crawl.tree_done": "Tree complete",
  "crawl.page_start": "Page opened",
  "crawl.page_done": "Page analyzed",
  "crawl.page_fail": "Page rejected",
  "crawl.ad_detected": "Ad detected",
  "crawl.ad_handled": "Ad handled",
  "llm.navigate": "Navigation decision",
  "llm.score": "Scoring decision",
  "llm.verify_live": "Verify live stream",
  "llm.ad_check": "Ad check",
  "llm.classify": "Page classification",
  "llm.call_start": "LLM call started",
  "llm.cooldown": "LLM cooldown",
  "llm.call_ok": "LLM call completed",
  "llm.rate_limit": "LLM rate limit",
  "llm.server_error": "LLM server error",
  "llm.http_error": "LLM HTTP error",
  "llm.timeout": "LLM timeout",
  "llm.failed": "LLM failed",
  "llm.unexpected_error": "LLM unexpected error",
  "stream.found": "Stream found",
  "stream.rejected": "Stream rejected",
  "db.node_upserted": "Node persisted",
  "db.stream_recorded": "Stream persisted",
  "runner.finished": "Runner finished",
  "runner.error": "Runner error",
  error: "Crawler error",
};

function App() {
  const [form, setForm] = useState(emptyForm);
  const [sessions, setSessions] = useState([]);
  const [activeId, setActiveId] = useState("");
  const [events, setEvents] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    refreshSessions();
    const timer = setInterval(refreshSessions, 3000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!activeId) return;
    setEvents([]);
    setSelectedId("");
    setError("");

    const source = new EventSource(`${API_BASE}/api/sessions/${activeId}/events`);
    source.addEventListener("crawler", (message) => {
      const event = JSON.parse(message.data);
      setEvents((current) => {
        const next = [...current, event];
        if (next.length <= EVENT_HISTORY_LIMIT) return next;
        return next.slice(next.length - EVENT_HISTORY_LIMIT);
      });
    });
    source.onerror = () => {
      setError("Live event stream disconnected. It usually reconnects automatically.");
    };

    return () => source.close();
  }, [activeId]);

  async function refreshSessions() {
    try {
      const res = await fetch(`${API_BASE}/api/sessions`);
      if (!res.ok) return [];
      const data = await res.json();
      setSessions(data);
      if (!activeId && data.length) {
        setActiveId(data[0].id);
      }
      return data;
    } catch {
      // Keep the current UI state when polling fails.
      return [];
    }
  }

  async function startSession(e) {
    e.preventDefault();
    setBusy(true);
    setError("");

    try {
      const res = await fetch(`${API_BASE}/api/sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Could not start scraper");
      setActiveId(data.id);
      setEvents([]);
      setSelectedId("");
      setForm((current) => ({ ...current, keyword: "" }));
      await refreshSessions();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function stopSession(id) {
    await fetch(`${API_BASE}/api/sessions/${id}`, { method: "DELETE" });
    await refreshSessions();
  }

  async function removeSession(id) {
    const confirmed = window.confirm(
      "Remove this session from the frontend, backend, and MongoDB session records?",
    );
    if (!confirmed) return;

    const res = await fetch(`${API_BASE}/api/sessions/${id}/remove`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setError(data.error || "Could not remove session");
      return;
    }

    const nextSessions = await refreshSessions();
    if (activeId === id) {
      const fallback = nextSessions.find((session) => session.id !== id);
      setActiveId(fallback?.id || "");
      setEvents([]);
      setSelectedId("");
    }
  }

  const active = sessions.find((session) => session.id === activeId);
  const llmSummary = useMemo(() => summarizeLLM(events, nowMs), [events, nowMs]);
  const graph = useMemo(() => buildGraph(events, active), [events, active]);
  const selected = selectedId ? graph.selectionMap.get(selectedId) : null;

  useEffect(() => {
    if (!graph.rootId) {
      setSelectedId("");
      return;
    }
    if (!selectedId || !graph.selectionMap.has(selectedId)) {
      setSelectedId(graph.rootId);
    }
  }, [graph.rootId, graph.selectionMap, selectedId]);

  return (
    <main className="shell">
      <section className="topbar">
        <div>
          <p className="eyebrow">spcrawler control room</p>
          <h1>Live piracy investigation graph</h1>
        </div>
        <div className="health">
          <span className="pulse" />
          {active ? active.status : "waiting"}
        </div>
      </section>

      <section className="workspace">
        <aside className="side">
          <form className="launcher" onSubmit={startSession}>
            <label>
              Keyword
              <input
                value={form.keyword}
                onChange={(e) => setForm({ ...form, keyword: e.target.value })}
                placeholder="India vs Australia live stream"
                required
              />
            </label>
            <label>
              Gemini API key
              <input
                type="password"
                value={form.api_key}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                placeholder="Paste key for this run"
              />
            </label>
            <label>
              Database name
              <input
                value={form.db_name}
                onChange={(e) => setForm({ ...form, db_name: e.target.value })}
              />
            </label>
            <label>
              Mongo URI
              <input
                value={form.mongo_uri}
                onChange={(e) => setForm({ ...form, mongo_uri: e.target.value })}
              />
            </label>
            <label>
              Proxy URL
              <input
                value={form.proxy_url}
                onChange={(e) => setForm({ ...form, proxy_url: e.target.value })}
                placeholder="http://user:pass@host:port"
              />
            </label>
            <button disabled={busy}>{busy ? "Starting..." : "Start scraper"}</button>
          </form>

          {error && <div className="error">{error}</div>}

          <div className="sessions">
            <div className="section-head">
              <h2>Sessions</h2>
              <span className="tiny-pill">{sessions.length}</span>
            </div>
            {sessions.length === 0 && <p className="muted">No sessions yet.</p>}
            {sessions.map((session) => (
              <button
                key={session.id}
                className={`session ${session.id === activeId ? "active" : ""}`}
                onClick={() => setActiveId(session.id)}
                type="button"
              >
                <span>{session.keyword}</span>
                <small>{session.status}</small>
                <div className="session-metrics">
                  <b>{session.pages_crawled || 0}</b>
                  <span>pages</span>
                  <b>{session.streams_found || 0}</b>
                  <span>streams</span>
                </div>
              </button>
            ))}
          </div>
        </aside>

        <section className="mainstage">
          <Stats active={active} graph={graph} llmSummary={llmSummary} />
          <TreeCanvas
            graph={graph}
            llmSummary={llmSummary}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onClear={() => setSelectedId(graph.rootId || "")}
          />
        </section>

        <aside className="inspector">
          <div className="inspect-head">
            <div>
              <p className="eyebrow">Node details</p>
              <h2>Inspector</h2>
            </div>
            {active && (
              <div className="inspect-actions">
                {["running", "starting"].includes(active.status) && (
                  <button className="ghost" onClick={() => stopSession(active.id)} type="button">
                    Stop
                  </button>
                )}
                <button className="danger" onClick={() => removeSession(active.id)} type="button">
                  Remove session
                </button>
              </div>
            )}
          </div>
          <div className="inspector-scroll">
            {selected ? (
              <NodeDetails detail={selected} />
            ) : (
              <p className="muted">Click a session, turn, page, stream, or recent event.</p>
            )}
          </div>
        </aside>
      </section>
    </main>
  );
}

function Stats({ active, graph, llmSummary }) {
  const values = [
    ["Turns", graph.turnCount],
    ["Candidates", graph.candidateCount],
    ["Pages", active?.pages_crawled ?? graph.pageCount],
    ["Streams", active?.streams_found ?? graph.streamCount],
    ["LLM calls", llmSummary.totalCalls],
    ["Rate limits", llmSummary.rateLimitCount],
  ];

  return (
    <div className="stats">
      {values.map(([label, value]) => (
        <div className="stat" key={label}>
          <strong>{value}</strong>
          <span>{label}</span>
        </div>
      ))}
      <div className="current-url">
        <span>Current URL</span>
        <strong>{active?.current_url || "Waiting for crawler activity"}</strong>
      </div>
      <div className="current-url">
        <span>LLM pacing</span>
        <strong>
          {llmSummary.cooldownRemaining > 0
            ? `Cooling down for ${llmSummary.cooldownRemaining}s`
            : `Ready | dominant: ${llmSummary.primaryOperation}`}
        </strong>
      </div>
    </div>
  );
}

function TreeCanvas({ graph, llmSummary, selectedId, onSelect, onClear }) {
  const containerRef = useRef(null);
  const dragRef = useRef(null);
  const fittedSessionRef = useRef("");
  const hasMeasuredRef = useRef(false);
  const canvasActiveRef = useRef(false);
  const spacePressedRef = useRef(false);
  const [canvasActive, setCanvasActive] = useState(false);
  const [spacePressed, setSpacePressed] = useState(false);
  const [viewport, setViewport] = useState({ scale: 1, tx: 40, ty: 40 });
  const [size, setSize] = useState({ width: 960, height: 640 });

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const updateSize = () => {
      // Use content-box dimensions to avoid feedback loops caused by borders.
      setSize({
        width: Math.max(320, el.clientWidth),
        height: Math.max(420, el.clientHeight),
      });
    };

    updateSize();
    const observer = new ResizeObserver(updateSize);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!graph.bounds || size.width <= 0 || size.height <= 0) return;

    const sessionChanged = fittedSessionRef.current !== graph.sessionKey;
    const firstMeasurement = !hasMeasuredRef.current;

    if (!sessionChanged && !firstMeasurement) {
      return;
    }

    setViewport(fitViewport(graph.bounds, size));
    fittedSessionRef.current = graph.sessionKey;
    hasMeasuredRef.current = true;
  }, [graph.sessionKey, size.width, size.height]);

  useEffect(() => {
    canvasActiveRef.current = canvasActive;
  }, [canvasActive]);

  useEffect(() => {
    spacePressedRef.current = spacePressed;
  }, [spacePressed]);

  useEffect(() => {
    const move = (e) => {
      if (!dragRef.current) return;
      const { startX, startY, originTx, originTy } = dragRef.current;
      setViewport((current) => ({
        ...current,
        tx: originTx + (e.clientX - startX),
        ty: originTy + (e.clientY - startY),
      }));
    };

    const up = () => {
      dragRef.current = null;
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, []);

  useEffect(() => {
    const shouldIgnoreTarget = (target) =>
      target instanceof HTMLElement &&
      (target.isContentEditable ||
        ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(target.tagName));

    const keydown = (e) => {
      if (shouldIgnoreTarget(e.target)) return;

      if (e.code === "Space" && canvasActiveRef.current) {
        e.preventDefault();
        setSpacePressed(true);
        return;
      }

      if (!canvasActiveRef.current) return;

      if (e.key === "+" || e.key === "=" || e.code === "NumpadAdd") {
        e.preventDefault();
        zoomBy(1.14);
        return;
      }

      if (e.key === "-" || e.key === "_" || e.code === "NumpadSubtract") {
        e.preventDefault();
        zoomBy(0.88);
      }
    };

    const keyup = (e) => {
      if (e.code === "Space") {
        setSpacePressed(false);
      }
    };

    const blur = () => {
      setSpacePressed(false);
      dragRef.current = null;
    };

    window.addEventListener("keydown", keydown);
    window.addEventListener("keyup", keyup);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", keydown);
      window.removeEventListener("keyup", keyup);
      window.removeEventListener("blur", blur);
    };
  }, [size.width, size.height]);

  function startDrag(e) {
    if (!spacePressedRef.current) return;
    if (e.target.closest("[data-node='true']")) return;
    e.preventDefault();
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      originTx: viewport.tx,
      originTy: viewport.ty,
    };
  }

  function zoomBy(factor) {
    setViewport((current) => {
      const centerX = size.width / 2;
      const centerY = size.height / 2;
      const nextScale = clamp(current.scale * factor, 0.35, 2.4);
      const graphX = (centerX - current.tx) / current.scale;
      const graphY = (centerY - current.ty) / current.scale;

      return {
        scale: nextScale,
        tx: centerX - graphX * nextScale,
        ty: centerY - graphY * nextScale,
      };
    });
  }

  return (
    <div className="canvas-shell">
      <div className="canvas-header">
        <div>
          <p className="eyebrow">Recursive graph</p>
          <h2>Session node to turns to links to sublinks</h2>
          <p className="muted canvas-hint">Use + and - to zoom. Hold Space and drag to move.</p>
        </div>
        <div className="canvas-controls">
          <button className="mini-btn" type="button" onClick={() => zoomBy(1.14)}>
            +
          </button>
          <button className="mini-btn" type="button" onClick={() => zoomBy(0.88)}>
            -
          </button>
          <button
            className="mini-btn"
            type="button"
            onClick={() => setViewport(fitViewport(graph.bounds, size))}
          >
            Fit
          </button>
        </div>
      </div>

      <div
        className={`canvas${spacePressed ? " canvas-space" : ""}${dragRef.current ? " canvas-dragging" : ""}`}
        ref={containerRef}
        tabIndex={0}
        onPointerDown={startDrag}
        onMouseEnter={() => setCanvasActive(true)}
        onMouseLeave={() => {
          setCanvasActive(false);
          setSpacePressed(false);
        }}
        onFocus={() => setCanvasActive(true)}
        onBlur={() => {
          setCanvasActive(false);
          setSpacePressed(false);
        }}
        onDoubleClick={() => setViewport(fitViewport(graph.bounds, size))}
      >
        {graph.nodes.length === 0 ? (
          <div className="empty">
            <div className="empty-orb" />
            <p>Start a scraper and the graph will populate live as Crawl4AI explores the web.</p>
          </div>
        ) : (
          <>
            <svg
              className="tree-svg"
              width={size.width}
              height={size.height}
              viewBox={`0 0 ${size.width} ${size.height}`}
              onClick={onClear}
            >
              <rect className="canvas-hit" x="0" y="0" width={size.width} height={size.height} />
              <g transform={`translate(${viewport.tx} ${viewport.ty}) scale(${viewport.scale})`}>
                {graph.edges.map((edge) => (
                  <path
                    key={edge.id}
                    d={edge.path}
                    className={`tree-edge edge-${edge.tone}`}
                    vectorEffect="non-scaling-stroke"
                  />
                ))}

                {graph.nodes.map((node) => {
                  const isSelected = selectedId === node.id;
                  const titleText = truncate(node.title, 26);
                  const subtitleText = truncate(node.subtitle, 31);
                  const captionText = truncate(node.caption, 31);
                  return (
                    <g
                      key={node.id}
                      data-node="true"
                      className="tree-node-group"
                      transform={`translate(${node.x},${node.y})`}
                      onPointerDown={(e) => e.stopPropagation()}
                      onClick={(e) => {
                        e.stopPropagation();
                        onSelect(node.id);
                      }}
                    >
                      <rect
                        className={`tree-node-rect tone-${node.tone}${isSelected ? " selected" : ""}`}
                        rx="18"
                        ry="18"
                        width={NODE_W}
                        height={NODE_H}
                      />
                      <clipPath id={`clip-${node.id.replace(/[^a-zA-Z0-9_-]/g, "_")}`}>
                        <rect x="12" y="10" width={NODE_W - 24} height={NODE_H - 20} rx="12" ry="12" />
                      </clipPath>
                      <g clipPath={`url(#clip-${node.id.replace(/[^a-zA-Z0-9_-]/g, "_")})`}>
                      <text x="16" y="22" className="tree-node-type">
                        {node.kind.toUpperCase()}
                      </text>
                      <text x={NODE_W / 2} y="46" className="tree-node-title">
                        {titleText}
                      </text>
                      <text x={NODE_W / 2} y="68" className="tree-node-subtitle">
                        {subtitleText}
                      </text>
                      <text x={NODE_W / 2} y="92" className="tree-node-caption">
                        {captionText}
                      </text>
                      </g>
                      {node.flagged && <circle className="flag-dot" cx={NODE_W - 18} cy={18} r="6" />}
                      {node.hasStream && <circle className="stream-dot" cx={NODE_W - 38} cy={18} r="6" />}
                    </g>
                  );
                })}
              </g>
            </svg>

            <div className="canvas-overlay canvas-decision">
              <p className="overlay-label">Current decision</p>
              <strong>{graph.currentDecision.title}</strong>
              <p className="overlay-copy">Why: {graph.currentDecision.why}</p>
              <p className="overlay-copy">Confidence: {graph.currentDecision.confidence}</p>
              <p className="overlay-copy">Next action: {graph.currentDecision.nextAction}</p>
            </div>

            <div className="canvas-right-stack">
              <div className="canvas-overlay canvas-llm">
                <div className="section-head">
                  <span className="overlay-label">LLM pacing</span>
                  <span className="tiny-pill">{llmSummary.totalCalls}</span>
                </div>
                <p className="overlay-copy">Main load: {llmSummary.primaryOperation}</p>
                <p className="overlay-copy">Rate limits: {llmSummary.rateLimitCount}</p>
                <p className="overlay-copy">
                  Breathing space:{" "}
                  {llmSummary.cooldownRemaining > 0
                    ? `${llmSummary.cooldownRemaining}s remaining`
                    : "No active cooldown"}
                </p>
                <p className="overlay-copy">Last retry wait: {llmSummary.lastRateLimitWait}s</p>
              </div>

              <div className="canvas-overlay canvas-latest">
                <div className="section-head">
                  <span className="overlay-label">Latest events</span>
                  <span className="tiny-pill">{graph.latestEvents.length}</span>
                </div>
                <div className="event-stack">
                  {graph.latestEvents.length === 0 && <p className="muted">No events yet.</p>}
                  {graph.latestEvents.map((item) => (
                    <button
                      key={item.id}
                      className={`event-chip${selectedId === item.id ? " active" : ""}`}
                      onClick={() => onSelect(item.id)}
                      type="button"
                    >
                      <span>{item.label}</span>
                      <small>{item.subtitle}</small>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function NodeDetails({ detail }) {
  if (!detail) return null;

  if (detail.kind === "session") {
    return (
      <div className="node-details">
        <p className="eyebrow">Session node</p>
        <h3 className="detail-title">{detail.keyword || "Live search session"}</h3>
        <div className="badge-row">
          <span className={`badge badge-${detail.statusTone}`}>{detail.status}</span>
          <span className="badge badge-blue">Turns: {detail.turnCount}</span>
          <span className="badge badge-blue">Candidates: {detail.candidateCount}</span>
        </div>
        <dl className="detail-list">
          <dt>Session ID</dt>
          <dd>{detail.sessionId || "--"}</dd>
          <dt>Crawler session</dt>
          <dd>{detail.crawlerSessionId || "--"}</dd>
          <dt>Started</dt>
          <dd>{detail.startedAt || "--"}</dd>
          <dt>Finished</dt>
          <dd>{detail.finishedAt || "--"}</dd>
          <dt>Pages crawled</dt>
          <dd>{detail.pagesCrawled}</dd>
          <dt>Streams found</dt>
          <dd>{detail.streamsFound}</dd>
          <dt>Current URL</dt>
          <dd className="detail-url-small">{detail.currentUrl || "--"}</dd>
        </dl>
        {detail.queries.length > 0 && (
          <DetailSection title="Search queries">
            <ul className="url-list">
              {detail.queries.map((query, index) => (
                <li key={`${query}-${index}`}>{query}</li>
              ))}
            </ul>
          </DetailSection>
        )}
        <EventTimeline events={detail.events} />
      </div>
    );
  }

  if (detail.kind === "turn") {
    return (
      <div className="node-details">
        <p className="eyebrow">Search turn</p>
        <h3 className="detail-title">Turn {detail.turn}</h3>
        <div className="badge-row">
          <span className="badge badge-blue">New results: {detail.newResults}</span>
          <span className="badge badge-blue">Total results: {detail.totalResults}</span>
        </div>
        <dl className="detail-list">
          <dt>Query</dt>
          <dd>{detail.query || "--"}</dd>
          <dt>Time</dt>
          <dd>{detail.ts || "--"}</dd>
        </dl>
        <DetailSection title={`Result links (${detail.results.length})`}>
          <ul className="url-list">
            {detail.results.map((item, index) => (
              <li key={`${item.url}-${index}`}>
                <a href={item.url} target="_blank" rel="noreferrer">
                  {item.title || item.url}
                </a>
              </li>
            ))}
          </ul>
        </DetailSection>
        <EventTimeline events={detail.events} />
      </div>
    );
  }

  if (detail.kind === "page") {
    const links = Array.isArray(detail.links) ? detail.links : [];
    const streams = Array.isArray(detail.streams) ? detail.streams : [];
    const iframes = Array.isArray(detail.iframes) ? detail.iframes : [];
    const players = normalizePlayers(detail.players);

    return (
      <div className="node-details">
        <p className="eyebrow">Page node</p>
        <h3 className="detail-title">{detail.title || detail.url || "Untitled page"}</h3>
        {detail.url && (
          <a className="detail-url" href={detail.url} target="_blank" rel="noreferrer">
            {detail.url}
          </a>
        )}
        <div className="decision-card">
          <span className={`decision-pill tone-${detail.tone}`}>{detail.stateLabel}</span>
          <strong>{detail.decision.title}</strong>
          <p>Why: {detail.decision.why}</p>
          <p>Confidence: {detail.decision.confidence}</p>
          <p>Next action: {detail.decision.nextAction}</p>
        </div>
        <div className="badge-row">
          <span className={`badge ${detail.isSuspicious ? "badge-orange" : "badge-gray"}`}>
            Suspicious: {detail.isSuspicious ? "Yes" : "No"}
          </span>
          <span className={`badge ${detail.isPlayerPage ? "badge-yellow" : "badge-gray"}`}>
            Player page: {detail.isPlayerPage ? "Yes" : "No"}
          </span>
          <span className={`badge ${detail.isPiracyHost ? "badge-red" : "badge-gray"}`}>
            Piracy host: {detail.isPiracyHost ? "Yes" : "No"}
          </span>
          <span className={`badge ${detail.isOfficial ? "badge-gray" : "badge-blue"}`}>
            Official: {detail.isOfficial ? "Yes" : "No"}
          </span>
        </div>
        <dl className="detail-list">
          <dt>Score</dt>
          <dd>{detail.score ?? "--"}</dd>
          <dt>Depth</dt>
          <dd>{detail.depth ?? "--"}</dd>
          <dt>Parent</dt>
          <dd className="detail-url-small">{detail.parentUrl || "Root result"}</dd>
          <dt>Tree</dt>
          <dd className="detail-url-small">{detail.treeCol || "--"}</dd>
          <dt>Latest event</dt>
          <dd>{eventLabels[detail.latestEventType] || detail.latestEventType || "--"}</dd>
          <dt>Updated</dt>
          <dd>{detail.ts || "--"}</dd>
        </dl>

        {detail.pageSummary && (
          <DetailSection title="Page summary">
            <p className="detail-copy">{detail.pageSummary}</p>
          </DetailSection>
        )}

        <DetailSection title={`All links in page (${links.length})`}>
          {links.length === 0 ? (
            <p className="muted">No link list was emitted for this page yet.</p>
          ) : (
            <ul className="url-list">
              {links.map((link, index) => (
                <li key={`${link.url || link}-${index}`}>
                  <a href={typeof link === "string" ? link : link.url} target="_blank" rel="noreferrer">
                    {typeof link === "string" ? link : link.title || link.url}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </DetailSection>

        <DetailSection title={`Players in page (${players.length})`}>
          {players.length === 0 ? (
            <p className="muted">No player-level stream groups detected.</p>
          ) : (
            <div className="player-list">
              {players.map((player) => (
                <div className="player-card" key={player.id}>
                  <strong>{player.id}</strong>
                  <ul className="url-list">
                    {player.streams.map((stream) => (
                      <li key={stream}>
                        <a href={stream} target="_blank" rel="noreferrer">
                          {stream}
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </DetailSection>

        <DetailSection title={`Iframes (${iframes.length})`}>
          {iframes.length === 0 ? (
            <p className="muted">No iframe sources recorded.</p>
          ) : (
            <ul className="url-list">
              {iframes.map((iframe) => (
                <li key={iframe}>
                  <a href={iframe} target="_blank" rel="noreferrer">
                    {iframe}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </DetailSection>

        <DetailSection title={`Streams (${streams.length})`}>
          {streams.length === 0 ? (
            <p className="muted">No stream URLs confirmed on this page yet.</p>
          ) : (
            <ul className="url-list">
              {streams.map((stream) => (
                <li key={stream}>
                  <a href={stream} target="_blank" rel="noreferrer">
                    {stream}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </DetailSection>
        <EventTimeline events={detail.events} />
      </div>
    );
  }

  if (detail.kind === "stream") {
    return (
      <div className="node-details">
        <p className="eyebrow">Stream node</p>
        <h3 className="detail-title">Confirmed stream</h3>
        {detail.streamUrl && (
          <a className="detail-url" href={detail.streamUrl} target="_blank" rel="noreferrer">
            {detail.streamUrl}
          </a>
        )}
        <dl className="detail-list">
          <dt>Source page</dt>
          <dd className="detail-url-small">{detail.sourceUrl || "--"}</dd>
          <dt>Player ID</dt>
          <dd>{detail.playerId || "--"}</dd>
          <dt>Stream type</dt>
          <dd>{detail.streamType || "--"}</dd>
          <dt>Score</dt>
          <dd>{detail.score ?? "--"}</dd>
          <dt>Discovered</dt>
          <dd>{detail.ts || "--"}</dd>
        </dl>
        <EventTimeline events={detail.events} />
      </div>
    );
  }

  return (
    <div className="node-details">
      <p className="eyebrow">Event</p>
      <h3 className="detail-title">{detail.label}</h3>
      <dl className="detail-list">
        <dt>Type</dt>
        <dd>{detail.event.type}</dd>
        <dt>Time</dt>
        <dd>{detail.event.ts || "--"}</dd>
        <dt>Session</dt>
        <dd>{detail.event.session_id || "--"}</dd>
      </dl>
      <DetailSection title="Payload">
        <pre className="detail-json">{JSON.stringify(detail.event.data || {}, null, 2)}</pre>
      </DetailSection>
    </div>
  );
}

function EventTimeline({ events }) {
  const safeEvents = Array.isArray(events) ? events : [];

  return (
    <DetailSection title={`Event timeline (${safeEvents.length})`}>
      {safeEvents.length === 0 ? (
        <p className="muted">No event details were attached to this node yet.</p>
      ) : (
        <div className="timeline-list">
          {safeEvents.map((event, index) => (
            <details className="timeline-item" key={`${event.ts || "event"}-${event.type}-${index}`}>
              <summary className="timeline-summary">
                <span className="timeline-label">{eventLabels[event.type] || event.type}</span>
                <span className="timeline-time">{event.ts || "--"}</span>
              </summary>
              <pre className="detail-json">{JSON.stringify(event.data || {}, null, 2)}</pre>
            </details>
          ))}
        </div>
      )}
    </DetailSection>
  );
}

function DetailSection({ title, children }) {
  return (
    <div className="detail-section">
      <h4>{title}</h4>
      {children}
    </div>
  );
}

function buildGraph(events, active) {
  const selectionMap = new Map();
  const pageMap = new Map();
  const turnMap = new Map();
  const streamMap = new Map();
  const childMap = new Map();
  const nodeEvents = new Map();
  const roots = [];

  const sessionId = active?.id || events[0]?.session_id || "root";
  const rootId = `session:${sessionId}`;

  const root = {
    id: rootId,
    kind: "session",
    tone: "blue",
    title: active?.keyword || findKeyword(events) || "Search session",
    subtitle: active?.status || "waiting",
    caption: "Session node",
    flagged: false,
    hasStream: false,
    detail: null,
  };

  roots.push(rootId);
  const nodeMap = new Map([[rootId, root]]);
  const queries = [];
  const candidateTurnByUrl = new Map();

  function pushNodeEvent(nodeId, event) {
    if (!nodeId) return;
    if (!nodeEvents.has(nodeId)) {
      nodeEvents.set(nodeId, []);
    }
    nodeEvents.get(nodeId).push(event);
  }

  for (const event of events) {
    for (const nodeId of exactNodeEventIds(event, rootId)) {
      pushNodeEvent(nodeId, event);
    }
  }

  for (const ev of events) {
    const data = ev.data || {};

    if (ev.type === "search.turn_done") {
      const turnId = `turn:${data.turn || turnMap.size + 1}`;
      const results = Array.isArray(data.results) ? data.results : [];
      const turnDetail = {
        kind: "turn",
        id: turnId,
        turn: data.turn || turnMap.size + 1,
        query: data.query || "",
        newResults: data.new_results || 0,
        totalResults: data.total || 0,
        results,
        ts: ev.ts || "",
        events: nodeEvents.get(turnId) || [],
      };

      turnMap.set(turnId, turnDetail);
      queries.push(turnDetail.query);

      nodeMap.set(turnId, {
        id: turnId,
        kind: "turn",
        tone: "blue",
        title: `Turn ${turnDetail.turn}`,
        subtitle: turnDetail.query || "Search iteration",
        caption: `${results.length} results`,
        flagged: false,
        hasStream: false,
        detail: turnDetail,
      });
      addChild(childMap, rootId, turnId);

      for (const result of results) {
        if (!result?.url) continue;
        candidateTurnByUrl.set(result.url, turnId);
        upsertPageNode(pageMap, {
          url: result.url,
          parentId: turnId,
          title: result.title || hostname(result.url),
          stateLabel: "Searching",
          tone: "blue",
          caption: "Candidate result",
          decision: defaultDecision("Searching", "Collected from search results", "65%", "Open candidate page"),
        });
      }
      continue;
    }

    if (ev.type === "crawl.tree_start") {
      if (data.start_url) {
        upsertPageNode(pageMap, {
          url: data.start_url,
          parentId: candidateTurnByUrl.get(data.start_url) || rootId,
          treeCol: data.tree_col || "",
          title: hostname(data.start_url),
          stateLabel: "Searching",
          tone: "blue",
          caption: "Root candidate",
          ts: ev.ts,
        });
      }
      continue;
    }

    if (isPageEvent(ev)) {
      const url = data.url;
      if (!url) continue;

      const parentId = data.parent_url
        ? `page:${data.parent_url}`
        : candidateTurnByUrl.get(url) || pageMap.get(url)?.parentId || rootId;

      if (data.parent_url && !pageMap.has(data.parent_url)) {
        upsertPageNode(pageMap, {
          url: data.parent_url,
          parentId: candidateTurnByUrl.get(data.parent_url) || rootId,
          title: hostname(data.parent_url),
          stateLabel: "Investigating",
          tone: "yellow",
          caption: "Discovered page",
          decision: defaultDecision("Investigating", "Referenced by a crawled page", "55%", "Inspect child page"),
        });
      }

      const existing = pageMap.get(url) || {
        url,
        parentId,
        title: hostname(url),
        links: [],
        iframes: [],
        players: {},
        streams: [],
        score: null,
        depth: null,
        stateLabel: "Investigating",
        tone: "yellow",
        caption: "Page node",
        decision: defaultDecision("Investigating", "Waiting for more crawler context", "58%", "Continue scraping"),
      };

      const merged = {
        ...existing,
        parentId,
        url,
        treeCol: data.tree_col || existing.treeCol || "",
        parentUrl: data.parent_url ?? existing.parentUrl ?? null,
        title: data.title || existing.title || hostname(url),
        pageSummary: data.page_summary || data.text_snippet || existing.pageSummary || "",
        links: Array.isArray(data.links_found) ? data.links_found : existing.links || [],
        iframes: Array.isArray(data.iframes) ? data.iframes : existing.iframes || [],
        players: isRecord(data.players) ? data.players : existing.players || {},
        streams: Array.isArray(data.stream_urls) ? data.stream_urls : existing.streams || [],
        isPlayerPage: coalesceBool(data.is_player_page, existing.isPlayerPage),
        isSuspicious: coalesceBool(data.is_suspicious, existing.isSuspicious),
        isPiracyHost: coalesceBool(data.is_piracy_host, existing.isPiracyHost),
        isOfficial: coalesceBool(data.is_official, existing.isOfficial),
        isAdPage: coalesceBool(data.is_ad_page, existing.isAdPage),
        flagged: coalesceBool(data.flagged, existing.flagged),
        score: asNumber(data.score, existing.score),
        depth: data.depth ?? existing.depth ?? null,
        latestEventType: ev.type,
        latestReason: data.reason || data.action || existing.latestReason || "",
        ts: ev.ts || existing.ts || "",
        events: nodeEvents.get(`page:${url}`) || existing.events || [],
      };

      merged.decision = decisionFromEvent(ev, merged);
      merged.tone = toneForPage(merged);
      merged.stateLabel = labelForTone(merged.tone);
      merged.caption = pageCaption(merged);

      pageMap.set(url, merged);
      continue;
    }

    if (ev.type === "stream.found") {
      const sourceUrl = data.source_url || "";
      if (sourceUrl && !pageMap.has(sourceUrl)) {
        upsertPageNode(pageMap, {
          url: sourceUrl,
          parentId: candidateTurnByUrl.get(sourceUrl) || rootId,
          title: hostname(sourceUrl),
          tone: "orange",
          stateLabel: "Suspicious",
          caption: "Source page",
          decision: defaultDecision("Extracting streaming links", "Stream candidate found on page", "82%", "Verify embedded player"),
        });
      }

      const id = `stream:${data.stream_url}`;
     streamMap.set(id, {
        kind: "stream",
        id,
        parentId: sourceUrl ? `page:${sourceUrl}` : rootId,
        streamUrl: data.stream_url,
        sourceUrl,
        playerId: data.player_id || "",
        streamType: data.stream_type || "",
        score: data.score ?? null,
        ts: ev.ts || "",
        tone: "red",
        title: "Confirmed stream",
        subtitle: data.stream_url || "",
        caption: data.player_id || "Verified live stream",
        flagged: true,
        hasStream: true,
        events: nodeEvents.get(id) || [],
      });

      const page = pageMap.get(sourceUrl);
      if (page) {
        page.streams = uniqueStrings([...(page.streams || []), data.stream_url].filter(Boolean));
        page.tone = "red";
        page.stateLabel = "Confirmed piracy";
        page.caption = pageCaption(page);
        page.decision = defaultDecision(
          "Extracting streaming links",
          "Verified live stream was attached to this page",
          "95%",
          "Escalate or archive evidence",
        );
      }
      continue;
    }
  }

  for (const [turnId, detail] of turnMap.entries()) {
    selectionMap.set(turnId, detail);
  }

  for (const [url, page] of pageMap.entries()) {
    const id = `page:${url}`;
    nodeMap.set(id, {
      id,
      kind: "page",
      tone: page.tone,
      title: page.title || hostname(url),
      subtitle: page.url,
      caption: page.caption,
      flagged: page.tone === "red" || page.flagged,
      hasStream: (page.streams || []).length > 0,
      detail: { kind: "page", id, ...page },
    });
    addChild(childMap, page.parentId || rootId, id);
    selectionMap.set(id, { kind: "page", id, ...page });
  }

  for (const [id, stream] of streamMap.entries()) {
    nodeMap.set(id, stream);
    addChild(childMap, stream.parentId || rootId, id);
    selectionMap.set(id, stream);
  }

  const rootDetail = {
    kind: "session",
    id: rootId,
    keyword: active?.keyword || root.title,
    status: active?.status || "running",
    statusTone: toneFromStatus(active?.status || "running"),
    sessionId: active?.id || "",
    crawlerSessionId: active?.crawler_session_id || events[0]?.session_id || "",
    startedAt: active?.started_at || "",
    finishedAt: active?.finished_at || "",
    currentUrl: active?.current_url || "",
    pagesCrawled: active?.pages_crawled ?? pageMap.size,
    streamsFound: active?.streams_found ?? streamMap.size,
    turnCount: turnMap.size,
    candidateCount: candidateTurnByUrl.size,
    queries: uniqueStrings(queries.filter(Boolean)),
    events: nodeEvents.get(rootId) || [],
  };
  root.detail = rootDetail;
  selectionMap.set(rootId, rootDetail);

  const positioned = layoutNodes(nodeMap, childMap, roots);
  const nodes = [];
  const edges = [];

  for (const [id, pos] of positioned.positions.entries()) {
    const node = nodeMap.get(id);
    if (!node) continue;
    nodes.push({
      ...node,
      x: pos.x,
      y: pos.y,
    });
  }

  for (const [parentId, children] of childMap.entries()) {
    const p = positioned.positions.get(parentId);
    if (!p) continue;
    for (const childId of children) {
      const c = positioned.positions.get(childId);
      if (!c) continue;
      const parentNode = nodeMap.get(parentId);
      const childNode = nodeMap.get(childId);
      const x1 = p.x + NODE_W / 2;
      const y1 = p.y + NODE_H;
      const x2 = c.x + NODE_W / 2;
      const y2 = c.y;
      const midY = (y1 + y2) / 2;

      edges.push({
        id: `${parentId}-${childId}`,
        tone: childNode?.tone || parentNode?.tone || "blue",
        path: `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`,
      });
    }
  }

  const latestEvents = events.slice(-8).reverse().map((event, index) => {
    const id = `event:${index}:${event.ts || index}`;
    const detail = {
      kind: "event",
      id,
      label: eventLabels[event.type] || event.type,
      event,
    };
    selectionMap.set(id, detail);
    return {
      id,
      label: detail.label,
      subtitle: latestEventSubtitle(event),
    };
  });

  return {
    sessionKey: sessionId,
    rootId,
    selectionMap,
    nodes,
    edges,
    bounds: positioned.bounds,
    latestEvents,
    currentDecision: buildCurrentDecision(events),
    turnCount: turnMap.size,
    candidateCount: candidateTurnByUrl.size,
    pageCount: pageMap.size,
    streamCount: streamMap.size,
  };
}

function layoutNodes(nodeMap, childMap, roots) {
  const leafColumns = new Map();
  let leafIndex = 0;

  function visibleChildren(id) {
    return (childMap.get(id) || []).filter((childId) => nodeMap.has(childId));
  }

  function assignLeaves(id) {
    const children = visibleChildren(id);
    if (children.length === 0) {
      leafColumns.set(id, leafIndex++);
      return;
    }
    children.forEach(assignLeaves);
  }

  roots.forEach(assignLeaves);

  function xFor(id) {
    const children = visibleChildren(id);
    if (children.length === 0) {
      return PAD_X + (leafColumns.get(id) || 0) * (NODE_W + H_GAP);
    }
    const xs = children.map(xFor);
    return (xs[0] + xs[xs.length - 1]) / 2;
  }

  const positions = new Map();
  const visited = new Set();
  let maxX = PAD_X + NODE_W;
  let maxY = PAD_Y + NODE_H;

  function place(id, depth) {
    if (visited.has(id)) return;
    visited.add(id);

    const x = xFor(id);
    const y = PAD_Y + depth * (NODE_H + V_GAP);
    positions.set(id, { x, y });
    maxX = Math.max(maxX, x + NODE_W);
    maxY = Math.max(maxY, y + NODE_H);

    visibleChildren(id).forEach((childId) => place(childId, depth + 1));
  }

  roots.forEach((id) => place(id, 0));

  return {
    positions,
    bounds: {
      minX: 0,
      minY: 0,
      width: Math.max(960, maxX + PAD_X),
      height: Math.max(560, maxY + PAD_Y),
    },
  };
}

function upsertPageNode(pageMap, update) {
  const existing = pageMap.get(update.url) || {
    url: update.url,
    links: [],
    iframes: [],
    players: {},
    streams: [],
  };

  pageMap.set(update.url, {
    ...existing,
    ...update,
    decision: update.decision || existing.decision || defaultDecision(
      "Investigating",
      "Page discovered in crawler flow",
      "50%",
      "Inspect page details",
    ),
  });
}

function isPageEvent(event) {
  return [
    "crawl.page_start",
    "crawl.page_done",
    "crawl.page_fail",
    "llm.classify",
    "llm.navigate",
    "llm.score",
    "llm.ad_check",
    "db.node_upserted",
  ].includes(event.type);
}

function decisionFromEvent(event, page) {
  const data = event.data || {};

  if (event.type === "stream.found") {
    return defaultDecision(
      "Extracting streaming links",
      "A verified live stream URL was captured from this page",
      "95%",
      "Escalate to manual review",
    );
  }

  if (event.type === "llm.navigate") {
    return defaultDecision(
      mapNavigateAction(data.action, data.signal),
      data.reason || data.signal || "Navigation cue emitted by the model",
      confidenceFromScore(page.score, 74),
      formatNextAction(data.next_urls),
    );
  }

  if (event.type === "llm.classify") {
    return defaultDecision(
      classifyLabel(page),
      data.reason || "Classification returned without a detailed reason",
      confidenceFromFlags(page),
      nextActionFromPage(page),
    );
  }

  if (event.type === "llm.score") {
    return defaultDecision(
      scoreLabel(data.combined),
      `Rule score ${data.rule ?? "--"} combined with LLM score ${data.llm ?? "--"}`,
      confidenceFromScore(data.combined, 68),
      nextActionFromPage(page),
    );
  }

  if (event.type === "llm.ad_check") {
    return defaultDecision(
      data.is_ad_page ? "Rejecting false positive" : "Checking redirect destination",
      data.ad_type ? `Ad pattern detected: ${data.ad_type}` : "Ad check completed",
      confidenceFromScore(page.score, data.is_ad_page ? 83 : 64),
      data.action ? `Attempt ${data.action}` : "Continue scraping page",
    );
  }

  if (event.type === "crawl.page_fail") {
    return defaultDecision(
      "Rejecting false positive",
      data.reason || "Crawler rejected this page",
      "88%",
      "Backtrack and inspect another branch",
    );
  }

  if (event.type === "crawl.page_done" || event.type === "db.node_upserted") {
    return defaultDecision(
      nextActionLabelForPage(page),
      page.pageSummary || "Page crawl completed",
      confidenceFromScore(page.score, 70),
      nextActionFromPage(page),
    );
  }

  if (event.type === "crawl.page_start") {
    return defaultDecision(
      "Investigating",
      "Crawler opened the page and is collecting DOM, links, iframes, and network requests",
      "54%",
      "Wait for Crawl4AI analysis",
    );
  }

  return page.decision || defaultDecision(
    "Investigating",
    "Crawler event received",
    confidenceFromScore(page.score, 58),
    nextActionFromPage(page),
  );
}

function buildCurrentDecision(events) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (["llm.navigate", "llm.classify", "llm.score", "llm.ad_check", "crawl.page_done", "crawl.page_fail", "stream.found"].includes(event.type)) {
      return decisionFromEvent(event, {
        score: event.data?.score ?? event.data?.combined ?? null,
        pageSummary: event.data?.page_summary || "",
        isSuspicious: event.data?.is_suspicious,
        isPiracyHost: event.data?.is_piracy_host,
        isOfficial: event.data?.is_official,
        isPlayerPage: event.data?.is_player_page,
        streams: event.data?.stream_urls || [],
      });
    }
  }

  return defaultDecision(
    "Searching",
    "Waiting for the crawler to emit the first actionable event",
    "Pending",
    "Start or resume a session",
  );
}

function defaultDecision(title, why, confidence, nextAction) {
  return { title, why, confidence, nextAction };
}

function toneForPage(page) {
  if (page.latestEventType === "crawl.page_fail" || page.isOfficial || page.isAdPage) return "gray";
  if ((page.streams || []).length > 0 || page.isPiracyHost || page.flagged || (page.score ?? 0) >= PIRACY_THRESHOLD) return "red";
  if (page.isSuspicious || page.isPlayerPage || (page.score ?? 0) >= 45) return "orange";
  if (page.latestEventType === "crawl.page_start") return "blue";
  return "yellow";
}

function labelForTone(tone) {
  return {
    blue: "Searching",
    yellow: "Investigating",
    orange: "Suspicious",
    red: "Confirmed piracy",
    gray: "Rejected / false positive",
  }[tone] || "Investigating";
}

function nextActionLabelForPage(page) {
  if ((page.streams || []).length > 0) return "Extracting streaming links";
  if (page.isPiracyHost) return "Comparing against known piracy patterns";
  if (page.isPlayerPage) return "Verifying embedded player";
  if (page.isSuspicious) return "Expanding suspicious domain";
  if (page.isOfficial || page.isAdPage) return "Rejecting false positive";
  return "Investigating";
}

function nextActionFromPage(page) {
  if ((page.streams || []).length > 0) return "Escalate to manual review";
  if (page.isPlayerPage) return "Scrape embedded iframe";
  if ((page.links || []).length > 0) return "Follow promising child links";
  if ((page.iframes || []).length > 0) return "Inspect iframe sources";
  return "Continue crawling descendants";
}

function classifyLabel(page) {
  if (page.isOfficial) return "Ignoring likely news article";
  if (page.isPiracyHost) return "Comparing against known piracy patterns";
  if (page.isPlayerPage) return "Verifying embedded player";
  if (page.isSuspicious) return "Expanding suspicious domain";
  return "Investigating";
}

function mapNavigateAction(action, signal) {
  if (action === "continue") {
    if ((signal || "").toLowerCase().includes("redirect")) return "Checking redirect destination";
    return "Following redirect chain -> candidate stream page";
  }
  if (action === "stop") return "Rejecting false positive";
  return signal ? `Checking ${signal}` : "Investigating";
}

function scoreLabel(score) {
  if (score == null) return "Investigating";
  if (score >= PIRACY_THRESHOLD) return "Comparing against known piracy patterns";
  if (score >= 45) return "Expanding suspicious domain";
  return "Ignoring likely news article";
}

function confidenceFromFlags(page) {
  if (page.isOfficial) return "90%";
  if (page.isPiracyHost) return "88%";
  if (page.isPlayerPage || page.isSuspicious) return "76%";
  return "62%";
}

function confidenceFromScore(score, fallback) {
  if (typeof score === "number") {
    return `${clamp(Math.round(score), 10, 99)}%`;
  }
  return `${fallback}%`;
}

function pageCaption(page) {
  if ((page.streams || []).length > 0) return `${page.streams.length} streams`;
  if ((page.iframes || []).length > 0) return `${page.iframes.length} iframes`;
  if ((page.links || []).length > 0) return `${page.links.length} links`;
  return page.stateLabel;
}

function formatNextAction(nextUrls) {
  if (Array.isArray(nextUrls) && nextUrls.length > 0) {
    return `Follow ${truncate(nextUrls[0], 42)}`;
  }
  return "Inspect next child page";
}

function latestEventSubtitle(event) {
  const data = event.data || {};
  return truncate(
    data.url ||
      data.stream_url ||
      data.query ||
      data.reason ||
      data.action ||
      data.context ||
      "Live crawler activity",
    48,
  );
}

function summarizeLLM(events, nowMs) {
  const operationCounts = new Map();
  let totalCalls = 0;
  let rateLimitCount = 0;
  let lastRateLimitWait = 0;
  let cooldownEndsAt = 0;

  for (const event of events) {
    const data = event.data || {};

    if (event.type === "llm.call_start") {
      const operation = prettifyOperation(data.operation);
      operationCounts.set(operation, (operationCounts.get(operation) || 0) + 1);
    }

    if (event.type === "llm.call_ok") {
      totalCalls = Math.max(totalCalls, data.call_count || 0);
    }

    if (event.type === "llm.rate_limit") {
      rateLimitCount += 1;
      lastRateLimitWait = Math.max(lastRateLimitWait, Math.round(data.wait_seconds || 0));
      if (event.ts) {
        const base = Date.parse(event.ts);
        if (!Number.isNaN(base)) {
          cooldownEndsAt = Math.max(cooldownEndsAt, base + ((data.wait_seconds || 0) * 1000));
        }
      }
    }

    if (event.type === "llm.cooldown" && event.ts) {
      const base = Date.parse(event.ts);
      if (!Number.isNaN(base)) {
        cooldownEndsAt = Math.max(cooldownEndsAt, base + (Number(data.wait_seconds || 0) * 1000));
      }
    }
  }

  const sortedOps = [...operationCounts.entries()].sort((a, b) => b[1] - a[1]);
  const primaryOperation = sortedOps[0]?.[0] || "idle";
  const cooldownRemaining = Math.max(0, Math.ceil((cooldownEndsAt - nowMs) / 1000));

  return {
    totalCalls,
    rateLimitCount,
    lastRateLimitWait,
    cooldownRemaining,
    primaryOperation,
  };
}

function normalizePlayers(players) {
  if (!isRecord(players)) return [];
  return Object.entries(players).map(([id, streams]) => ({
    id,
    streams: Array.isArray(streams) ? streams : [],
  }));
}

function prettifyOperation(value) {
  if (!value) return "unknown";
  return String(value).replace(/_/g, " ");
}

function toneFromStatus(status) {
  if (status === "finished") return "green";
  if (status === "failed" || status === "stopped") return "gray";
  return "blue";
}

function addChild(childMap, parentId, childId) {
  if (!parentId || parentId === childId) return;
  if (!childMap.has(parentId)) childMap.set(parentId, []);
  const children = childMap.get(parentId);
  if (!children.includes(childId)) children.push(childId);
}

function findKeyword(events) {
  for (const event of events) {
    const keyword = event.data?.keyword;
    if (keyword) return keyword;
  }
  return "";
}

function hostname(value) {
  if (!value) return "";
  try {
    return new URL(value).hostname.replace(/^www\./, "");
  } catch {
    return value;
  }
}

function truncate(value, size) {
  if (!value) return "";
  return value.length > size ? `${value.slice(0, size - 3)}...` : value;
}

function uniqueStrings(values) {
  return [...new Set(values)];
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function asNumber(value, fallback) {
  return typeof value === "number" ? value : fallback;
}

function coalesceBool(value, fallback) {
  return typeof value === "boolean" ? value : fallback;
}

function isRecord(value) {
  return value && typeof value === "object" && !Array.isArray(value);
}

createRoot(document.getElementById("root")).render(<App />);
