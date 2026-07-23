# Academic Door GitHub 子站建设手册

本文用于把一个新项目交给独立 Agent。目标是让所有子站能够独立开发、自动部署，又能稳定接入 Academic Door 主页。

## 1. 新项目启动

1. 在 `academic-door` Organization 下建立独立仓库。
2. 仓库名使用小写英文和连字符，能直接作为 GitHub Pages 路径。
3. 本地使用独立目录或 Codex 工作树，不在旧生产仓库里直接开发。
4. 默认分支为 `main`。
5. 新增 `README.md`、`AGENTS.md`、`LICENSE`、`.gitignore` 和隐私审计。
6. 公开仓库不得包含密钥、账号、私有稿件、PDF、完整网页缓存或本机路径。

## 2. 推荐目录

```text
project/
├─ .github/workflows/      # 测试、更新、部署、健康检查
├─ config/                 # 项目与来源配置
├─ collectors/             # 官方数据采集器
├─ data/                   # 小型可复现 fixture / 翻译缓存
├─ schemas/                # 公共数据契约
├─ scripts/                # 更新、校验和隐私审计入口
├─ public/
│  ├─ api/v1/              # 公共 JSON
│  └─ project-manifest.json
├─ src/                    # 网站与交互工具
├─ tests/                  # Schema 和业务不变量测试
└─ docs/                   # 架构、接入和维护说明
```

大型原始数据和自动生成历史不要提交到 `main`。需要持续发布的数据使用 `data` 分支、Release、对象存储或分片文件。

## 3. 网站技术路线

默认使用 Astro 静态站点：

- 不需要服务器。
- 可直接部署 GitHub Pages。
- 页面可读取同仓库 JSON。
- 后续可以渐进增加客户端搜索和编辑器。

基础配置：

```js
export default defineConfig({
  site: "https://academic-door.github.io",
  base: "/repository-name",
  output: "static",
});
```

所有静态资源、内部链接和数据请求都必须使用 `BASE_URL`，不能假定部署在域名根路径。

## 4. 项目 Manifest

每个子站在 `/project-manifest.json` 提供至少：

```json
{
  "schema_version": "1.0",
  "project_id": "journals",
  "name": "Academic Door Journals",
  "status": "active",
  "homepage": "https://academic-door.github.io/journals/",
  "health_url": "https://academic-door.github.io/journals/api/v1/health.json",
  "updated_at": "ISO-8601"
}
```

主页 Agent 只能依赖这个稳定接口，不读取子项目内部文件结构。

## 5. GitHub Actions 四条基础链路

### Test

在 push 和 pull request 时执行：

- 单元测试。
- Schema 验证。
- 隐私审计。
- 静态网站构建。

### Update

定时或手动执行：

- 从官方来源采集。
- 运行质量门。
- 生成公共 JSON。
- 发布到 `data` 分支。
- 触发 Pages 部署。

### Deploy

- checkout `main`。
- 叠加 `data` 分支的公共数据。
- 构建静态站点。
- 使用 GitHub Pages Actions 部署。

### Health

- 定时访问主页、数据接口和 Manifest。
- 检查 HTTP 状态和关键字段。
- 失败时创建或更新 GitHub Issue，不静默失败。

## 6. 分支和 PR

`main` 默认保护：

- 禁止删除。
- 禁止强推。
- 代码变更走 Pull Request。
- `test` 状态通过后才能合并。
- 单人维护阶段不强制第二账号审批，但合并动作本身仍由维护者确认。

数据机器人只写 `data` 分支，不修改 `main`。

## 7. 新期刊接入

Agent 的交付物必须包括：

1. 期刊配置。
2. 官方卷期 fixture。
3. 可复用平台采集器或明确说明为何必须专用。
4. 官网总条目、研究论文、排除项和顺序审计。
5. 至少一个 Schema 测试和一个业务不变量测试。
6. 公开页面和 JSON API。
7. 健康检查。
8. 来源、版权和已知限制说明。

接入不以“网页上看到了”为完成标准，而以官方数量、顺序、字段来源和远端部署回读为标准。

## 8. Agent 任务模板

把下面内容连同本手册交给新 Agent：

```text
你负责 Academic Door 的【项目名】。

边界：
- 只修改【仓库名】。
- 使用 Academic Door 公共 Schema、Manifest、导航和隐私规则。
- 官方来源优先；数量、顺序、排除项和缺失字段必须可审计。
- 不把密钥、本机路径、私有稿件、PDF 或完整网页缓存提交到 GitHub。
- 外部写入后必须从 GitHub 和公开 Pages 回读验证。

交付：
1. 数据采集与质量门；
2. 公共 JSON；
3. 网站页面；
4. 测试、更新、部署和健康工作流；
5. README 与已知限制；
6. PR 和公开站点验收证据。
```

## 9. 上线验收

- GitHub 仓库默认分支为 `main`。
- Test 工作流绿色。
- Pages 使用 GitHub Actions 部署且 HTTPS 开启。
- 公开页面和数据接口返回 200。
- Manifest 能被主页读取。
- 数据更新时间来自最新自动更新。
- 浏览器无控制台错误。
- 手机宽度可用。
- 隐私审计为 0。
- README 明确数据来源、项目状态和已知缺口。

## 10. 变更原则

先打通一条纵向链路，再横向扩期刊；先减少运营时间，再增加自动化层。任何新增组件都必须回答两个问题：

1. 它是否明显减少了运营者每天的操作？
2. 它是否提高了数据可靠性或公共可用性？

两者都不是，就不进入主线。
