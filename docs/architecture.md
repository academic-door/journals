# Academic Door Journals 产品架构

> 当前 public product map / Parent-child navigation / Composer public-private boundary 以 main-control Decision 0019 与 `governance/PUBLIC_PRODUCT_MAP.md` 为准。本文只描述 Journals owner-local implementation；旧版“public full Composer”内容已被该决策取代。

本文是 Academic Door 各项目和 Agent 的共同边界。子项目可以独立迭代，但不得各自发明重复的数据格式、导航、质量标准或发布流程。

## 1. 产品使命

Academic Door 面向中文读者建设开放、可靠、可检索、可复用的经济学学术公共品。产品不是单一公众号自动化脚本，而是由结构化数据、公共网站和内容生产工具组成的学术信息基础设施。

北极星目标：

1. 重要经济学论文和期刊更新不漏报。
2. 所有中文内容可追溯到官方来源。
3. 同一份数据同时服务网站、检索、RSS、公众号和其他平台。
4. 运营者每天只需完成选题、编辑和最终发布，不再维护冗长的中间状态。
5. 每个子项目可以由独立 Agent 开发，但接入 Academic Door 时遵守同一契约。

## 2. 产品版图

```text
Academic Door 门厅 /
├─ 每日之门 · Econ Papers Daily
├─ 前沿之门 · Working Papers
├─ 顶刊之门 · /journals/top5/
└─ 领域之门 · /journals/fields/

Academic Door · 期刊 /journals/
├─ 顶刊之门
├─ 领域之门
├─ 跨刊检索
├─ 数据状态
└─ Composer Preview / 发布预览（只读 public entry）
```

`/journals/` 是期刊 umbrella landing，不是第五扇 Door。完整 Composer 是 internal subsystem，不是公开 Door。

| 层级 | 负责内容 | 不负责内容 |
|---|---|---|
| Parent 门厅 | 四扇公开 Door 的统一入口、品牌与跨产品导航 | 复制子产品全部业务 UI |
| Journals 数据层 | 期刊采集、canonical issue/archive、双语整理、质量门、公共 API | 私有稿件与编辑状态 |
| Journals 公共网站 | 顶刊/领域浏览、历史、检索、reader-safe Status、只读 Composer Preview | 匿名编辑、复制导出、Theme Lab |
| 私有 Composer | 编辑、排序、renderer/theme、copy/export、草稿与 publication history | 期刊 source authority 与 canonical issue ownership |
| 公众号运营 | 人工最终判断与发布 | 维护抓取器或公共数据状态 |

## 3. 默认七项架构决策

1. **独立仓库：** `academic-door/journals` 作为期刊主线仓库，不继续堆入旧的公众号生产目录。
2. **一个引擎：** TOP5 与 Econ Field Journals 共享采集器接口、Schema、质量门和 Composer，不建立两套平行系统。
3. **静态优先：** Astro 构建静态网站，GitHub Pages 免费托管；第一阶段不引入服务器和数据库。
4. **官方优先：** 卷期名单与顺序以期刊官网为准，文章页负责作者、摘要和 DOI，Crossref 只用于补充。
5. **数据与代码分离：** `main` 保存代码、配置和可复现样板；自动更新结果发布到 `data` 分支，部署时只叠加 `public/api/**`、`public/project-manifest.json` 与 `public/backfill-status.md`。静态脚本和样式始终由 `main` 提供。
6. **人工编辑优先：** Composer 第一阶段采用“网页编辑 → 复制富文本到微信”，不把 Notion 或微信 API 设为必经路径。
7. **渐进迁移：** 旧工作流继续服务尚未迁移的论文解读；新系统稳定后按栏目逐步下线旧链路，不一次性破坏生产。

## 4. 统一数据流

```text
期刊官方卷期页
→ 采集卷期名单和官网顺序
→ 采集每篇文章详情
→ 标准化字段和稳定 ID
→ 中文标题 / 摘要与术语处理
→ 完整性、顺序、重复和来源质量门
→ Issue JSON
→ TOP5 / Field 网站
→ Composer
→ 复制到微信后台
→ 人工最终检查并发布
```

数据状态只有四类：

