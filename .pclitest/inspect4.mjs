import { chromium } from "playwright";

const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
await page.goto("http://127.0.0.1:8765", { waitUntil: "networkidle" });
await page.waitForTimeout(1200);

const info = await page.evaluate(() => {
  const bars = document.querySelectorAll(".bar");
  const out = [];
  for (const b of bars) {
    const cs = window.getComputedStyle(b);
    const inline = b.getAttribute("style");
    out.push({
      inlineStyle: b.style.cssText,
      attrStyle: b.getAttribute("style"),
      computedHeight: cs.height,
      computedMin: cs.minHeight,
    });
  }
  return out.slice(0, 4);
});
console.log(JSON.stringify(info, null, 2));
await browser.close();