# 每日程序：配置与运行

## 它每天做什么

```text
SQLite 题库
   │  30 天窗口专题严格轮转（每 15 天各一次）；近 180 天不重复；精选/热度加权
   ▼
随机一道免费题
   │  读取本地完整题面
   ▼
AI 生成结构化 JSON
   │  人话解释 / 思路 / 分帧动画 / 代码 / 自检
   ▼
单文件 HTML
   │  自包含 CSS + JS，可离线播放动画
   ├── 写入 site/day/YYYY-MM-DD.html
   ├── 更新 site/index.html 归档
   └── 生成邮件静态预览 + 附件
           ▼
       SMTP 发信
```

- 专题选择是严格轮转：先选近 30 天出现次数最少的专题，次数相同则选最久没出现的；只有从未出现的专题之间才随机。每天成功运行时，任意连续 15 天里 15 个专题各出现一次，任意 30 天内各出现两次，且不会连续两天出现同一专题。
- 覆盖按“本次要补的专题”（history 中的 `coverage_topic`）统计，而不是按题目的主专题统计；如果目标专题没有可用题目而退到任意专题，`selection.coverage_satisfied` 会记为 `false`，控制台也会给出警告。旧历史只有 `topic` 字段时自动回退使用它。
- 同一专题内优先没做过的精选题；具体题目按 curated + likes 的对数权重抽取，避免永远只抽到同一批超高频题。
- 难度按最近 7 天 2:4:1（简单:中等:困难）软配额：先在已选专题内找当天最缺的难度，题池不足时放宽难度，不会为了难度更换专题。配置见 `config/app.json` 的 `selection.difficulty_mix`；删除该项即关闭。指定 `--difficulty` 时以指定为准。
- 历史记录在 `data/daily/history.json`，默认避开近 180 天已抽过的题；覆盖计数是滚动的，不要求自然月对齐。
- 每天为一次运行；如果某天没开机，下一天会按实际历史继续补覆盖，而不是跳过。
- 分析 JSON 在 `data/daily/YYYY-MM-DD.json`；重跑同一天默认不会重复调用 AI。
- 页面在 `site/day/YYYY-MM-DD.html`，完全自包含，不依赖 CDN。

## 1. 配置

### 复用已有的单词项目

如果 `~/Library/LaunchAgents/dev.love.daily.plist` 已存在，可以直接引用它的凭据文件：

```bash
python3 scripts/reuse_love_config.py          # 先预览
python3 scripts/reuse_love_config.py --write  # 写入 .env，默认 dry-run
```

它只读取 plist 里的 `SMTP_USER`、`LOVE_MAIL_TO`、`SMTP_PASSWORD_FILE`、`DEEPSEEK_API_KEY_FILE` 等**路径和地址**，不读取、不打印密码/key 内容。生成的 `.env` 权限为 600。

这种方式下 `.env` 里只有凭据文件的路径，不包含密码/key 内容。

### 从零配置

推荐用交互式脚本，密码输入不会显示，也不会出现在聊天记录里：

```bash
python3 scripts/setup.py
```

它会写入项目根目录 `.env`，权限设为 600。也可以手动复制：

```bash
cp .env.example .env
$EDITOR .env
```

### AI

DeepSeek：

```dotenv
AI_API_KEY=sk-...
AI_BASE_URL=https://api.deepseek.com/v1
AI_MODEL=deepseek-v4-flash
```

OpenAI 或其他兼容服务：

```dotenv
AI_API_KEY=...
AI_BASE_URL=https://api.openai.com/v1
AI_MODEL=gpt-4.1-mini
```

调用的是 OpenAI-compatible `POST /chat/completions`，JSON mode 不被支持时会自动去掉 `response_format` 重试。AI 输出必须通过结构校验，否则不会生成页面、不会发信。

### SMTP

| 邮箱 | SMTP_HOST | 端口 | 加密 | 密码 |
| --- | --- | ---: | --- | --- |
| QQ 邮箱 | `smtp.qq.com` | 465 | `ssl` | QQ 邮箱「授权码」 |
| Gmail | `smtp.gmail.com` | 465 | `ssl` | Google App Password |
| 163 邮箱 | `smtp.163.com` | 465 | `ssl` | 客户端授权码 |
| Outlook | `smtp-mail.outlook.com` | 587 | `starttls` | 账户密码 / App Password |

```dotenv
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_SECURITY=ssl
SMTP_USER=you@qq.com
SMTP_PASS=授权码
MAIL_FROM=you@qq.com
MAIL_TO=you@qq.com
```

注意：这里填的是邮箱的**SMTP 授权码 / App Password**，不是网页登录密码。QQ 邮箱需要在「设置 → 账户」开启 SMTP 服务并生成授权码。

### 邮件 dry-run

```dotenv
MAIL_DRY_RUN=1
```

`1` 时只写 `.eml` 到 `data/daily/outbox/`，不真的发信。确认预览正常后改成 `0`，或者运行：

```bash
python3 scripts/run_daily.py --send
```

### 公网链接

