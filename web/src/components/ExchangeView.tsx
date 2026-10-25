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

type Placed = { handle: string; x: number; y: number; fill: string; text: string; label: string; faded: boolean; you: boolean; above: boolean }
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
      const ink = INKS[(meIndex >= 0 ? (i - meIndex + n) % n : i + 1) % INKS.length]
      const node = view.nodes.find((x) => x.handle === m.handle)
      const y = cy + r * Math.sin(b)
      placed.set(m.handle, {
        handle: m.handle, x: cx + r * Math.cos(b), y, fill: ink.fill, text: ink.text,
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
      fill: n.handle === me ? INKS[0].fill : "#fff",
      text: n.handle === me ? INKS[0].text : "#000",
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
    end.current?.scrollIntoView({ block: "nearest" })
  }, [shown.length])

  const stats = view?.stats ?? { agents: 0, listings: 0, loops: 0 }
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-head text-3xl leading-none">Exchange · live</h2>
          <p className="mt-1 text-sm">
            What every agent posted, under random handles, and every message that crossed between agents. Nothing else.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Stat label="agents" value={stats.agents} tone="bg-white" />
          <Stat label="listings" value={stats.listings} tone="bg-sunny" />
          <Stat label="loops found" value={stats.loops} tone="bg-lime" />
          <Button variant="outline" className="bg-white" disabled={busy} onClick={onMarket}>
            <Plus className="size-4" /> Busy market
          </Button>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <Card className="overflow-hidden bg-white p-2">
          <svg
            viewBox={`0 -26 ${W} ${H + 30}`}
            className="h-auto w-full"
            role="img"
            aria-label={`${stats.listings} listings on the exchange, ${clusters.length} trades drawn as loops`}
          >
            <defs>
              <marker id="xhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" orient="auto" markerUnits="userSpaceOnUse">
                <path d="M0,0 L10,5 L0,10 z" fill="#000" />
              </marker>
            </defs>
            {waitingTop != null && (
              <text x={8} y={waitingTop + 2} style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 700 }}>
                WAITING FOR A MATCH
              </text>
            )}
            {halos.map((h) => (
              <g key={h.id} className="pop" style={{ transformOrigin: `${h.x}px ${h.y}px` }}>
                <circle cx={h.x} cy={h.y} r={h.r} fill={h.done ? "#f4f0e6" : "#fff3c4"} stroke="#000" strokeWidth="2" strokeDasharray={h.done ? "5 5" : undefined} />
                <text x={h.x} y={h.y - h.r - 0} dy="-4" textAnchor="middle" style={{ fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 700 }}>
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
                const sx = a.x + (dx / len) * (NODE + 3)
                const sy = a.y + (dy / len) * (NODE + 3)
                const ex = b.x - (dx / len) * (NODE + 4)
                const ey = b.y - (dy / len) * (NODE + 4)
                const mx = (sx + ex) / 2 - (dy / len) * bend
                const my = (sy + ey) / 2 + (dx / len) * bend
                const d = `M ${sx} ${sy} Q ${mx} ${my} ${ex} ${ey}`
                return (
                  <g key={`${t.id}-${m.handle}`} opacity={t.state === "handed_off" ? 0.45 : 1}>
                    <path d={d} fill="none" stroke="#000" strokeWidth="6" strokeLinecap="round" markerEnd="url(#xhead)" />
                    <path d={d} fill="none" stroke={a.fill} strokeWidth="3" strokeLinecap="round" className={t.state === "handed_off" ? undefined : "flow"} />
                  </g>
                )
              }),
            )}
            {[...placed.values()].map((p) => (
              <g key={p.handle} opacity={p.faded ? 0.5 : 1} className="pop" style={{ transformOrigin: `${p.x}px ${p.y}px` }}>
                <circle cx={p.x + 2.5} cy={p.y + 2.5} r={NODE} fill="#000" />
                <circle cx={p.x} cy={p.y} r={NODE} fill={p.fill} stroke="#000" strokeWidth="2.5" />
                <text x={p.x} y={p.y} dy="0.35em" textAnchor="middle" fill={p.text} style={{ fontFamily: "var(--font-head)", fontSize: p.you ? 10 : 12 }}>
                  {p.you ? "You" : short(p.handle)}
                </text>
                <text
                  x={p.x}
                  y={p.above ? p.y - NODE - 8 : p.y + NODE + 16}
                  textAnchor="middle"
                  style={{ fontSize: 13, fontWeight: 700, paintOrder: "stroke", stroke: "#fffdf6", strokeWidth: 4, strokeLinejoin: "round" }}
                >
                  {p.label}
                </text>
              </g>
            ))}
            {!view?.nodes.length && (
              <text x={W / 2} y={H / 2 - 20} textAnchor="middle" style={{ fontFamily: "var(--font-head)", fontSize: 22 }}>
                No listings yet
              </text>
            )}
          </svg>
          <p className="px-2 pb-1 text-xs text-muted-foreground">
            Yellow circles are trades; arrows run giver to receiver, in each person's colour. White dots are still waiting.
          </p>
        </Card>

        <Card className="gap-0 overflow-hidden bg-white p-0">
          <div className="flex items-center justify-between border-b-2 border-black bg-sunny px-3 py-2">
            <h2 className="font-head text-base">Wire log</h2>
            <div className="flex items-center gap-1.5" role="group" aria-label="Filter the wire log">
              <button
                type="button"
                aria-pressed={!onlyReveals}
                onClick={() => setOnlyReveals(false)}
                className={cn("rounded border-2 border-black px-1.5 text-xs font-bold", !onlyReveals ? "bg-black text-white" : "bg-white")}
              >
                all {wire.length}
              </button>
              <button
                type="button"
                aria-pressed={onlyReveals}
                onClick={() => setOnlyReveals(true)}
                className={cn("rounded border-2 border-black px-1.5 text-xs font-bold", onlyReveals ? "bg-you text-white" : "bg-white")}
              >
                identity reveals {reveals}
              </button>
            </div>
          </div>
          <ScrollArea className="h-[52vh] xl:h-[calc(100vh-16rem)] [&_[data-slot=scroll-area-viewport]>div]:!block">
            <ol className="flex flex-col gap-1 p-3 font-mono text-[12px] leading-snug" style={{ fontFamily: "var(--font-mono)" }} aria-label="Messages that crossed the exchange">
              {shown.map((m) => (
                <WireLine key={m.i} m={m} />
              ))}
              {onlyReveals && !reveals && <li className="text-muted-foreground">No loop has been accepted by everyone yet.</li>}
              <div ref={end} />
            </ol>
          </ScrollArea>
          <div className="flex items-center gap-2 border-t-2 border-black bg-black px-3 py-2 text-xs font-bold text-white">
            <EyeOff className="size-4 shrink-0" aria-hidden />
            never on the wire: names (until all accept) · phone numbers · value floors · reasons
          </div>
        </Card>
      </div>
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <span className={cn("inline-flex items-baseline gap-1.5 rounded border-2 border-black px-2.5 py-1 shadow-sm", tone)}>
      <b key={value} className="pop font-head text-lg">{value}</b>
      <span className="text-sm font-medium">{label}</span>
    </span>
  )
}

