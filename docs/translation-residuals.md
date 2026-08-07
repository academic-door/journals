# 翻译残余清单（2026-08-07 收盘 v2）

自动管道 + 浏览器授权捕获 + 手工校正翻译已把所有可自动解决的缺口清零。**线上 486 个卷期全部双语 100% 完整**，仅剩 4 个卷期未发布，原因均为“出版社不提供摘要”，需要运营决策。

## 剩余 4 个未发布卷期：出版社无摘要（各缺 1 篇）

ScienceDirect 预置数据 `hasScholarlyAbstract=false`，RePEc “No abstract is available”，Crossref / OpenAlex / Semantic Scholar 均为空。**无法自动补齐**（标题-only 上线 / 等待出版社补充 / 从 PDF 人工提取，三选一）：

| 期刊 | 卷期 | DOI | 标题 |
|---|---|---|---|
| FOODPOLICY | foodpolicy-134-c | 10.1016/j.foodpol.2025.102890 | The policy relevance of maximum residue limits analyses |
| FOODPOLICY | foodpolicy-137-c | 10.1016/j.foodpol.2025.102911 | Evidence for promoting pesticide-free, non-organic cereal production |
| WD | wd-192-c | 10.1016/j.worlddev.2025.107006 | Cracks in the “gold standard”: The Eurocentrism of mining in development economics |
| LUP | lup-153-c | 10.1016/j.landusepol.2025.107544 | Sustainable urban planning for addressing the compounded challenges of rapid urbanization |

## 本轮新增完成（2026-08-07 第二波）

- **JEBO 242 特刊（2026 年 2 月）**：官网目录完整捕获（22 项 → 19 篇研究文章 + 3 项排除：Editorial Board 与 2 篇特刊导言）。新增 11 篇摘要/作者/DOI（Crossref 补作者），reasoner 翻译全部通过，**已发布 tr=19/19**。
- 修正状态文件阻塞标记（blocked → incomplete），使浏览器授权目录可被工作流接管。
- 测试仍为 299 项全绿。

## 进行中卷期（出版方未发布完整内容，交给每小时定时任务自动补齐）

JFE-184、JIE-164、JPubE-262、QE-17-4、TE-21-2/21-3、WD-207/208、LUP-170/171 等。
