# 官网顺序的人工确认与溯源台账

## 为什么需要它

采集器每一轮都在自证：它这次确实到达了官网卷期页，如果出版方明天开始封锁，`official_verified` 会自动掉回 `pending_official`。这是一个会自我纠正的断言。

人工用已登录浏览器确认官网目录不具备这个性质。结论一旦写入，就会被 `update_journals.py` 的溯源保护持久化——只要文章 DOI 集合不变就一直有效，而编号卷期的 DOI 集合可能长期不变。也就是说，公开数据对外宣称"官网目录已核对"，而支撑它的证据如果只存在于某台本机上，CI 复核不了、协作者复核不了、半年后的自己也复核不了。

人工核验通道本身是架构 §5 允许的（见 #11），但**人工通道的前提是可审计**，否则从外部看，它与直接把标记改成 `true` 没有区别。这个台账就是用来补上这一环的。

## 记录了什么，不记录什么

台账文件：`data/provenance/order-verification.json`（进仓库，可审计）

每条记录只包含可公开的字段：

| 字段 | 含义 |
|---|---|
| `journal_id` / `issue_id` | 确认的是哪一刊、哪一期 |
| `official_url` | 当时访问的官网卷期页 |
| `captured_at` | 确认时间 |
| `item_count` | 官网条目数 |
| `sequence_sha256` | 顺序摘要，供后续证明"看的是同一份顺序" |
| `expires_at` | 有效期，默认 90 天 |

**不记录**：页面全文、PDF、Cookie、账号、本机绝对路径。存顺序摘要而非完整清单，既让台账保持精简，也避免把出版方的目录数据整份复制进仓库。

## 用法

登记一次确认：

```powershell
python scripts/provenance_ledger.py record `
  --journal-id eer `
  --issue-id eer-189-c `
  --official-url "https://www.sciencedirect.com/journal/european-economic-review/vol/189/suppl/C" `
  --identifiers "S0014292126000425,S0014292126000431"
```

查看台账与新鲜度：

```powershell
python scripts/provenance_ledger.py list
```

## 对账与升级路径

`scripts/audit_public_data.py` 会检查：公开数据里带 `browser_order_verification` 的期刊，台账中必须有**同一卷期**且**未过期**的记录。三种情况会被指出：

- 声称已核对，但台账无记录；
- 台账记录的卷期与线上卷期不一致（说明卷期已滚动，旧确认不再适用）；
- 确认已超过有效期。

**当前为警告级**（打印 `WARN provenance …`，不影响退出码），这样在台账补齐之前不会打断 CI。台账填好后，在工作流里给审计加 `--strict-provenance`，缺失或过期的凭据即判失败：

```yaml
python scripts/audit_public_data.py --strict-provenance
```

## 有效期到了怎么办

重新确认一次并 `record` 覆盖即可。如果暂时无法重新确认，正确的做法是让该刊的顺序回落为 `pending_official`——读者看到"目录顺序待官网复核"是准确的，而看到一条已经过期两年的"已核对"则不是。
