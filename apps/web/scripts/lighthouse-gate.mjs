import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { launch } from "chrome-launcher";
import lighthouse from "lighthouse";

const baseUrl = process.env.MANGAFLOW_WEB_URL ?? "http://127.0.0.1:3000";
const projectId = process.env.MANGAFLOW_PROJECT_ID;
const auditDir = process.env.MANGAFLOW_LH_AUDIT_DIR;
const routes = projectId
  ? ["/", `/projects/${projectId}/storyboard`, `/projects/${projectId}/generate`, `/projects/${projectId}/workflow`, "/settings"]
  : ["/", "/settings"];
const minimums = {
  performance: 0.85,
  accessibility: 0.90,
  "best-practices": 0.90,
};
const metricKeys = [
  "first-contentful-paint",
  "largest-contentful-paint",
  "total-blocking-time",
  "speed-index",
  "cumulative-layout-shift",
];

const chrome = await launch({
  chromeFlags: ["--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check"],
});

let failed = false;
try {
  if (auditDir) await mkdir(auditDir, { recursive: true });
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
    const metrics = Object.fromEntries(
      metricKeys.map((key) => [key, result.lhr.audits[key]?.numericValue ?? null]),
    );
    const opportunities = Object.values(result.lhr.audits)
      .filter((audit) => audit.details?.type === "opportunity" && (audit.numericValue ?? 0) > 0)
      .sort((left, right) => (right.numericValue ?? 0) - (left.numericValue ?? 0))
      .slice(0, 5)
      .map((audit) => ({
        id: audit.id,
        title: audit.title,
        wastedMs: Math.round(audit.numericValue ?? 0),
      }));
    process.stdout.write(
      `${route} ${Object.entries(scores).map(([key, value]) => `${key}=${Math.round(value * 100)}`).join(" ")}`,
    );
    process.stdout.write(
      ` fcp=${Math.round(metrics["first-contentful-paint"] ?? 0)} lcp=${Math.round(metrics["largest-contentful-paint"] ?? 0)} tbt=${Math.round(metrics["total-blocking-time"] ?? 0)} cls=${Number(metrics["cumulative-layout-shift"] ?? 0).toFixed(3)}`,
    );
    if (opportunities.length) {
      process.stdout.write(
        ` opp=${opportunities.map((item) => `${item.id}:${item.wastedMs}`).join(",")}`,
      );
    }
    process.stdout.write("\n");
    if (auditDir) {
      const slug = route.replaceAll("/", "_") || "_root";
      await writeFile(
        path.join(auditDir, `${slug}.json`),
        JSON.stringify(
          {
            route,
            scores,
            metrics,
            opportunities,
            lcp: result.lhr.audits["largest-contentful-paint"]?.details ?? null,
            layoutShifts: result.lhr.audits["layout-shifts"]?.details ?? null,
          },
          null,
          2,
        ),
      );
    }
    for (const [category, minimum] of Object.entries(minimums)) {
      if (scores[category] < minimum) failed = true;
    }
  }
} finally {
  await chrome.kill();
}

if (failed) process.exitCode = 1;
