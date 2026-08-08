import { chromium } from "playwright";

const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
await page.goto("http://127.0.0.1:8765", { waitUntil: "networkidle" });
await page.waitForTimeout(1500);

const html = await page.evaluate(() => {
  const col = document.querySelector(".bar-col.today");
  return col ? col.outerHTML.slice(0, 800) : "no today";
});
console.log(html);
await browser.close();