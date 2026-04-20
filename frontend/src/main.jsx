import React, { useEffect, useMemo, useState, useRef } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8080";

const emptyForm = {
  keyword: "",
  api_key: "",
  db_name: "sports_scraper",
  mongo_uri: "mongodb://localhost:27017",
  proxy_url: "",
};

const eventLabels = {
  "session.created":    "Session created",
  "session.finished":   "Session finished",
  "search.start":       "Search started",
  "search.turn_done":   "Search turn",
  "search.complete":    "Search complete",
  "search.candidates":  "Candidates",
  "crawl.tree_start":   "Tree started",
  "crawl.tree_done":    "Tree finished",
  "crawl.page_start":   "Page opened",
  "crawl.page_done":    "Page scored",
  "crawl.page_fail":    "Page failed",
  "llm.navigate":       "LLM navigate",
  "llm.score":          "LLM score",
  "llm.verify_live":    "Verify stream",
  "llm.ad_check":       "Ad check",
  "llm.classify":       "Page classified",
  "stream.found":       "Stream found",
  "stream.rejected":    "Stream rejected",
  "db.node_upserted":   "DB node",
  "db.stream_recorded": "DB stream",
  "runner.finished":    "Runner finished",
  "runner.error":       "Runner error",
  error:                "Crawler error",
};

function App() {
  const [form, setForm]         = useState(emptyForm);
  const [sessions, setSessions] = useState([]);
  const [activeId, setActiveId] = useState("");
  const [events, setEvents]     = useState([]);
  const [selected, setSelected] = useState(null);
  const [busy, setBusy]         = useState(false);
  const [error, setError]       = useState("");
  const feedRef                 = useRef(null);

  useEffect(() => {
    refreshSessions();
    const timer = setInterval(refreshSessions, 3000);
    return () => clearInterval(timer);
  }, []);

  // Auto-scroll event feed to bottom on new events
  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight;
    }
  }, [events]);

  useEffect(() => {
    if (!activeId) return;
    setEvents([]);
    setSelected(null);
    const source = new EventSource(`${API_BASE}/api/sessions/${activeId}/events`);
    source.addEventListener("crawler", (message) => {
      const event = JSON.parse(message.data);
      setEvents((current) => [...current.slice(-499), event]);
    });
    source.onerror = () =>
      setError("Live event stream disconnected. Reopening usually happens automatically.");
    return () => source.close();
  }, [activeId]);

  async function refreshSessions() {
    try {
      const res = await fetch(`${API_BASE}/api/sessions`);
      if (!res.ok) return;
      const data = await res.json();
      setSessions(data);
      if (!activeId && data.length) setActiveId(data[0].id);
    } catch {}
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
      setForm((f) => ({ ...f, keyword: "" }));
      await refreshSessions();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function stopSession(id) {
    const session = sessions.find((s) => s.id === id);
    const dbName  = session?.db_name || form.db_name || "the database";
    const drop    = window.confirm(
      `Also drop the MongoDB database "${dbName}" for this session?\n\nOK = stop + drop DB\nCancel = stop only`
    );
    await fetch(`${API_BASE}/api/sessions/${id}?drop_db=${drop}`, { method: "DELETE" });
    await refreshSessions();
  }

  const active = sessions.find((s) => s.id === activeId);
  const tree   = useMemo(() => buildTree(events), [events]);

  return (
    <main className="shell">
      <section className="topbar">
        <div>
          <p className="eyebrow">spcrawler console</p>
          <h1>Live scraper sessions</h1>
        </div>
        <div className="health">
          <span className="pulse" />
          {active ? active.status : "waiting"}
        </div>
      </section>

      <section className="workspace">
        {/* LEFT — launcher + sessions */}
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
            <h2>Sessions</h2>
            {sessions.length === 0 && <p className="muted">No sessions yet.</p>}
            {sessions.map((s) => (
              <button
                className={`session ${s.id === activeId ? "active" : ""}`}
                key={s.id}
                onClick={() => setActiveId(s.id)}
                type="button"
              >
                <span>{s.keyword}</span>
                <small>{s.status} / {s.events} events</small>
              </button>
            ))}
          </div>
        </aside>

        {/* CENTRE — stats + tree + event feed at bottom */}
        <section className="mainstage">
          <Stats active={active} />
          <TreeCanvas tree={tree} onSelect={setSelected} selected={selected} />

          <div className="event-feed-wrap">
            <h2>Latest events</h2>
            <div className="event-feed-scroll" ref={feedRef}>
              {events.length === 0 && <p className="muted" style={{ padding: "8px 10px" }}>No events yet.</p>}
              {events.slice(-100).map((ev, i) => (
                <button
                  key={`${ev.ts}-${i}`}
                  className={`event-row${selected === ev ? " active" : ""}`}
                  onClick={() => setSelected(ev)}
                  type="button"
                >
                  <span className="event-label">{eventLabels[ev.type] || ev.type}</span>
                  <span className="event-meta">
                    <span className="event-type">{ev.type}</span>
                    {ev.data?.url && <span className="event-url">{shorten(ev.data.url, 55)}</span>}
                  </span>
                </button>
              ))}
            </div>
          </div>
        </section>

        {/* RIGHT — node details inspector */}
        <aside className="inspector">
          <div className="inspect-head">
            <h2>Node details</h2>
            {active && ["running", "starting"].includes(active.status) && (
              <button className="ghost" onClick={() => stopSession(active.id)} type="button">
                Stop
              </button>
            )}
          </div>
          <div className="inspector-scroll">
            {selected ? (
              <NodeDetails node={selected} />
            ) : (
              <p className="muted">Click a node or event to inspect details.</p>
            )}
          </div>
        </aside>
      </section>
    </main>
  );
}

