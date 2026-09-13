/* Headless render test: boots the REAL app in jsdom against the live API,
   signs in, and reports any runtime crash + whether content actually renders. */
import { createServer } from "vite";
import { JSDOM, VirtualConsole } from "jsdom";

const errors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => {
  const msg = (e.detail && (e.detail.stack || e.detail.message)) || e.message || String(e);
  if (!/not implemented/i.test(msg)) errors.push(msg.slice(0, 1200));
});
vc.on("error", (...a) => errors.push("console.error: " + a.map(String).join(" ").slice(0, 1200)));

const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
  url: "http://127.0.0.1:5173/",
  pretendToBeVisual: true,
  virtualConsole: vc,
});

global.window = dom.window;
global.document = dom.window.document;
global.navigator = dom.window.navigator;
global.localStorage = dom.window.localStorage;
global.Element = dom.window.Element;
global.HTMLElement = dom.window.HTMLElement;
global.SVGElement = dom.window.SVGElement;
global.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 16);
global.cancelAnimationFrame = (id) => clearTimeout(id);
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });

const realFetch = globalThis.fetch.bind(globalThis);
global.fetch = () => Promise.reject(new TypeError("Failed to fetch"));

const login = await (await realFetch("http://127.0.0.1:8000/api/auth/login", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email: "demo@forexmind.ai", password: "demo1234" }),
})).json();
if (!login.token) { console.log("LOGIN FAILED:", JSON.stringify(login).slice(0, 300)); process.exit(1); }
dom.window.localStorage.setItem("fm_token", login.token);

const vite = await createServer({ server: { middlewareMode: true }, appType: "spa", logLevel: "error" });
try {
  await vite.ssrLoadModule("/src/main.tsx");
  await new Promise((r) => setTimeout(r, 6000)); // let splash pass + polls land
  const html = dom.window.document.body.innerHTML;
  const text = dom.window.document.body.textContent || "";
  console.log("body chars:", html.length);
  console.log("app still mounted (not black):", dom.window.document.body.innerHTML.length > 500);
  console.log("shows graceful reconnect state:", /reach your agent|Retrying automatically/i.test(text));
  console.log("errors captured:", errors.length);
  errors.slice(0, 4).forEach((e) => console.log("\n----- ERROR -----\n" + e));
} finally {
  await vite.close();
  process.exit(errors.length ? 2 : 0);
}
