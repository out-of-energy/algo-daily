# 数据字典

数据集版本 `1.0.0`，生成时间见 `data/normalized/manifest.json`。

## 1. 紧凑索引

文件：

- `data/normalized/index.json`：数组，包含全部 4,475 题；
- `data/normalized/index.jsonl`：一行一题，适合流式读取。

字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | string | 数据格式版本，目前 `1.0.0` |
| `uid` | string | 全局唯一 ID，格式 `leetcode-cn:<题号>`；LCP/LCR 等使用原始编号 |
| `source` | string | 来源 ID，目前 `leetcode-cn` |
| `source_id` | string | 来源平台题号，保留 `"1"`、`"LCP 72"`、`"面试题 01.01"` 原样 |
| `frontend_id` | string | 展示用题号，与 `source_id` 相同 |
| `slug` | string | LeetCode 的 URL slug，也是完整记录文件名 |
| `title` | string | 英文标题 |
| `title_zh` | string | 中文标题 |
| `difficulty` | string | `Easy` / `Medium` / `Hard` |
| `difficulty_level` | number | `1` / `2` / `3`，方便排序 |
| `paid_only` | boolean | 是否付费题 |
| `has_statement` | boolean | 本地是否有完整正文；付费题恒为 `false` |
| `statement_lang` | string\|null | 主正文语言：`en` / `zh`；无正文为 `null` |
| `in_scope` | boolean | 是否属于 15 个算法专题 |
| `scope` | string | `algorithm` / `database` / `shell` / `concurrency` / `javascript` / `pandas` / `pending` |
| `primary_topic` | string\|null | 主专题 ID；不在专题内为 `null` |
| `topic_ids` | string[] | 所有关联专题，按匹配分降序，`topic_ids[0] === primary_topic` |
| `topic_scores` | object | 专题 ID → 规则匹配分 |
| `curated_topic_ids` | string[] | 该题命中的精选题单所属专题 |
| `classification_confidence` | string | `high` / `medium` / `low` / `none` |
| `needs_review` | boolean | 是否为待归类题 |
| `tags` | string[] | LeetCode 标签 slug |
| `ac_rate` | number\|null | 通过率，百分数 |
| `likes` / `dislikes` | number\|null | 点赞/点踩，仅免费题详情里有 |
| `official_url` | string | 官方题面链接 |

## 2. 完整题目记录

文件：`data/normalized/problems/<slug>.json`，只有 3,688 道免费题。

除上面的索引字段外，还有：

```json
{
  "schema_version": "1.0.0",
  "uid": "leetcode-cn:200",
  "source": "leetcode-cn",
  "source_id": "200",
  "slug": "number-of-islands",
  "title": "Number of Islands",
  "title_zh": "岛屿数量",
  "difficulty": "Medium",
  "difficulty_level": 2,
  "paid_only": false,
  "has_statement": true,
  "statement_lang": "en",
  "statement": {
    "lang": "en",
    "format": "html",
    "html": "<p>Given an ...</p>",
    "text": "Given an m x n 2D binary grid ...",
    "translated": false,
    "examples": "grid = [...]\n...",
    "sample_testcase": "...",
    "hints": ["..."]
  },
  "topic_scores": {"graph": 10.0},
  "curated_topic_ids": ["graph"],
  "classification_confidence": "high",
  "needs_review": false,
  "topics": [
    {
      "id": "graph",
      "label_zh": "图：BFS · DFS · 拓扑 · 并查集 · 最短路",
      "short_zh": "图",
      "score": 10.0,
      "is_primary": true,
      "curated": true,
      "matched_tags": ["breadth-first-search", "depth-first-search", "union-find"],
      "reason": "curated+tags:breadth-first-search,depth-first-search,union-find"
    }
  ],
  "topic_ids": ["graph"],
  "primary_topic": "graph",
  "classification": {
    "method": "rules-v1",
    "confidence": "high",
    "in_scope": true,
    "scope": "algorithm",
    "needs_review": false,
    "curated_topic_ids": ["graph"]
  },
  "in_scope": true,
  "scope": "algorithm",
  "tags": [{"slug": "array", "name": "Array", "name_zh": "数组"}],
  "tag_slugs": ["array"],
  "tag_names": ["Array"],
  "tag_names_zh": ["数组"],
  "code_snippets": {
    "python3": {"lang": "Python3", "code": "class Solution:\n    ..."},
    "java": {"lang": "Java", "code": "class Solution { ... }"}
  },
  "meta_data": {"name": "numIslands", "params": [], "return": {}},
  "stats": {"ac_rate": 64.2, "total_accepted": "1.5M", "total_submission": "2.4M"},
  "ac_rate": 64.2,
  "likes": 3057,
  "dislikes": 800,
  "official_url": "https://leetcode.cn/problems/number-of-islands/",
  "statement_source": "leetcode.cn",
  "fetched_at": "2026-10-24T04:23:08+00:00",
  "license": "Problem statements are LeetCode's copyrighted content, stored locally for personal study only. Do not redistribute."
}
```

