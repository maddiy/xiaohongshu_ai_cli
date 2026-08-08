import { chromium } from "playwright";

const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
await page.goto("http://127.0.0.1:8765", { waitUntil: "networkidle" });
await page.waitForTimeout(1200);

const info = await page.evaluate(() => {
  const bars = document.querySelectorAll(".bar");
  return [...bars].map(b => {
    const cs = window.getComputedStyle(b);
    const trk = b.parentElement;
    const trkCs = window.getComputedStyle(trk);
    return {
      inline: b.getAttribute("style"),
      computed: cs.height,
      parentH: trkCs.height,
      parentInline: trk.getAttribute("style"),
    };
  });
});
console.log(JSON.stringify(info, null, 2));
await browser.close();