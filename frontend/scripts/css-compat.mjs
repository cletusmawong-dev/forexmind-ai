// Old-browser CSS compat (2026-10-01): convert modern space-slash rgb()
// to legacy comma rgba() in built CSS. Tailwind's framework internals
// (ring / gradient / shadow vars) still emit rgb(1 2 3 / 0.5), which older
// tablet browsers fail to parse. This rewrites every remaining occurrence:
//   rgb(1 2 3 / 0.5)  -> rgba(1, 2, 3, 0.5)
//   rgb(1 2 3 / 10%)  -> rgba(1, 2, 3, 0.1)
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const dir = new URL("../dist/assets", import.meta.url).pathname;
const pct = (a) => (a.endsWith("%") ? String(parseFloat(a) / 100) : a);
let files = 0, converted = 0;
for (const f of readdirSync(dir)) {
  if (!f.endsWith(".css")) continue;
  const p = join(dir, f);
  let css = readFileSync(p, "utf8");
  css = css.replace(
    /rgb\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\/\s*([\d.%]+)\s*\)/g,
    (_, r, g, b, a) => { converted++; return `rgba(${r}, ${g}, ${b}, ${pct(a)})`; }
  );
  css = css.replace(
    /rgb\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\/\s*(var\([^)]*\))\s*\)/g,
    (_, r, g, b, a) => { converted++; return `rgba(${r}, ${g}, ${b}, ${a})`; }
  );
  writeFileSync(p, css);
  files++;
}
console.log(`css-compat: ${files} css file(s), ${converted} rgb() -> rgba() converted`);
