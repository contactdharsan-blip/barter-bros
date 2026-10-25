import { cn } from "@/lib/utils"

/** One ink per person in a loop. The demo student is always electric blue. */
export const INKS = [
  { fill: "var(--you)", text: "#fff" },
  { fill: "var(--coral)", text: "#000" },
  { fill: "var(--lime)", text: "#000" },
  { fill: "var(--purple)", text: "#fff" },
  { fill: "var(--primary)", text: "#000" },
]

export function short(handle: string) {
  return handle.replace(/^h-/, "").slice(0, 2)
}

/** "BIO 181 textbook" from "BIO 181 textbook (good)". */
export function bare(label: string) {
  return label.replace(/\s*\((new|good|fair)\)$/, "")
}

const PALETTE: Record<string, { fill: string; text: string }> = {
  blue: { fill: "var(--you)", text: "#fff" },
  coral: { fill: "var(--coral)", text: "#000" },
  lime: { fill: "var(--lime)", text: "#000" },
  purple: { fill: "var(--purple)", text: "#fff" },
  yellow: { fill: "var(--primary)", text: "#000" },
}

/** A profile's avatar: initials on its palette colour. */
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
      className={cn("grid shrink-0 place-items-center rounded-full border-[3px] border-black font-head shadow-sm", className)}
      style={{ width: size, height: size, background: c.fill, color: c.text, fontSize: size * 0.38 }}
    >
      {profile.initials}
    </span>
  )
}
