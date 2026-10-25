import { cn } from "@/lib/utils"

/** One ink per person in a loop, tuned to read on #030712. The demo student is always blue.
 *  `rgb` feeds the glow; `text` is the initials colour on the filled node. */
export const INKS = [
  { fill: "var(--ink-blue)", rgb: "96, 165, 250", text: "#04122b" },
  { fill: "var(--ink-coral)", rgb: "251, 113, 133", text: "#2a0710" },
  { fill: "var(--ink-lime)", rgb: "163, 230, 53", text: "#132503" },
  { fill: "var(--ink-purple)", rgb: "192, 132, 252", text: "#1e0b33" },
  { fill: "var(--ink-yellow)", rgb: "251, 191, 36", text: "#2a1a02" },
]

export function short(handle: string) {
  return handle.replace(/^h-/, "").slice(0, 2)
}

/** "BIO 181 textbook" from "BIO 181 textbook (good)". */
export function bare(label: string) {
  return label.replace(/\s*\((new|good|fair)\)$/, "")
}

// keyed by the backend's colour names (barter/profiles.py COLORS)
const PALETTE: Record<string, (typeof INKS)[number]> = {
  blue: INKS[0],
  coral: INKS[1],
  lime: INKS[2],
  purple: INKS[3],
  yellow: INKS[4],
}

/** A profile's avatar: initials on its palette colour, with a glass rim. */
export function ProfileAvatar({
  profile,
  size = 44,
  className,
}: {
  profile: { initials: string; color: string }
  size?: number
  className?: string
}) {
  const c = PALETTE[profile.color] ?? PALETTE.yellow
  return (
    <span
      aria-hidden
      className={cn("grid shrink-0 place-items-center rounded-full font-display font-bold", className)}
      style={{
        width: size,
        height: size,
        background: c.fill,
        color: c.text,
        fontSize: size * 0.38,
        letterSpacing: 0,
        boxShadow: `inset 0 1px 0 rgba(255,255,255,0.45), inset 0 -2px 6px rgba(0,0,0,0.2), 0 0 0 1px rgba(255,255,255,0.18), 0 0 14px rgba(${c.rgb}, 0.35)`,
      }}
    >
      {profile.initials}
    </span>
  )
}
