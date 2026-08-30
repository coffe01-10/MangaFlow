# AI 供应商与模型平台

## 协议与预设

用户可创建两类兼容连接：`OPENAI` 与 `ANTHROPIC`。OpenAI 协议支持模型列表、Chat Completions、Responses、图片生成和图片编辑端点模板；Anthropic 协议支持 Messages 文本与多模态请求。Vertex AI 和 Gemini API 是已有工作流的内置原生连接，不属于用户可新增协议。

系统预置 OpenAI、Anthropic、Gemini API、DeepSeek、OpenRouter、OpenCode Zen、硅基流动、Vercel AI Gateway、阿里云百炼、火山方舟、月之暗面、智谱 GLM、阶跃星辰、腾讯混元、xAI、MiniMax、MIMO、302.AI、AiHubMix、TokenPony、随想、AckAI 等连接。预设只提供非敏感 URL 和端点；未录入凭据时不会发送请求。第三方聚合网关标记为 `THIRD_PARTY`。

## 凭据配置

生产环境先在服务端 `.env` 设置一次固定主密钥：

```env
MANGAFLOW_CREDENTIAL_MASTER_KEY=<URL-safe Base64 编码的 32 字节随机值>
```

开发环境在第一次保存 API Key 时自动生成 `storage/.provider-credential-master-key`，无需手动配置；该文件与数据库必须一起备份，否则已有密钥无法解密。非开发环境不会自动生成主密钥。

API Key 使用 AES-256-GCM 加密写入 `provider_keys`。设置页只显示标签、末四位提示、健康状态与冷却时间。一个连接可保存多个 Key；任务按最久未使用优先轮换，认证失败会停用该 Key，限流会进入冷却。

## 模型发现与能力

连接按传输协议声明 `supports_model_discovery` 与支持的模型类型，不再根据供应商名称猜测功能。“同步模型”只在协议明确支持发现时读取上游目录，记录文字/图片类型、输入输出模态、可用操作、API surface、上下文和价格元数据；不支持发现的连接使用预设种子或手动添加，不会把本地已有模型伪装成一次同步结果。无法从上游声明中确认的能力标记为 `INFERRED`，必须人工修正并执行能力测试。自动路由只使用 `VERIFIED` 模型；显式选择仍会校验模型、连接、供应商是否启用及是否支持当前操作。

所有连接共用 `GET /providers/connections/{id}/health` 与 `POST /providers/connections/{id}/verify`。`CREDENTIALS` 只验证环境凭据或 API Key/目录访问，不生成文字或图片；`MODEL_SMOKE` 必须绑定目录模型，结果同时更新连接健康和 `model_probes`。图片冒烟需要显式费用确认。旧 `/settings/vertex/status|verify` 在过渡期内部转发统一验证服务；已无人使用的 `/models/vertex/*` 别名已经移除。

连接验证与目录同步是两个独立动作，避免一次点击重复请求模型列表。旧 `/connections/{id}/test` 仍兼容现有客户端，但内部使用统一验证服务。探测记录保存在 `model_probes`，连接和模型同步保存最近成功时间与中位延迟。

## 路由与审计

显式选择使用模型目录 ID 或兼容旧别名；选择 `auto` 时按可靠性、优先级、延迟和相对成本评分。项目可保存默认文字模型及上次图片模型。每次生成记录实际供应商、协议、目录模型、上游模型 ID、路由原因、提示词校验和、引用资产、用量和输出。

页面、人物、服装和风格任务在排队时建立 `job_asset_references` 租约。任务结束前，API 拒绝删除或改用途这些参考图；Worker 在付费调用前再次验证数据库绑定和文件存在性，防止校验与调用之间的竞态。

## 网络安全

自定义 Base URL 不得包含账号、密码、查询参数或片段。正式调用默认只允许 HTTPS，拒绝本机、链路本地、组播、未指定和私有地址；开发环境可使用显式 loopback HTTP，私有网络必须通过 `ALLOW_PRIVATE_PROVIDER_NETWORKS=true` 主动开启。请求禁止覆盖 `Authorization`、`x-api-key`、`Host` 和 `Content-Length`，且不会自动跟随重定向。
