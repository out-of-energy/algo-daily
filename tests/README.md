# 数据存储测试

这些测试验证的是「题库存储得是否合理」，不是题目算法本身。

运行：

```bash
python3 -m unittest discover -s tests -v
# 或
make test
```

覆盖范围：

| 测试文件 | 关注点 |
| --- | --- |
| `test_catalog_integrity.py` | 索引、manifest、JSONL、专题文件、精选题单、字段域、计数快照 |
| `test_statements.py` | 3,688 道免费题正文、279 道中文回填、HTML→text、代码模板、索引与完整记录一致 |
| `test_classification.py` | 15 个代表题、主分类规则、树/图排除、待归类不猜测、分类结果可重放 |
| `test_sqlite.py` | SQLite 与 JSON 对账、付费题零正文、视图、标签多对多、FTS、专题计数 |
| `test_store_api.py` | `algo_store.py` 的筛选、计数、随机抽题、中英文搜索、导出、付费题读取 |
| `test_daily_app.py` | 30 天专题覆盖、每日选题、AI JSON 归一化、动画场景校验与状态继承、代码沙箱执行校验、HTML 转义、邮件 dry-run、HTML 附件 |

测试依赖本地题库：没有构建时，数据相关测试会被跳过并提示先运行 `make data`。

`tests/support.py` 里的 `EXPECTED` 是 2026-10-24 参考快照的计数。本地数据正好是这份快照时，计数必须完全相等，意外的数据丢失不会被悄悄接受；如果是自己新抓取的数据（LeetCode 题目会增加），则改为用本地 `manifest.json` 的计数校验索引、JSONL、SQLite 之间是否一致。有意把新数据定为参考快照时，更新 `EXPECTED` 即可。

测试不写数据文件；导出测试和邮件测试使用临时目录。当前共 98 个测试，其中 8 个纯逻辑测试（动画 DSL、结果比较、JSON 修复）不依赖题库。
