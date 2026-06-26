# ChatGPT Review Agent Skill 中文教程

让 Codex 把 ChatGPT 当作外部代码审阅 agent 使用。

[English README](README.md)

支持两种模式：

- **Packet review：** 不需要 MCP。Codex 把相关代码打包成 zip，发给任意 ChatGPT reviewer 模型，等待回复，再把最新版回复保存成本地 markdown。
- **MCP connector review：** 让 ChatGPT 通过一个很小的 MCP server 读取本地文件。只有当前 ChatGPT 模型真的能调用 connector tools 时才用。

当前实测结论是：**Pro 不能调用 MCP connector tools**。所以 Pro 走 packet review；High/extra-high 先做 smoke test，通过后再走 MCP。

## Before Proceeding

不用配置 MCP 也能用这个 skill。

如果你只是想让 GPT 当审阅 agent 用，或者暂时不想折腾 connector：

1. 让 Codex 使用 `$chatgpt-review-agent`。
2. Codex 从相关文件生成 review packet zip。
3. Codex 在侧边 ChatGPT 里上传/发送 packet。
4. ChatGPT 回复审阅意见。
5. Codex 抓取最新回复并保存到本地 markdown。

这已经足够完成大多数外部 GPT 审阅。MCP 是进阶模式：让 ChatGPT 自己通过 connector 读取本地文件。

## 安装 Skill

把 skill 目录复制到 Codex skills 目录：

```powershell
Copy-Item -Recurse .\skills\chatgpt-review-agent $env:USERPROFILE\.codex\skills\
```

然后重启 Codex。

使用示例：

```text
Use $chatgpt-review-agent to ask ChatGPT Pro to review this change and save the review markdown locally.
```

## Packet Review

适合任意 GPT reviewer 模型，尤其是 MCP 不存在、不可用、不稳定、不值得配置的情况。

`<skill-dir>`、`<repo-root>`、`<relative/file.py>` 都是占位符。Codex 应该从当前 workspace 和 skill source 位置自己推断真实路径。

生成 packet：

```bash
python <skill-dir>/scripts/build_review_packet.py \
  --repo <repo-root> \
  --out .chatgpt-review/review-packet.md \
  --zip .chatgpt-review/review-packet.zip \
  --goal "Review this change for bugs and missing tests." \
  --file <relative/file.py> \
  --dir tests
```

zip 里包含 `review-packet.md` 和支持文件。ChatGPT 可以读取上传 zip 里的内容，所以多文件审阅优先用 zip。

之后 Codex 应该：

1. 在 Codex 侧边浏览器/tab 打开 ChatGPT。
2. 选择 Pro，或者其他不需要工具的 reviewer 模型。
3. 上传 `.chatgpt-review/review-packet.zip`。
4. 要求 ChatGPT 只审阅 packet，不调用工具。
5. 等生成结束。
6. 保存最新 assistant 回复，通常是 `.chatgpt-review/review.md`。

## MCP Connector Review

只有当你希望 ChatGPT 通过 connector 读取本地文件时才用。

流程：

1. 启动本地 MCP server。
2. 用 HTTPS URL 暴露它。
3. 在 ChatGPT 创建 app/connector。
4. 在 ChatGPT 输入框左下角 `+` 里选择这个 app。
5. smoke test `list_allowed_roots`。
6. 通过后再让 ChatGPT 审阅文件。

## 一次性引导配置

新手配置时，把 `AGENT_SETUP_PROMPT.md` 交给 Codex。

如果当前 Codex turn 不能弹出选项，agent 应该告诉用户：

```text
请先单独输入 /plan 并回车。
进入 Plan mode 后，再发送：引导设置 MCP。
```

进入 Plan mode 后，Codex 应该自己推断：

- 当前 repo root
- Codex skills root
- 操作系统
- 端口，默认通常是 `8765`
- 已提供的公网 HTTPS URL
- 是否开启 source edit

setup 脚本本身不是问卷。它们读取环境变量并生成一键启动脚本。

Windows：

```cmd
setup.cmd
```

macOS/Linux：

```bash
sh setup.sh
```

生成的启动脚本：

```text
start-review-mcp.cmd
start-review-mcp.sh
```

生成的启动脚本默认使用持久 token 文件：

```text
.review-mcp-token
```

保留这个文件。普通重启 MCP server 时，ChatGPT connector 不应该因此断连。

可用环境变量：

```text
REVIEW_REPO_ROOT=<repo-root>
REVIEW_SKILLS_ROOT=<skills-root>
REVIEW_PUBLIC_URL=<public-url>
REVIEW_HOST=127.0.0.1
REVIEW_PORT=8765
REVIEW_ENABLE_EDIT=n
REVIEW_TOKEN_FILE=<local-token-file>
```

默认行为：

- `.chatgpt-review/` 下的 review artifact 写入工具开启
- 白名单 shell 工具开启
- source edit 关闭
- token 文件在 `<this-repo>/.review-mcp-token`

只有明确希望 ChatGPT 侧模型直接改源文件时，才开启：

```text
REVIEW_ENABLE_EDIT=yes
```

## 手动启动 MCP Server

在本 repo 里运行：

```bash
python mcp_server.py \
  --root <repo-root> \
  --root <skills-root> \
  --host 127.0.0.1 \
  --port 8765 \
  --public-url <public-url> \
  --token-file .review-mcp-token
```

只有需要 ChatGPT 写源文件时才加：

