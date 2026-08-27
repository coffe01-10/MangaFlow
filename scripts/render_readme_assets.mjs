// Render only README diagram SVGs. sharp is already installed with the web workspace.
// No application, model provider, browser session or network request is used.
import { readFile, writeFile, access } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outputRoot = path.join(root, "assets", "readme");
const args = process.argv.slice(2);
if (args.some((arg) => !["--force", "--check"].includes(arg)) || args.length > 1) {
  throw new Error("Usage: node scripts/render_readme_assets.mjs [--force | --check]");
}
const specs = [
  ["overview", 1840],
  ["overview-mobile", 860],
  ["overview-en", 1840],
  ["overview-mobile-en", 860],
];
if (!args.length) {
  for (const [name] of specs) {
    const exists = await access(path.join(outputRoot, `${name}.png`)).then(() => true, () => false);
    if (exists) throw new Error(`${name}.png already exists; use --force to regenerate`);
  }
}
for (const [name, width] of specs) {
  const source = await readFile(path.join(outputRoot, `${name}.svg`));
  const png = await sharp(source, { density: 144 }).resize({ width }).png().toBuffer();
  const target = path.join(outputRoot, `${name}.png`);
  if (args.includes("--check")) {
    const existing = await readFile(target);
    if (!existing.equals(png)) throw new Error(`${name}.png is out of sync with its SVG`);
    console.log(`PASS: ${name}.png matches its SVG (same renderer and fonts)`);
  } else {
    await writeFile(target, png, { flag: args.includes("--force") ? "w" : "wx" });
    console.log(path.relative(root, target));
  }
}