function Stats({ active }) {
  const values = [
    ["Pages",      active?.pages_crawled ?? 0],
    ["Streams",    active?.streams_found ?? 0],
    ["Results",    active?.search_results ?? 0],
    ["Candidates", active?.candidates_registered ?? 0],
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
    </div>
  );
}

function TreeCanvas({ tree, onSelect, selected }) {
  const svgW = tree.width;
  const svgH = tree.height;
  return (
    <div className="canvas">
      {tree.nodes.length === 0 ? (
        <div className="empty">
          <img
            alt=""
            src="https://images.unsplash.com/photo-1516321318423-f06f85e504b3?auto=format&fit=crop&w=900&q=70"
          />
          <p>Start a scraper and the tree will grow as events arrive.</p>
        </div>
      ) : (
        <svg
          className="tree-svg"
          width={svgW}
          height={svgH}
          viewBox={`0 0 ${svgW} ${svgH}`}
        >
          {tree.edges.map((edge) => (
            <path key={edge.id} d={edge.path} className="tree-edge" />
          ))}
          {tree.nodes.map((node) => {
            const isSelected = selected && nodeMatchesEvent(selected, node);
            return (
              <g
                key={node.id}
                className={`tree-node-group${isSelected ? " selected" : ""}`}
                transform={`translate(${node.x},${node.y})`}
                onClick={() => onSelect(node.event)}
                style={{ cursor: "pointer" }}
              >
                <rect
                  rx={8} ry={8}
                  width={NODE_W} height={NODE_H}
                  className={`tree-node-rect kind-${node.kind}${isSelected ? " selected" : ""}${node.flagged ? " flagged" : ""}`}
                />
                <text className="tree-node-kind" x={10} y={16}>{node.kind.toUpperCase()}</text>
                <foreignObject x={8} y={20} width={NODE_W - 16} height={NODE_H - 28}>
                  <div xmlns="http://www.w3.org/1999/xhtml" className="tree-node-inner">
                    <strong className="tree-node-title">{node.title}</strong>
                    <span className="tree-node-sub">{node.subtitle}</span>
                  </div>
                </foreignObject>
                {node.flagged   && <circle cx={NODE_W - 10} cy={10} r={5} className="flag-dot" />}
                {node.hasStream && <circle cx={NODE_W - 22} cy={10} r={5} className="stream-dot" />}
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}

function NodeDetails({ node }) {
  if (!node) return null;

  const type = node.type || "";
  const data = node.data || {};

  const Badge = ({ label, value, pos = "badge-yes", neg = "badge-no" }) => (
    <span className={`badge ${value ? pos : neg}`}>{label}: {value ? "Yes" : "No"}</span>
  );

  const Header = ({ eyebrow, title, url }) => (
    <>
      <p className="eyebrow">{eyebrow}</p>
      {title && <h3 className="detail-title">{title}</h3>}
      {url   && <a className="detail-url" href={url} target="_blank" rel="noreferrer">{url}</a>}
    </>
  );

  if (type === "crawl.page_done" || type === "db.node_upserted") {
    const streams = data.stream_urls || [];
    const iframes = data.iframes    || [];
    const links   = data.links_found;
    return (
      <div className="node-details">
        <Header eyebrow={eventLabels[type] || type} title={data.title || "Untitled page"} url={data.url} />
        <div className="badge-row">
          <Badge label="Flagged"     value={data.flagged}         pos="badge-warn" />
          <Badge label="Player page" value={data.is_player_page}  pos="badge-yes"  />
          <Badge label="Suspicious"  value={data.is_suspicious}   pos="badge-warn" />
          <Badge label="Ad page"     value={data.is_ad_page}      pos="badge-warn" />
        </div>
        <dl className="detail-list">
          <dt>Score</dt>
          <dd><span className={`score-pill ${data.score >= 60 ? "score-high" : data.score >= 30 ? "score-mid" : "score-low"}`}>{data.score ?? "—"} / 100</span></dd>
          <dt>Depth</dt>      <dd>{data.depth ?? "—"}</dd>
          <dt>Parent</dt>     <dd className="detail-url-small">{data.parent_url || "Root"}</dd>
          <dt>Time</dt>       <dd>{node.ts || "—"}</dd>
          <dt>Session</dt>    <dd>{node.session_id || "—"}</dd>
          <dt>Status</dt>     <dd><span className="badge badge-ok">Crawled</span></dd>
          {data.tree_col && <><dt>Tree col</dt><dd className="detail-url-small">{data.tree_col}</dd></>}
        </dl>

        {streams.length > 0 && (
          <div className="detail-section">
            <h4>Stream URLs ({streams.length})</h4>
            <ul className="url-list">
              {streams.map((u, i) => <li key={i}><a href={u} target="_blank" rel="noreferrer">{u}</a></li>)}
            </ul>
          </div>
        )}
        {iframes.length > 0 && (
          <div className="detail-section">
            <h4>Iframes ({iframes.length})</h4>
            <ul className="url-list">
              {iframes.slice(0, 8).map((u, i) => <li key={i}><a href={u} target="_blank" rel="noreferrer">{u}</a></li>)}
              {iframes.length > 8 && <li className="muted">…and {iframes.length - 8} more</li>}
            </ul>
          </div>
        )}
        <div className="detail-section">
          <h4>Child links</h4>
          <p className="muted">{typeof links === "number" ? `${links} links extracted` : "No link data"}</p>
        </div>
      </div>
    );
  }

  if (type === "stream.found") {
    return (
      <div className="node-details">
        <Header eyebrow="Stream found" title="Live stream URL" url={data.stream_url} />
        <dl className="detail-list">
          <dt>Source page</dt> <dd className="detail-url-small">{data.source_url}</dd>
          <dt>Stream type</dt> <dd><span className="badge badge-ok">{data.stream_type || "unknown"}</span></dd>
          <dt>Player ID</dt>   <dd>{data.player_id || "—"}</dd>
          <dt>Score</dt>       <dd><span className={`score-pill ${data.score >= 60 ? "score-high" : "score-mid"}`}>{data.score ?? "—"} / 100</span></dd>
          <dt>Session</dt>     <dd>{node.session_id || "—"}</dd>
          <dt>Time</dt>        <dd>{node.ts || "—"}</dd>
        </dl>
      </div>
    );
  }

  if (type === "crawl.page_fail") {
    return (
      <div className="node-details">
        <Header eyebrow="Page failed" url={data.url} />
        <dl className="detail-list">
          <dt>Reason</dt> <dd><span className="badge badge-warn">{data.reason || "unknown"}</span></dd>
          <dt>Depth</dt>  <dd>{data.depth ?? "—"}</dd>
          <dt>Tree</dt>   <dd className="detail-url-small">{data.tree_col}</dd>
          <dt>Time</dt>   <dd>{node.ts || "—"}</dd>
        </dl>
      </div>
    );
  }

  if (type === "llm.classify") {
    return (
      <div className="node-details">
        <Header eyebrow="Page classification" url={data.url} />
        <div className="badge-row">
          <Badge label="Official"    value={data.is_official}    pos="badge-ok"   />
          <Badge label="Suspicious"  value={data.is_suspicious}  pos="badge-warn" />
          <Badge label="Player page" value={data.is_player_page} pos="badge-yes"  />
          <Badge label="Piracy host" value={data.is_piracy_host} pos="badge-warn" />
        </div>
        <dl className="detail-list">
          <dt>Reason</dt> <dd>{data.reason || "—"}</dd>
          <dt>Time</dt>   <dd>{node.ts || "—"}</dd>
        </dl>
      </div>
    );
  }

  if (type === "crawl.ad_detected" || type === "llm.ad_check") {
    return (
      <div className="node-details">
        <Header eyebrow="Ad detection" url={data.url} />
        <div className="badge-row">
          <Badge label="Has ad"    value={data.has_ad}    pos="badge-warn" />
          <Badge label="Is ad page" value={data.is_ad_page} pos="badge-warn" />
        </div>
        <dl className="detail-list">
          <dt>Ad type</dt> <dd><span className="badge badge-warn">{data.ad_type || "none"}</span></dd>
          <dt>Action</dt>  <dd>{data.action || "none"}</dd>
          {data.wait_seconds > 0 && <><dt>Wait</dt><dd>{data.wait_seconds}s</dd></>}
          {data.selector_hint && <><dt>Selector</dt><dd>{data.selector_hint}</dd></>}
          <dt>Time</dt>    <dd>{node.ts || "—"}</dd>
        </dl>
      </div>
    );
  }

  if (type === "search.candidates") {
    const candidates = data.candidates || [];
    return (
      <div className="node-details">
        <p className="eyebrow">Search candidates</p>
        <h3 className="detail-title">{candidates.length} parent URLs queued</h3>
        <ul className="url-list">
          {candidates.slice(0, 30).map((u, i) => <li key={i}><a href={u} target="_blank" rel="noreferrer">{u}</a></li>)}
          {candidates.length > 30 && <li className="muted">…and {candidates.length - 30} more</li>}
        </ul>
      </div>
    );
  }

  if (type === "llm.score" || type === "llm.navigate") {
    return (
      <div className="node-details">
        <Header eyebrow={eventLabels[type] || type} url={data.url} />
        <dl className="detail-list">
          {data.score != null && <><dt>Score</dt><dd><span className={`score-pill ${data.score >= 60 ? "score-high" : data.score >= 30 ? "score-mid" : "score-low"}`}>{data.score} / 100</span></dd></>}
          {data.reason   && <><dt>Reason</dt>  <dd>{data.reason}</dd></>}
          {data.action   && <><dt>Action</dt>  <dd>{data.action}</dd></>}
          {data.next_url && <><dt>Next URL</dt><dd className="detail-url-small">{data.next_url}</dd></>}
          <dt>Time</dt> <dd>{node.ts || "—"}</dd>
        </dl>
      </div>
    );
  }

  if (type === "crawl.page_start") {
    return (
      <div className="node-details">
        <Header eyebrow="Page opened" url={data.url} />
        <dl className="detail-list">
          <dt>Depth</dt>   <dd>{data.depth ?? "—"}</dd>
          <dt>Parent</dt>  <dd className="detail-url-small">{data.parent_url || "Root"}</dd>
          <dt>Tree</dt>    <dd className="detail-url-small">{data.tree_col || "—"}</dd>
          <dt>Session</dt> <dd>{node.session_id || "—"}</dd>
          <dt>Time</dt>    <dd>{node.ts || "—"}</dd>
        </dl>
      </div>
    );
  }

  // Generic fallback
  return (
    <div className="node-details">
      <p className="eyebrow">{eventLabels[type] || type}</p>
      <dl className="detail-list">
        <dt>Session</dt> <dd>{node.session_id || "—"}</dd>
        <dt>Time</dt>    <dd>{node.ts || "—"}</dd>
        {Object.entries(data).map(([k, v]) => (
          <React.Fragment key={k}>
            <dt>{k}</dt>
            <dd className="detail-val">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
          </React.Fragment>
        ))}
      </dl>
    </div>
  );
}

// ─── Tree layout (top-down, leaves spread left→right) ─────────────────────────

const NODE_W = 180;
const NODE_H = 80;
const H_GAP  = 36;
const V_GAP  = 56;
const PAD_X  = 40;
const PAD_Y  = 40;

function buildTree(events) {
  const nodeMap  = new Map();
  const childMap = new Map();
  const roots    = [];

  for (const ev of events) {
    const id  = nodeId(ev);
    const pid = parentId(ev);

    if (!nodeMap.has(id)) {
      nodeMap.set(id, {
        id,
        event:     ev,
        kind:      ev.type.split(".")[0],
        title:     titleFor(ev),
        subtitle:  subtitleFor(ev),
        flagged:   ev.data?.flagged || ev.data?.score >= 60,
        hasStream: ev.type === "stream.found",
        children:  [],
      });
    } else {
      const n = nodeMap.get(id);
      n.event     = ev;
      n.flagged   = ev.data?.flagged || ev.data?.score >= 60;
      n.hasStream = n.hasStream || ev.type === "stream.found";
    }

    if (pid && pid !== id) {
      if (!childMap.has(pid)) childMap.set(pid, new Set());
      childMap.get(pid).add(id);
    } else {
      if (!roots.includes(id)) roots.push(id);
    }
  }

  for (const [pid, set] of childMap.entries()) {
    const parent = nodeMap.get(pid);
    if (parent) parent.children = [...set];
  }

  // Pass 1: number the leaves left→right in DFS order
  let leafCounter = 0;
  const leafCol   = new Map();

  function assignLeaves(id) {
    const node = nodeMap.get(id);
    if (!node) return;
    const kids = (node.children || []).filter((c) => nodeMap.has(c));
    if (kids.length === 0) {
      leafCol.set(id, leafCounter++);
    } else {
      for (const cid of kids) assignLeaves(cid);
    }
  }
  for (const rid of roots) assignLeaves(rid);

  // Pass 2: x = centre of leaf columns under this node; y = depth * row height
  const positioned = new Map();
  let maxX = 0;
  let maxY = 0;

  function xForNode(id) {
    const node = nodeMap.get(id);
    if (!node) return PAD_X;
    const kids = (node.children || []).filter((c) => nodeMap.has(c));
    if (kids.length === 0) {
      const col = leafCol.get(id) ?? 0;
      return PAD_X + col * (NODE_W + H_GAP);
    }
    const xs = kids.map((c) => xForNode(c));
    return (xs[0] + xs[xs.length - 1]) / 2;
  }

  function place(id, depth) {
    if (positioned.has(id)) return;
    const node = nodeMap.get(id);
    if (!node) return;

    const x = xForNode(id);
    const y = PAD_Y + depth * (NODE_H + V_GAP);
    positioned.set(id, { x, y });
    maxX = Math.max(maxX, x + NODE_W);
    maxY = Math.max(maxY, y + NODE_H);

    const kids = (node.children || []).filter((c) => nodeMap.has(c));
    for (const cid of kids) place(cid, depth + 1);
  }

  for (const rid of roots) place(rid, 0);

  const nodes = [];
  const edges = [];

  for (const [id, pos] of positioned.entries()) {
    const node = nodeMap.get(id);
    if (!node) continue;
    nodes.push({ ...node, x: pos.x, y: pos.y });
  }

  for (const [pid, set] of childMap.entries()) {
    const ppos = positioned.get(pid);
    if (!ppos) continue;
    for (const cid of set) {
      const cpos = positioned.get(cid);
      if (!cpos) continue;
      const x1 = ppos.x + NODE_W / 2;
      const y1 = ppos.y + NODE_H;
      const x2 = cpos.x + NODE_W / 2;
      const y2 = cpos.y;
      const my = (y1 + y2) / 2;
      edges.push({
        id:   `${pid}-${cid}`,
        path: `M ${x1} ${y1} C ${x1} ${my}, ${x2} ${my}, ${x2} ${y2}`,
      });
    }
  }

  return {
    nodes,
    edges,
    width:  Math.max(900, maxX + PAD_X),
    height: Math.max(480, maxY + PAD_Y),
  };
}

function nodeId(ev) {
  const d = ev.data || {};
  if (d.url)        return `page:${d.url}`;
  if (d.stream_url) return `stream:${d.stream_url}`;
  if (d.start_url)  return `tree:${d.start_url}`;
  if (d.tree_col)   return `tree:${d.tree_col}`;
  return `${ev.type}:${ev.session_id || "root"}`;
}

function parentId(ev) {
  const d = ev.data || {};
  if (d.parent_url) return `page:${d.parent_url}`;
  if (d.source_url) return `page:${d.source_url}`;
  // Attach page events to their tree root
  if (
    (ev.type === "crawl.page_start" ||
     ev.type === "crawl.page_done"  ||
     ev.type === "crawl.page_fail") &&
    d.tree_col
  ) {
    return `tree:${d.tree_col}`;
  }
  return null;
}

function nodeMatchesEvent(ev, node) {
  return nodeId(ev) === node.id;
}

function titleFor(ev) {
  const d = ev.data || {};
  if (d.title) return d.title;
  return eventLabels[ev.type] || ev.type;
}

function subtitleFor(ev) {
  const d    = ev.data || {};
  const text = d.url || d.stream_url || d.query || d.keyword || d.context || ev.session_id || "";
  return shorten(text, 40);
}

function shorten(value, size) {
  if (!value) return "";
  return value.length > size ? `${value.slice(0, size - 1)}…` : value;
}

createRoot(document.getElementById("root")).render(<App />);