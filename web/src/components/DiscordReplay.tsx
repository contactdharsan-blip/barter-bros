import { useEffect, useRef, useState } from "react"
import { Check, FastForward, RotateCcw } from "lucide-react"

import type { ChatLine, SessionView } from "@/api"
import { ProfileAvatar } from "@/components/bits"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"

const STEP = 650 // ms between messages during playback

function reducedMotion() {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
}

/** A read-only playback of the student's DM thread with their agent, styled like Discord.
 *  Driven by the same session events; students type and tap in Discord itself. */
export function DiscordReplay({
  view,
  onPress,
  busy,
}: {
  view: SessionView
  onPress: (actionId: string) => void
  busy: boolean
}) {
  const total = view.chat.length
  const [shown, setShown] = useState(() => (reducedMotion() ? total : Math.min(1, total)))
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (shown >= total) return
    if (reducedMotion()) {
      setShown(total)
      return
    }
    const id = window.setTimeout(() => setShown((n) => n + 1), view.chat[shown]?.from === "you" ? STEP * 1.3 : STEP)
    return () => window.clearTimeout(id)
  }, [shown, total, view.chat])

  useEffect(() => {
    end.current?.scrollIntoView({ block: "end", behavior: reducedMotion() ? "auto" : "smooth" })
  }, [shown])

  const lines = view.chat.slice(0, shown)
  const typing = shown < total && view.chat[shown].from === "agent"
  const me = view.profile
  const caughtUp = shown >= total
  const open = caughtUp ? [...view.chat].reverse().flatMap((l) => l.actions).filter((a) => a.open) : []

  return (
    <section aria-label="Replay of the Discord DM thread" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="font-head text-3xl leading-none">In Discord</h2>
          <p className="mt-1 text-sm">
            A replay of {me.name.split(" ")[0]}'s DMs with their agent. Students talk to it in Discord or iMessage,
            never on this site. This is a playback, not an input box.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" className="bg-white" onClick={() => setShown(Math.min(1, total))}>
            <RotateCcw className="size-4" /> Replay
          </Button>
          <Button variant="outline" size="sm" className="bg-white" disabled={shown >= total} onClick={() => setShown(total)}>
            <FastForward className="size-4" /> Skip
          </Button>
        </div>
      </div>

      <div className="overflow-hidden rounded border-[3px] border-black bg-white shadow-lg">
        <div className="flex items-center gap-2 border-b-2 border-black bg-[#f2f3f5] px-3 py-2">
          <span className="font-bold text-[#5865f2]">@</span>
          <span className="font-bold">Barter Bros</span>
          <span className="rounded bg-[#5865f2] px-1 text-[10px] font-bold text-white">APP</span>
          <span className="ml-auto text-xs text-[#5c5e66]">Direct message · replay</span>
        </div>
        <ScrollArea className="h-[58vh] lg:h-[calc(100vh-19rem)]">
          <ol className="flex flex-col gap-1 py-3" aria-live="polite">
            {lines.map((line, i) => (
              <Message key={line.id} line={line} me={me} head={i === 0 || lines[i - 1].from !== line.from} later={view.chat.slice(i + 1)} />
            ))}
            {typing && (
              <li className="px-4 py-1 text-xs font-medium text-[#5c5e66]" aria-hidden>
                <span className="inline-flex gap-0.5 align-middle">
                  <Dot d={0} /> <Dot d={120} /> <Dot d={240} />
                </span>{" "}
                <b>Barter Bros</b> is typing…
              </li>
            )}
            <div ref={end} />
          </ol>
        </ScrollArea>
        <div className="border-t-2 border-black bg-[#f2f3f5] px-3 py-2.5">
          <div className="rounded bg-[#e3e5e8] px-3 py-2 text-sm text-[#5c5e66]" aria-hidden>
            Message @Barter Bros (in Discord)
          </div>
        </div>
      </div>
      {open.length > 0 && (
        <div className="pop flex flex-col gap-2 rounded border-2 border-black bg-sunny px-3 py-2.5 shadow-md">
          <span className="text-sm font-bold">
            Demo driver: tap a button for {me.name.split(" ")[0]}, the way their Discord app would
          </span>
          <div className="flex flex-wrap gap-2">
            {open.map((a) => (
              <Button
                key={a.id}
                variant={a.style === "secondary" ? "outline" : "default"}
                className={a.style === "secondary" ? "bg-white" : "bg-black text-white hover:bg-black"}
                disabled={busy}
                onClick={() => onPress(a.id)}
              >
                {a.label}
              </Button>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

function Dot({ d }: { d: number }) {
  return <span className="size-1.5 animate-bounce rounded-full bg-[#5c5e66]" style={{ animationDelay: `${d}ms` }} />
}

function clock(t: number) {
  return new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
}

function Message({ line, me, head, later }: { line: ChatLine; me: SessionView["profile"]; head: boolean; later: ChatLine[] }) {
  const bot = line.from === "agent"
  // which button was pressed: the student's next message repeats its label
  const answer = later.find((l) => l.from === "you")?.text
  return (
    <li className={cn("rise flex gap-3 px-4 hover:bg-[#f7f7f8]", head ? "mt-2 pt-0.5" : "")}>
      <div className="w-10 shrink-0">
        {head &&
          (bot ? (
            <span className="grid size-10 place-items-center rounded-full border-2 border-black bg-sunny font-head text-sm" aria-hidden>
              BB
            </span>
          ) : (
            <ProfileAvatar profile={me} size={40} className="border-2 shadow-none" />
          ))}
      </div>
      <div className="min-w-0 flex-1">
        {head && (
          <div className="flex items-baseline gap-1.5">
            <span className={cn("font-bold", bot ? "text-[#5865f2]" : "text-black")}>{bot ? "Barter Bros" : me.name}</span>
            {bot && <span className="rounded bg-[#5865f2] px-1 text-[10px] font-bold text-white">APP</span>}
            <span className="text-xs text-[#5c5e66]">Today at {clock(line.t)}</span>
          </div>
        )}
        <p className="text-[15px] leading-snug break-words text-[#2e3338]">{line.text}</p>
        {line.actions.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-2">
            {line.actions.map((a) => {
              const chosen = !a.open && answer === a.label
              return (
                <span
                  key={a.id}
                  role="img"
                  aria-label={`Discord button: ${a.label}${chosen ? " (pressed)" : a.open ? " (waiting)" : ""}`}
                  className={cn(
                    "inline-flex items-center gap-1 rounded-[3px] px-4 py-1.5 text-sm font-medium text-white select-none",
                    a.style === "primary" ? "bg-[#5865f2]" : a.style === "success" ? "bg-[#248046]" : "bg-[#6d6f78]",
                    !a.open && !chosen && "opacity-40",
                    chosen && "ring-2 ring-black ring-offset-1",
                    a.open && "pop",
                  )}
                >
                  {chosen && <Check className="size-4" />}
                  {a.label}
                </span>
              )
            })}
          </div>
        )}
      </div>
    </li>
  )
}
