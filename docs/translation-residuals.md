# 翻译残余清单（2026-08-07 更新）

自动管道 + 浏览器授权捕获（内置浏览器 + 已登录机构账号）已处理大部分缺口。本清单只保留仍无法自动完成的条目。

## 本轮已完成（浏览器授权捕获 + reasoner 翻译）

- ERE 89-1：2 篇 Springer 摘要捕获，已发布（tr=7/7）
- JAERE 13-1 / 13-2 / 13-3：11 篇 Chicago 摘要捕获 + 官网目录确认，已发布（tr=8/8 各期）
- JLE（Journal of Labor Economics）43-3 / 44-1 / 44-2：5 篇 Chicago 摘要捕获 + 官网目录确认，已发布（tr=8/8、10/10、10/10）
- 非文章类条目排除（出版社无摘要，官方标注 Editorial/Policy Comment）：wd-186-c（Editorial）、joe-248-c（Editorial）、foodpolicy-137-c 的 Policy Comment（VAT reductions）——对应工作流运行中

## 剩余缺英文摘要（出版社确认无摘要，无法自动补齐）

以下 4 篇为真实研究文章，但 ScienceDirect 预置数据 `hasScholarlyAbstract=false`，RePEc 页面“No abstract is available”，Crossref / OpenAlex / Semantic Scholar 均为空。**无法通过抓取或翻译自动解决，需要运营决策**（标题-only 上线 / 等待出版社补充 / 从 PDF 人工提取）：

| 期刊 | 卷期 | DOI | 标题 |
|---|---|---|---|
| FOODPOLICY | foodpolicy-134-c | 10.1016/j.foodpol.2025.102890 | The policy relevance of maximum residue limits analyses |
| FOODPOLICY | foodpolicy-137-c | 10.1016/j.foodpol.2025.102911 | Evidence for promoting pesticide-free, non-organic cereal production |
| WD | wd-192-c | 10.1016/j.worlddev.2025.107006 | Cracks in the “gold standard”: The Eurocentrism of mining in development economics |
| LUP | lup-153-c | 10.1016/j.landusepol.2025.107544 | Sustainable urban planning for addressing the compounded challenges of rapid urbanization |

## 翻译重试中（有英文摘要，reasoner 批次运行中）

| 期刊 | 卷期 | DOI | 标题 | 状态 |
|---|---|---|---|---|
| JEBO | jebo-240-c | 10.1016/j.jebo.2025.107296 | Faces matter | missing |
| JEBO | jebo-240-c | 10.1016/j.jebo.2025.107344 | Old age allowances and cognitive function | pending |
| JLE | jle-43-2 | 10.1086/727201 | Effect of Business Uncertainty on Turnover | pending |
| JPubE | jpube-257-c | 10.1016/j.jpubeco.2026.105613 | Fiscal consequences of corporate tax avoidance | pending |
| JPubE | jpube-257-c | 10.1016/j.jpubeco.2026.105617 | How do holistic wrap-around anti-poverty programs affect employment | pending |
| LUP | lup-150-c | 10.1016/j.landusepol.2024.107440 | Co-design in policy development | blocked |

## 进行中卷期（出版方未发布完整内容，交给每小时定时任务自动补齐，不处理）

JFE-184、JIE-164、JPubE-262、QE-17-4、TE-21-2/21-3、WD-207/208、LUP-170/171、JEBO-242 特刊等。
