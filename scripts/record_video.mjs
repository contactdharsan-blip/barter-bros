// Record docs/demo.mp4: the real web app against a local server, driven in WebKit, with title
// cards and captions rendered as HTML (no Discord, no Claude, no network beyond 127.0.0.1).
//
//   cd web && npm run build && cd .. && node scripts/record_video.mjs [out.mp4]
//
// Starts `python -m barter.server` on VIDEO_PORT (default 8797) and stops it at the end. Jordan's
// free-times DM goes through POST /api/channels/discord/demo-jordan/messages, the endpoint a
// Discord adapter calls; the buttons are pressed through the page's demo driver. The webm from
// Playwright is converted with ffmpeg (FFMPEG, default `ffmpeg`) to H.264, no audio.
// WebKit only (owner rule: no Chromium); playwright-core is borrowed read-only from another checkout.

import { spawn, spawnSync } from "node:child_process"
import { existsSync, mkdirSync, readdirSync, rmSync } from "node:fs"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const PLAYWRIGHT = "/Users/Dharsan/Downloads/free-food-demo/node_modules/playwright-core/index.mjs"
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..")
const OUT = resolve(process.argv[2] ?? join(ROOT, "docs", "demo.mp4"))
const PORT = Number(process.env.VIDEO_PORT ?? 8797)
const BASE = `http://127.0.0.1:${PORT}`
const PYTHON = process.env.PYTHON ?? join(ROOT, ".venv", "bin", "python")
const FFMPEG = process.env.FFMPEG ?? "ffmpeg"
const SIZE = { width: 1280, height: 720 }
const REPO = "github.com/contactdharsan-blip/barter-bros"
const TIMES_DM = "I'm free saturday morning"

if (!existsSync(join(ROOT, "web", "dist", "index.html"))) {
  console.error("web/dist is missing: run `cd web && npm run build` first")
  process.exit(2)
}
const WORK = join(ROOT, "out", "video")
rmSync(WORK, { recursive: true, force: true })
mkdirSync(WORK, { recursive: true })

const { webkit } = await import(PLAYWRIGHT)
const server = spawn(PYTHON, ["-m", "barter.server", "--port", String(PORT)], { cwd: ROOT, stdio: ["ignore", "inherit", "inherit"] })
const stop = () => server.kill("SIGTERM")
process.on("exit", stop)

async function waitForServer() {
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch(`${BASE}/api/health`)).ok) return
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 100))
  }
  throw new Error("server did not start")
}

const FONT = "'Bricolage Grotesque', 'General Sans', system-ui, sans-serif"
function card(title, lines) {
  return `<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400..800&display=swap" rel="stylesheet">
<style>
  html,body{margin:0;height:100%;background:#030712;color:#f8fafc;font-family:${FONT}}
  body{display:grid;place-items:center;background:radial-gradient(60% 60% at 30% 20%,rgba(52,211,153,.18),transparent),radial-gradient(50% 50% at 80% 90%,rgba(45,212,191,.12),transparent),#030712}
  .c{max-width:980px;padding:0 48px}
  h1{font-size:76px;line-height:1;margin:0 0 28px;font-weight:800;display:flex;gap:14px;align-items:center}
  h1 span{background:linear-gradient(135deg,#6ee7b7,#34d399,#2dd4bf);color:#022c22;border-radius:14px;padding:2px 16px}
  p{font-size:30px;line-height:1.35;margin:0 0 14px;color:#cbd5e1}
  p.small{font-size:22px;color:#94a3b8}
  b{color:#6ee7b7;font-weight:700}
</style></head><body><div class="c"><h1>${title}</h1>${lines.map((l) => `<p${l.small ? ' class="small"' : ""}>${l.text ?? l}</p>`).join("")}</div></body></html>`
}

