<p align="center">
  <img src="https://academic-door.github.io/assets/academic-door-logo.png" width="144" alt="Academic Door / 学术传送门">
</p>

# Academic Door Journals

Academic Door 的统一经济学期刊数据引擎、TOP5/Field Journals 公共网站与公众号排版工具。

计划公开地址：<https://academic-door.github.io/journals/>

## 产品范围

- `TOP5`：AER、JPE、QJE、RES、Econometrica 的卷期监测与中英文目录。
- `Field Journals`：在统一引擎上按领域扩展期刊，不重复建设平行系统。
- `Academic Door Composer`：载入结构化期刊数据或 Markdown，编辑、预览、换主题并复制富文本到微信公众号。
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
- [x] AER 官方目录采集器初版
- [x] TOP5 页面骨架
- [x] Composer 最小可用版
- [ ] AER 完整中文数据纵向验收
- [ ] JPE / QJE / RES / Econometrica 适配器
- [ ] Field Journals 配置化扩展

## 本地开发

```powershell
pnpm install
pnpm run dev
```

Python 数据检查：

```powershell
python -m pip install -r requirements.txt
python scripts/update_journals.py --journal AER
python -m unittest discover -s tests -v
```

## 公共接口

```text
/journals/api/v1/index.json
/journals/api/v1/collections/top5.json
/journals/api/v1/journals/aer/issues/current.json
/journals/api/v1/health.json
/journals/project-manifest.json
```

## 隐私边界

本仓库不得提交 API Key、微信 AppSecret、邮箱密码、Notion/Zotero 数据、本机绝对路径、未发布稿件、论文 PDF 或完整网页缓存。真实凭据只允许放入 GitHub Actions Secrets 或本地 `.env`。

## 许可

代码采用 [MIT License](LICENSE)。第三方期刊数据和摘要不适用本仓库的软件许可，详见 [DATA_USAGE.md](DATA_USAGE.md)。
