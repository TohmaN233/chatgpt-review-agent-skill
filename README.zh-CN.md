# 面向 Codex 的 ChatGPT Agent

[English](README.md) | 简体中文

> 四种专注角色，三条清晰路线，让 ChatGPT 参与 Codex 正在处理的工作。

在 Codex 工作流中，让 ChatGPT 担任 **审阅者、顾问、编辑或实现者**。只需处理选定文件时，用 ZIP Packet；需要直接访问本地工作区时，配置 MCP；处理远端仓库或 PR 时，使用已授权的 GitHub 路线。

## 按任务选择角色和路线

- **用配置 Skill 自动准备 MCP。** 它会准备 Bridge 运行环境和平台依赖，并为所选工作区配置一个 Bridge 与 Connector。有 Cloudflare 托管域名时可选择 Named Tunnel；没有时使用 Quick Tunnel，地址变化后会更新该工作区已有的 Connector。
- **按任务选择角色。** 可进行审阅与验证、推理与规划、文本编辑或软件实现；每种角色都有对应的工作规范。
- **按来源选择路线。** 本地文件默认使用无需 MCP 配置的 ZIP；需要直接访问单个本地工作区时选择 MCP；远端仓库或 PR 使用已授权的 GitHub 路线。ZIP 与 MCP 可分别选择，发生错误时不会静默切换路线。

## 安装

从 [`TohmaN233/chatgpt-review-agent-skill`](https://github.com/TohmaN233/chatgpt-review-agent-skill) 安装两个 Skill：

```bash
npx skills add TohmaN233/chatgpt-review-agent-skill --skill chatgpt-agent --skill chatgpt-agent-setup
```

安装只添加 Skills，然后退出；不会下载或启动 MCP Bridge。从本地仓库安装时，Windows 运行 `setup.cmd`，macOS/Linux 运行 `bash setup.sh`。

安装后运行 `$chatgpt-agent-setup`。默认使用 ZIP；只有需要配置 Connector 时才选择 MCP。配置 Skill 会按所选路线完成后续步骤。

## 选择角色

| 角色 | 适用任务 |
| --- | --- |
| `reviewer`（审阅者） | 找出代码、研究或写作中的具体问题；依据证据核对声明、需求和验收标准。 |
| `advisor`（顾问） | 回答推理问题、比较方案或制定有范围的计划。 |
| `editor`（编辑） | 修改文本和文档，同时保留作者意图与表达风格。 |
| `implementer`（实现者） | 在所选路线的写入权限范围内修改软件行为或源代码。 |

示例：

```text
$chatgpt-agent 审阅这些改动中的正确性问题和回归风险。
$chatgpt-agent 比较迁移方案并给出计划。
$chatgpt-agent 将 docs/guide.md 改写得更适合首次使用者。
$chatgpt-agent 实现指定改动并说明验证结果。
```

## 选择路线

| 路线 | 适用场景 | 访问方式与结果 |
| --- | --- | --- |
| **ZIP**（默认） | 所选本地文件足以完成任务 | Codex 创建有范围的 Packet 并发送给 ChatGPT。无需配置 MCP 或 Connector。ChatGPT 返回回答或补丁，由 Codex 在本地应用。订阅方案支持时也可使用 ChatGPT Pro 模型。 |
| **MCP**（主动选择） | 让 ChatGPT 直接访问单个本地工作区 | 工作区 Connector 提供本地文件读取。会话初始为只读；保存报告或修改源文件需要相应的任务级授权。其他任务仍可使用 ZIP。 |
| **GitHub（beta）** | 远端仓库、分支或 PR | 通过已授权的 GitHub Host 集成访问，并固定到明确的源码版本。审阅为只读；实现写入获授权的任务分支。 |

选择 MCP 后，配置 Skill 会准备 Bridge 包和缺少的平台依赖。有 Cloudflare 托管域名时，选择地址稳定的 Named Tunnel；没有域名时，选择 Quick Tunnel，重启导致地址变化后，流程会更新该工作区现有的 Connector。也可沿用已有的自管 HTTPS 地址。ChatGPT 或 Cloudflare 登录时可能需要你完成授权。

## 角色与路线如何配合

按期望结果选择角色，按证据所在位置选择路线。例如，审阅者可以检查 ZIP Packet、本地 MCP 工作区或 GitHub PR；顾问可以基于 Packet 推理，也可以依据本地或 GitHub 资料规划；编辑者和实现者可以在 ZIP 模式下返回有范围的补丁，也可以通过 MCP 或 GitHub 任务分支执行获授权的修改。

## 进一步了解

- [ZIP Packet 流程](docs/workflows/PACKET.md)
- [MCP 本地工作区任务](docs/workflows/LOCAL.md)
- [GitHub 路线](docs/workflows/GITHUB.md)
- [MCP 配置](docs/setup/ONE_COMMAND.md)
- [路线模型](docs/ROUTE_MODEL.md)

MIT License · 由 [@TohmaN233](https://github.com/TohmaN233) 维护。
