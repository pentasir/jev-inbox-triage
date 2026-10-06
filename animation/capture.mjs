// Render animation/index.html frame by frame and encode an MP4.
//   node capture.mjs                 full video -> animation/inbox-triage.mp4
//   node capture.mjs --stills 2,5,9  PNG stills at those seconds -> animation/stills/
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { mkdirSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// Needs Playwright: `npm i -D playwright && npx playwright install chromium`.
// PLAYWRIGHT_DIR can point at another project that already has it installed.
const require = createRequire(process.env.PLAYWRIGHT_DIR ? join(process.env.PLAYWRIGHT_DIR, "package.json") : import.meta.url);
const { chromium } = require("playwright");

const HERE = dirname(fileURLToPath(import.meta.url));
const FPS = 30;
const url = pathToFileURL(join(HERE, "index.html")).href + "?capture=1";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1080, height: 1350 }, deviceScaleFactor: 1 });
await page.goto(url);
await page.waitForFunction(() => typeof window.renderAt === "function");

const stillsArg = process.argv.indexOf("--stills");
if (stillsArg > -1) {
  const dir = join(HERE, "stills");
  mkdirSync(dir, { recursive: true });
  for (const s of process.argv[stillsArg + 1].split(",").map(Number)) {
    await page.evaluate(t => window.renderAt(t), s);
    await page.screenshot({ path: join(dir, `t${String(s).padStart(5, "0")}.png`) });
  }
  console.log(`stills -> ${dir}`);
} else {
  const total = await page.evaluate(() => window.TOTAL);
  const frames = Math.ceil(total * FPS);
  const dir = join(HERE, "frames");
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  for (let f = 0; f < frames; f++) {
    await page.evaluate(t => window.renderAt(t), f / FPS);
    await page.screenshot({ path: join(dir, `${String(f).padStart(5, "0")}.png`) });
    if (f % 150 === 0) console.log(`frame ${f}/${frames}`);
  }
  const out = join(HERE, "inbox-triage.mp4");
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-framerate", String(FPS), "-i", join(dir, "%05d.png"),
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-preset", "slow", "-movflags", "+faststart", out]);
  rmSync(dir, { recursive: true, force: true });
  console.log(`video (${total.toFixed(1)}s, ${frames} frames) -> ${out}`);
}
await browser.close();
