# 自动审批（GitHub App）

`main` 分支保护要求至少 1 次审批。GitHub 的 `GITHUB_TOKEN` 不能提交审批，且 PR 作者不能给自己审批，因此需要一个独立的 GitHub App 作为“自动审批机器人”：在必需检查通过、作者受信时自动 Approve。

## 一、创建 GitHub App

1. 打开 https://github.com/organizations/academic-door/settings/apps/new （或进入 org → Settings → Developer settings → GitHub Apps → New GitHub App）。
2. 填写：
   - GitHub App name：`journal-auto-approver`
   - Homepage URL：`https://github.com/academic-door/journals`
   - Permissions：
     - Metadata：Read-only
     - Contents：Read-only
     - Pull requests：Read and write（提交审批必需）
     - Checks：Read-only
   - Subscribe to events：Pull request
   - 其余保持默认。
3. 创建后点击 Generate a private key，下载 `journal-auto-approver.private-key.pem`。私钥只保存一份，严禁提交到仓库。

## 二、安装到仓库

1. org → Settings → Installed GitHub Apps → 找到 `journal-auto-approver` → Configure → Install。
2. 选择 Only select repositories → `journals`。

## 三、配置 Secrets

仓库 Settings → Secrets and variables → Actions，添加两个 Secret：

- `AUTO_REVIEWER_APP_ID`：GitHub App 设置页顶部的 App ID。
- `AUTO_REVIEWER_PRIVATE_KEY`：PEM 文件的 Base64。PowerShell 生成命令：

```powershell
[Convert]::ToBase64String([System.IO.File]::ReadAllBytes("C:\path\to\journal-auto-approver.private-key.pem"))
```

## 四、生效方式

- `.github/workflows/auto-approve.yml` 合并到 `main` 后自动生效（`pull_request_target` 使用 main 上的工作流定义）。
- 对每个非 Draft、作者在受信名单（`SIMON-WORLD`、`ukinch605`）内、且必需检查 `test` 通过的 PR，自动提交 APPROVE。
- 需要立即审批存量 PR（如 #69）：Actions → Auto-approve PR → Run workflow → 填入 PR 号。
- 修改受信名单：编辑 `.github/workflows/auto-approve.yml` 中 `case " $AUTHOR " in` 的匹配列表。

## 五、安全说明

- App 仅具备读取内容/元数据、提交 PR 审批的权限，不能推送代码、不能修改仓库设置。
- 自动审批只在 `test` 检查通过且作者受信时触发；Draft PR 不会审批。
- App 不能审批它自己创建的 PR；目前 PR 均由 SIMON-WORLD 身份创建，不受影响。