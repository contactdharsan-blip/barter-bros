import { useEffect, useRef, useState } from "react"

export type Channel = "web" | "discord" | "imessage"

export type Item = {
  category: string
  attributes: Record<string, string | number>
  condition: string
  label: string
}

export type Want = {
  category: string
  attributes: Record<string, string | number | (string | number)[]>
  min_condition: string | null
  label: string
}

export type Member = {
  handle: string
  channel: Channel
  you: boolean
  gives: Item | null
  to: string | null
  receiver: boolean
  vote: boolean | null
  plan_vote: boolean | null
  receipt: boolean | null
  profile: Profile | null
}

export type Profile = {
  handle: string
  name: string
  initials: string
  color: "blue" | "coral" | "lime" | "purple" | "yellow"
  campus: string
  bio: string
  member_since: string
  completed: number
  showed_up: number
  no_shows: number
}

export type HistoryEntry = {
  trade: string
  state: TradeState
  t: number
  gave: string | null
  got: string | null
  people: number
  reason: string
}

export type TradeState = "proposed" | "accepted_by_all" | "meetup_agreed" | "handed_off" | "unwound"

export type Trade = {
  id: string
  state: TradeState
  shape: "cycle" | "chain"
  reason: string
  revealed: boolean
  members: Member[]
  plan: { slot: string; spot: string } | null
  mine?: {
    gives: Item | null
    gets: Item | null
    to: string | null
    from: string | null
    worth: { value: number; floor: number | null } | null
  }
}

export type ChatAction = { id: string; label: string; style: "primary" | "secondary" | "success"; open: boolean }
export type ChatLine = { id: number; from: "you" | "agent"; text: string; t: number; actions: ChatAction[] }

export type SessionView = {
  id: string
  handle: string
  channel: Channel
  profile: Profile
  chat: ChatLine[]
  listing: {
    have: Item | null
    est_value: number | null
    wants: Want[]
    floor: number | null
    parser: string
    posted: boolean
    open: boolean
  }
  trades: Trade[]
  history: HistoryEntry[]
  generation: number
}

export type ExchangeNode = {
  handle: string
  channel: Channel
  have: Item | null
  wants: string[]
  status: "listed" | "in_trade" | "done"
  trade: string | null
}

export type ExchangeView = {
  generation: number
  nodes: ExchangeNode[]
  trades: Trade[]
  stats: { agents: number; listings: number; loops: number }
}

export type WireMessage = {
  i: number
  t: number
  sender: string
  recipient: string
  kind: string
  payload: Record<string, unknown>
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!r.ok) {
    let detail = r.statusText
    try {
      detail = (await r.json()).detail ?? detail
    } catch {
      /* not JSON */
    }
    throw new ApiError(r.status, String(detail))
  }
  return r.json() as Promise<T>
}

export const api = {
  seed: (scenario: string) =>
    call<{ session?: string; handle?: string; channel?: Channel; user_ref?: string; bots: number }>(
      "POST",
      `/api/demo/seed?scenario=${encodeURIComponent(scenario)}`,
    ),
  session: (sid: string) => call<SessionView>("GET", `/api/sessions/${sid}`),
  /** The demo driver: press a button in the student's DM thread, as their Discord client would. */
  press: (channel: Channel, userRef: string, action: string) =>
    call<{ replies: unknown[] }>("POST", `/api/channels/${channel}/${encodeURIComponent(userRef)}/press`, { action }),
  reset: () => call<{ ok: boolean }>("POST", "/api/demo/reset"),
}

/** Your session, live over SSE. `onGone` fires when the server no longer knows it (a reset). */
export function useSession(sid: string | null, onGone: () => void) {
  const [view, setView] = useState<SessionView | null>(null)
  const gone = useRef(onGone)
  gone.current = onGone
  useEffect(() => {
    setView(null)
    if (!sid) return
    let closed = false
    api.session(sid).then(
      (v) => !closed && setView(v),
      (e) => e instanceof ApiError && e.status === 404 && gone.current(),
    )
    const es = new EventSource(`/api/events?session=${encodeURIComponent(sid)}`)
    es.addEventListener("session", (e) => setView(JSON.parse((e as MessageEvent).data)))
    es.addEventListener("gone", () => {
      es.close()
      gone.current()
    })
    return () => {
      closed = true
      es.close()
    }
  }, [sid])
  return [view, setView] as const
}

/** The judge's view and the wire log, live over SSE. */
export function useExchange(enabled: boolean) {
  const [view, setView] = useState<ExchangeView | null>(null)
  const [wire, setWire] = useState<WireMessage[]>([])
  useEffect(() => {
    if (!enabled) return
    setWire([])
    const es = new EventSource("/api/events")
    let pending: WireMessage[] = []
    let frame = 0
    const flush = () => {
      frame = 0
      const batch = pending
      pending = []
      setWire((w) => [...w, ...batch].slice(-400))
    }
    es.addEventListener("wire", (e) => {
      pending.push(JSON.parse((e as MessageEvent).data))
      if (!frame) frame = requestAnimationFrame(flush)
    })
    es.addEventListener("exchange", (e) => setView(JSON.parse((e as MessageEvent).data)))
    es.addEventListener("reset", () => {
      pending = []
      setWire([])
    })
    return () => {
      es.close()
      if (frame) cancelAnimationFrame(frame)
    }
  }, [enabled])
  return { view, wire }
}

