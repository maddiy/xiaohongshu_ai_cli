import { chromium } from "playwright";

const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
await page.goto("http://127.0.0.1:8765", { waitUntil: "networkidle" });
await page.waitForTimeout(1200);

const info = await page.evaluate(() => {
  const bars = document.querySelectorAll(".bar");
  return [...bars].slice(0, 4).map(b => {
    const cs = window.getComputedStyle(b);
    return {
      inline: b.getAttribute("style"),
      computed: { height: cs.height, minH: cs.minHeight, pos: cs.position, bottom: cs.bottom, left: cs.left, right: cs.right, display: cs.display, bg: cs.backgroundImage.slice(0, 80) },
      offsetH: b.offsetHeight,
      rectH: b.getBoundingClientRect().height,
    };
  });
});
console.log(JSON.stringify(info, null, 2));
await browser.close();