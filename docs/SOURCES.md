# 数据来源与许可证

## 当前已导入

### LeetCode 中文站

- 主页：<https://leetcode.cn/problemset/>
- 使用接口：`https://leetcode.cn/graphql/`
- 导入范围：
  - 4,475 道题的公开元数据：题号、中英文标题、难度、标签、付费状态、通过率；
  - 3,688 道免费题的完整英文题面、示例、提示、代码模板；
  - 787 道付费题只保留元数据和官方链接，**没有请求或保存付费题正文**。
- 正文语言：英文为主；279 道只有中文正文的题已通过 `translatedContent` 回填中文。标题均提供中文。
- 版权：题面、示例、官方代码模板归 LeetCode 所有。数据集只用于本机个人学习，不应公开分发、再发布或商用。本仓库不提交任何题面数据（`data/` 已在 `.gitignore` 中），使用者通过 `make data` 在本地自行获取。

## 已评估、但未导入的 GitHub 仓库

这些仓库主要提供题解、题单或与 LeetCode 重复的数据，不会增加新的题目 ID；部分仓库没有明确许可证，所以没有把内容并入数据集。

| 仓库 | 内容 | 许可证 | 处理 |
| --- | --- | --- | --- |
| [doocs/leetcode](https://github.com/doocs/leetcode) | 多语言题解，覆盖 LeetCode / 剑指 Offer / 面试金典 | CC-BY-SA-4.0 | 可作为后续题解来源，需署名并保持相同许可证 |
| [neetcode-gh/leetcode](https://github.com/neetcode-gh/leetcode) | NeetCode 题解代码 | MIT | 可作为后续代码/题单来源 |
| [labuladong/fucking-algorithm](https://github.com/labuladong/fucking-algorithm) | 算法教程与题解 | 仓库未声明明确许可证 | 只作为学习参考链接，不复制正文 |
| [neenza/leetcode-problems](https://github.com/neenza/leetcode-problems) | LeetCode 题目 JSON 数据 | 仓库未声明明确许可证 | 与已抓取数据重复，不导入 |

说明：「公开可见」不等于「可以任意再分发」。如果后续要接 GitHub 题解，会按仓库许可证保留作者、仓库地址和许可证文本。

## 为什么暂时没有接其他 OJ

其他平台（LintCode、牛客、Codeforces、AtCoder 等）的题目结构、难度体系、标签体系和访问条款都不同，不能直接混进同一张表而不再做映射。当前 schema 已经预留：

```json
{
  "uid": "<source>:<source_problem_id>",
  "source": "<source>",
  "source_id": "<platform id>"
}
```

后续每接一个平台，只需要增加一个 adapter：

1. 拉取元数据（题号、标题、难度、标签、是否付费）；
2. 免费题才抓正文；
3. 把平台标签映射到 `config/topics.json` 的 15 个专题；
4. 重新运行 `scripts/build_dataset.py`；
5. 用 `scripts/validate_dataset.py` 校验。

这样 `data/normalized/index.json` 和 SQLite 仍然是统一入口，不会因为多平台而分裂成多套查询方式。

## 付费题处理原则

- 不绕过登录、订阅、付费墙或反爬限制去获取付费题正文。
- 付费题只保存公开可见的标识信息，并保留官方链接。
- 如果本机已经有合法的 LeetCode 订阅，仍建议直接在官网阅读付费题；本数据集不会把订阅内容复制到本地。

## 使用边界

这份数据默认是**本地个人学习用途**：

- 不要把 `data/normalized/problems/`、`data/raw/leetcode/problems/` 或 `data/algorithms.db` 上传到公开仓库；
- 不要把题面批量做成公开网站或商业产品；
- 引用题目时保留官方链接；
- 如果接入开源题解，按对应仓库的许可证处理署名与相同方式共享要求。
