# 期刊更新监测

## 目标

Academic Door 每两小时检查 41 本期刊是否出现新卷期或当前卷期新增文章。普通检查只读取低成本元数据，不反复访问出版商网页；确认有变化后，才启动对应期刊的完整采集、翻译和质量门。

## 分层来源

1. **轻量发现：** Crossref DOI 注册元数据；配置了官方 RSS 的期刊同时读取 RSS。Elsevier 期刊以 ScienceDirect 官方 RSS 确认已公开卷期，最多提前识别下一个自然月，避免把更晚的预登记卷期误当成当前卷期。
2. **变化确认：** 同一卷期出现新 DOI、新卷号、官方 RSS 与 Crossref 一致，或同一候选连续两轮稳定出现。
3. **深度采集：** 只对确认变化的期刊运行现有官方采集器；官网受限时使用已经审计的出版方元数据镜像或 Crossref 补充。Elsevier 英文摘要依次尝试 Article Metadata、ScienceDirect Search（含返回的 Abstract 链接）、Scopus Abstract Retrieval 和 Article Retrieval；API 返回的 teaser 只作线索，不冒充完整摘要。
4. **双状态发布：** 目录名单、顺序、DOI、作者通过质量门后写入 `detected.json`，网站可以立即显示“整理中”的最新卷期；英文摘要和中文内容全部通过后才更新 `current.json` 并开放 Composer 复制与导出。

这套机制不需要登录学校图书馆，也不依赖验证码。学校授权只用于本机补齐极少数公开元数据无法取得的文章内容。

## 运行频率

- `Monitor journal updates`：每两小时第 17 分钟运行。
- `Update journal data`：北京时间每天 05:35 做一次全量安全巡检。
- 连续三次失败才创建 GitHub Issue；恢复后自动关闭，避免偶发网络波动制造噪音。

技术状态写入：

- `data/monitoring/state.json`：仅保存在 `data` 分支。
- `/api/v1/monitoring.json`：公开健康接口。
- GitHub Actions 运行摘要：本轮变化、待确认和失败列表。

读者页面不展示“备用来源”“顺序待复核”等运维文案；这些信息保留在状态页和 API。

## 私人邮件通知

私人邮件分为两个阶段：

1. **发现新卷期：** 官方目录名单和顺序通过质量门、写入 `detected.json` 后立即通知，邮件列出卷期、文章数、摘要整理进度和目录链接。
2. **新卷期已就绪：** 英文摘要、中文内容和完整质量门通过，且 Composer 同步成功后再通知，邮件增加 Composer 直达链接，可以直接进入发布。

若同一轮发现时内容已经全部完成，只发送“已就绪”邮件，避免连续发送两封重复通知。同一阶段有多本期刊更新时只发送一封中文汇总邮件。通知程序按阶段、卷期和文章清单指纹去重。SMTP
临时失败不会阻断公开数据或网站部署；待发送事件保存在 `data` 分支，
后续巡检自动重试。连续故障通过去重 GitHub Issue 提醒维护者。

首次启用通知程序只为现有卷期建立去重基线，不会把当前 41 本期刊全部
当作新更新发送。

在仓库 `Settings → Secrets and variables → Actions` 配置：

| Secret | 说明 |
| --- | --- |
| `SMTP_HOST` | SMTP 服务器，例如 `smtp.example.com` |
| `SMTP_PORT` | 可选；STARTTLS 默认 587，SSL 默认 465 |
| `SMTP_SECURITY` | `starttls`（默认）或 `ssl` |
| `SMTP_USERNAME` | SMTP 登录账号；无认证中继可不填 |
| `SMTP_PASSWORD` | SMTP 授权码或密码；必须与账号同时设置 |
| `SMTP_FROM` | 发件地址；不填时使用 `SMTP_USERNAME` |
| `NOTIFICATION_EMAIL_TO` | 收件地址；多个地址用逗号或分号分隔 |
| `ELSEVIER_API_KEY` | 可选；Elsevier 官方 API 密钥，用于通过 Article Metadata、ScienceDirect Search、Scopus Abstract Retrieval 和 Article Retrieval 补齐新卷期英文摘要 |

凭据只作为 GitHub Actions Secrets 注入发送步骤，不会写入状态文件、
邮件结果、运行摘要或日志。任何必填配置缺失时，程序安全跳过发送并保留
待发事件；不会猜测账号或把凭据写进仓库。

`ELSEVIER_API_KEY` 同样只在采集步骤中作为请求头使用，不写入公开数据、
缓存或日志。未配置时，系统仍能发现 ScienceDirect 新卷期；若公开元数据
尚未提供完整摘要，质量门会保留线上上一期并等待后续来源补齐。

## 本机学校授权补充

学校账号、Cookie、验证码和浏览器会话永远不进入 GitHub Actions。需要补齐时，在已经拥有合法访问权限的本机运行：

```powershell
python scripts/local_authorized_enrichment.py `
  public/api/v1/journals/jde/issues/current.json `
  --dry-run
```

确认任务清单后，可安装并登录本机 `paper-fetch`，再移除 `--dry-run`。产物只写入被 Git 忽略的 `data/runtime/local-enrichment/`，不会自动进入网站；须经过字段核对后才能人工导入。

本机补充仅限运营者本人已经通过学校或出版方合法取得访问权限的内容。不得利用本接口绕过验证码、付费墙、访问控制或出版方技术限制；遇到验证码必须由运营者在本机按正常授权流程处理，自动化不得代答或规避。

当前适配基于 `Dictation354/paper-fetch-skill`：

- 审计固定提交：`ee3572b7672af44d274f933aade851c6c50fa744`
- 许可：MIT
- 用途：已知 DOI 的本机授权补充，不负责发现新卷期。
- 安全边界：不在 CI 安装，不上传全文、PDF、Cookie、账号或本机路径。

`nature-downloader` 更适合 Zotero/PDF 获取；文献综述和主题搜索类 Skill 不接入本期刊监测链路。