async function caption(page, html) {
  await page.evaluate((h) => {
    let el = document.getElementById("bb-caption")
    if (!el) {
      el = document.createElement("div")
      el.id = "bb-caption"
      Object.assign(el.style, {
        // under the exchange graph, clear of the DM thread, the demo driver and the wire log
        position: "fixed", left: "486px", bottom: "20px", width: "436px", zIndex: "9999", boxSizing: "border-box",
        padding: "16px 20px", borderRadius: "14px", background: "rgba(3,7,18,0.92)",
        border: "1.5px solid rgba(110,231,183,0.75)", boxShadow: "0 0 28px rgba(52,211,153,0.25)",
        color: "#f1f5f9", font: "600 19px/1.36 'Bricolage Grotesque', system-ui, sans-serif",
        transition: "opacity 250ms", pointerEvents: "none",
      })
      document.body.appendChild(el)
    }
    el.innerHTML = h
    el.style.opacity = h ? "1" : "0"
  }, html)
}
const tag = (t) => `<span style="display:inline-block;margin-bottom:4px;padding:1px 9px;border-radius:7px;background:#34d399;color:#022c22;font-size:15px;font-weight:800;letter-spacing:.04em;text-transform:uppercase">${t}</span><br>`

async function driver(page, label) {
  const b = page.getByRole("button", { name: label, exact: true })
  await b.waitFor({ timeout: 60000 })
  return b
}

