import { useEffect, useMemo, useRef, useState } from "react"
import { EyeOff, Plus } from "lucide-react"

import type { ExchangeView as XView, Trade, WireMessage } from "@/api"
import { INKS, bare, short } from "@/components/bits"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"

const NODE = 17
const CELL_W = 300 // one trade per cell
const CELL_H = 260
const SPOT_W = 150 // one waiting listing per spot
const SPOT_H = 84

/** `ink` indexes INKS; null is a listing still waiting, drawn as a clear glass dot. */
type Placed = { handle: string; x: number; y: number; ink: number | null; label: string; faded: boolean; you: boolean; above: boolean }
type Halo = { id: string; x: number; y: number; r: number; label: string; done: boolean }

function clip(label: string, n: number) {
  return label.length > n ? `${label.slice(0, n - 1)}…` : label
}

/** A grid, no physics: trades in cells (a ring per trade), waiting listings in a row of spots
 *  below. Item labels sit under their node (above for the top node), so nothing overlaps. */
function layout(view: XView, me: string | null, cols: number) {
  const latest = new Map<string, Trade>()
  for (const t of view.trades) if (t.state !== "unwound") for (const m of t.members) latest.set(m.handle, t)
  const clusters = view.trades.filter(
    (t) => t.state !== "unwound" && t.members.every((m) => latest.get(m.handle)?.id === t.id),
  )
  const W = cols * CELL_W
  const placed = new Map<string, Placed>()
  const halos: Halo[] = []
  clusters.forEach((t, ci) => {
    const cx = (ci % cols) * CELL_W + CELL_W / 2
    const cy = Math.floor(ci / cols) * CELL_H + CELL_H / 2 + 6
    const n = t.members.length
    const r = n === 2 ? 62 : 78
    const done = t.state === "handed_off"
    halos.push({ id: t.id, x: cx, y: cy, r: r + 44, done,
      label: `${t.id} · ${n}-${t.shape === "chain" ? "chain" : "way"}${done ? " · done" : ""}` })
    const meIndex = t.members.findIndex((m) => m.handle === me)
    t.members.forEach((m, i) => {
      const b = (n === 2 ? Math.PI : -Math.PI / 2) + (i * 2 * Math.PI) / n
      const ink = (meIndex >= 0 ? (i - meIndex + n) % n : i + 1) % INKS.length
      const node = view.nodes.find((x) => x.handle === m.handle)
      const y = cy + r * Math.sin(b)
      placed.set(m.handle, {
        handle: m.handle, x: cx + r * Math.cos(b), y, ink,
        label: clip(node?.have ? bare(node.have.label) : "nothing", 17), faded: done, you: m.handle === me,
        above: y < cy - 10,
      })
    })
  })
  const top = Math.ceil(clusters.length / cols) * CELL_H + (clusters.length ? 10 : 0)
  const rest = view.nodes.filter((n) => !placed.has(n.handle))
  const per = Math.floor(W / SPOT_W)
  rest.forEach((n, i) => {
    placed.set(n.handle, {
      handle: n.handle,
      x: (i % per) * SPOT_W + SPOT_W / 2,
      y: top + Math.floor(i / per) * SPOT_H + 30,
      ink: n.handle === me ? 0 : null,
      label: clip(n.have ? bare(n.have.label) : `wants ${n.wants[0]?.replace(/\s*\(.*$/, "") ?? "?"}`, 18),
      faded: n.status === "done",
      you: n.handle === me,
      above: false,
    })
  })
  const H = Math.max(top + Math.ceil(rest.length / per) * SPOT_H + (rest.length ? 0 : 10), 200)
  return { placed, clusters, halos, W, H, waitingTop: rest.length ? top : null }
}

function useNarrow() {
  const query = "(max-width: 640px)"
  const [narrow, setNarrow] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mq = window.matchMedia(query)
    const on = () => setNarrow(mq.matches)
    mq.addEventListener("change", on)
    return () => mq.removeEventListener("change", on)
  }, [])
  return narrow
}

