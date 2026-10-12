# Claudecode-RecallHook

中文 | [English](README.en.md)

面向 Claude Code 的本地 Markdown 记忆召回工具。通过 `UserPromptSubmit` hook 检索与当前输入相关的笔记，附加文件路径和简短描述，供 Claude 按需阅读原文。

支持中文笔记索引、多路检索、注入预算和记忆档位管理。部署时需要配置语料目录、初始化数据库，并添加 hook。

## 它做什么

- 将带 YAML frontmatter 的 Markdown 文件建立索引，原文留在文件中。
- 结合触发词、字符 n-gram 与描述向量检索，使用 RRF 融合排名。
- 嵌入服务不可用时，使用 TF-IDF 作为降级检索路线。
- 按预算注入文件指针和简短描述，不直接注入整篇正文。
- 提供档位、触发词、去重、限频和注入日志的维护接口。
- 可选：从 Claude Code 会话记录提取轮次，经本地模型筛选为候选卡，再由人工审核整理成 Markdown。

检索返回文件级线索，正文由 Claude 按需读取。会话采集和候选卡筛选为独立流程，候选内容经审核后整理为长期笔记。

## 运行环境

README 覆盖已有 Markdown 的基础索引与 hook 接入。完整的本地模型准备、会话采集、候选卡审核和档位同步尚未形成从空环境开始的部署流程；当前版本未经过独立干净环境验收。

基础索引和关键词召回需要 Python 3.11 或更新版本，以及 `requirements.txt` 中的 NumPy、PyYAML。代码使用 `tomllib`，因此不支持 Python 3.10。

向量召回需要兼容的本地嵌入服务，示例配置为 `bge-small-zh-v1.5`，512 维。候选卡筛选和自动触发词生成需要另外的本地生成模型服务。模型文件和 llama.cpp 不随仓库分发。

`services.py` 的进程管理针对 Windows；其他平台可自行启动兼容服务，但本项目未验证其他平台的完整部署。

## 最小接入

先只接入已有 Markdown 的索引和召回，不启动会话采集、候选卡筛选或档位同步。

1. 在仓库根目录将 `config.example.toml` 复制为 `config.toml`。已有配置先备份，不要直接覆盖。
2. 修改 `[[corpus]]` 的 `path` 为自己的记忆目录，确认 `[db].path` 指向新数据库。
3. `[db].path` 和 `[services]` 中的相对路径相对于仓库根目录解析。`[[corpus]].path` 当前直接交给文件系统解析，相对路径依赖启动进程的工作目录；hook 的工作目录通常是正在使用的项目，因此语料目录应填写绝对路径。示例中的 `memory` 仅适用于从本仓库根目录运行命令。
4. 在仓库根目录执行以下 PowerShell 命令：

```powershell
python -m pip install -r ./requirements.txt
python ./src/init_db.py
python ./src/index_docs.py
python ./src/memctl.py status
python ./src/memctl.py query 示例项目
python ./src/hook_inject.py --prompt "示例项目如何部署" --no-log --raw --verbose
```

最后一条不会写注入日志，可用于查看模拟输出；它不能证明真实 Claude Code 事件已经接入。

注意：`memctl` 的查询入口会确保数据库 schema，因此不属于严格只读数据库探针。`index_docs.py --dry-run` 目前不累计新增和更新数量，不要根据它报告的零判断没有变化。

建议为记忆提供清楚的 `description` 和少量具体触发词。例如：

```markdown
---
name: example-project
description: 示例项目的部署约束与操作入口
metadata:
  type: project
triggers: [示例项目]
---

这里记录需要跨会话保留的事实、约束与原因。
```

支持 `metadata.type` 的 `user`、`feedback`、`project`、`reference` 分类。新文件初始档位为 `warm`。向量索引主要依据描述，不是全文分块检索；描述空缺会影响召回质量。

## 配置 hook

将以下示例中的 Python 和脚本路径替换为本机绝对路径，再合并到实际工作项目的 `.claude/settings.local.json`。这是你运行 Claude Code 的项目配置，不要求在本仓库启动 Claude Code。

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"C:/Python312/python.exe\" \"D:/Projects/Claudecode-RecallHook/src/hook_inject.py\"",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

不要覆盖整份设置或已有其他 hook。这里不使用 `async`，因为输出需要附加到当前消息。hook 发生异常时静默退出，不阻塞输入；排查时可在命令末尾临时增加 `--verbose` 查看 stderr。

CC Switch 等配置切换工具可能按配置档案重写用户级 `~/.claude/settings.json`，覆盖手工添加的 hook。可将 hook 放在实际工作项目的 `.claude/settings.local.json`，或通过切换工具维护用户级配置。项目级设置只对相应项目生效。

同一 hook 不要在多个作用域重复添加。配置后在真实交互会话中提交已知应该命中的问题，确认附加上下文；未命中、限频、去重或预算不足都可能导致无输出。

## 可选的本地模型

`config.example.toml` 的 `[services]` 提供 `llama_server`、`embed_model`、`qwen_model` 路径。先把它们改为实际文件，再使用 Windows 服务启动脚本：

```powershell
python ./src/services.py start --only embed
python ./src/services.py status
```

服务管理脚本使用固定端口：嵌入 8765、生成模型 8080。仅修改 `[embed]` 或 `[llama]` 的 endpoint 不会改变它启动时的端口。也可自行管理服务，只将兼容 endpoint 填入配置。

日常召回不要求生成模型常驻。`screen.py` 和 `gen_triggers.py` 才需要生成模型；模型文件由使用者自行准备。

注意：当前 `services.py stop --only qwen` 不会只停止 qwen，停止逻辑不使用 `--only` 筛选。不要将它用于隔离停止服务，更不要用 `stop --all` 操作共享模型环境。

## 档位与记忆维护

| 档位 | 用途 |
|---|---|
| `always` | 同步到 `MEMORY.md` 机器区块，不走 hook |
| `warm` | 按相关性召回指针 |
| `resting` | 保留有限的触发词召回入口 |
| `archived` | 不自动注入 |

预算和阈值部分存于数据库 `meta`，并非全部由 TOML 决定。默认 hook 最多选择 5 条指针，同时受总字符预算限制；路径表头也占预算，实际结果可能少于 5 条。

`capture.py`、`screen.py`、`gen_triggers.py` 会写数据库；`tier.py` 还可能改写 `MEMORY.md` 的机器区块。数据库迁移、批量采集、档位调整和记忆文件改写前，先备份并核对目标路径。不要在不理解配置时对真实记忆运行这些命令。

Markdown 原文是内容来源，但数据库还有人工触发词、档位、队列和审计状态，删除后不一定能从 Markdown 完整恢复。备份需要同时覆盖内容和状态。

## 文件结构

```text
Claudecode-RecallHook/
  README.md
  LICENSE
  config.example.toml
  requirements.txt
  src/
  scripts/
```

`src/` 包含命令行入口与 `memlib/` 逻辑模块；`scripts/card-distribution.py` 用于查看候选卡分布。

运行时数据库位于 `data/`，模型文件位于 `models/`，服务日志位于 `logs/`。这些目录及本机 `config.toml` 默认由 Git 忽略。

## 许可

本仓库许可证见 [LICENSE](LICENSE)。第三方模型和运行时遵守各自许可证。
