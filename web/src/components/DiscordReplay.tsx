import { useEffect, useRef, useState, type ReactNode } from "react"
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

/** A read-only playback of the student's DM thread with their agent, in Discord's dark theme
 *  inside a glass frame. Driven by the same session events; students type and tap in Discord itself. */
export function DiscordReplay({
  view,
  onPress,
  busy,
  extra,
}: {
  view: SessionView
  onPress: (actionId: string) => void
  busy: boolean
  /** The recorded demo's next DM from the student, shown in the driver once the replay catches up. */
  extra?: ReactNode
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
    <section aria-label="Replay of the Discord DM thread" className="flex min-w-0 flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-3xl leading-none font-bold">In Discord</h2>
          <p className="mt-1.5 text-sm text-slate-400">
            A replay of {me.name.split(" ")[0]}'s DMs with their agent. Students talk to it in Discord or iMessage,
            never on this site. This is a playback, not an input box.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => setShown(Math.min(1, total))}>
            <RotateCcw className="size-4" /> Replay
          </Button>
          <Button variant="outline" size="sm" disabled={shown >= total} onClick={() => setShown(total)}>
            <FastForward className="size-4" /> Skip
          </Button>
        </div>
      </div>

      {/* glass frame around a Discord-dark window */}
      <div className="glass p-1.5">
        <div className="overflow-hidden rounded-[1.1rem] bg-(--dc-chat) text-(--dc-text)">
          <div className="flex items-center gap-2 border-b border-black/30 bg-(--dc-bar) px-3 py-2.5 shadow-[0_1px_0_rgba(0,0,0,0.2)]">
            <span className="text-lg leading-none font-semibold text-(--dc-muted)">@</span>
            <span className="font-semibold text-white">Barter Bros</span>
            <span className="rounded bg-(--dc-blurple) px-1 text-[10px] font-semibold text-white">APP</span>
            <span className="ml-auto text-xs text-(--dc-muted)">Direct message · replay</span>
          </div>
          <ScrollArea className="h-[58vh] lg:h-[calc(100vh-19rem)]">
            <ol className="flex flex-col gap-1 py-3" aria-live="polite">
              {lines.map((line, i) => (
                <Message key={line.id} line={line} me={me} head={i === 0 || lines[i - 1].from !== line.from} later={view.chat.slice(i + 1)} />
              ))}
              {typing && (
                <li className="px-4 py-1 text-xs font-medium text-(--dc-muted)" aria-hidden>
                  <span className="inline-flex gap-0.5 align-middle">
                    <Dot d={0} /> <Dot d={120} /> <Dot d={240} />
                  </span>{" "}
                  <b className="text-(--dc-text)">Barter Bros</b> is typing…
                </li>
              )}
              <div ref={end} />
            </ol>
          </ScrollArea>
          <div className="bg-(--dc-chat) px-3 pt-1 pb-3">
            <div className="rounded-lg bg-(--dc-composer) px-3 py-2.5 text-sm text-(--dc-muted)" aria-hidden>
              Message @Barter Bros (in Discord)
            </div>
          </div>
        </div>
      </div>
      {(open.length > 0 || (caughtUp && extra)) && (
        <div className="pop glass flex flex-col gap-2 border-emerald-300/30 px-3.5 py-3 shadow-[0_8px_32px_rgba(0,0,0,0.3),0_0_24px_rgba(52,211,153,0.12)]">
          <span className="text-sm font-semibold text-slate-100">
            {open.length > 0 || !extra
              ? `Demo driver: tap a button for ${me.name.split(" ")[0]}, the way their Discord app would`
              : `Demo driver: send ${me.name.split(" ")[0]}'s next DM, as they would in Discord`}
          </span>
          <div className="flex flex-wrap gap-2">
            {caughtUp && extra}
            {open.map((a) => (
              <Button key={a.id} variant={a.style === "secondary" ? "outline" : "default"} disabled={busy} onClick={() => onPress(a.id)}>
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
  return <span className="size-1.5 animate-bounce rounded-full bg-(--dc-muted)" style={{ animationDelay: `${d}ms` }} />
}

function clock(t: number) {
  return new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
}

function Message({ line, me, head, later }: { line: ChatLine; me: SessionView["profile"]; head: boolean; later: ChatLine[] }) {
  const bot = line.from === "agent"
  // which button was pressed: the student's next message repeats its label
  const answer = later.find((l) => l.from === "you")?.text
  return (
    <li className={cn("rise flex gap-3 px-4 transition-colors duration-150 hover:bg-(--dc-hover)", head ? "mt-2 pt-0.5" : "")}>
      <div className="w-10 shrink-0">
        {head &&
          (bot ? (
            <span
              className="grid size-10 place-items-center rounded-full bg-linear-to-br from-emerald-300 to-teal-400 font-display text-sm font-bold text-[#022c22] shadow-[inset_0_1px_0_rgba(255,255,255,0.4)]"
              aria-hidden
            >
              BB
            </span>
          ) : (
            <ProfileAvatar profile={me} size={40} />
          ))}
      </div>
      <div className="min-w-0 flex-1">
        {head && (
          <div className="flex flex-wrap items-baseline gap-x-1.5">
            <span className={cn("font-semibold", bot ? "text-emerald-300" : "text-white")}>{bot ? "Barter Bros" : me.name}</span>
            {bot && <span className="rounded bg-(--dc-blurple) px-1 text-[10px] font-semibold text-white">APP</span>}
            <span className="text-xs text-(--dc-muted)">Today at {clock(line.t)}</span>
          </div>
        )}
        <p className="text-[15px] leading-snug break-words text-(--dc-text)">{line.text}</p>
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
                    a.style === "primary" ? "bg-(--dc-blurple)" : a.style === "success" ? "bg-(--dc-green)" : "bg-(--dc-grey)",
                    !a.open && !chosen && "opacity-40",
                    chosen && "ring-2 ring-white/70 ring-offset-1 ring-offset-(--dc-chat)",
                    a.open && "appear",
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
