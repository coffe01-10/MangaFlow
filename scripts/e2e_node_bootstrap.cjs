// Only the isolated acceptance controller supplies this preload. Next's own
// __NEXT_PROCESSED_ENV flag does not prevent reading dotenv file contents.
if (!/^[0-9a-f]{32}$/.test(process.env.MANGAFLOW_E2E_RUN_ID ?? "")) {
  throw new Error("Isolated Node preload requires an acceptance run");
}
const envPath = require.resolve("@next/env");
const nextEnv = require(envPath);
require.cache[envPath].exports = {
  ...nextEnv,
  loadEnvConfig() {
    return { combinedEnv: process.env, parsedEnv: {}, loadedEnvFiles: [] };
  },
  processEnv() { return [process.env, {}]; },
};
