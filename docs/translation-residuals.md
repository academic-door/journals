# 翻译残余清单（2026-08-07 最终）

自动管道 + 浏览器授权捕获 + 手工校正翻译已处理所有可自动解决的缺口。本清单只保留出版社本身不提供摘要、无法自动补齐的条目。

## 本轮已完成（2026-08-07）

- ERE 89-1：2 篇 Springer 摘要浏览器捕获，已发布（tr=7/7）
- JAERE 13-1/13-2/13-3：11 篇 Chicago 摘要捕获 + 官网目录确认，已发布（tr=8/8 各期）
- JLE（Journal of Labor Economics）43-2/43-3/44-1/44-2：6 篇 Chicago 摘要捕获（含 43-2 坏摘要修复）+ 官网目录确认，已发布或发布中
- WD 186、JOE 248：Editorial 无摘要条目正式排除，卷期已发布
- LUP 150：特刊导言（无摘要）正式排除，卷期发布中
- JEBO 240、JPubE 257：4 篇数字保真困难文章经手工校正翻译（operator-manual）通过校验，发布中
- 修复两个校验器边界缺陷（标题/摘要换行粘连、标识符与数据同值互斥），299 项测试全绿

## 唯一剩余缺口：出版社无摘要的研究文章（4 篇）

ScienceDirect 预置数据 `hasScholarlyAbstract=false`，RePEc “No abstract is available”，Crossref / OpenAlex / Semantic Scholar 均为空。**无法自动补齐，需要运营决策**（标题-only 上线 / 等待出版社补充 / 从 PDF 人工提取）：

| 期刊 | 卷期 | DOI | 标题 |
|---|---|---|---|
| FOODPOLICY | foodpolicy-134-c | 10.1016/j.foodpol.2025.102890 | The policy relevance of maximum residue limits analyses |
| FOODPOLICY | foodpolicy-137-c | 10.1016/j.foodpol.2025.102911 | Evidence for promoting pesticide-free, non-organic cereal production |
| WD | wd-192-c | 10.1016/j.worlddev.2025.107006 | Cracks in the “gold standard”: The Eurocentrism of mining in development economics |
| LUP | lup-153-c | 10.1016/j.landusepol.2025.107544 | Sustainable urban planning for addressing the compounded challenges of rapid urbanization |

这 4 篇所在的 4 个卷期（foodpolicy-134-c、foodpolicy-137-c、wd-192-c、lup-153-c）因此保持未发布，其余文章均已双语完整。

## 进行中卷期（出版方未发布完整内容，交给每小时定时任务自动补齐）

JFE-184、JIE-164、JPubE-262、QE-17-4、TE-21-2/21-3、WD-207/208、LUP-170/171、JEBO-242 特刊等。
