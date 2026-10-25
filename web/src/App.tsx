import { useCallback, useState } from "react"
import { Play, RotateCcw } from "lucide-react"
import { toast } from "sonner"

import { api, useExchange, useSession, type Channel } from "@/api"
import { DiscordReplay } from "@/components/DiscordReplay"
import { ExchangeView } from "@/components/ExchangeView"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"

type Demo = { session: string; channel: Channel; user_ref: string }
const KEY = "bb.demo"

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
    <TooltipProvider>
      <div className="mx-auto flex min-h-dvh max-w-[1500px] flex-col gap-5 px-4 pt-4 pb-10 lg:px-8">
        <header className="flex items-center justify-between gap-3">
          <a href="/" className="flex items-center gap-1.5 font-head text-2xl leading-none lg:text-3xl" aria-label="Barter Bros home">
            barter
            <span className="rounded border-2 border-black bg-black px-1.5 py-0.5 text-white shadow-[3px_3px_0_0_var(--you)]">bros</span>
          </a>
          <div className="flex items-center gap-2">
            {demo && (
              <Button variant="outline" size="sm" className="bg-white" disabled={busy} onClick={start}>
                <RotateCcw className="size-4" /> Restart demo
              </Button>
            )}
          </div>
        </header>

        <main className="grid gap-6 lg:grid-cols-[minmax(0,430px)_minmax(0,1fr)] lg:items-start">
          {demo && view ? (
            <DiscordReplay
              view={view}
              busy={busy}
              onPress={(id) => run(async () => void (await api.press(demo.channel, demo.user_ref, id)))}
            />
          ) : (
            <Card className="pop gap-4 bg-white px-5 py-6">
              <h1 className="text-[2.1rem] leading-[1.02]">Tell your agent what you have and what you want. It finds the trade.</h1>
              <p className="text-base">
                Students DM their Barter Bros agent in Discord or iMessage. Agents meet in an exchange that finds trade
                loops of 2 to 4 people. Partners are anonymous handles until everyone says yes.
              </p>
              <Button size="lg" className="h-14 self-start text-lg" disabled={busy} onClick={start}>
                <Play className="size-5" /> Try the demo (be Jordan)
              </Button>
              <p className="text-sm text-muted-foreground">
                Jordan has a BIO 181 textbook and needs a USB-C charger. Nobody with a charger wants the book, but there's
                a loop. Everyone here is invented.
              </p>
            </Card>
          )}
          <ExchangeView
            view={exchange.view}
            wire={exchange.wire}
            me={view?.handle ?? null}
            busy={busy}
            onMarket={() => run(async () => void (await api.seed("market")))}
          />
        </main>
      </div>
      <Toaster position="top-center" />
    </TooltipProvider>
  )
}
