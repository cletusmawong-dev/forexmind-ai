import { chromium } from "playwright-core";
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
await page.goto("http://localhost:5173/", { waitUntil: "networkidle" });
await page.waitForTimeout(3500);
await page.fill('input[type="email"]', "demo@forexmind.ai");
await page.fill('input[type="password"]', "demo1234");
await page.locator("button").last().click();
await page.waitForTimeout(4000);
await page.locator('a[href="/learning"]:visible').first().click();
await page.waitForTimeout(2500);
const strip = await page.locator('[role="status"]').textContent().catch(() => null);
console.log("strip:", strip?.replace(/\s+/g, " ").trim());
// walk all tabs to confirm no crash
for (const t of ["Research", "Queue", "Experiments", "Patterns", "Insights", "History", "Signal IQ"]) {
  await page.locator(`button:has-text("${t}")`).first().click().catch(() => console.log("miss", t));
  await page.waitForTimeout(500);
}
console.log("tabs walked");
await page.locator('button:has-text("Signal IQ")').first().click();
await page.waitForTimeout(600);
await page.screenshot({ path: "/tmp/lab_strip.png" });
console.log(errors.length ? "ERRORS: " + errors[0] : "no js errors");
await browser.close();
