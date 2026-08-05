# Agent working agreement

## Mission

Build the Academic Door unified journal data engine, TOP5/Field Journals
public site, and Composer. Optimize for a three-minute human publishing flow:
open the notified issue, make any optional selection, copy, paste, and publish.

## Required boundaries

- Do not modify the `academic-door.github.io`, `nber-working-papers-cn`, or
  `econ-paper-monitor` repositories from this repository.
- Do not add Notion or WeChat API as a required publishing step.
- Never commit credentials, private drafts, local absolute paths, PDFs, or raw
  publisher HTML.
- Official issue pages determine issue membership and article order.
- Crossref may enrich missing fields but must not determine the issue roster.
- Missing data must remain visible; never fabricate metadata or translations.
- Code changes use a branch and pull request. Generated data is validated
  before deployment.

## Required verification

- Run Python tests.
- Run the Astro build.
- Validate public JSON against the schema.
- Confirm no secrets or local absolute paths are staged.
- After deployment, read back the site, data API, Composer, health endpoint,
  and project manifest.

## 任务结束汇报（必读）

每次任务结束时必须汇报以下内容，按类别列出，不要只给文件名让用户去猜：

- **可直接打开的交付物入口**：创建或修改了 GitHub Issue / PR / Release / Actions 运行 / 部署页面 / 仓库页面时，必须给出对应 URL；创建或修改了本地关键文件时，必须给出可点击的绝对路径链接（Codex 桌面端使用 `[名称](<E:/绝对/路径/文件>)` 格式，不要使用 `file://`，不要只贴裸路径）。
- **新增文件**：列出新增文件的绝对路径。
- **修改文件**：列出修改文件的绝对路径。
- **移动文件**：如有移动，列出源路径与目标路径。
- **未处理文件**：列出已知但未处理（或有缺口）的文件。
- **测试或验证**：说明已验证结果（测试数量、审计结果、线上读回等）。
- **产物分类**：HTML / 网页 / 图片 / PDF / PPTX / 报告 / 脚本 / 导出文件等，说明在哪里看、怎么打开；产物太多时只列最重要入口，并说明完整清单记录在哪个 `notes.md` / `TASKS.md` / 报告文件里。
- **本地服务**：如启动了本地服务，给出本地 URL；如服务已停止也要说明。
- **图片/视觉产物**：能直接展示的应在汇报中展示或给出可点击绝对路径。
