"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Markdown from "react-markdown";
import {
  F,
  E,
  byId,
  COL,
  LABEL,
  STEPS,
  HIST,
  type DuplicationResult,
  type Finding,
} from "@/lib/data";
import { SESSIONS_WITH_CHARTS, sessionForFinding, type Session } from "@/lib/sessions";
import { findExpert, buildExpertMarkdown } from "@/lib/expert_finder";
import { SessionTranscript } from "./transcript";

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

/* ---- stream item model ---- */
type StreamItem =
  | { kind: "user"; id: number; text: string }
  | { kind: "think"; id: number; revealed: number; done: boolean }
  | { kind: "answer"; id: number; res: DuplicationResult }
  | { kind: "error"; id: number; msg: string };

async function callCheckDuplication(
  hypothesis_text: string,
  forcedId?: string,
): Promise<DuplicationResult> {
  const res = await fetch("/api/check_duplication", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hypothesis_text, forcedId }),
  });
  if (!res.ok) throw new Error("Breadcrumbs API error " + res.status);
  return res.json();
}

export default function Home() {
  const [items, setItems] = useState<StreamItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [input, setInput] = useState("");
  const [highlight, setHighlight] = useState<string[]>([]);
  const [openSession, setOpenSession] = useState<Session | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [activeCat, setActiveCat] = useState<string>("");
  const [navOpen, setNavOpen] = useState(false);

  const streamRef = useRef<HTMLDivElement>(null);
  const idRef = useRef(0);
  const nextId = () => ++idRef.current;

  // keep the stream pinned to the bottom as it grows
  useEffect(() => {
    const s = streamRef.current;
    if (s) s.scrollTop = s.scrollHeight;
  }, [items]);

  // Esc closes the expanded trail
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  // questions grouped by category for the "Explore MOSAIC" sidebar
  const groups = useMemo(() => {
    const g: Record<string, Finding[]> = {};
    F.forEach((f) => {
      (g[f.cat] = g[f.cat] || []).push(f);
    });
    return Object.entries(g);
  }, []);

  const patch = (id: number, next: Partial<StreamItem>) =>
    setItems((prev) => prev.map((it) => (it.id === id ? ({ ...it, ...next } as StreamItem) : it)));

  async function ask(text: string, forcedId?: string) {
    if (busy) return;
    setBusy(true);
    setHighlight([]);

    // ── expert-finder intent (client-side, mirrors lib/duplication.ts) ──
    // Falls through silently when the question isn't an expert-finder question,
    // so sidebar clicks (forcedId) and normal hypotheses are unaffected.
    const expert = findExpert(text);
    if (expert && !forcedId) {
      setItems((prev) => [...prev, { kind: "user", id: nextId(), text }]);
      await wait(400);

      const thinkId = nextId();
      setItems((prev) => [...prev, { kind: "think", id: thinkId, revealed: 0, done: false }]);
      // Reuse the existing thinking animation; v1 keeps STEPS as-is.
      for (let i = 0; i < STEPS.length; i++) {
        await wait(350);
        patch(thinkId, { revealed: i + 1 });
      }
      patch(thinkId, { revealed: STEPS.length, done: true });

      setItems((prev) => [
        ...prev,
        {
          kind: "answer",
          id: nextId(),
          res: {
            verdict: "match",
            matches: [],
            searched: F.length,
            markdown: buildExpertMarkdown(text, expert),
          },
        },
      ]);
      setHighlight([]);
      setBusy(false);
      return;
    }
    // ────────────────────────────────────────────────────────────────────

    setItems((prev) => [...prev, { kind: "user", id: nextId(), text }]);
    await wait(400);

    const thinkId = nextId();
    setItems((prev) => [...prev, { kind: "think", id: thinkId, revealed: 0, done: false }]);

    // fire the call and animate in parallel — real backend latency just fills the animation
    const pending = callCheckDuplication(text, forcedId).catch(
      (err) => ({ __error: String(err) }) as unknown as DuplicationResult,
    );

    for (let i = 0; i < STEPS.length; i++) {
      await wait(500);
      patch(thinkId, { revealed: i + 1 });
    }

    const res = await pending;
    patch(thinkId, { revealed: STEPS.length, done: true });

    const err = (res as unknown as { __error?: string }).__error;
    if (err) {
      setItems((prev) => [...prev, { kind: "error", id: nextId(), msg: err }]);
      setHighlight([]);
    } else {
      setItems((prev) => [...prev, { kind: "answer", id: nextId(), res }]);
      // light the trail one marker at a time so it draws itself in
      const ids = (res.matches ?? []).map((mm) => mm.id).filter((id) => byId[id]);
      setHighlight([]);
      for (let k = 0; k < ids.length; k++) {
        await wait(260);
        setHighlight(ids.slice(0, k + 1));
      }
    }
    setBusy(false);
  }

  const submit = () => {
    const t = input.trim();
    if (!t) return;
    setInput("");
    ask(t);
  };

  const gstatus = highlight.length
    ? `// ${highlight.length} marker${highlight.length > 1 ? "s" : ""} on your path`
    : "// your org's explored ground";

  // Explore MOSAIC: pick a topic on the left, its questions surface as chips in the chat
  const currentCat = activeCat || groups[0]?.[0] || "";
  const catList = groups.find(([c]) => c === currentCat)?.[1] ?? [];

  return (
    <div className={"app" + (navOpen ? " navopen" : "")}>
      {/* ================= SIDEBAR ================= */}
      <aside className={"col side" + (navOpen ? "" : " min")}>
        <button
          className="navtoggle"
          onClick={() => setNavOpen((v) => !v)}
          aria-label={navOpen ? "Collapse menu" : "Expand menu"}
        >
          {navOpen ? "«" : "☰"}
        </button>
        {navOpen && (
          <>
        <div className="brand">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
            <path
              d="M3 20 C7 14, 5 9, 11 6 S 19 5, 21 3"
              stroke="#9BA694"
              strokeWidth="2"
              strokeDasharray="2 5"
              strokeLinecap="round"
            />
            <circle cx="3" cy="20" r="2.4" fill="#657262" />
            <circle cx="11" cy="6" r="3" fill="#C6862B" />
            <circle cx="21" cy="3" r="2.4" fill="#A7AFA2" />
          </svg>
          Breadcrumbs
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img className="brand-logo" src="/owkin-logo.svg" alt="Owkin" />
        </div>

        <div className="side-sub">Explore MOSAIC</div>
        <div className="catlist">
          {groups.map(([cat]) => (
            <button
              className={"catbtn" + (cat === currentCat ? " on" : "")}
              key={cat}
              onClick={() => setActiveCat(cat)}
            >
              {cat}
            </button>
          ))}
        </div>

        <div className="side-sub">Session history</div>
        <div>
          {HIST.map((h, i) => (
            <div className="hist" key={i}>
              <div className="ht">{h.t}</div>
              <div className="hm">{h.m}</div>
            </div>
          ))}
        </div>
        <div className="side-sub">Replay a real run</div>
        <div>
          {SESSIONS_WITH_CHARTS.map((s) => (
            <button className="hist histbtn" key={s.id} onClick={() => setOpenSession(s)}>
              <div className="ht">{s.title || "K Pro session"}</div>
              <div className="hm">
                {s.counts.plots} chart{s.counts.plots === 1 ? "" : "s"} · {s.counts.tables} table
                {s.counts.tables === 1 ? "" : "s"}
                {s.counts.omitted ? ` · ${s.counts.omitted} omitted` : ""}
              </div>
            </button>
          ))}
        </div>
          </>
        )}
      </aside>

      {/* ================= CHAT ================= */}
      <main className="col chat">
        <div className="chat-head">
          <h2>Retrace</h2>
          <div className="sesstag">
            <i />New session · Dr. Chen · nothing in context
          </div>
        </div>

        <div className="stream" ref={streamRef}>
          {items.length === 0 && (
            <div className="empty">
              <h3>Ask before you run.</h3>
              <p>
                Breadcrumbs checks your organization&apos;s own trail first — including what
                colleagues abandoned — before it checks the published world.
              </p>
            </div>
          )}

          {items.map((it) => {
            if (it.kind === "user") {
              return (
                <div className="msg user" key={it.id}>
                  <div className="bub">{it.text}</div>
                </div>
              );
            }
            if (it.kind === "think") return <ThinkBlock key={it.id} item={it} />;
            if (it.kind === "error") {
              return (
                <div className="msg" key={it.id}>
                  <div className="ans">
                    <div className="verdict open">
                      <span className="vm" />
                      Couldn&apos;t reach the trail
                    </div>
                    <div className="card openp">
                      <div className="q">The Breadcrumbs server didn&apos;t respond.</div>
                      <div className="e mono" style={{ fontSize: 12 }}>
                        {it.msg}
                      </div>
                    </div>
                  </div>
                </div>
              );
            }
            return <AnswerBlock key={it.id} res={it.res} onOpenSession={setOpenSession} />;
          })}
        </div>

        <div className="composer">
          <div className="chiprow">
            <span className="chiprow-lab">{currentCat}</span>
            {catList.map((f) => (
              <button className="qchip" key={f.id} onClick={() => ask(f.q, f.id)}>
                {f.q.length > 62 ? f.q.slice(0, 62) + "…" : f.q}
              </button>
            ))}
          </div>
          <div className="cwrap">
            <input
              className="cin"
              placeholder="Ask a research question…"
              autoComplete="off"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submit();
              }}
            />
            <button className="cbtn" onClick={submit} disabled={busy}>
              Retrace
            </button>
          </div>
        </div>
      </main>

      {/* ================= GRAPH ================= */}
      <aside className="col gcol">
        <div className="gh">
          <h3>The trail</h3>
          <div className="gh-r">
            <span className="gc">22 markers</span>
            <button
              className="gexpand"
              onClick={() => setExpanded(true)}
              title="Expand trail to fullscreen"
              aria-label="Expand trail to fullscreen"
            >
              <span className="gexpand-ic">⤢</span> Fullscreen
            </button>
          </div>
        </div>
        <div className="gsub">{gstatus}</div>

        <TrailGraph highlight={highlight} onPick={(f) => ask(f.q, f.id)} />

        <div className="legend">
          <div className="lrow">
            <span className="d" style={{ background: "#657262" }} />
            confirmed finding
          </div>
          <div className="lrow">
            <span className="d" style={{ background: "#C6862B" }} />
            matches your question
          </div>
          <div className="lrow">
            <span className="d" style={{ background: "#3E7A5E" }} />
            in progress right now
          </div>
          <div className="lrow">
            <span className="d" style={{ background: "#A7AFA2" }} />
            abandoned — dead end
          </div>
        </div>

        <div className="stats">
          <div className="stat">
            <b>19</b>
            <span>confirmed</span>
          </div>
          <div className="stat">
            <b>2</b>
            <span>in flight</span>
          </div>
          <div className="stat">
            <b>2</b>
            <span>dead ends</span>
          </div>
        </div>
      </aside>

      {openSession && (
        <SessionTranscript session={openSession} onClose={() => setOpenSession(null)} />
      )}

      {/* ================= FULLSCREEN TRAIL ================= */}
      {expanded && (
        <div className="trailmodal" onClick={() => setExpanded(false)}>
          <div className="tm-inner" onClick={(e) => e.stopPropagation()}>
            <div className="tm-head">
              <h3>The trail</h3>
              <div className="gsub">{gstatus}</div>
            </div>
            <button className="tm-close" onClick={() => setExpanded(false)} aria-label="Close">
              ✕
            </button>
            <div className="tm-body">
              <TrailGraph highlight={highlight} onPick={(f) => ask(f.q, f.id)} large />
            </div>
            <div className="tm-legend">
              <span className="lrow">
                <span className="d" style={{ background: "#657262" }} />confirmed
              </span>
              <span className="lrow">
                <span className="d" style={{ background: "#C6862B" }} />matches your question
              </span>
              <span className="lrow">
                <span className="d" style={{ background: "#3E7A5E" }} />in progress
              </span>
              <span className="lrow">
                <span className="d" style={{ background: "#A7AFA2" }} />abandoned — dead end
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ---------------- thinking block ---------------- */
function ThinkBlock({ item }: { item: Extract<StreamItem, { kind: "think" }> }) {
  return (
    <div className="msg">
      <div className="think">
        <div className="think-h">
          {item.done ? <span className="dot" /> : <span className="spin" />}
          {item.done ? "TRAIL RETRACED" : "RETRACING YOUR STEPS…"}
        </div>
        <div>
          {STEPS.slice(0, item.revealed).map((s, i) => (
            <div className={"tstep" + (item.done || i < item.revealed - 1 ? " done" : "")} key={i}>
              {s}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ---------------- answer block ---------------- */
function AnswerBlock({
  res,
  onOpenSession,
}: {
  res: DuplicationResult;
  onOpenSession: (s: Session) => void;
}) {
  // Any matched finding that was actually answered in a stored K Pro run gets a link to replay
  // that run — the duplication check says "someone did this"; the transcript shows what they saw.
  const runs: { label: string; session: Session }[] = [];
  const seen = new Set<string>();
  (res.matches ?? []).forEach((m) => {
    const session = sessionForFinding(m.hypothesis_text);
    if (session && !seen.has(session.id)) {
      seen.add(session.id);
      runs.push({ label: m.id, session });
    }
  });

  const runLinks = runs.length ? (
    <div className="runlinks">
      {runs.map(({ label, session }) => (
        <button className="runlink" key={session.id} onClick={() => onOpenSession(session)}>
          <span className="runlink-ic" aria-hidden>
            ▸
          </span>
          View the actual K Pro run
          <span className="runlink-meta mono">
            {label} · {session.counts.plots} chart{session.counts.plots === 1 ? "" : "s"}
          </span>
        </button>
      ))}
    </div>
  ) : null;

  // When the backend sends a markdown write-up, render it as the answer body.
  if (res.markdown) {
    return (
      <div className="msg">
        <div className="ans">
          <div className="md">
            <Markdown>{res.markdown}</Markdown>
          </div>
          {runLinks}
        </div>
      </div>
    );
  }

  const open = res.verdict === "open" || !res.matches?.length;

  if (open) {
    return (
      <div className="msg">
        <div className="ans">
          <div className="verdict open">
            <span className="vm" />
            No markers on this path — you&apos;re the first here
          </div>
          <div className="card openp">
            <div className="ch">
              <span>open trail</span>
              <span className="cid">no prior internal work</span>
            </div>
            <div className="q">
              Nothing on your org&apos;s trail matches this, and nothing in the published record
              searched.
            </div>
            <div className="e">
              This path looks open. As you explore it, Breadcrumbs drops a marker — so the next
              person who asks finds <em>you</em>.
            </div>
          </div>
          <div className="calib">
            {"// Breadcrumbs doesn't cry duplicate. When a path is new, it says so — and starts mapping it."}
          </div>
        </div>
      </div>
    );
  }

  const m = res.matches;
  const n = m.length;
  return (
    <div className="msg">
      <div className="ans">
        <div className="verdict">
          <span className="vm" />
          {n} marker{n > 1 ? "s" : ""} on your trail — you&apos;re not the first here
        </div>

        {m.map((f, i) => {
          const L = LABEL[f.status] || LABEL.confirmed;
          const tag = i === 0 ? L.tag : f.status === "confirmed" ? "related work" : L.tag;
          return (
            <div className={`card ${L.cls}`} key={f.id + "-" + i}>
              <div className="ch">
                <span>{tag}</span>
                <span className="cid">
                  {f.id} · {f.author || ""} {f.disease ? "· " + f.disease : ""}
                </span>
              </div>
              <div className="q">{f.hypothesis_text}</div>
              {f.effect && <div className="e" dangerouslySetInnerHTML={{ __html: f.effect }} />}
              {f.reason && <div className="r" dangerouslySetInnerHTML={{ __html: f.reason }} />}
            </div>
          );
        })}

        {res.external && (
          <div className="ext">
            <b>published record</b>
            <span dangerouslySetInnerHTML={{ __html: res.external }} />
          </div>
        )}
        {runLinks}
        <div className="calib">
          {"// no claim of novelty — Breadcrumbs reports what it found on your trail, and what it didn't."}
          <br />
          {`// internal checked first · ${res.searched ?? "—"} markers searched · external second`}
        </div>
      </div>
    </div>
  );
}

/* ---------------- trail graph ---------------- */
// a cairn — the stacked-stone marker hikers leave to mark a trail. Three
// stones, centered on (x,y) so the graph edges still meet it cleanly.
function Cairn({ x, y, s, fill }: { x: number; y: number; s: number; fill: string }) {
  return (
    <g>
      <ellipse cx={x} cy={y + s * 0.95} rx={s} ry={s * 0.55} fill={fill} />
      <ellipse cx={x} cy={y} rx={s * 0.72} ry={s * 0.46} fill={fill} />
      <ellipse cx={x} cy={y - s * 0.9} rx={s * 0.46} ry={s * 0.37} fill={fill} />
    </g>
  );
}

// scenery — faint pines that turn the abstract graph into a trail through woods
function Pine({ x, y, s }: { x: number; y: number; s: number }) {
  return (
    <g className="pine">
      <path d={`M${x} ${y - s} L${x + s * 0.6} ${y - s * 0.2} L${x - s * 0.6} ${y - s * 0.2} Z`} fill="#33472a" />
      <path d={`M${x} ${y - s * 0.5} L${x + s * 0.82} ${y + s * 0.5} L${x - s * 0.82} ${y + s * 0.5} Z`} fill="#3d5632" />
      <rect x={x - s * 0.1} y={y + s * 0.5} width={s * 0.2} height={s * 0.36} fill="#4a3922" />
    </g>
  );
}

// topographic contour lines — gentle waves across the map read as terrain
const CONTOURS = [110, 175, 240, 305, 370, 435].map((baseY, k) => {
  const amp = 9 + (k % 3) * 4;
  const phase = k * 42;
  let d = `M -20 ${baseY}`;
  for (let x = 30; x <= 400; x += 55) {
    const cy = baseY + Math.sin((x + phase) / 55) * amp;
    const cx = x - 27;
    const ccy = baseY + Math.sin((x - 27 + phase) / 55) * amp;
    d += ` Q ${cx} ${ccy} ${x} ${cy}`;
  }
  return d;
});

// pines tucked into the negative space, away from the marker cluster
const PINES: [number, number, number][] = [
  [24, 448, 11], [62, 476, 14], [112, 460, 9], [166, 486, 13],
  [222, 468, 10], [278, 488, 14], [322, 458, 9], [356, 436, 12],
  [142, 508, 10], [244, 508, 9], [360, 118, 9], [20, 130, 10],
];

function TrailTerrain() {
  return (
    <g className="terrain">
      <rect x="0" y="0" width="380" height="520" fill="url(#tmglow)" />
      {CONTOURS.map((d, i) => (
        <path key={i} d={d} className="contour" />
      ))}
      {PINES.map(([x, y, s], i) => (
        <Pine key={i} x={x} y={y} s={s} />
      ))}
    </g>
  );
}

function TrailGraph({
  highlight,
  onPick,
  large = false,
}: {
  highlight: string[];
  onPick: (f: Finding) => void;
  large?: boolean;
}) {
  const active = highlight.length > 0;
  const hset = new Set(highlight);
  // anchor the hover card to the marker's real on-screen position (cx,cy) so it
  // can be viewport-fixed — that keeps it from being clipped by the scrolling panel.
  const [hover, setHover] = useState<{ f: Finding; cx: number; cy: number } | null>(null);
  // grace delay so moving the cursor from the marker onto the card (across the
  // gap) doesn't close it — and so the card itself can be hovered & scrolled.
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelClose = () => {
    if (closeTimer.current) {
      clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  };
  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = setTimeout(() => setHover(null), 160);
  };

  // the retrace path: thread the matched markers in reveal order — grows
  // one segment at a time as `highlight` fills in.
  const tpts = highlight.map((id) => byId[id]).filter(Boolean);
  const trailD = tpts.map((p, i) => `${i ? "L" : "M"} ${p.x} ${p.y}`).join(" ");

  return (
    <div className="graphwrap">
    <svg className={"graph" + (large ? " large" : "")} viewBox="0 0 380 520">
      <defs>
        <radialGradient id="tmglow" cx="45%" cy="34%" r="82%">
          <stop offset="0%" stopColor="#aeb9a4" stopOpacity="0.12" />
          <stop offset="62%" stopColor="#aeb9a4" stopOpacity="0" />
        </radialGradient>
      </defs>
      <TrailTerrain />
      {E.map(([a, b, r], i) => {
        const A = byId[a];
        const B = byId[b];
        const dup = r === "duplicate_of";
        const dim = active && !(hset.has(a) && hset.has(b));
        return (
          <line
            key={i}
            className={"gedge" + (dim ? " dim" : "")}
            x1={A.x}
            y1={A.y}
            x2={B.x}
            y2={B.y}
            stroke={dup ? "#7d8a78" : "#3d4a3f"}
            strokeWidth={dup ? 1.5 : 1}
            strokeDasharray={dup ? "3 3" : undefined}
          />
        );
      })}

      {/* the breadcrumb trail lighting up through the matched markers */}
      {tpts.length > 1 && (
        <>
          <path className="trailglow" d={trailD} />
          <path className="trailpath" d={trailD} />
        </>
      )}

      {F.map((f) => {
        const on = hset.has(f.id);
        const dim = active && !on;
        const abandoned = f.st === "abandoned";
        const live = f.st === "in_progress";
        const fill = on ? (abandoned ? COL.abandoned : COL.match) : COL[f.st];
        const s = on ? 3.5 : live ? 3.0 : 2.7;
        return (
          <g
            className={"gnode" + (dim ? " dim" : "")}
            key={f.id}
            onClick={() => onPick(f)}
            onMouseEnter={(e) => {
              const r = e.currentTarget.getBoundingClientRect();
              cancelClose();
              setHover({ f, cx: r.left + r.width / 2, cy: r.top + r.height / 2 });
            }}
            onMouseLeave={scheduleClose}
          >
            {/* invisible hit target — bigger than the cairn so hover is easy to land */}
            <circle cx={f.x} cy={f.y} r={11} fill="transparent" />
            <circle className="hoverhalo" cx={f.x} cy={f.y} r={7.5} fill={on ? COL.match : "#9BA694"} />
            {(on || live) && (
              <circle
                className="ring"
                cx={f.x}
                cy={f.y}
                r={6}
                fill={on ? COL.match : COL.in_progress}
              />
            )}
            <Cairn x={f.x} y={f.y} s={s} fill={fill} />
            {abandoned && (
              <path
                d={`M${f.x - 2.3} ${f.y - 2.3} l4.6 4.6 M${f.x + 2.3} ${f.y - 2.3} l-4.6 4.6`}
                stroke="#121A12"
                strokeWidth={1.2}
                strokeLinecap="round"
              />
            )}
            <text x={f.x} y={f.y + 9.5} textAnchor="middle" className={"glabel" + (on ? " lit" : "")}>
              {f.id}
            </text>
          </g>
        );
      })}
    </svg>

    {hover && (
      <NodeCard
        hover={hover}
        large={large}
        onMouseEnter={cancelClose}
        onMouseLeave={scheduleClose}
      />
    )}
    </div>
  );
}

/* ---------------- node hover card ----------------
   The proof-of-data panel for the demo: hover any marker and see the full
   record behind it — the fields the UI renders, plus the raw JSON payload.
   Viewport-fixed and clamped so it never spills off-screen or gets clipped
   by the scrolling trail panel. */
function NodeCard({
  hover,
  large,
  onMouseEnter,
  onMouseLeave,
}: {
  hover: { f: Finding; cx: number; cy: number };
  large: boolean;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
}) {
  const { f, cx, cy } = hover;
  const vw = typeof window !== "undefined" ? window.innerWidth : 1280;
  const vh = typeof window !== "undefined" ? window.innerHeight : 800;
  const cardW = large ? 400 : 340;
  const estH = 380; // upper bound; the JSON block scrolls if content is taller
  const gap = 16;

  // sit to the left of the marker if the right side would overflow
  const flipX = cx + gap + cardW > vw - 12;
  let left = flipX ? cx - gap - cardW : cx + gap;
  let top = cy - 44;
  // clamp fully inside the viewport
  left = Math.max(12, Math.min(left, vw - cardW - 12));
  top = Math.max(12, Math.min(top, vh - estH - 12));

  const record = {
    id: f.id,
    status: f.st,
    disease: f.dis,
    category: f.cat,
    author: f.au,
    entities: f.ent,
    hypothesis_text: f.q,
    effect: f.eff,
    ...(f.rz ? { reason_abandoned: f.rz } : {}),
    coords: { x: f.x, y: f.y },
  };

  return (
    <div
      className={"nodecard" + (large ? " nc-large" : "")}
      style={{ left, top, width: cardW }}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      <div className="nc-head">
        <span className={"nc-badge nc-" + f.st}>{f.st.replace("_", " ")}</span>
        <span className="nc-id mono">{f.id}</span>
      </div>
      <div className="nc-q">{f.q}</div>
      <div className="nc-grid mono">
        <span>disease</span>
        <b>{f.dis}</b>
        <span>author</span>
        <b>{f.au}</b>
        <span>category</span>
        <b>{f.cat}</b>
        <span>entities</span>
        <b>{f.ent.length ? f.ent.join(", ") : "—"}</b>
      </div>
      <div className="nc-eff">{f.eff}</div>
      {f.rz && <div className="nc-rz">⚑ {f.rz}</div>}
      <div className="nc-json-lab mono">raw record</div>
      <pre className="nc-json mono">{JSON.stringify(record, null, 2)}</pre>
    </div>
  );
}