const KIND: Record<string, string> = {
  offer: "bg-[#d6e4ff]",
  "edge?": "bg-[#e6d9ff]",
  edge: "bg-[#e6d9ff]",
  proposal: "bg-[#ffd6cf]",
  vote: "bg-lime",
  "free?": "bg-accent",
  free: "bg-accent",
  "spot?": "bg-accent",
  spot: "bg-accent",
  meetup: "bg-sunny",
  "plan-vote": "bg-lime",
  receipt: "bg-lime",
  done: "bg-lime",
  identity: "bg-you text-white",
  unwound: "bg-[#ffb3ab]",
  "no-show": "bg-[#ffb3ab]",
}

function clock(t: number) {
  return new Date(t * 1000).toLocaleTimeString([], { hour12: false })
}

function WireLine({ m }: { m: WireMessage }) {
  if (m.kind === "identity") {
    const profiles = (m.payload.profiles ?? []) as { handle: string; name: string; campus: string }[]
    return (
      <li className="rise border-b border-dashed border-black/15 pb-1 [overflow-wrap:anywhere]">
        <span className="text-muted-foreground">{clock(m.t)} </span>
        <span className="rounded-sm border border-black bg-you px-1 font-bold text-white">identity revealed (all accepted)</span>{" "}
        exchange → <span className="font-bold">{m.recipient}</span>{" "}
        <span className="text-[#3d3a35]">
          {String(m.payload.trade)}: {profiles.map((p) => `${p.handle} = ${p.name}, ${p.campus}`).join("; ")}
        </span>
      </li>
    )
  }
  const body = JSON.stringify(m.payload)
  return (
    <li className="rise border-b border-dashed border-black/15 pb-1 [overflow-wrap:anywhere]">
      <span className="text-muted-foreground">{clock(m.t)} </span>
      <span className={cn("rounded-sm border border-black px-1 font-bold", KIND[m.kind] ?? "bg-muted")}>{m.kind}</span>{" "}
      <span className="font-bold">{m.sender}</span> → <span className="font-bold">{m.recipient}</span>{" "}
      <span className="text-[#3d3a35]">{body.length > 220 ? `${body.slice(0, 219)}…` : body}</span>
    </li>
  )
}