```text
--enable-edit
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

期望结果：

```json
{"status":"ok","root":"<repo-root>"}
```

## HTTPS URL

ChatGPT custom app/connector 需要 HTTPS endpoint。

临时测试可以用任意 HTTPS tunnel URL：

```text
https://temporary-url.example/mcp
```

长期使用建议准备自己的域名：

```text
https://repo.example.com/mcp
```

启动 MCP server 时，`--public-url` 填 base URL，不要加 `/mcp`：

```bash
python mcp_server.py \
  --root <repo-root> \
  --root <skills-root> \
  --host 127.0.0.1 \
  --port 8765 \
  --public-url https://repo.example.com \
  --token-file .review-mcp-token
```

检查：

```powershell
Invoke-RestMethod https://repo.example.com/.well-known/oauth-authorization-server
Invoke-WebRequest https://repo.example.com/mcp
```

`/mcp` 在没有认证信息时拒绝访问是正常的；这仍然说明路由到达了 MCP server。

## Cloudflare 自有域名教程

当随机 `trycloudflare.com` 地址太麻烦时，用这个。

1. 把域名托管到 Cloudflare，或者使用已经在 Cloudflare 管理的域名。
2. 打开 Cloudflare Zero Trust。
3. 进入 **Networks -> Tunnels**。
4. 创建或复用一个 tunnel。
5. 在本机安装/运行 Cloudflare connector。Windows 下 Cloudflare 可能给出类似命令：

```cmd
cloudflared.exe service install <token>
```

6. 在 tunnel 里添加 **Public Hostname**：

```text
Subdomain: repo
Domain: example.com
Type: HTTP
URL: http://127.0.0.1:8765
```

7. 你的 public MCP base URL 是：

```text
https://repo.example.com
```

8. ChatGPT connector endpoint 是：

```text
https://repo.example.com/mcp
```

如果 Cloudflare 提示该 host 已有 A、AAAA 或 CNAME 记录，要么删除冲突 DNS 记录，要么换一个 subdomain。

如果 Cloudflare 显示 `1016`，说明这个 hostname 没有正确路由到 tunnel service。把 tunnel Public Hostname 的 service URL 修正为：

```text
http://127.0.0.1:8765
```

## ChatGPT App / Connector 配置

在 ChatGPT 里：

连接后交互示例：

![Codex and ChatGPT connected review example](assets/codex-chatgpt-connected-review.png)

1. 打开 **Apps**。
2. 打开 **Advanced settings**。
3. 开启 **Developer mode**。
4. 创建 app。
5. 名字建议包含 `connect`，例如：

```text
connectcodex
```

这不是协议硬要求，但方便在输入框左下角 `+` 菜单里找到，也和我们测试过的流程一致。

6. connector/MCP URL 填：

```text
https://repo.example.com/mcp
```

7. 完成 OAuth 流程。
8. 如果 ChatGPT 提供 refresh/rescan tools，就执行一次。
9. 在 ChatGPT 输入框左下角点击 `+`。
10. 选择你的 app，例如 `connectcodex`。
11. 使用能调用工具的模型，通常是 High/extra-high。

Smoke prompt：

```text
Use the selected connector only. Smoke test: call list_allowed_roots only. Reply whether a real tool call happened and paste the returned roots or exact error. Do not call any other tool.
```

通过条件：

- ChatGPT UI 显示真实 tool call；
- 回复返回 `<repo-root>` 和 `<skills-root>` 之类的 roots。

如果 Pro 不能调用工具，走 packet review。

如果 ChatGPT 卡在找工具，重新从输入框 `+` 里选择 app，然后重试一次 smoke prompt。

## MCP Tools

内置 server 暴露：

- `list_allowed_roots`
- `tree`
- `read_text`
- `search_text`
- `write_review`
- `list_review_artifacts`
- `run_command`
- `write_text`，仅 `--enable-edit` 时暴露

安全边界：

- roots 必须通过 `--root` 显式允许
- review 写入限制在 `.chatgpt-review/`
- source edit 必须显式 `--enable-edit`
- shell 是固定白名单
- `.env`、private keys、`.git` 等敏感路径会被阻止
- `tree`、`read_text`、`search_text` 有上限
- Python stdlib-only，不安装包

允许的 `run_command`：

```text
git status --short
git diff --stat
git diff
python -m pytest
npm test
```

## Troubleshooting

**Error fetching OAuth configuration**

- 检查 `<public-url>/.well-known/oauth-authorization-server`。
- 启动 server 时传 `--public-url <public-url>`。
- 检查 tunnel 是否路由到 `http://127.0.0.1:8765`。

**Message stream error while looking for tools**

- 用 `/health` 确认 server 还活着。
- 在 ChatGPT 输入框 `+` 里重新选择 connector app。
- 重试一次 smoke prompt。

**Cloudflare 1016**

- public hostname 没有路由到 tunnel target。
- 修正 tunnel Public Hostname service URL：`http://127.0.0.1:8765`。

**Pro cannot call tools**

这是一些 ChatGPT surface 里的预期现象。使用 packet review。

**Tool call appears fake**

除非 ChatGPT UI 显示 tool call，或者 MCP server log 有匹配的 `/mcp` 请求，否则当作未验证。

## 文件

```text
AGENT_SETUP_PROMPT.md
mcp_server.py
setup.cmd
setup.sh
assets/codex-chatgpt-connected-review.png
skills/chatgpt-review-agent/SKILL.md
skills/chatgpt-review-agent/scripts/build_review_packet.py
skills/chatgpt-review-agent/references/setup.md
skills/chatgpt-review-agent/references/browser-workflows.md
```