let browser
let started = 0
try {
  await waitForServer()
  await fetch(`${BASE}/api/demo/reset`, { method: "POST" })
  browser = await webkit.launch()
  const context = await browser.newContext({ viewport: SIZE, recordVideo: { dir: WORK, size: SIZE } })
  const opened = Date.now()
  const page = await context.newPage()
  const errors = []
  page.on("pageerror", (e) => errors.push(String(e)))
  const wait = (ms) => page.waitForTimeout(ms)

  // opening card
  await page.setContent(card("barter <span>bros</span>", [
    "Students DM their own agent what they have and want. The agents find trade loops of 2 to 4 people.",
    { text: "Cubbie Hackathon, Track 2 · team hercules · everyone in this demo is invented", small: true },
  ]), { waitUntil: "networkidle" })
  started = Date.now()
  await wait(5000)

  await page.goto(BASE)
  const start = page.getByRole("button", { name: "Try the demo (be Jordan)", exact: true })
  await start.waitFor()
  await caption(page, `${tag("What you're seeing")}Left: a web replay of Jordan's DMs with their agent, from the same service the Discord bot calls. It is not a live Discord session. Right: the exchange, and every message that crossed between agents.`)
  await wait(6500)

  // At 1280x720 the demo driver sits below the fold, so the page is scrolled to show the whole DM
  // thread and the driver ("pinned"), and up to the top only to press "Busy market". Recording aid
  // only: the page's min-height is raised so the scroll holds while the driver comes and goes.
  // Presses are mouse clicks at the button, so Playwright never scrolls on its own.
  let pinned = 0
  const scroll = async (top) => {
    await page.evaluate((y) => window.scrollTo({ top: y, behavior: "smooth" }), top)
    await wait(700)
  }
  const tap = async (locator) => {
    const box = await locator.boundingBox()
    if (!box || box.y < 0 || box.y + box.height > SIZE.height) throw new Error(`${locator} is off screen`)
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
  }

  await tap(start)
  await page.getByText("In Discord", { exact: true }).waitFor()
  await caption(page, `${tag("Conflicting constraints")}Jordan has a BIO 181 textbook and needs a USB-C charger, but nobody with a charger wants the book. Sam wants the book and has a phone case; Priya has a charger and wants that case. A 3-way loop.`)
  await driver(page, "Accept")
  pinned = await page.evaluate(() => {
    document.body.style.minHeight = "1600px"
    const dm = document.querySelector("section[aria-label='Replay of the Discord DM thread']")
    return Math.round(dm.getBoundingClientRect().bottom + window.scrollY - window.innerHeight + 14)
  })
  await scroll(pinned)
  await wait(2800)
  await caption(page, `${tag("Conflicting constraints")}Jordan's floor is $30 and stays private. The exchange asked "would you take this charger?" and Jordan's agent answered yes ($35 to Jordan). Only the yes crossed the wire.`)
  await wait(6000)

  await caption(page, `${tag("A busy market")}Twelve more agents post listings. They form their own 2-way, 3-way and chain trades; listings nobody fits keep waiting.`)
  await scroll(0)
  await tap(page.getByRole("button", { name: "Busy market", exact: true }))
  await wait(5500)

  await scroll(pinned)
  await fetch(`${BASE}/api/channels/discord/demo-jordan/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: TIMES_DM }),
  })
  await caption(page, `${tag("Students confirm")}Jordan DMs when they're free. The agent reads it back and won't use the times until Jordan taps "That's right".`)
  const right = await driver(page, "That's right")
  await wait(3500)
  await tap(right)
  await wait(3000)

  await caption(page, `${tag("Students confirm")}Jordan accepts the loop. One no would unwind it for everyone.`)
  const accept = await driver(page, "Accept")
  await wait(2000)
  await tap(accept)
  await page.getByText("identity revealed (all accepted)").first().waitFor()
  await caption(page, `${tag("What agents share")}Before everyone says yes: random handles, item listings, and yes/no answers. Only then does an identity message reveal names and campus. Contact details, floors and reasons never cross.`)
  await wait(2500)
  await tap(page.getByRole("button", { name: /^identity reveals/ }))
  await wait(5500)
  await tap(page.getByRole("button", { name: /^all \d+/ }))

  const works = await driver(page, "Works for me")
  await caption(page, `${tag("Conflicting constraints")}Jordan is free mornings, Sam 11 to 1, Priya 10:30 to 12:30, and only Noble Library is on both Jordan's and Priya's lists. Each agent said yes or no to one slot at a time: Sat 11:00 at Noble Library.`)
  await wait(7500)
  await tap(works)

  const got = await driver(page, "Got it ✓")
  await caption(page, `${tag("Students confirm")}Meetup agreed by all three. After the handoff, each receiver taps "Got it".`)
  await wait(4500)
  await tap(got)
  await page.getByText(/Done: you have the USB-C 20 W charger/).waitFor({ timeout: 20000 })
  await caption(page, `${tag("Done")}All three confirmed the handoff. The trade closes, and its ring fades on the exchange.`)
  await wait(5500)
  console.log(`scroll at the end: ${await page.evaluate(() => window.scrollY)} (pinned ${pinned})`)

  await page.setContent(card("barter <span>bros</span>", [
    `<b>${REPO}</b>`,
    "Run it locally for the live version, or step through the recorded demo on the project site.",
    { text: "Demo partners are scripted bots. The Discord bot is built and tested against fakes; it has not run on a live server yet.", small: true },
  ]), { waitUntil: "networkidle" })
  await wait(6000)

  if (errors.length) throw new Error(`page errors: ${errors.join("; ")}`)
  await context.close()
  const webm = readdirSync(WORK).filter((f) => f.endsWith(".webm")).map((f) => join(WORK, f))[0]
  const skip = ((started - opened) / 1000).toFixed(2)
  const r = spawnSync(FFMPEG, ["-y", "-loglevel", "error", "-ss", skip, "-i", webm, "-an", "-c:v", "libx264", "-preset", "slow",
    "-crf", "20", "-pix_fmt", "yuv420p", "-r", "25", "-movflags", "+faststart", OUT], { stdio: "inherit" })
  if (r.status !== 0) throw new Error(`ffmpeg failed (${r.status})`)
  console.log(`video: ${OUT} (from ${webm}, first ${skip}s trimmed)`)
} catch (e) {
  console.error(`record_video FAILED: ${e.stack ?? e}`)
  process.exitCode = 1
} finally {
  await browser?.close()
  stop()
}
