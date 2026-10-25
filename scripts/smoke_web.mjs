// WebKit smoke test of the Barter Bros web page, through the real UI.
//
//   cd web && npm run build && cd .. && node scripts/smoke_web.mjs
//
// Starts the server on a spare port, runs the Jordan demo at iPhone size (390x844) and desktop
// size (1440x900): landing, the loop in the Discord replay, the identity reveal on the wire, the
// meetup, "got it" and done. Screenshots go to out/screens/. WebKit only (owner rule: no Chromium).
// playwright-core is borrowed read-only from another checkout; nothing is installed here.

import { spawn } from "node:child_process"
import { existsSync, mkdirSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const PLAYWRIGHT = "/Users/Dharsan/Downloads/free-food-demo/node_modules/playwright-core/index.mjs"
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..")
const OUT = join(ROOT, "out", "screens")
const PORT = Number(process.env.SMOKE_PORT ?? 8797)
const BASE = `http://127.0.0.1:${PORT}`

if (!existsSync(join(ROOT, "web", "dist", "index.html"))) {
  console.error("web/dist is missing: run `cd web && npm run build` first")
  process.exit(2)
}
mkdirSync(OUT, { recursive: true })

const { webkit } = await import(PLAYWRIGHT)
const server = spawn(join(ROOT, ".venv", "bin", "python"), ["-m", "barter.server", "--port", String(PORT)], {
  cwd: ROOT,
  stdio: ["ignore", "inherit", "inherit"],
})
const stop = () => server.kill("SIGTERM")
process.on("exit", stop)

async function waitForServer() {
  for (let i = 0; i < 100; i++) {
    try {
      const r = await fetch(`${BASE}/api/health`)
      if (r.ok) return
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 100))
  }
  throw new Error("server did not start")
}

const shots = []
async function shot(page, size, name) {
  const path = join(OUT, `${size}-${name}.png`)
  await page.screenshot({ path, fullPage: true })
  shots.push(path)
}

async function skipReplay(page) {
  const skip = page.getByRole("button", { name: "Skip" })
  if (await skip.isEnabled()) await skip.click()
}

// The demo driver shows a button once the replay has caught up; new messages keep arriving while
// bots answer, so keep skipping until the button is there.
async function driver(page, label) {
  const button = page.getByRole("button", { name: label, exact: true })
  for (let i = 0; i < 100; i++) {
    await skipReplay(page)
    if (await button.isVisible()) return button
    await page.waitForTimeout(200)
  }
  throw new Error(`no "${label}" button after 20 s`)
}

async function tap(page, label) {
  await (await driver(page, label)).click()
}

async function run(browser, size, viewport) {
  await fetch(`${BASE}/api/demo/reset`, { method: "POST" })
  const context = await browser.newContext({ viewport, deviceScaleFactor: 2 })
  const page = await context.newPage()
  const errors = []
  page.on("pageerror", (e) => errors.push(String(e)))
  await page.goto(BASE)
  await page.getByRole("button", { name: "Try the demo (be Jordan)" }).waitFor()
  await page.waitForTimeout(600) // let the entrance pop finish
  await shot(page, size, "01-landing")

  await page.getByRole("button", { name: "Try the demo (be Jordan)" }).click()
  await page.getByText("In Discord", { exact: true }).waitFor()
  await page.waitForTimeout(2500) // let the playback run a little, as a viewer would see it
  await shot(page, size, "02-replay-playing")
  await driver(page, "Accept")
  await page.getByRole("button", { name: "Busy market" }).click()
  await page.getByText("loops found").waitFor()
  await page.waitForTimeout(3500) // the market bots answer one by one
  await shot(page, size, "03-loop-found")

  await tap(page, "Accept")
  await page.getByText("identity revealed (all accepted)").first().waitFor()
  await driver(page, "Works for me")
  await page.getByRole("button", { name: /^identity reveals/ }).click()
  await page.waitForTimeout(400)
  await shot(page, size, "04-identity-revealed")
  await page.getByRole("button", { name: /^all \d+/ }).click()

  await tap(page, "Works for me")
  await driver(page, "Got it ✓")
  await page.waitForTimeout(400)
  await shot(page, size, "05-meetup-agreed")

  await tap(page, "Got it ✓")
  await page.getByText(/Done: you have the USB-C 20 W charger/).waitFor({ timeout: 15000 })
  await skipReplay(page)
  await page.waitForTimeout(600)
  await shot(page, size, "06-done")

  const state = await (await fetch(`${BASE}/api/exchange`)).json()
  const t1 = state.trades.find((t) => t.id === "T1")
  if (t1?.state !== "handed_off") throw new Error(`${size}: T1 is ${t1?.state}, expected handed_off`)
  if (errors.length) throw new Error(`${size}: page errors: ${errors.join("; ")}`)
  await context.close()
}

let browser
try {
  await waitForServer()
  browser = await webkit.launch()
  await run(browser, "iphone", { width: 390, height: 844 })
  await run(browser, "desktop", { width: 1440, height: 900 })
  console.log(`smoke ok: the demo ran end to end in WebKit at both sizes\n${shots.join("\n")}`)
} catch (e) {
  console.error(`smoke FAILED: ${e.stack ?? e}`)
  process.exitCode = 1
} finally {
  await browser?.close()
  stop()
}