如果页面部署到了 GitHub Pages / Cloudflare Pages / 自己的服务器，填写：

```dotenv
PUBLIC_BASE_URL=https://your-name.github.io/algo-daily
```

邮件里就会出现「打开完整动画页」的链接。留空时，邮件会提示打开附件 HTML。

## 2. 先预览

```bash
# 配置自检（不会输出密码）
python3 scripts/doctor.py

# 内置示例：不调用 AI、不写历史、只生成 .eml
python3 scripts/run_daily.py --demo

# 本地打开
python3 scripts/serve.py --open
# http://127.0.0.1:8765/demo.html
```

生成的 `.eml` 在：

```text
data/daily/outbox/2026-10-24-demo.eml
```

可以用 macOS「邮件」或任意 `.eml` 阅读器打开检查。

## 3. 真实运行

```bash
# 真实 AI，但只写 .eml
python3 scripts/run_daily.py --dry-run

# 指定专题 / 难度
python3 scripts/run_daily.py --topic graph --difficulty Medium --dry-run

# 指定题目（调试用）
python3 scripts/run_daily.py --slug number-of-islands --dry-run

# 真实发信
python3 scripts/run_daily.py
# 或强制忽略 MAIL_DRY_RUN
python3 scripts/run_daily.py --send
```

常用参数：

| 参数 | 作用 |
| --- | --- |
| `--date YYYY-MM-DD` | 生成指定日期，默认按 `Asia/Shanghai` 取今天 |
| `--topic <id>` | 指定专题，如 `dynamic-programming` |
| `--difficulty Easy/Medium/Hard` | 指定难度 |
| `--slug <slug>` | 指定题目 |
| `--demo` | 内置示例，不调用 AI、不写历史、默认 dry-run |
| `--dry-run` | 只写 `.eml` |
| `--send` | 强制真实发送 |
| `--no-email` | 只生成页面 |
| `--force` | 重新生成当天分析（默认沿用当天已有结果） |
| `--reroll` | 配合 `--force`，重新随机选题 |
| `--resend` | 复用已有分析，只重发邮件 |
| `--json` | 输出机器可读结果，适合脚本/日志 |

## 4. 定时任务

macOS 使用 `launchd`：

```bash
# 每天 08:00
python3 scripts/schedule.py install --time 08:00

# 状态
python3 scripts/schedule.py status

# 卸载
python3 scripts/schedule.py uninstall
```

相关路径：

- plist：`~/Library/LaunchAgents/com.kk.algorithm.daily.plist`
- 日志：`data/daily/logs/launchd.out.log`、`launchd.err.log`

注意：

- Mac 关机时不会执行；睡眠时可能延迟到唤醒后。
- 首次安装前确保 `python3 scripts/run_daily.py --dry-run` 已成功。
- 定时任务使用当前 Python 的绝对路径；如果换了 Python 环境，需要重新安装。
- 不建议把 `.env` 放在 iCloud 同步目录或公开仓库。

## 5. 邮件和动画的边界

邮件客户端普遍不支持 JavaScript，所以：

- 邮件正文：排版好的静态摘要 + 第一个关键决策帧（没有则为最后一帧）的 HTML 可视化。
- 邮件附件：完整的单文件 HTML，包含播放/暂停/上一步/下一步/倍速的 CSS + JS 动画。
- 如果配置了 `PUBLIC_BASE_URL`：邮件里会有网页链接，手机上直接打开。

动画是一个「场景」（visual v2，定义在 `scripts/daily_app/visual.py`）：1–4 个面板同时显示，
每帧只写变化的字段，归一化时按「持久字段继承、瞬时高亮每帧清空」展开成完整快照。
渲染器复用 DOM，所以指针会平滑移动、变化的值会闪烁并显示 +1/-1，并同步高亮右侧代码行。

面板类型：

- `array`：数组、窗口、指针；值可以逐帧修改（一维 DP、原地交换）；
- `map`：哈希表、计数器、标量变量；
- `stack` / `queue`：单调栈、调用栈、BFS 队列；
- `grid`：网格 BFS/DFS、矩阵；
- `table`：DP 表，支持逐格填值和依赖格高亮；
- `linked_list`：链表，`next` 可变，反转时箭头真正掉头；
- `graph` / `tree`：图或树，树自动分层布局，支持边权和节点注释；
- `cards`：设计题步骤、候选方案对比（选中/否决）。

校验是严格的：越界下标、不存在的面板/节点/边、重复 caption、`line` 写成行号或指向空行、
最后一帧没有结果，都会带着具体错误回传给模型修正（首次调用加最多 2 次重试，共 3 次尝试）。
旧格式（v1 的 `type` + `data`）在渲染时会自动升级，已有页面可以直接重新生成。

### 发布前的执行校验（`scripts/daily_app/verify.py`）

动画和代码在发布前会在沙箱子进程里真实运行（`python -I`、临时目录、CPU/内存/文件大小限制、
单用例 2 秒、总计 10 秒，可在 `config/app.json` 的 `verify` 里调整）：

