# AI 供应商与模型平台

## 协议与预设

目录当前声明 `OPENAI`、`ANTHROPIC`、`GOOGLE_NATIVE` 和 `VERTEX_NATIVE` 四种传输协议。用户可创建前两类兼容连接；后两类原生协议由内置预设提供。OpenAI 协议支持模型列表、Chat Completions、Responses、图片生成和图片编辑端点模板；Anthropic 协议支持 Messages 文本与多模态请求。协议只决定传输、凭据形态和能力声明，不决定产品排序、默认选择或路由优先级。

系统预置多种官方与兼容连接。预设只提供非敏感 URL、端点与声明能力；未录入凭据时不会发送请求，新预设模型以 `DECLARED` 进入目录，必须经过对应操作的冒烟验证后才可参与自动路由。第三方聚合网关标记为 `THIRD_PARTY`。设置页按用户筛选与名称展示目录，不给任何预设固定置顶或默认模型。

## 凭据配置

生产环境先在服务端 `.env` 设置一次固定主密钥：

```env
MANGAFLOW_CREDENTIAL_MASTER_KEY=<URL-safe Base64 编码的 32 字节随机值>
```

开发环境在第一次保存 API Key 时自动生成 `storage/.provider-credential-master-key`，无需手动配置；该文件与数据库必须一起备份，否则已有密钥无法解密。非开发环境不会自动生成主密钥。

API Key 使用 AES-256-GCM 加密写入 `provider_keys`。设置页只显示标签、末四位提示、健康状态与冷却时间。一个连接可保存多个 Key；任务按最久未使用优先轮换，认证失败会停用该 Key，限流会进入冷却。

## 模型发现与能力

连接按传输协议声明 `supports_model_discovery` 与支持的模型类型，不再根据供应商名称猜测功能。“同步模型”只在协议明确支持发现时读取上游目录，记录文字/图片类型、输入输出模态、可用操作、API surface、上下文和价格元数据；不支持发现的连接使用预设种子或手动添加，不会把本地已有模型伪装成一次同步结果。无法从上游声明中确认的能力标记为 `INFERRED`，必须人工修正并执行能力测试。自动路由只使用 `VERIFIED` 模型；显式选择仍会校验模型、连接、供应商是否启用及是否支持当前操作。

所有连接共用 `GET /providers/connections/{id}/health` 与 `POST /providers/connections/{id}/verify`。`CREDENTIALS` 只验证环境凭据或 API Key/目录访问，不生成文字或图片；`MODEL_SMOKE` 必须绑定目录模型，结果同时更新连接健康和 `model_probes`。每次图片冒烟只执行一种操作，并要求显式费用确认。旧 `/settings/vertex/status|verify` 在过渡期内部转发统一验证服务；产品前端不再调用该入口，已无人使用的 `/models/vertex/*` 别名已经移除。旧 `ProviderHealth` 表只为这一单版本兼容入口保留，不再作为产品状态事实来源。

`CLI_SESSION` 表示登录态由外部 CLI 管理。应用不显示密钥表单、不读取或复制会话 token，也不代用户登录；presence/version/login/capability 四步探测分别写入 `ModelProbe`，连接据此派生为 `AVAILABLE`、`UNAVAILABLE`、`UNAUTHENTICATED` 或 `UNSUPPORTED`。只有 `AVAILABLE` 的 CLI 连接可调用，探测不会替用户启停连接；具体 CLI 仍由 V02-14 分项注册。

连接验证与目录同步是两个独立动作，避免一次点击重复请求模型列表。旧 `/connections/{id}/test` 仍兼容现有客户端，但内部使用统一验证服务。探测记录保存在 `model_probes`，连接和模型同步保存最近成功时间与中位延迟。

## 模型展示偏好

模型的调用开关、当前可用性与创作界面展示偏好是三个独立状态。`AIModel.enabled` 决定模型是否允许调用，`GET /models` 的 `enabled` 是结合供应商、连接和凭据派生的当前可用性，`AIModel.display_enabled` 只决定模型是否应出现在创作界面的新选择列表。隐藏模型不会退出自动路由，仍可被已有项目、工作流和历史任务按真实目录 ID 引用。

设置页通过 `GET /providers/connections/{id}/models` 读取连接下的完整管理目录。单行偏好沿用 `PATCH /providers/models/{id}` 的乐观版本锁；批量偏好使用 `PATCH /providers/models/visibility`，每项携带期望版本并独立提交。批量响应分别列出成功项以及模型缺失、连接缺失、版本冲突项；重复写入相同目标值不会增加版本。`GET /models` 始终返回包含隐藏模型的完整目录，并分别提供 `enabled` 与 `display_enabled`。

## 路由与审计

显式选择使用模型目录 ID 或兼容旧别名；选择 `auto` 时按可靠性、优先级、延迟和相对成本评分。项目可保存默认文字模型及上次图片模型。每次生成记录实际供应商、协议、目录模型、上游模型 ID、路由原因、提示词校验和、引用资产、用量和输出。

页面、人物、服装和风格任务在排队时建立 `job_asset_references` 租约。任务结束前，API 拒绝删除或改用途这些参考图；Worker 在付费调用前再次验证数据库绑定和文件存在性，防止校验与调用之间的竞态。

## 网络安全

自定义 Base URL 不得包含账号、密码、查询参数或片段。正式调用默认只允许 HTTPS，拒绝本机、链路本地、组播、未指定和私有地址；开发环境可使用显式 loopback HTTP，私有网络必须通过 `ALLOW_PRIVATE_PROVIDER_NETWORKS=true` 主动开启。请求禁止覆盖 `Authorization`、`x-api-key`、`Host` 和 `Content-Length`，且不会自动跟随重定向。
