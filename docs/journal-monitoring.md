# 期刊更新监测

## 目标

Academic Door 每两小时检查 41 本期刊是否出现新卷期或当前卷期新增文章。普通检查只读取低成本元数据，不反复访问出版商网页；确认有变化后，才启动对应期刊的完整采集、翻译和质量门。

## 分层来源

1. **轻量发现：** Crossref DOI 注册元数据；配置了官方 RSS 的期刊同时读取 RSS。
2. **变化确认：** 同一卷期出现新 DOI、新卷号、官方 RSS 与 Crossref 一致，或同一候选连续两轮稳定出现。
3. **深度采集：** 只对确认变化的期刊运行现有官方采集器；官网受限时使用已经审计的出版方元数据镜像或 Crossref 补充。
4. **发布保护：** 新结果未通过名单、DOI、作者、摘要、重复和 Schema 质量门时，不覆盖线上上一版。

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

## 本机学校授权补充

学校账号、Cookie、验证码和浏览器会话永远不进入 GitHub Actions。需要补齐时，在已经拥有合法访问权限的本机运行：

```powershell
python scripts/local_authorized_enrichment.py `
  public/api/v1/journals/jde/issues/current.json `
  --dry-run
```

确认任务清单后，可安装并登录本机 `paper-fetch`，再移除 `--dry-run`。产物只写入被 Git 忽略的 `data/runtime/local-enrichment/`，不会自动进入网站；须经过字段核对后才能人工导入。

当前适配基于 `Dictation354/paper-fetch-skill`：

- 审计固定提交：`ee3572b7672af44d274f933aade851c6c50fa744`
- 许可：MIT
- 用途：已知 DOI 的本机授权补充，不负责发现新卷期。
- 安全边界：不在 CI 安装，不上传全文、PDF、Cookie、账号或本机路径。

`nature-downloader` 更适合 Zotero/PDF 获取；文献综述和主题搜索类 Skill 不接入本期刊监测链路。