export function ExchangeView({
  view,
  wire,
  me,
  onMarket,
  busy,
}: {
  view: XView | null
  wire: WireMessage[]
  me: string | null
  onMarket: () => void
  busy: boolean
}) {
  const narrow = useNarrow()
  const empty: XView = { generation: 0, nodes: [], trades: [], stats: { agents: 0, listings: 0, loops: 0 } }
  const { placed, clusters, halos, W, H, waitingTop } = useMemo(
    () => layout(view ?? empty, me, narrow ? 1 : 3),
    [view, me, narrow], // eslint-disable-line react-hooks/exhaustive-deps
  )
  const [onlyReveals, setOnlyReveals] = useState(false)
  const shown = onlyReveals ? wire.filter((m) => m.kind === "identity") : wire
  const reveals = wire.filter((m) => m.kind === "identity").length
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => {
    // scroll the log, not the page: scrollIntoView also scrolled the window, so a phone opened
    // the landing page halfway down
    const log = end.current?.closest("[data-slot=scroll-area-viewport]")
    if (log) log.scrollTop = log.scrollHeight
  }, [shown.length])

  const stats = view?.stats ?? { agents: 0, listings: 0, loops: 0 }
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-3xl leading-none font-bold">
            Exchange <span className="text-emerald-300">· live</span>
          </h2>
          <p className="mt-1.5 text-sm text-slate-400">
            What every agent posted, under random handles, and every message that crossed between agents. Nothing else.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Stat label="agents" value={stats.agents} />
          <Stat label="listings" value={stats.listings} />
          <Stat label="loops found" value={stats.loops} accent />
          <Button variant="outline" size="sm" disabled={busy} onClick={onMarket}>
            <Plus className="size-4" /> Busy market
          </Button>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] xl:items-start">
        <Card className="gap-1 p-2">
          <svg
            viewBox={`0 -26 ${W} ${H + 30}`}
            className="h-auto w-full"
            role="img"
            aria-label={`${stats.listings} listings on the exchange, ${clusters.length} trades drawn as loops`}
          >
            <defs>
              {/* one arrowhead per ink: a marker does not take its path's stroke colour */}
              {INKS.map((ink, i) => (
                <marker key={i} id={`xhead-${i}`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="10" markerHeight="10" orient="auto" markerUnits="userSpaceOnUse">
                  <path d="M0,0 L10,5 L0,10 z" fill={ink.fill} />
                </marker>
              ))}
              <filter id="soft-glow" x="-50%" y="-50%" width="200%" height="200%">
                <feGaussianBlur stdDeviation="4" />
              </filter>
              <radialGradient id="ring-fill">
                <stop offset="0%" stopColor="rgb(52,211,153)" stopOpacity="0.10" />
                <stop offset="70%" stopColor="rgb(45,212,191)" stopOpacity="0.04" />
                <stop offset="100%" stopColor="rgb(255,255,255)" stopOpacity="0.02" />
              </radialGradient>
            </defs>
            {waitingTop != null && (
              <text x={8} y={waitingTop + 2} fill="#94a3b8" style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 700, letterSpacing: "0.08em" }}>
                WAITING FOR A MATCH
              </text>
            )}
            {halos.map((h) => (
              <g key={h.id} className="appear" style={{ transformOrigin: `${h.x}px ${h.y}px` }} opacity={h.done ? 0.55 : 1}>
                {!h.done && (
                  <circle cx={h.x} cy={h.y} r={h.r} fill="none" stroke="rgb(52,211,153)" strokeOpacity="0.5" strokeWidth="6" filter="url(#soft-glow)" />
                )}
                <circle
                  cx={h.x}
                  cy={h.y}
                  r={h.r}
                  fill="url(#ring-fill)"
                  stroke={h.done ? "rgba(255,255,255,0.3)" : "rgba(110,231,183,0.7)"}
                  strokeWidth="1.5"
                  strokeDasharray={h.done ? "5 5" : undefined}
                />
                <circle cx={h.x} cy={h.y} r={h.r - 3} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="1" />
                <text
                  x={h.x}
                  y={h.y - h.r}
                  dy="-6"
                  textAnchor="middle"
                  fill={h.done ? "#94a3b8" : "#a7f3d0"}
                  style={{ fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 700 }}
                >
                  {h.label}
                </text>
              </g>
            ))}
            {clusters.map((t) =>
              t.members.map((m, i) => {
                if (t.shape === "chain" && i === t.members.length - 1) return null
                const a = placed.get(m.handle)!
                const b = placed.get(t.members[(i + 1) % t.members.length].handle)!
                const dx = b.x - a.x
                const dy = b.y - a.y
                const len = Math.hypot(dx, dy) || 1
                const bend = t.members.length === 2 ? 18 : 0
                const sx = a.x + (dx / len) * (NODE + 5)
                const sy = a.y + (dy / len) * (NODE + 5)
                const ex = b.x - (dx / len) * (NODE + 6)
                const ey = b.y - (dy / len) * (NODE + 6)
                const mx = (sx + ex) / 2 - (dy / len) * bend
                const my = (sy + ey) / 2 + (dx / len) * bend
                const d = `M ${sx} ${sy} Q ${mx} ${my} ${ex} ${ey}`
                const ink = a.ink ?? 0
                return (
                  <g key={`${t.id}-${m.handle}`} opacity={t.state === "handed_off" ? 0.45 : 1}>
                    <path d={d} fill="none" stroke={INKS[ink].fill} strokeOpacity="0.45" strokeWidth="7" strokeLinecap="round" filter="url(#soft-glow)" />
                    <path d={d} fill="none" stroke="rgba(3,7,18,0.85)" strokeWidth="5" strokeLinecap="round" />
                    <path
                      d={d}
                      fill="none"
                      stroke={INKS[ink].fill}
                      strokeWidth="2.5"
                      strokeLinecap="round"
                      markerEnd={`url(#xhead-${ink})`}
                      className={t.state === "handed_off" ? undefined : "flow"}
                    />
                  </g>
                )
              }),
            )}
            {[...placed.values()].map((p) => {
              const ink = p.ink == null ? null : INKS[p.ink]
              return (
                <g key={p.handle} opacity={p.faded ? 0.5 : 1} className="appear" style={{ transformOrigin: `${p.x}px ${p.y}px` }}>
                  {ink && <circle cx={p.x} cy={p.y} r={NODE + 2} fill={ink.fill} opacity="0.5" filter="url(#soft-glow)" />}
                  <circle cx={p.x} cy={p.y} r={NODE + 4} fill="none" stroke="rgba(255,255,255,0.16)" strokeWidth="1" />
                  <circle
                    cx={p.x}
                    cy={p.y}
                    r={NODE}
                    fill={ink ? ink.fill : "rgba(255,255,255,0.10)"}
                    stroke={ink ? "rgba(255,255,255,0.55)" : "rgba(255,255,255,0.38)"}
                    strokeWidth="1.25"
                  />
                  <text
                    x={p.x}
                    y={p.y}
                    dy="0.35em"
                    textAnchor="middle"
                    fill={ink ? ink.text : "#f8fafc"}
                    style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: p.you ? 11 : 12 }}
                  >
                    {p.you ? "You" : short(p.handle)}
                  </text>
                  <text
                    x={p.x}
                    y={p.above ? p.y - NODE - 9 : p.y + NODE + 18}
                    textAnchor="middle"
                    fill="#e2e8f0"
                    style={{ fontSize: 13, fontWeight: 600, paintOrder: "stroke", stroke: "#030712", strokeWidth: 4, strokeLinejoin: "round" }}
                  >
                    {p.label}
                  </text>
                </g>
              )
            })}
            {!view?.nodes.length && (
              <text x={W / 2} y={H / 2 - 20} textAnchor="middle" fill="#94a3b8" style={{ fontFamily: "var(--font-display)", fontSize: 22, fontWeight: 600 }}>
                No listings yet
              </text>
            )}
          </svg>
          <p className="px-2 pb-1 text-xs text-slate-400">
            Glowing rings are trades; arrows run giver to receiver, in each person's colour. Clear dots are still waiting.
          </p>
        </Card>

        <Card className="gap-0 p-0">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 bg-white/[0.03] px-3 py-2">
            <h2 className="text-base font-semibold">Wire log</h2>
            <div className="seg" role="group" aria-label="Filter the wire log">
              <button type="button" aria-pressed={!onlyReveals} onClick={() => setOnlyReveals(false)} className="seg-btn">
                all {wire.length}
              </button>
              <button type="button" aria-pressed={onlyReveals} onClick={() => setOnlyReveals(true)} className="seg-btn">
                identity reveals {reveals}
              </button>
            </div>
          </div>
          <ScrollArea className="h-[52vh] xl:h-[calc(100vh-16rem)] [&_[data-slot=scroll-area-viewport]>div]:!block">
            <ol className="flex flex-col gap-1 p-3 font-mono text-[12px] leading-relaxed" aria-label="Messages that crossed the exchange">
              {shown.map((m) => (
                <WireLine key={m.i} m={m} />
              ))}
              {onlyReveals && !reveals && <li className="text-slate-400">No loop has been accepted by everyone yet.</li>}
              <div ref={end} />
            </ol>
          </ScrollArea>
          <div className="flex items-center gap-2 border-t border-white/10 bg-white/[0.04] px-3 py-2.5 text-xs font-semibold text-slate-300">
            <EyeOff className="size-4 shrink-0 text-emerald-300" aria-hidden />
            never on the wire: names (until all accept) · phone numbers · value floors · reasons
          </div>
        </Card>
      </div>
    </div>
  )
}

