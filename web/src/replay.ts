import { useEffect, useState } from "react"

import type { Channel, ExchangeView, SessionView, WireMessage } from "@/api"

/** One step of `demo-replay.json` (written by scripts/record_demo.py from the real service): the
 *  action the student took, then what the server would have shown after it. */
export type ReplayAction =
  | { kind: "seed" | "market"; label: string }
  | { kind: "message"; label: string; text: string }
  | { kind: "press"; label: string; id: string }

export type ReplayStep = { action: ReplayAction; session: SessionView; exchange: ExchangeView; wire: WireMessage[] }
export type Replay = { about: string; channel: Channel; user_ref: string; handle: string; steps: ReplayStep[] }

/** Live when the Python server answers; otherwise (a static host such as GitHub Pages) the recording. */
export function useMode() {
  const [mode, setMode] = useState<"checking" | "live" | "replay">("checking")
  useEffect(() => {
    fetch("./api/health")
      .then(async (r) => (r.ok && (await r.json()).ok === true ? "live" : "replay"))
      .catch(() => "replay" as const)
      .then(setMode)
  }, [])
  return mode
}

export async function loadReplay(): Promise<Replay> {
  const r = await fetch("./demo-replay.json")
  if (!r.ok) throw new Error(`the recorded demo didn't load (${r.status})`)
  return r.json()
}