### `statement`

| 字段 | 说明 |
| --- | --- |
| `lang` | 主正文语言：`en` 或 `zh`。3,409 道英文，279 道只有中文正文的题已回填中文 |
| `html` | 官方题面 HTML，可直接渲染；`lang=zh` 时是中文题面 |
| `text` | 去标签后的纯文本，保留示例换行与缩进，适合喂给 AI |
| `translated` | 是否存在中文正文（`lang=zh` 或附带了 `zh` 备用文本） |
| `examples` | 官方示例输入/输出文本 |
| `sample_testcase` | LeetCode 原始样例输入 |
| `hints` | 官方提示 |

### `topics[]`

| 字段 | 说明 |
| --- | --- |
| `id` | 专题 ID，对应 `config/topics.json` |
| `label_zh` / `short_zh` | 展示名称 |
| `score` | 匹配分，用于排序和选主分类 |
| `is_primary` | 是否主分类 |
| `curated` | 是否在人工精选题单中 |
| `matched_tags` | 命中的 LeetCode 标签 |
| `reason` | 可审计的命中原因，例如 `curated+tags:dynamic-programming` |

## 3. 专题文件

`data/normalized/by-topic/<topic-id>.json`：

```json
{
  "schema_version": "1.0.0",
  "topic": {
    "id": "graph",
    "order": 7,
    "label_zh": "图：BFS · DFS · 拓扑 · 并查集 · 最短路",
    "short_zh": "图",
    "patterns": ["网格 DFS/BFS", "拓扑排序", "并查集", "Dijkstra / Bellman-Ford"],
    "tag_weights": {"graph": 4.0},
    "must_ids": [133, 200, 207],
    "exclude_tags": ["tree", "binary-tree", "binary-search-tree"]
  },
  "count": 374,
  "primary_count": 295,
  "free_with_statement_count": 308,
  "problems": [
    {
      "...": "紧凑索引字段",
      "topic_match": {
        "score": 10.0,
        "is_primary": true,
        "matched_tags": ["graph"],
        "reason": "curated+tags:graph"
      }
    }
  ]
}
```

排序：主分类在前，然后按难度、题号。这道题也会出现在它的其他关联专题里。

## 4. 特殊集合

- `data/normalized/pending-classification.json`：LeetCode 未提供标签的题，共 89 道（56 免费 / 33 付费）。不猜测分类。
- `data/normalized/out-of-scope.json`：394 道非 15 专题题，`scope` 为 `database` / `shell` / `concurrency` / `javascript` / `pandas`。
- `data/normalized/topics.json`：专题元数据、标签权重、精选题 ID、排除规则、各专题计数。

## 5. SQLite

文件：`data/algorithms.db`。

表：

| 表 | 内容 |
| --- | --- |
| `problems` | 全量 4,475 题；免费题含 `statement_lang` / `statement_html` / `statement_text` |
| `tags` | 176 个标签 |
| `problem_tags` | 题目-标签多对多 |
| `topics` | 15 个专题，含 `must_ids_json`、`tag_weights_json` |
| `problem_topics` | 题目-专题多对多，含 `score`、`is_primary`、`curated`、`matched_tags_json` |
| `sources` | 来源与许可证说明 |
| `meta` | schema 版本、生成时间、FTS tokenizer |
| `problems_fts` | FTS5 索引（trigram），用于英文/长词检索 |

视图：

- `v_problem_list`：常用列表字段；
- `v_daily_candidates`：`in_scope=1 AND paid_only=0 AND has_statement=1`，目前 3,465 题；
- `v_pending_classification`：待归类题。

常用查询：

```sql
-- 每个专题有多少题
SELECT t.label_zh, COUNT(*) AS n
FROM topics t
JOIN problem_topics pt ON pt.topic_id = t.id
GROUP BY t.id
ORDER BY t.sort_order;

-- 双指针专题里，免费且 Medium/Hard 的题
SELECT p.frontend_id, p.title_zh, p.difficulty, p.slug
FROM problems p
JOIN problem_topics pt ON pt.problem_uid = p.uid
WHERE pt.topic_id = 'two-pointers-sliding-window'
  AND p.paid_only = 0
  AND p.has_statement = 1
  AND p.difficulty_level >= 2
ORDER BY p.likes DESC
LIMIT 20;
```

注意：FTS5 的 trigram 分词对中文两字词不可靠，本项目的查询 API 用 `LIKE` 兜底。中文检索优先用 `algo_store.py list --search`。

## 6. 分类字段的边界

- `in_scope=true` 不代表本地一定有正文：527 道付费题有标签、进了专题，但 `has_statement=false`。
- `paid_only=true` 的记录一定没有本地正文。
- `pending` 不在 15 个专题内，但保留在索引和 SQLite 中。
- 精简索引里的 `tags` 是标签 slug 数组；完整记录里的 `tags` 是对象数组。
- `primary_topic` 只在 `in_scope=true` 时非空。
