# 翻译残余清单（2026-08-07 收盘 v2）

自动管道 + 浏览器授权捕获 + 手工校正翻译已把所有可自动解决的缺口清零。**线上 486 个卷期全部双语 100% 完整**，仅剩 4 个卷期未发布，原因均为“出版社不提供摘要”，需要运营决策。

## 2026-08-08 新增：A 类 8 本 Elsevier 领域刊上线（2025–2026）

新增 JHE（卫生经济学）、JCE（比较经济学）、CER（中国经济评论）、Energy Economics（能源经济学）、Ecological Economics（生态经济学）、Labour Economics（劳动经济学）、RED（经济动态评论）、JEDC（经济动态与控制）8 本刊，全部接入官方/RePEc 目录与双语翻译。**113 个发现卷期中 110 期已 100% 双语完整上线**，仅剩 3 个出版社尚未定稿的进行中最新卷（labeco-102-c、jedc-191-c、ecolecon-250-c），由每小时定时任务在出版社定稿后自动补齐。

本轮处理要点：
- 5 篇官方类型为 Editorial/News 的特刊导言（CER 102675、ECOLECON 108846、ENERGY 108117/109266/109312）按前页内容排除，不再阻塞整期。
- 3 篇深模型反复失败的数字保真硬骨头（ENERGY 109456、ENERGY 109521、LABECO 102804）以 operator-manual 渠道人工译文补齐并本地校验通过。
- 采集/更新管线加固：JCE 一卷多期按“卷+期”匹配；RED 的 RePEc 卷标题格式回退到出版社 RSS；回填失败也保留已采集成果；新刊缺 current.json 时以最新归档兜底；fields 页期刊数改为动态读取。

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