function Stat({ label, value, accent = false }: { label: string; value: number; accent?: boolean }) {
  return (
    <span
      className={cn(
        "glass-subtle inline-flex min-h-9 items-baseline gap-1.5 rounded-xl px-2.5 py-1",
        accent && "border-emerald-300/40 bg-emerald-400/10 shadow-[0_0_16px_rgba(52,211,153,0.18)]",
      )}
    >
      <b key={value} className={cn("appear inline-block font-mono text-lg font-semibold tabular", accent ? "text-emerald-300" : "text-slate-50")}>
        {value}
      </b>
      <span className="text-sm font-medium text-slate-400">{label}</span>
    </span>
  )
}

const KIND: Record<string, string> = {
  offer: "pill-offer",
  "edge?": "pill-edge",
  edge: "pill-edge",
  proposal: "pill-proposal",
  vote: "pill-ok",
  "free?": "pill-plan",
  free: "pill-plan",
  "spot?": "pill-plan",
  spot: "pill-plan",
  meetup: "pill-plan",
  "plan-vote": "pill-ok",
  receipt: "pill-ok",
  done: "pill-ok",
  identity: "pill-reveal",
  unwound: "pill-bad",
  "no-show": "pill-bad",
  relay: "pill-plan",
  withdraw: "pill-bad",
}

