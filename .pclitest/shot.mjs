import { chromium } from "playwright";

const browser = await chromium.launch({
  channel: "chrome",
  headless: true,
});
const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
await page.goto("http://127.0.0.1:8765", { waitUntil: "networkidle" });
await page.waitForTimeout(1200);
await page.screenshot({ path: "dashboard.png", fullPage: false });
// dump dashboard content for diagnosis
const info = await page.evaluate(() => {
  const el = document.getElementById("dailyStats");
  return {
    html: el ? el.innerHTML.slice(0, 2000) : "NO #dailyStats",
    chartCount: document.querySelectorAll(".bar").length,
    barHeights: [...document.querySelectorAll(".bar")].map(b => b.style.height),
  };
});
console.log("CHART_INFO=" + JSON.stringify(info, null, 2));
await browser.close();
