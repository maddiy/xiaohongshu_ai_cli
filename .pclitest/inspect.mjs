import { chromium } from "playwright";

const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
await page.goto("http://127.0.0.1:8765", { waitUntil: "networkidle" });
await page.waitForTimeout(1200);

const info = await page.evaluate(() => {
  const todayCol = document.querySelector(".bar-col.today");
  if (!todayCol) return { error: "no today" };
  const r = todayCol.getBoundingClientRect();
  const received = todayCol.querySelector(".bar-track.received .bar");
  const sent = todayCol.querySelector(".bar-track.sent .bar");
  const cs = window.getComputedStyle(received);
  const cs2 = window.getComputedStyle(sent);
  const cs3 = window.getComputedStyle(todayCol);
  return {
    colRect: { w: r.width, h: r.height, x: r.x, y: r.y },
    receivedHeight: cs.height,
    sentHeight: cs2.height,
    colHeight: cs3.height,
    bgImage: cs3.background.slice(0, 200),
  };
});
console.log(JSON.stringify(info, null, 2));
await browser.close();