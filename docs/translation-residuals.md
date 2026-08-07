# 翻译残余清单（2026-08-07 收盘）

自动管道 + 浏览器授权捕获 + 手工校正翻译已把所有可自动解决的缺口清零。**线上 485 个卷期全部双语 100% 完整**，仅剩 5 个卷期未发布，原因均为“出版社侧无法自动补齐”，需要运营决策。

## 剩余 5 个未发布卷期

### 1–4：出版社不提供摘要的研究文章（4 篇）

ScienceDirect 预置数据 `hasScholarlyAbstract=false`，RePEc “No abstract is available”，Crossref / OpenAlex / Semantic Scholar 均为空。**无法自动补齐**（标题-only 上线 / 等待出版社补充 / 从 PDF 人工提取，三选一）：

| 期刊 | 卷期 | DOI | 标题 |
|---|---|---|---|
| FOODPOLICY | foodpolicy-134-c | 10.1016/j.foodpol.2025.102890 | The policy relevance of maximum residue limits analyses |
| FOODPOLICY | foodpolicy-137-c | 10.1016/j.foodpol.2025.102911 | Evidence for promoting pesticide-free, non-organic cereal production |
| WD | wd-192-c | 10.1016/j.worlddev.2025.107006 | Cracks in the “gold standard”: The Eurocentrism of mining in development economics |
| LUP | lup-153-c | 10.1016/j.landusepol.2025.107544 | Sustainable urban planning for addressing the compounded challenges of rapid urbanization |

### 5：JEBO 242 特刊（2026 年 2 月）

官网目录显示约 23 篇文章，staging 只收集到 8 篇（Crossref/RePEc 兜底不全）。完整性守卫按设计拦截。需要一轮完整的“浏览器授权目录+摘要”捕获（含特刊导言，约 15 篇），可在下轮用 `import_browser_authorized_snapshot` 流程补齐。

## 本轮（2026-08-07）完成清单

- ERE 89-1、JAERE 13-1/2/3、JLE（JOLE）43-2/43-3/44-1/44-2、WD 186、JOE 248、LUP 150、JEBO 240、JPubE 257、EJ 135-667、JEH 85-1/86-1：全部发布，双语 100%
- 修复的代码缺陷（均已测试）：
  - 数字校验器：CJK 标识符跨“标题/摘要”换行误粘连
  - 数字校验器：标识符与数据同值时豁免逻辑误吞数据值
  - Elsevier 致谢脚注剥离：digit-fused 标记（Crossref 风格）与 35% 位置阈值
  - 分类器：Editors’ Report 归为 editorial 前置内容
- 手工校正翻译（operator-manual）：JEBO 107296/107344、JPubE 105613/105617、EJ ueae108，均通过数字保真校验
- 测试：299 项全绿
