import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react"
import { Play, RotateCcw, SkipForward } from "lucide-react"
import { toast } from "sonner"

import { api, useExchange, useSession, type Channel } from "@/api"
import { DiscordReplay } from "@/components/DiscordReplay"
import { ExchangeView } from "@/components/ExchangeView"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Toaster } from "@/components/ui/sonner"
import { loadReplay, useMode, type Replay, type ReplayAction } from "@/replay"

type Demo = { session: string; channel: Channel; user_ref: string }
const KEY = "bb.demo"
const REPO = "https://github.com/contactdharsan-blip/barter-bros#readme"

function load(): Demo | null {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "null")
  } catch {
    return null
  }
}

function save(d: Demo | null) {
  try {
    if (d) localStorage.setItem(KEY, JSON.stringify(d))
    else localStorage.removeItem(KEY)
  } catch {
    /* private mode: the demo still works for this tab */
  }
}

export default function App() {
  const mode = useMode()
  return (
    <>
      <div className="app-bg" aria-hidden>
        <div className="glow-blob glow-a" />
        <div className="glow-blob glow-b" />
        <div className="glow-blob glow-c" />
      </div>
      {mode === "live" && <LiveDemo />}
      {mode === "replay" && <RecordedDemo />}
      <Toaster position="top-center" />
    </>
  )
}

function Shell({ actions, banner, children }: { actions: ReactNode; banner?: ReactNode; children: ReactNode }) {
  return (
    <div className="relative z-10 mx-auto flex min-h-dvh max-w-[1500px] flex-col gap-5 px-4 pt-4 pb-10 lg:px-8">
      <header className="flex items-center justify-between gap-3">
        <a href="./" className="flex items-center gap-1.5 font-display text-2xl leading-none font-bold text-slate-50 lg:text-3xl" aria-label="Barter Bros home">
          barter
          <span className="rounded-lg bg-linear-to-br from-emerald-300 via-emerald-400 to-teal-400 px-2 py-0.5 text-[#022c22] shadow-[0_0_18px_rgba(52,211,153,0.35),inset_0_1px_0_rgba(255,255,255,0.4)]">
            bros
          </span>
        </a>
        <div className="flex items-center gap-2">{actions}</div>
      </header>
      {banner}
      <main className="grid gap-6 lg:grid-cols-[minmax(0,430px)_minmax(0,1fr)] lg:items-start">{children}</main>
    </div>
  )
}

function Landing({ busy, onStart }: { busy: boolean; onStart: () => void }) {
  return (
    <Card className="pop featured gap-4 px-5 py-6">
      <h1 className="text-[2.1rem] leading-[1.04] font-bold">
        Tell your agent what you have and what you want.{" "}
        <span className="bg-linear-to-r from-emerald-300 to-teal-300 bg-clip-text text-transparent">It finds the trade.</span>
      </h1>
      <p className="text-base text-slate-300">
        Students DM their Barter Bros agent in Discord or iMessage. Agents meet in an exchange that finds trade
        loops of 2 to 4 people. Partners are anonymous handles until everyone says yes.
      </p>
      <Button size="lg" className="min-h-14 self-start text-lg" disabled={busy} onClick={onStart}>
        <Play className="size-5" /> Try the demo (be Jordan)
      </Button>
      <p className="text-sm text-slate-400">
        Jordan has a BIO 181 textbook and needs a USB-C charger. Nobody with a charger wants the book, but there's
        a loop. Everyone here is invented.
      </p>
    </Card>
  )
}

/** Against the Python server: seed, then press Jordan's buttons through the chat-adapter endpoint. */
function LiveDemo() {
  const [demo, setDemo] = useState<Demo | null>(load)
  const [busy, setBusy] = useState(false)
  const gone = useCallback(() => {
    save(null)
    setDemo(null)
  }, [])
  const [view] = useSession(demo?.session ?? null, gone)
  const exchange = useExchange(true)

  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    try {
      await fn()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const start = () =>
    run(async () => {
      const r = await api.seed("book-charger-case-3way")
      const d = { session: r.session!, channel: r.channel!, user_ref: r.user_ref! }
      save(d)
      setDemo(d)
    })

  return (
    <Shell
      actions={
        demo && (
          <Button variant="outline" size="sm" disabled={busy} onClick={start}>
            <RotateCcw className="size-4" /> Restart demo
          </Button>
        )
      }
    >
      {demo && view ? (
        <DiscordReplay
          view={view}
          busy={busy}
          onPress={(id) => run(async () => void (await api.press(demo.channel, demo.user_ref, id)))}
        />
      ) : (
        <Landing busy={busy} onStart={start} />
      )}
      <ExchangeView
        view={exchange.view}
        wire={exchange.wire}
        me={view?.handle ?? null}
        busy={busy}
        onMarket={() => run(async () => void (await api.seed("market")))}
      />
    </Shell>
  )
}

/** No server (the static site): step through scripts/record_demo.py's recording. The student's
 *  buttons stay pressable, but only the recorded next action plays. */
function RecordedDemo() {
  const [replay, setReplay] = useState<Replay | null>(null)
  const [at, setAt] = useState(-1) // -1: the landing page, before "Try the demo"
  useEffect(() => {
    loadReplay().then(setReplay, (e) => toast.error(String(e instanceof Error ? e.message : e)))
  }, [])
  const steps = replay?.steps ?? []
  const step = at >= 0 ? steps[at] : null
  const next = steps[at + 1]?.action ?? null
  const wire = useMemo(() => steps.slice(0, at + 1).flatMap((s) => s.wire), [steps, at])

  const attempt = (matches: (a: ReplayAction) => boolean) => {
    if (next && matches(next)) setAt(at + 1)
    else
      toast(
        next
          ? `Recorded demo: the next step is "${next.label}". Run it locally to try other answers.`
          : "Recorded demo: that's the end of the run. Restart it, or run it locally to try other answers.",
      )
  }
  const message = next?.kind === "message" ? next : null

  return (
    <Shell
      actions={
        at >= 0 && (
          <Button variant="outline" size="sm" onClick={() => setAt(0)}>
            <RotateCcw className="size-4" /> Restart demo
          </Button>
        )
      }
      banner={
        <div className="glass-subtle flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl px-3.5 py-2.5 text-sm text-slate-200" role="note">
          <span className="min-w-0 flex-1">
            <b className="text-emerald-300">Recorded demo:</b> a replay of a real run of the agents.{" "}
            <a className="underline underline-offset-2 hover:text-white" href={REPO}>
              Run it locally for the live version
            </a>
            .
          </span>
          {replay && (
            <span className="flex items-center gap-2">
              <span className="font-mono text-xs text-slate-400">
                step {at + 1}/{steps.length}
              </span>
              <Button size="sm" disabled={!next} onClick={() => setAt(at + 1)}>
                <SkipForward className="size-4" /> {next ? `Next: ${next.label}` : "The end"}
              </Button>
            </span>
          )}
        </div>
      }
    >
      {step ? (
        <DiscordReplay
          view={step.session}
          busy={false}
          onPress={(id) => attempt((a) => a.kind === "press" && a.id === id)}
          extra={
            message && (
              <Button variant="default" onClick={() => setAt(at + 1)}>
                {message.label}
              </Button>
            )
          }
        />
      ) : (
        <Landing busy={!replay} onStart={() => attempt((a) => a.kind === "seed")} />
      )}
      <ExchangeView
        view={step?.exchange ?? null}
        wire={wire}
        me={step?.session.handle ?? null}
        busy={false}
        live={false}
        onMarket={() => attempt((a) => a.kind === "market")}
      />
    </Shell>
  )
}
