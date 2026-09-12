#!/usr/bin/env node
// Refuse a stale/placeholder dist/frontend before `tauri build` bundles it
// (#444). The tracked apps/desktop/dist/frontend/index.html is a PLACEHOLDER:
// every `_next/static/chunks/*` asset it references is gitignored, so a
// `tauri build` from a clean clone (frontendDist=../dist/frontend) would
// otherwise embed a two-file frontend with dangling script refs — a
// white-screen installer shipped silently. The guard walks index.html's
// local href/src references and refuses unless every one resolves to a
// real file on disk (a directory, the static server's 404 under
// trailingSlash:false, counts as dangling).
//
// Wired as tauri.conf.json's beforeBuildCommand (object form with cwd="..",
// so it is independent of where the tauri CLI was invoked). Also runnable
// directly: `node guard-frontend-dist.mjs [distDir]` (the optional argument
// exists for the regression test; production uses the repo layout).

import { readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const distDir = resolve(
  process.argv[2] ?? join(repoRoot, "apps/desktop/dist/frontend")
);
const indexPath = join(distDir, "index.html");

const fail = (message) => {
  console.error(`frontend-dist guard: ${message}`);
  console.error(
    "run apps/desktop/scripts/build-frontend-static.sh first (README §3 step d)"
  );
  process.exit(1);
};

let html;
try {
  html = readFileSync(indexPath, "utf8");
} catch {
  fail(`${indexPath} is missing — there is no frontend to bundle.`);
}

const references = new Set();
// All three HTML attribute-value forms are legal: Next's static export emits
// double quotes, but a hand-edited or tool-transformed index.html (a quote-
// stripping minifier pass is lossless HTML) may carry single-quoted or
// UNQUOTED values — either would reduce the extracted set to zero and make
// the guard PASS vacuously over a dangling placeholder (the exact white-
// screen-installer path this guard exists to block). The unquoted class
// mirrors the HTML5 tokenizer's unquoted-attribute-value state: the value
// ends at tab/LF/FF/space or `>`; quotes, `<`, `&`, and non-ASCII spaces
// are parse errors but PART of the value, so excluding them here (e.g. via
// JS \s, which matches U+00A0) would extract a prefix the browser never
// requests and could pass a guard over a file the page never loads. The
// backslash form is also captured so a Windows-style ref resolves through
// join() like its forward-slash twin.
for (const match of html.matchAll(
  /(?:href|src)\s*=\s*(?:"([^"]+)"|'([^']+)'|([^\t\n\f >]+))/gi
)) {
  const raw = match[1] ?? match[2] ?? match[3];
  const ref = raw.split(/[?#]/, 1)[0].replace(/\\/g, "/");
  if (
    !ref ||
    ref.startsWith("http:") ||
    ref.startsWith("https:") ||
    ref.startsWith("data:") ||
    ref.startsWith("//") ||
    ref.startsWith("#") ||
    ref === "/"
  ) {
    continue;
  }
  references.add(ref);
}

// A reference must resolve to a FILE, not merely to something that exists:
// existsSync answers true for directories (and the unquoted form captures a
// trailing slash — `src=chunks/>` extracts `chunks/`), so a reference to a
// directory used to pass the guard while the static server (and the tauri
// asset handler) answers 404 for it — a broken page shipped silently. Every
// reference a Next export emits is a file; anything else is dangling.
const missing = [...references].filter((ref) => {
  try {
    return !statSync(join(distDir, ref)).isFile();
  } catch {
    return true;
  }
});
if (missing.length > 0) {
  fail(
    `index.html references ${missing.length} missing local asset(s), e.g. ${missing[0]} ` +
      "(dangling chunk references — this is the tracked placeholder or a stale export)."
  );
}
console.log(`frontend-dist guard: ${indexPath} references ${references.size} local assets, all present.`);