- `detected`：发现新卷期，尚未完成采集。
- `incomplete`：可以浏览，但存在明确列出的缺失字段。
- `ready`：通过当前栏目要求的全部质量门。
- `error`：采集或结构异常，需要系统维护，不要求运营者逐篇排查。

禁止“无提示卡住”。所有不完整状态必须同时输出机器可读 `quality_flags` 和用户可见说明。

## 5. 来源权威与字段契约

来源优先级：

1. 官方卷期页：卷、期、目录名单、文章类型、顺序。
2. 官方文章页：英文标题、作者、英文摘要、DOI、正式链接。
3. Crossref：仅补齐元数据，不得覆盖冲突的官方字段。
4. 其他公开来源：只能作为标明出处的 fallback。

更新发现与完整采集分离：系统每两小时通过 Crossref、官方 RSS 等低成本来源识别变化，只对确认有变化的期刊访问完整卷期与文章页。一次元数据异常不触发人工任务；连续失败达到阈值后才告警。目录名单和顺序通过质量门后，新结果写入 `detected.json` 供网站立即展示；只有英文摘要和中文内容全部通过发布质量门后，才更新 `current.json`。因此最新卷期可以显示“整理中”，上一份可发布数据不会被不完整结果覆盖。

每篇论文至少保留：

- 稳定 `paper_id`
- `sequence` 与 `source_sequence`
- 中英文标题
- 作者
- 中英文摘要
- DOI 与官方链接
- 文章类型
- 字段来源
- 翻译状态和提示词/术语表版本
- 质量标记

每个卷期至少保留：

- 稳定 `issue_id`
- 期刊、卷、期和日期
- 官网总条目数
- 可发布学术内容数，以及研究论文、学术评论和短文的分项数
- 被排除条目及原因
- 详情页失败数
- 抓取时间
- 整体质量状态


统一内容类型与计数口径：

- `research-article`、`comment` 和 `short-communication` 属于可发布学术内容，进入期刊目录和 Composer。
- `correction` 不进入论文目录，但作为“另有 X 篇勘误”单独展示并保留审计记录。
- `editorial`、`front-matter` 和其他非学术条目不进入论文目录，只保留排除原因。
- `official_items` 表示官网或官方订阅源观察到的全部条目；`publishable_items` 表示可发布学术内容；两者不得混用。

## 6. 质量门

进入 `ready` 前至少检查：

1. 官网名单数量与解析数量一致。
2. 研究论文顺序与官网一致且连续。
3. DOI 和稳定 ID 不重复。
4. 作者、英文标题、英文摘要和来源链接完整。
5. 学术性评论与回复按正文保留；卷首、致辞、勘误和其他非学术条目按统一类型留下审计记录。
6. 中文标题与摘要完整；AI 翻译不得伪造缺失原文。
7. Schema 验证通过。
8. 公开数据不含密钥、本机路径、私有稿件或个人账号信息。

站点可以展示 `incomplete` 数据，但不得把它标为已完成；Composer 可以预览它，但在英文摘要和中文内容通过质量门前禁用复制与导出。目录顺序、备用来源、字段来源和完整 `quality_flags` 属于内部质量信息，统一在状态页与公共 API 披露，不在面向读者的卷期内容页显示技术性核验提示。

## 7. Composer public/private boundary

Journals 不再发布完整匿名编辑器。

公开 `/journals/composer/` 的职责只有：

- 展示 bounded read-only Composer Preview / 发布预览；
- 可读取公开 canonical issue 数据生成代表性只读成品；
- 保留 `journal` / `issue` identity handoff；
- 提供 **进入 Composer 工作台** 的 authenticated entry；
- 不暴露 renderer、主题实验、custom CSS、草稿/历史、copy/export 或 authenticated ready payload。

`/journals/themes/` 从 public IA 退役，可保留 noindex compatibility retirement surface。

完整工作台由私有 `academic-door/academic-door-composer` 负责。Journals 的 READY email / upstream sync 继续保持现有 canonical identity 与 sync-before-email contract。

## 8. GitHub 仓库版图

