"use client";

import { api, type ProviderProfile } from "@/lib/api";
import { useMutation } from "@tanstack/react-query";
import { CircleAlert, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { classifyCreateError } from "./provider-copy";
import { isProviderBaseUrl } from "./provider-json";

export function ProviderCreateForm({
  open,
  onCreated,
}: {
  open: boolean;
  onCreated: (provider: ProviderProfile) => void;
}) {
  const [name, setName] = useState("");
  const [protocol, setProtocol] = useState<"OPENAI" | "ANTHROPIC">("OPENAI");
  const [baseUrl, setBaseUrl] = useState("");
  const [responses, setResponses] = useState(false);
  const [field, setField] = useState<"name" | "url" | null>(null);
  const [localError, setLocalError] = useState("");
  const nameRef = useRef<HTMLInputElement>(null);
  const urlRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) nameRef.current?.focus();
  }, [open]);

  const create = useMutation({
    mutationFn: () => api.createProvider({
      name: name.trim(),
      protocol,
      base_url: baseUrl.trim(),
      use_responses_api: protocol === "OPENAI" ? responses : false,
    }),
    onSuccess: (provider) => {
      setName("");
      setBaseUrl("");
      setResponses(false);
      setField(null);
      setLocalError("");
      onCreated(provider);
    },
    onError: (error: Error) => {
      const nextField = classifyCreateError(error.message);
      setField(nextField);
      setLocalError("");
      (nextField === "url" ? urlRef : nameRef).current?.focus();
    },
  });

  if (!open) return null;

  const errorMessage = localError || create.error?.message || "";
  const nameInvalid = Boolean(errorMessage) && field === "name";
  const urlInvalid = Boolean(errorMessage) && field === "url";

  return (
    <div id="provider-create-panel" className="provider-create">
      <form
        className="provider-create-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!name.trim() || !baseUrl.trim()) return;
          if (!isProviderBaseUrl(baseUrl.trim())) {
            setField("url");
            setLocalError("供应商 Base URL 必须是 HTTP(S) 地址");
            urlRef.current?.focus();
            return;
          }
          setLocalError("");
          create.mutate();
        }}
      >
        <input
          ref={nameRef}
          aria-label="供应商名称"
          aria-invalid={nameInvalid}
          aria-describedby={nameInvalid ? "provider-create-error" : undefined}
          value={name}
          onChange={(event) => {
            setName(event.target.value);
            if (field === "name") setField(null);
          }}
          placeholder="供应商名称"
        />
        <select
          aria-label="协议"
          value={protocol}
          onChange={(event) => setProtocol(event.target.value as "OPENAI" | "ANTHROPIC")}
        >
          <option value="OPENAI">OpenAI 协议</option>
          <option value="ANTHROPIC">Anthropic 协议</option>
        </select>
        <input
          ref={urlRef}
          aria-label="Base URL"
          aria-invalid={urlInvalid}
          aria-describedby={urlInvalid ? "provider-create-error" : undefined}
          value={baseUrl}
          onChange={(event) => {
            setBaseUrl(event.target.value);
            if (field === "url") {
              setField(null);
              setLocalError("");
            }
          }}
          placeholder="https://api.example.com/v1"
        />
        {protocol === "OPENAI" && (
          <label className="provider-check">
            <input
              type="checkbox"
              checked={responses}
              onChange={(event) => setResponses(event.target.checked)}
            />
            文本优先使用 Responses API
          </label>
        )}
        <button type="submit" disabled={!name.trim() || !baseUrl.trim() || create.isPending}>
          <Plus size={14} />创建
        </button>
      </form>
      {errorMessage && (
        <p id="provider-create-error" className="form-error" role="alert">
          <CircleAlert size={14} />{errorMessage}
        </p>
      )}
    </div>
  );
}
