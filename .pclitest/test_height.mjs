import { chromium } from "playwright";

const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
await page.goto("http://127.0.0.1:8765", { waitUntil: "networkidle" });
await page.waitForTimeout(1500);

// 在浏览器中直接测试一个 bar 元素，设置style.height并读取
const result = await page.evaluate(() => {
  const bar = document.querySelector(".bar-col.today .bar-track.received .bar");
  if (!bar) return { error: "no bar" };
  // 直接读取 inline style 各种方式
  const r1 = {
    styleCssText: bar.style.cssText,
    styleHeight: bar.style.height,
    attrHeight: bar.getAttribute("style"),
    computedHeight: window.getComputedStyle(bar).height,
    offsetHeight: bar.offsetHeight,
    getBoundingClientRect: JSON.stringify(bar.getBoundingClientRect()),
  };
  // 手动设置一个不同的值测试
  bar.style.height = "80px";
  const r2 = {
    afterManualSet: window.getComputedStyle(bar).height,
    cssTextAfter: bar.style.cssText,
  };
  return { r1, r2 };
});
console.log(JSON.stringify(result, null, 2));
await browser.close();