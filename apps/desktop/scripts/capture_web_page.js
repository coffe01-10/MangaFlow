/* Web-side page capture for NUI-6 side-by-side evidence.
 * usage: node testseed/web_capture.js <url_path> <out_png> [wait_selector]
 * Fixed viewport 1280x860 to pair with the WPF window captures. */
const { chromium } = require('playwright');

(async () => {
  const [, , urlPath, outPng, waitSelector] = process.argv;
  if (!urlPath || !outPng) {
    console.error('usage: node web_capture.js <url_path> <out_png> [wait_selector]');
    process.exit(2);
  }
  const browser = await chromium.launch({ channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1500, height: 975 } });
  await page.goto('http://127.0.0.1:3000' + urlPath, { waitUntil: 'domcontentloaded', timeout: 60000 });
  try {
    await page.waitForLoadState('networkidle', { timeout: 20000 });
  } catch {
    /* dev overlay long-polls; networkidle is best-effort */
  }
  if (waitSelector) {
    await page.waitForSelector(waitSelector, { timeout: 30000 });
  }
  await page.waitForTimeout(800);
  await page.screenshot({ path: outPng });
  await browser.close();
  console.log('captured', urlPath, '->', outPng);
})();