function clock(t: number) {
  return new Date(t * 1000).toLocaleTimeString([], { hour12: false })
}

function WireLine({ m }: { m: WireMessage }) {
  if (m.kind === "identity") {
    const profiles = (m.payload.profiles ?? []) as { handle: string; name: string; campus: string }[]
    return (
      <li className="rise row-reveal rounded-md border-b border-white/[0.06] py-1 pr-1.5 pl-2.5 [overflow-wrap:anywhere]">
        <span className="text-slate-400">{clock(m.t)} </span>
        <span className="pill pill-reveal">identity revealed (all accepted)</span>{" "}
        <span className="text-slate-400">exchange →</span> <span className="font-semibold text-slate-100">{m.recipient}</span>{" "}
        <span className="text-emerald-100/80">
          {String(m.payload.trade)}: {profiles.map((p) => `${p.handle} = ${p.name}, ${p.campus}`).join("; ")}
        </span>
      </li>
    )
  }
  const body = JSON.stringify(m.payload)
  return (
    <li className="rise border-b border-white/[0.06] pb-1 [overflow-wrap:anywhere]">
      <span className="text-slate-400">{clock(m.t)} </span>
      <span className={cn("pill", KIND[m.kind])}>{m.kind}</span>{" "}
      <span className="font-semibold text-slate-100">{m.sender}</span> <span className="text-slate-400">→</span>{" "}
      <span className="font-semibold text-slate-100">{m.recipient}</span>{" "}
      <span className="text-slate-400">{body.length > 220 ? `${body.slice(0, 219)}…` : body}</span>
    </li>
  )
}
