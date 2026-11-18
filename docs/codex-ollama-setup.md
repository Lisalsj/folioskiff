# Codex 与 Ollama 配置

paper-reader 支持 Codex CLI、Ollama Local 和 Ollama Cloud。三者都先经过只读能力探测，模型列表优先动态发现，探测失败时才允许手填模型 ID。模型生成由用户明确执行关键词生成、报告、Assistant、翻译、比较或资源精炼等操作触发；能力探测和分析预检只做检查。

研究意图页和设置页都会展示已发现的后端与模型。生成关键词时可以临时选择后端、模型和推理强度；不选择模型时 Ollama 会使用列表中的第一个已发现模型，Codex 会使用其默认模型。请求完成后页面会显示实际后端和模型，便于确认确实发生了哪一次推理。

## Codex CLI

按照 [Codex CLI 官方文档](https://learn.chatgpt.com/docs/codex/cli) 安装并登录；应用使用稳定的 `codex exec --json --output-schema` 非交互入口。Windows 上会先忽略失效的 PATH `.ps1` shim，再发现并验证桌面应用随附的 `OpenAI/Codex/bin/*/codex.exe`；桌面进程本身只用于状态提示，只有 CLI 可执行且 `login status` 成功才显示可生成。paper-reader 不复制 token，也不修改 `~/.codex/config.toml`。每次任务在只读隔离目录中运行，只放入用户选择的论文版本、标注和必要元数据。

## Ollama Local

按照 [Ollama Windows 官方文档](https://docs.ollama.com/windows) 安装并启动本机服务。paper-reader 通过回环地址访问 [Ollama API](https://docs.ollama.com/api/introduction)，不会替用户开放局域网监听。先在 Ollama 中准备模型，再在设置页点击“重新检测”；模型缺失不会阻止应用的非 AI 功能。

关键词请求在“最小”强度使用 16,384 的本地上下文和 2,048 的输出预算；其他强度使用 32,768 的上下文和 16,384 的输出预算。Ollama 的思考和最终答案共用输出预算；小预算可能全部用于思考，导致最终 `content` 为空。应用通过 `/api/show` 读取模型支持的思考控制，布尔模型使用开关，有分级控制的模型使用其支持的级别；“最小”会在模型允许时关闭思考。

报告、Assistant、翻译、比较和相关推荐精炼共用同一个 Ollama 后端。未指定模型时使用第一个已发现模型；本地请求依据输入长度保留上下文，短请求不再继承 262k 的全局配置。自动上下文最多 128k，超出时要求缩小输入范围。未显式设置的输出预算为“最小”2,048、其他强度16,384；耗尽预算且未完成答案时返回 `output_limit`，连续思考流也受整个请求的时间限制。

Ollama thinking 模型的思考内容和最终答案是两种流片段；应用只用最终 `content` 做 JSON Schema 校验。报告和 Assistant 可展示流式进度；关键词页显示等待状态和完成后的模型信息。若 `ollama ps` 长时间停在 `Stopping...`，先在 Ollama 应用中重启服务，再重新检测。

## Ollama Cloud

按照 [Ollama Cloud 官方文档](https://docs.ollama.com/cloud) 获取合法凭据。key 仅写入操作系统凭据库；数据库、日志、备份、诊断包和 Git 发布内容都不得包含 key。界面始终把 Cloud 标为云端，发送前展示证据范围。

## 探测状态与处理

| 状态码 | 含义与建议操作 |
|---|---|
| `ready` | 后端可用；无需操作。 |
| `not_installed` | 命令未安装；从官方入口安装后重新检测。 |
| `not_authenticated` | 尚未登录或凭据缺失；在对应工具完成登录后重新检测。 |
| `configuration_invalid` | 配置文件或端点无效；修正对应工具配置，不要把凭据粘贴到 issue。 |
| `version_unsupported` | 版本不满足兼容范围；升级后重新检测。 |
| `service_not_running` | 本地 Ollama 服务未启动；启动服务并确认回环端口后重试。 |
| `probe_timeout` | 探测超时；检查本地服务负载、端点和网络，再重试。 |
| `unavailable` | 无法归类的不可用状态；查看脱敏详情并运行安全诊断。 |

探测详情只显示安全的命令版本、状态码和修复建议，不回显环境变量、Authorization header 或配置文件正文。若修复后仍失败，参见 [故障排查](troubleshooting.md) 导出安全诊断包；诊断包仍应在提交前人工检查。