| 仓库 | 职责 |
|---|---|
| `academic-door/academic-door.github.io` | 品牌主页与项目导航 |
| `academic-door/nber-working-papers-cn` | NBER 数据、中文内容与站点 |
| `academic-door/econ-paper-monitor` | 每日论文监测与站点 |
| `academic-door/journals` | 顶刊之门、领域之门、统一期刊引擎、跨刊检索、Status 与 public Composer Preview |
| `academic-door/.github` | Organization 公开介绍 |
| `academic-door/agent-workflow-template` | Agent 协作、PR、验收模板 |

每个项目都应公开一个 `project-manifest.json`，让主页读取项目名称、状态、入口、更新时间和健康地址。主页只做聚合，不复制子项目业务逻辑。

## 9. Agent 分工原则

每个 Agent 只拥有一个清晰边界：

- **主页 Agent：** 读取 Manifest、做导航和品牌体验，不改采集规则。
- **NBER Agent：** 维护 NBER 官方批次与周/月报，不改 Journals Schema。
- **Econ Papers Daily Agent：** 维护每日论文发现和筛选，不负责公众号排版。
- **Journals Agent：** 维护期刊适配器、统一 Schema、质量门、读者站点、public Preview/Entry 与 journal/issue upstream handoff。
- **Composer Agent：** 维护私有编辑工作台、renderer、主题、复制/导出、草稿与 publication history，不重新定义期刊 canonical identity。
- **数据质量 Agent：** 维护 fixture、数量/顺序/重复/来源审计，不直接编辑 UI。

跨仓库需求通过 Issue 和 Project Manifest 协作，不允许 Agent 在未说明的情况下顺手重构其他项目。

## 10. 迁移路线

### Phase 1：纵向样板

- AER 官方目录完整采集。
- TOP5 页面展示。
- Composer 可编辑、换主题、复制。
- GitHub Actions 定时更新和 Pages 部署。

### Phase 2：TOP5 完整化

- 接入 JPE、QJE、RES、Econometrica。
- 建立跨期刊术语表和中文翻译缓存。
- 增加历史卷期、检索、RSS 和更新提醒。

### Phase 3：Econ Field Journals

- 按领域分批接入，不按“见到一个写一个脚本”扩张。
- 优先支持发展、劳动、环境、农业、城市与公共经济学。
- 每种官网平台只写一个可复用适配器。

### Phase 4：内容生产成熟化

- Composer 主题与公众号兼容性回归测试。
- 建立可复用栏目模板但保留自由编辑。
- 依据实际节省时间的数据，再决定是否增加云端稿件和平台 API。

## 11. v1 验收标准

v1 纵向样板完成需同时满足：

- 官方 AER 卷期总数、研究论文数、排除项和顺序可解释。
- 详情页失败数为 0。
- Schema、单元测试、隐私审计、静态构建全部通过。
- GitHub Actions 可在云端完成采集、写入 `data`、触发部署。
- Pages 的期刊门厅、顶刊之门、领域之门、跨刊检索、Status、public Composer Preview 和 JSON API 可访问。
- public Composer Preview 只读且保留 journal/issue handoff；完整编辑/主题/copy-export 在受保护的私有 Composer 中验证。
- 不依赖旧 Notion/微信 API 链路。

## 12. 当前边界

TOP5 第一版已接入 AER、JPE、QJE、RES 与 Econometrica，并为当前卷期生成中英文标题和摘要。目录完整性、来源、翻译数字一致性与隐私检查均进入自动质量门。

部分出版平台会限制自动访问，或官方 RSS 晚于官网卷期页更新。此时引擎会显式记录 fallback 来源与质量标记，并保留上一份可用数据；不得把 Crossref 等补充来源伪装成已完成的官网验证。下一阶段重点是历史卷期、Econ Field Journals 与 Composer 的公众号兼容性回归。

学校图书馆授权、Cookie、验证码与浏览器登录态只能留在运营者本机。它们可通过本机授权补充接口处理极少数缺失内容，但不得进入 GitHub Actions、公共数据或仓库历史，也不能成为无人值守更新的必需依赖。

本机授权补充只处理运营者已经合法获得访问权限的内容，严禁用于绕过验证码、付费墙或出版方访问控制。
