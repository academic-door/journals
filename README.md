<p align="center">
  <img src="https://academic-door.github.io/assets/academic-door-logo.png" width="144" alt="Academic Door / 学术传送门">
</p>

# Academic Door Journals

Academic Door 的统一经济学期刊数据引擎、TOP5/领域顶刊公共网站与公众号排版工具。

公开地址：<https://academic-door.github.io/journals/>

## 产品范围

- `TOP5`：AER、JPE、QJE、RES、Econometrica 的卷期监测与中英文目录。
- `领域顶刊`（Econ Field Journals）：Academic Door 当前监测的 44 本高水平经济学领域期刊，按综合、理论与产业组织、公共与国际、金融、发展与应用、城市宏观劳动计量与环境、农业与资源环境七类组织。与 TOP5 合计 49 本。
- `Academic Door Composer`：载入最新或历史卷期，选文、排序、编辑、预览、换主题并复制富文本到微信公众号。
- `跨刊检索`：检索最新或全部历史卷期的中英文标题、作者、摘要、期刊、领域、年份、卷期与中国相关论文。
- `Data API`：为主页、RSS、公众号和后续平台输出提供同一份标准数据。

## 北极星流程

```text
期刊官网
→ 自动采集与质量检查
→ Academic Door 标准数据
→ 期刊网站 / Composer
→ 复制到微信后台
→ 人工最终检查与发布
```

第一版不依赖 Notion，不把微信公众号 API 作为必经路径。

## 当前里程碑

- [x] 仓库与 Pages 骨架
- [x] Academic Door Issue Schema v1
- [x] AER、JPE、QJE、RES、Econometrica 最新卷期接入
- [x] TOP5 中英文目录、检索与来源追溯
- [x] GitHub Models 中文标题与摘要翻译管线
- [x] Composer 选文、实时预览、主题参数、自定义 CSS、Theme Lab、富文本复制与导出
- [x] TOP5 卷期预取、issue-level 年月显示、微信兼容目录复制与 Academic Door 标签图标
- [x] 数据完整性、翻译数字一致性与隐私质量门
- [x] 44 本高水平经济学领域期刊接入统一数据引擎（全站合计 49 本）
- [x] TOP5 / 领域顶刊统一分类入口与 Composer 数据源
- [x] 每两小时增量监测、按需深度采集、连续失败告警与上一版保护
- [x] 新卷期/补录完成质量门与 Composer 同步后的私人邮件通知
- [x] 最新卷期不可变历史归档与逐刊卷期索引
- [x] 网站与 Composer 历史卷期选择、最新/全部历史跨刊检索
- [x] Composer 逐篇勾选、拖动排序、中国相关筛选和本机设置保存
- [x] 本机学校授权补充接口（凭据和全文不进入 GitHub）
- [ ] TOP5 2025—2026 年 64 期官方目录断点回填
- [ ] 单独规划中文顶级期刊数据引擎

## 本地开发

```powershell
pnpm install
pnpm run dev
```

Python 数据检查：

```powershell
python -m pip install -r requirements.txt
python scripts/update_journals.py --journal ALL --translate
python scripts/journal_monitor.py
python scripts/rebuild_public_snapshot.py --check
python scripts/audit_public_data.py
python -m unittest discover -s tests -v
```

TOP5 历史回填先查看官方卷期计划，再按单刊、单年度小批执行：

```powershell
python scripts/backfill_history.py --journal ALL --from-year 2025 --to-year 2026 --plan-only
python scripts/backfill_history.py --journal AER --from-year 2025 --to-year 2025 --translate --max-issues 12 --max-translations 50
```

历史回填不覆盖 `current.json`、不触发新卷期邮件；翻译和暂存进度可在下一次运行中继续。

## 公共接口

`main` 中的 `public/` 同时包含可审查的基线数据和站点静态代码。`data`
分支只拥有 `public/api/**`、`public/project-manifest.json` 与
`public/backfill-status.md`；部署时仅叠加这三类生成数据。`public/search.js`
等静态代码始终来自 `main`，数据更新不得覆盖。

```text
/journals/api/v1/index.json
/journals/api/v1/collections/top5.json
/journals/api/v1/collections/fields.json
/journals/api/v1/journals/aer/issues/current.json
/journals/api/v1/journals/jpe/issues/current.json
/journals/api/v1/journals/qje/issues/current.json
/journals/api/v1/journals/res/issues/current.json
/journals/api/v1/journals/ecta/issues/current.json
/journals/api/v1/collections/fields.json
/journals/api/v1/journals/{journal_id}/issues/current.json
/journals/api/v1/journals/{journal_id}/issues/index.json
/journals/api/v1/journals/{journal_id}/issues/{issue_id}.json
/journals/api/v1/search/latest.json
/journals/api/v1/search/all.json
/journals/api/v1/health.json
/journals/api/v1/monitoring.json
/journals/project-manifest.json
```

## 架构与协作

- [Academic Door 产品架构 v1.0](docs/architecture.md)
- [Academic Door GitHub 子站建设手册](docs/github-site-playbook.md)
- [新增期刊适配器说明](docs/adding-a-journal.md)
- [期刊更新监测与本机授权补充](docs/journal-monitoring.md)

## 隐私边界

本仓库不得提交 API Key、微信 AppSecret、邮箱密码、Notion/Zotero 数据、本机绝对路径、未发布稿件、论文 PDF 或完整网页缓存。真实凭据只允许放入 GitHub Actions Secrets 或本地 `.env`。

## 许可

代码采用 [MIT License](LICENSE)。第三方期刊数据和摘要不适用本仓库的软件许可，详见 [DATA_USAGE.md](DATA_USAGE.md)。