1. **官方示例**：输入来自题库 `statement.examples`，期望输出从题面 `Output:` 解析；代码必须全部通过。
2. **边界用例**：模型在 `tests` 里按 traps 写 2–5 个用例，expected 要求按题意独立推算；与代码实际输出不一致就打回。
3. **动画例子**：`visual.test` 是动画所用例子的结构化输入/结果；代码实际输出必须等于它，最后一帧 badge 必须写出返回值。

支持普通函数、ListNode/TreeNode 参数与返回、原地修改（`output.paramindex`）、设计题（ops/args 回放）。
比较方式由模型在 `judge.compare` 声明（exact / unordered / unordered_deep / float / run_only）；
题面写「any order」时自动放宽顺序，写「return any valid answer」时只校验能运行。

失败时，具体的「输入 / 期望 / 实际」会回传给模型修正（每题共最多 3 次尝试）。仍失败则这道题不发布；
自动选题时会在同一专题内换题，最多 `verify.reroll_limit` 次。手动指定 `--slug` 时不换题，直接报错退出。
页面上会显示校验结果；使用特殊判题（约 1.6% 的题）时标注「未自动验证」。
内置 `--demo` 也会实际执行上述校验，并在通过后展示徽标；不会调用 AI 或写入每日历史。
动画代码区的长行自动换行，不必依赖系统可能隐藏的横向滚动条。

## 6. 部署页面（可选）

`site/` 是纯静态文件，任意静态托管都可以：

- GitHub Pages：把 `site/` 发布到 Pages；
- Cloudflare Pages / Netlify / Vercel：构建目录填 `site`；
- 自己的服务器：`python3 scripts/serve.py` 或 Nginx。

部署后把地址写入 `PUBLIC_BASE_URL`，邮件中的链接就会指向公网页面。注意：题面版权归 LeetCode，公开部署等于公开题面，需自行确认使用边界；仅自己使用建议不要公开。

## 7. 排错

### AI 返回 401 / 402

检查 `AI_API_KEY`、`AI_BASE_URL`、`AI_MODEL`。DeepSeek 的 base URL 不应带 `/v1` 以外的错误路径；程序会自动补 `/chat/completions`。

### AI 输出 JSON 不合法

每题共最多尝试 3 次（首次加最多 2 次重试），错误信息会回传给模型。仍失败时，自动选题会在同专题内换题；手动指定题目或换题次数耗尽时退出，**不会发送失败题目的分析**。原始响应在 `data/daily/raw/`，可以检查模型是否被截断。可尝试降低题面长度或换模型。

### SMTP 登录失败

- QQ/163 必须用授权码，不是登录密码；
- Gmail 需要两步验证后生成 App Password；
- 检查端口和加密方式是否匹配：465 用 `ssl`，587 用 `starttls`；
- 公司/校园邮箱可能关闭了 SMTP。

发送失败后会等待 2 秒自动重试一次；仍失败则保存分析和失败状态，并以非零退出码结束。
再次运行同一天时会自动复用分析补发，不调用 AI；已发送成功的记录不会自动重发。
`--no-email` 禁止补发，`--dry-run` 只生成预览，`--resend` 可显式重发。
SMTP 无法保证 exactly-once：若服务端已接收但连接在确认前断开，重试可能产生重复邮件。
定时任务仍每天运行一次；即时重试也失败时，需手动重跑或另行配置同日补偿任务。

### 邮件里没有动画

这是预期行为：邮件客户端不运行 JavaScript。打开附件 HTML，或配置 `PUBLIC_BASE_URL`。附件被邮箱标记为危险时，可先下载再打开。

### 定时任务没有跑

```bash
python3 scripts/schedule.py status
tail -100 data/daily/logs/launchd.err.log
```

先手动跑一次：

```bash
python3 scripts/run_daily.py --dry-run
```

## 8. 安全

- 执行校验是带资源限制的子进程，不是 OS 级安全边界：`-I` 不会禁止读取本机文件或访问网络。资源限制也可能因平台不支持而无法设置；不要用它执行不可信来源的任意代码。需要强隔离时应在受限容器或虚拟机中运行。
- `.env` 权限 600，已在 `.gitignore`；
- 程序不会把 key 写到页面、日志或邮件；
- 邮件只发给 `MAIL_TO` 中的地址；
- 不保存付费题正文；
- AI 生成内容会标注“可能有误”，代码请以原题约束和本地测试为准。

## 9. 测试

```bash
python3 -m unittest discover -s tests -v
# 113 tests
```

每日程序部分的测试覆盖：

- 示例分析归一化、非法 visual 拒绝；
- HTML 页面包含动画数据、代码、转义；
- 邮件 dry-run、HTML 附件、收件人回退；
- 选题的免费/专题约束、近期去重、专题均衡、120 天轮转模拟、难度软配额、兜底覆盖记录；
- 配置不输出密钥。

真实 SMTP 发信和真实 AI 调用无法在没有 key 的环境里自动测试；正式使用前用 `--demo --send` 发一封测试邮件。
