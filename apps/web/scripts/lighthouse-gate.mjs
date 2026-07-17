import { launch } from "chrome-launcher";
import lighthouse from "lighthouse";

const baseUrl = process.env.MANGAFLOW_WEB_URL ?? "http://127.0.0.1:3000";
const projectId = process.env.MANGAFLOW_PROJECT_ID;
const routes = projectId
  ? ["/", `/projects/${projectId}/storyboard`, `/projects/${projectId}/generate`, `/projects/${projectId}/workflow`, "/settings"]
  : ["/", "/settings"];
const minimums = {
  performance: 0.85,
  accessibility: 0.90,
  "best-practices": 0.90,
};

const chrome = await launch({
  chromeFlags: ["--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check"],
});

let failed = false;
try {
  for (const route of routes) {
    const result = await lighthouse(`${baseUrl}${route}`, {
      port: chrome.port,
      output: "json",
      logLevel: "error",
      onlyCategories: Object.keys(minimums),
    });
    if (!result?.lhr) throw new Error(`Lighthouse 没有返回 ${route} 的结果`);
    const scores = Object.fromEntries(
      Object.keys(minimums).map((category) => [category, result.lhr.categories[category].score ?? 0]),
    );
    process.stdout.write(`${route} ${Object.entries(scores).map(([key, value]) => `${key}=${Math.round(value * 100)}`).join(" ")}\n`);
    for (const [category, minimum] of Object.entries(minimums)) {
      if (scores[category] < minimum) failed = true;
    }
  }
} finally {
  await chrome.kill();
}

if (failed) process.exitCode = 1;
