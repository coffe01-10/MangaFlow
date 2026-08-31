"use client";

import { api, type ProviderConnection } from "@/lib/api";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, Save } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { mapOptimisticConflict } from "./provider-copy";
import { validateJsonRecord } from "./provider-json";

export function ConnectionAdvanced({
  connection,
  busy,
}: {
  connection: ProviderConnection;
  busy: boolean;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [baseUrl, setBaseUrl] = useState(connection.base_url);
  const [responses, setResponses] = useState(connection.use_responses_api);
  const [endpointText, setEndpointText] = useState(
    JSON.stringify(connection.endpoint_templates, null, 2),
  );
  const [headerText, setHeaderText] = useState(
    JSON.stringify(connection.extra_headers, null, 2),
  );
  const [status, setStatus] = useState("");
  const [cliExecutable, setCliExecutable] = useState(
    String(connection.nonsecret_config.cli_executable ?? "codex"),
  );
  const isCLI = connection.credential_source === "CLI_SESSION";
  const panelId = `connection-advanced-${connection.id}`;
  const baseUrlRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (open) baseUrlRef.current?.focus();
  }, [open]);
  const endpointResult = validateJsonRecord(endpointText, "endpoint_templates");
  const headerResult = validateJsonRecord(headerText, "extra_headers");
  const canSave = endpointResult.ok && headerResult.ok && Boolean(baseUrl.trim());

  const saveConnection = useMutation({
    mutationFn: () => {
      if (!endpointResult.ok || !headerResult.ok) {
        throw new Error("连接配置未通过校验");
      }
      return api.updateProviderConnection(connection.id, {
        version: connection.version,
        base_url: baseUrl.trim(),
        use_responses_api: responses,
        endpoint_templates: endpointResult.value,
        extra_headers: headerResult.value,
      });
    },
    onSuccess: (saved) => {
      setBaseUrl(saved.base_url);
      setResponses(saved.use_responses_api);
      setEndpointText(JSON.stringify(saved.endpoint_templates, null, 2));
      setHeaderText(JSON.stringify(saved.extra_headers, null, 2));
      setStatus("连接已保存");
      queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
  });
  const saveCLI = useMutation({
    mutationFn: () => api.updateProviderConnection(connection.id, {
      version: connection.version,
      nonsecret_config: {
        ...connection.nonsecret_config,
        cli_executable: cliExecutable.trim(),
      },
    }),
    onSuccess: (saved) => {
      setCliExecutable(String(saved.nonsecret_config.cli_executable ?? "codex"));
      setStatus("CLI 路径已保存，请重新探测");
      queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
  });

  const saveError = saveConnection.error ?? saveCLI.error;
  const conflict = saveError
    ? mapOptimisticConflict(saveError.message, "connection")
    : "";

  function discardDraft() {
    setBaseUrl(connection.base_url);
    setResponses(connection.use_responses_api);
    setEndpointText(JSON.stringify(connection.endpoint_templates, null, 2));
    setHeaderText(JSON.stringify(connection.extra_headers, null, 2));
    setCliExecutable(String(connection.nonsecret_config.cli_executable ?? "codex"));
    saveConnection.reset();
    saveCLI.reset();
    queryClient.invalidateQueries({ queryKey: ["providers"] });
  }

  return (
    <div className="provider-advanced">
      <button
        type="button"
        className="provider-advanced-toggle"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((current) => !current)}
      >
        连接与端点
      </button>
      {open && (
        <div id={panelId} className="provider-advanced-panel">
          {isCLI ? (
            <>
              <label>
                <span>Codex CLI 可执行文件</span>
                <input
                  ref={baseUrlRef}
                  aria-label="Codex CLI 可执行文件"
                  value={cliExecutable}
                  onChange={(event) => setCliExecutable(event.target.value)}
                  placeholder="codex 或绝对路径"
                />
              </label>
              <p className="provider-field-hint">
                只接受命令名 codex 或绝对路径；应用不会安装 CLI，也不会代你登录。
              </p>
              <button
                type="button"
                disabled={!cliExecutable.trim() || busy || saveCLI.isPending}
                onClick={() => saveCLI.mutate()}
              >
                <Save size={14} />保存 CLI 路径
              </button>
            </>
          ) : (
            <>
              <label>
                <span>Base URL</span>
                <input
                  ref={baseUrlRef}
                  aria-label="Base URL"
                  value={baseUrl}
                  onChange={(event) => setBaseUrl(event.target.value)}
                />
              </label>
              {connection.protocol === "OPENAI" && (
                <label className="provider-check">
                  <input
                    type="checkbox"
                    checked={responses}
                    onChange={(event) => setResponses(event.target.checked)}
                  />
                  文本优先使用 Responses API
                </label>
              )}
              <label>
                <span>端点模板（JSON）</span>
                <textarea
                  aria-label="端点模板"
                  aria-invalid={!endpointResult.ok}
                  aria-describedby={!endpointResult.ok ? `connection-${connection.id}-endpoint-error` : undefined}
                  value={endpointText}
                  onChange={(event) => setEndpointText(event.target.value)}
                />
              </label>
              {!endpointResult.ok && (
                <p
                  id={`connection-${connection.id}-endpoint-error`}
                  className="form-error"
                  role="alert"
                >
                  <CircleAlert size={14} />{endpointResult.message}
                </p>
              )}
              <label>
                <span>额外请求头（禁止 Authorization、x-api-key、Host、Content-Length）</span>
                <textarea
                  aria-label="额外请求头"
                  aria-invalid={!headerResult.ok}
                  aria-describedby={!headerResult.ok ? `connection-${connection.id}-header-error` : undefined}
                  value={headerText}
                  onChange={(event) => setHeaderText(event.target.value)}
                />
              </label>
              {!headerResult.ok && (
                <p
                  id={`connection-${connection.id}-header-error`}
                  className="form-error"
                  role="alert"
                >
                  <CircleAlert size={14} />{headerResult.message}
                </p>
              )}
              <button
                type="button"
                disabled={!canSave || busy || saveConnection.isPending}
                onClick={() => saveConnection.mutate()}
              >
                <Save size={14} />保存连接
              </button>
            </>
          )}
          {status && !saveError && (
            <p className="save-success" role="status">{status}</p>
          )}
          {conflict && (
            <>
              <p id={`connection-${connection.id}-connection-error`} className="form-error" role="alert">
                <CircleAlert size={14} />{conflict}
              </p>
              <button type="button" onClick={discardDraft}>放弃草稿并重新加载</button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
