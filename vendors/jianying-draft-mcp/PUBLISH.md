# 发布到 GitHub —— 三种路径，选一种

仓库已经准备好了，位置：`github/jianying-draft-mcp`（15 个文件，commit `475b788`）。
本地 git 已初始化并提交，只差「推到远端」这一步需要你的账号授权。

---

## 路径 A（推荐）：你授权一次，我全自动建仓 + 推送

1. 在任意终端跑：

```bat
gh auth login
```

选 `GitHub.com` → `HTTPS` → `Login with a web browser`，浏览器里点一次授权即可。

2. 回来说一声「登录好了」，我会自动执行：

```bat
gh repo create jianying-draft-mcp --private --source=. --remote=origin --push
```

想公开就把 `--private` 换成 `--public`。

---

## 路径 B：你自己建仓库，我来推

1. 在 github.com 新建空仓库（**不要**勾选 README / .gitignore / LICENSE）
2. 把仓库地址发我，我执行：

```bat
git remote add origin https://github.com/<你>/jianying-draft-mcp.git
git push -u origin main
```

（首次推送 Windows 会弹 GitHub 登录窗口，授权一次即可）

---

## 路径 C：不想碰命令行

用 `github/jianying-draft-mcp-repo.zip`（或直接把 `github/jianying-draft-mcp` 文件夹拖进 GitHub 网页上传）。

---

## 上传前已做的脱敏（你复核一下）

| 项目 | 处理 |
|---|---|
| 本机绝对路径（`C:\Users\96254\...`） | 配置模板全部换成 `<PYTHON>` / `<REPO>` 占位符 |
| 你的草稿名（`高燃混剪_NASA点火`） | 文档里替换成 `<sample-draft>` |
| `draft_content.json` / 备份 / 日志 / 导出物 | 已进 `.gitignore` |
| API key / token / 密码 | 扫描过，无 |
| 会员绕过、patch 过的 DLL、逆向报告 | **不包含**：仓库里只有 MCP 服务器本体和文档 |
| 个人邮箱 / 用户名 | 提交作者是占位 `jianying-draft-mcp <noreply@users.noreply.github.com>`，你可以改 |

要改提交作者：

```bat
git -C github/jianying-draft-mcp commit --amend --reset-author --no-edit
```

---

## 推送后建议立刻做的三件事

1. **改 LICENSE 里的 `<YOUR NAME>`** 为你的名字或 GitHub ID
2. 在仓库 Settings → About 填描述：
   `MCP server for CapCut / JianYing Pro draft projects — 47 tools, stdio + HTTP/SSE`
   Topics：`mcp` `model-context-protocol` `capcut` `jianying` `video-editing` `automation` `python`
3. 如果打算收 issue：勾上 Issues；把 README 顶部徽章里的 `<you>` 换成实际用户名

---

## 一个提醒

仓库里**不含**任何逆向分析报告、不含修改过的 `metasecml.dll`、不含会员校验相关脚本。
这些内容不适合公开（涉及商业软件的内部实现与付费功能），建议只在本地保留。
