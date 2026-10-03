const BLOCKED_HEADERS = new Set(["authorization", "x-api-key", "host", "content-length"]);

export type JsonObjectField = "endpoint_templates" | "extra_headers";

export type JsonParseResult =
  | { ok: true; value: Record<string, string> }
  | { ok: false; message: string };

export function validateJsonRecord(text: string, field: JsonObjectField): JsonParseResult {
  const trimmed = text.trim() === "" ? "{}" : text;
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return {
      ok: false,
      message: field === "endpoint_templates"
        ? "端点模板不是合法 JSON"
        : "额外请求头不是合法 JSON",
    };
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { ok: false, message: "必须是 JSON 对象，不能是数组或单值" };
  }
  const record = parsed as Record<string, unknown>;
  for (const [key, value] of Object.entries(record)) {
    if (!key || typeof value !== "string") {
      return { ok: false, message: "每个键和值都必须是字符串" };
    }
  }
  if (field === "extra_headers") {
    const blocked = Object.keys(record).some((key) => BLOCKED_HEADERS.has(key.toLowerCase()));
    if (blocked) {
      return { ok: false, message: "不能设置 Authorization、x-api-key、Host 或 Content-Length" };
    }
  }
  return { ok: true, value: record as Record<string, string> };
}

export function isProviderBaseUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    if (parsed.username || parsed.password || parsed.search || parsed.hash) return false;
    if (parsed.protocol === "https:") return Boolean(parsed.hostname);
    if (parsed.protocol === "http:") {
      // Keep the loopback allowlist aligned with the backend validators
      // (provider_catalog.py / compatible.py): WHATWG URL returns IPv6
      // hostnames bracketed, so [::1] needs the brackets stripped to match ::1.
      const host = parsed.hostname.toLowerCase();
      const bare = host.startsWith("[") && host.endsWith("]") ? host.slice(1, -1) : host;
      return bare === "localhost" || bare === "127.0.0.1" || bare === "::1";
    }
    return false;
  } catch {
    return false;
  }
}
