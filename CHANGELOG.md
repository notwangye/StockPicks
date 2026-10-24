# 更新日志 (Changelog)

本项目的所有重要变更都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本遵循 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)。

---

## [Unreleased] - 2026-10-24

### 🐛 选股修复 + 全面代码审查（三轮迭代）

本轮包含三部分：**中长线选不出股**的根因修复、**超短线不看重财务指标**的策略调整、
以及针对前两轮改动的**全面深度审查**（发现并修复 7 个缺陷，含 1 个自己引入的严重缺陷）。

#### 🔴 根因修复：中长线选不出股票

`selector/fiscal.py` 财务解析存在 4 个叠加缺陷，导致基本面通过率仅 1.7%：

| # | 缺陷 | 修复 |
|---|------|------|
| 1 | 同比基期只取最近 4 个季度，而"去年同期"最早落在第 5 位 | 放宽到 8 个季度 |
| 2 | 最新季度 `period` 写成 `'06-30'`，历史季度是 `'0630'`，字符串匹配永远命中不了 | 改为解析「季度月份 + 年份」比较 |
| 3 | ROE 未年化（归母净利润是年初至今累计值，阈值却是年度口径） | 按季度月份年化（Q1×4 / H1×2 / Q3×4/3 / 年报×1） |
| 4 | 约 31% 股票最新季度缺 `总股本`；`name` 为 `None` 时正则抛异常 | 回溯查找股本；`name` 空值兜底 |

**效果**：基本面通过率 1.7% → 33.3%（同一样本 1.8% → 27.6%），跨 6 个历史报告期稳定在 26%~31%，
中长线稳定选出 4 只（此前经常 0 只）。

#### 🎯 策略调整：超短线不再看重财务指标

`selector/strategies.py` 超短线策略原本沿用中长线的财务硬淘汰，与策略定位冲突。调整后：

- 超短线**只做 ST 风险排除**，财务评分低 / ROE 一般 / 净利润下滑 / 无财务数据**一律保留**
- 排序键移除 `financial_score`，短线名次不再由财务决定
- 激进型 120 分里有 40 分原本是**结构性死分**（本地 K 线无 `turnover`/`change_pct` 字段）：
  - 换手率用固定 1e8 股本粗估 → 改用**真实流通股本**（98% 股票换手率被高估 2 倍以上）
  - 换手率 5 日放大倍数恒为 0 → 用成交量序列还原（比值与股本无关）
  - 涨停基因恒为 0 → 由收盘价推算涨跌幅，并**按板块区分涨停阈值**

#### 🔍 全面审查发现的 7 个缺陷（本轮新增修复）

| # | 严重度 | 问题 | 修复 |
|---|--------|------|------|
| 1 | **严重** | 「60 日涨停基因」实际统计**全部 360 根** K 线（=1.5 年），中位涨停 4 次 vs 真 60 日 0 次，83% 股票被高估 → **48% 股票白拿满分 15 分，该维度彻底失效** | 显式裁剪 60 根窗口，满分比例 48%→16% |
| 2 | **严重** | 移除 `financial_score` 排序键后，**同分股票次序由线程完成顺序决定**，结果不可复现 | 新增确定性次级键（技术分→换手异动→连板→代码），**不含财务因子** |
| 3 | 高 | 中长线路径**从不传股票代码**，创业板/科创板沿用主板 10% 阈值 → 10~15% 普通上涨被误判涨停 | 三处调用点贯通传 `code` |
| 4 | 高 | 「相对 250 日位置」实际取整条数组极值（中长线=360 根），15% 股票偏移 >0.1 足以跨过 0.25/0.75 阈值 | 显式裁剪 250 根窗口 |
| 5 | 高 | ST 判定只依赖财务文件快照，而**快照落后 7 周**；短线选股里 ST 是唯一硬闸门 → 9 只近期被 ST 的股票漏判 | 「财务快照 + 每日更新名称」**取并集**（排除数 242→251，+0.16%） |
| 6 | 中 | `get_float_shares` 口径混用：最新季度有总股本就立即返回，漏掉更早季度的流通股本 → 换手率被低估 | 先扫完所有季度的流通股本，都没有才退回总股本 |
| 7 | 低 | `_as_float('nan'/'inf')` 原样返回使所有比较恒为 False；`2026-6-30` 这类不补零日期解析失败导致年化系数丢失 | 拒绝非有限值；日期改用正则解析 |

另加固：换手率序列除零保护（防 inf 污染评分）、`turnover_estimated` 标记一致性、
中长线/RSRS 排序补代码兜底键。

#### ✅ 验证

- 测试 **169 个全部通过**（新增 19 个回归测试，覆盖窗口边界、板块阈值、排序确定性、股本口径、ST 并集）
- 四条策略各连续跑 3 次，**结果完全一致**（可复现性已修复）
- 实测选股：中长线 4 只 / 激进型 3 只 / 稳健型 3 只 / RSRS 组合 5 只

---

## [Unreleased] - 2026-07-22

### ✨ V2.3 质量提升与文档站

#### 🎯 CI 加 mypy 静态检查
- `.github/workflows/ci.yml` 新增 `typecheck` job
- 跑 `mypy selector/ config.py stock_selector.py --ignore-missing-imports --no-strict-optional`
- 后续 PR 必须通过 mypy 0 错误

#### 📚 GitHub Pages 文档站
- 新增 `mkdocs.yml` — MkDocs Material 主题配置
- 新增 `docs/` 目录 (12 个页面，~30 KB Markdown)
- `.github/workflows/ci.yml` 新增 `docs` job
- 推送 master 后自动构建并部署到 GitHub Pages
- 文档内容:
  - 首页 / 快速上手
  - 4 篇选股策略详解
  - 17 项指标说明 + RSRS 专题
  - 本地 / Docker / 数据更新 3 篇部署文档
  - PR 指南 / 本地开发 / 测试 3 篇贡献文档
  - 后端 API + 选股模块 2 篇 API 参考

#### 📖 README 更新
- 新增"📚 在线文档站"章节，链接到 GitHub Pages
- 新增 `mkdocs serve` 本地预览说明

### ✨ V2.2 Docker + 类型提示 + PR 手册 (2026-07-22)

### 🛠️ 改进
- **架构调整**：从老版本 `D:\stock-picks\` 完整迁移到独立 V2 项目
- **代码清理**：移除 5 个空目录占位符，重整包结构
- **风险声明**：明确标注"不进行实盘交易"

### 🐛 Bug 修复
- 修复 `calculate_rsrs()` 函数**未返回 slope 字段**的 P0 bug（导致前端 RSRS 斜率永远显示为 0）
- 修复前端 `StockDetail.vue` 中**涨跌幅计算错误**（改用昨收价计算）

### 📝 文档
- 新增 README.md（项目介绍、快速启动、贡献指南）
- 新增 CONTRIBUTING.md（贡献流程、代码规范）
- 新增 CHANGELOG.md（本文件）
- 新增 LICENSE（MIT）
- 新增 .gitignore（覆盖敏感文件、缓存、构建产物）
- 新增 .env.example（环境变量模板，替代已误提交的 .env）

### ✨ V2.1 模块化重构（2026-07-21）

#### D1. 拆分选股引擎 - 模块化架构
- 原 `stock_selector.py` (1706 行) 拆分为 `backend/selector/` 包 (6 个模块):
  - `cache.py` (55 行) - LRU 缓存
  - `data_fetcher.py` (244 行) - 历史数据获取
  - `fiscal.py` (241 行) - 基本面财务数据
  - `indicators.py` (366 行) - 17 种技术指标
  - `scorer.py` (363 行) - 5 套打分函数
  - `strategies.py` (393 行) - 选股主流程
- `stock_selector.py` 变为 79 行薄壳,仅负责重导出 (向后兼容所有旧 import)
- `StockSelector` 门面类代理所有指标/打分/选择方法
- 纯函数设计: `select_*_stocks` 接收 fetcher 参数,便于测试
- `DataFetcher` / `FiscalDataFetcher` 接受可选 `historical_dir` / `fiscal_dir` 参数

#### D2. pytest 单元测试 - 87 个用例
- `tests/conftest.py` - 共享 fixtures (合成历史数据)
- `tests/test_cache.py` - 8 用例
- `tests/test_config.py` - 12 用例
- `tests/test_data_fetcher.py` - 12 用例
- `tests/test_fiscal.py` - 8 用例
- `tests/test_indicators.py` - 23 用例 (含 RSRS 修复回归保护)
- `tests/test_scorer.py` - 14 用例
- `tests/test_split_compat.py` - 10 用例

**运行**: `cd backend && python -m pytest tests/ -v` → **87 passed in ~7s**

#### D3. GitHub Actions CI
- `.github/workflows/ci.yml`
- 后端: Ubuntu + Windows × Python 3.11 / 3.12
- 前端: Ubuntu + Windows × Node 18 / 20 / 22
- flake8 + pyflakes 静态检查
- Codecov 覆盖率上传

---

## [2.2.0] - 2026-07-21

### ✨ 新增

#### T1. Docker 一键部署
- 根目录新增 `docker-compose.yml` (2.1 KB)
- `Dockerfile` (后端 Python 3.12-slim, 1.0 KB)
- `frontend/Dockerfile` (多阶段构建 Node 20 + nginx, 582 B)
- `frontend/nginx.conf` (SPA + API 反代, 1.3 KB)
- `.dockerignore` (减小镜像体积, 664 B)
- 启劢后: 前端 http://localhost:8080, 后端 http://localhost:5001
- 数据持久化: stock_picks_data / stock_picks_fiscal / stock_picks_logs volumes
- 健康检查: /api/health + /healthz + Docker HEALTHCHECK

#### T2. V2.2 批量加类型提示
- 新增 `selector/_types.py` (2.0 KB) - 共享类型别名
  - `HistData` / `StockDataList` / `Indicators` / `ScoreResult` / `StockPick` / `MarketThresholds`
- 为 6 个模块加类型提示: cache / data_fetcher / fiscal / indicators / scorer / strategies
- 为 `config.py` 加类型提示
- 为 `selector/__init__.py` (StockSelector 门面类) 加类型提示
- 重大发现: 重构时手滑调整了 `evaluate_mid_long_term` 参数顺序 (stock_data, hist_data → hist_data, stock_data=None), 被测试拼住
- 修复: 参数顺序智能判定 (接受旧/新两种调用方式,保持 100% 向后兼容)

#### T3. PR 操作手册
- 工具脚本: `tools/gen_pr_manual.py` (生成 DOCX, 20 KB)
- 成品: `E:\技术文件资料\PR操作手册_股票智能选股.docx` (42.8 KB)
- 内容: 7 章 + 附录,含：
  - 什么是 PR (A 股类比)
  - 准备工作 (Git + GitHub)
  - Fork + 提 PR 全流程
  - PR 评审怎么进行
  - GitHub Actions 怎么看
  - 常见问题排查 (5 个)
  - Git 命令速查

### 🐛 Bug 修复
- 修复 `evaluate_mid_long_term` / `evaluate_short_term` 重构后参数顺序错误
  - 同时支持旧 API `evaluate_mid_long_term(stock_data, hist_data)` 和新 API
  - 向后兼容 100% 保留 (测试 87/87 通过)

### 🔧 工具
- `mypy` 静态类型检查集成 (8 个核心文件 0 错误)
- 修正 `Indicators` 类型为 `Dict[str, Any]` (原为 `Dict[str, float]`, 限制了 macd/bollinger 子字典)

---

## [2.0.0] - 2026-05-29 至 2026-06-08

### ✨ 核心功能
- **多策略选股引擎**（4 套并行策略）：
  - 中长线价值评分（95 分制）
  - 激进超短线（100 分制，追涨停）
  - 稳健超短线（100 分制，超跌反弹）
  - RSRS/KDJ/RSI/CCI 组合（60 分制，新增 RSRS 指标）
- **A股真实佣金模型**：印花税 0.05% + 过户费 0.001% + 规费 0.003% + 佣金 0.01%
- **Backtrader 回测引擎**：SQN + 夏普比率 + 最大回撤 + 年化收益
- **17 种技术指标**（基于 stockstats + finta）：MA、MACD、KDJ、RSI、CCI、RSRS、BOLL、ATR 等
- **Bokeh 交互式图表**：单页面内嵌图表（24 小时缓存）
- **APScheduler 定时任务**：
  - 交易日 17:30 下载历史数据
  - 交易日 18:30 三步法选股 + 自动回测
  - 每天 07:00 早间资讯更新
- **Vue 3 + Element Plus Web 界面**：
  - Dashboard 仪表盘
  - StockList 选股结果列表
  - StockDetail 单股分析（含买卖评分）
  - Backtest 回测结果
  - Indicators 技术指标页

### 🏗️ 架构
- 前后端分离
- Tornado 6.x + APScheduler 3.10 + Backtrader 1.9
- 本地 JSON 文件存储历史数据（5000+ 只股票）

### ⚙️ 部署
- Gunicorn 多进程生产部署
- Docker 支持（Dockerfile + docker-compose.yml）
- Nginx 反向代理配置
- 健康检查 `/api/health`

---

## 开发记录

- **2026-05-29**：项目初始化，从老版本 `D:\stock-picks\` 重构
- **2026-06-01**：完成 V2 系统技术评估报告（[V2系统技术评估报告.txt](V2系统技术评估报告.txt)）
- **2026-06-08**：完成部署文档（DEPLOY.md），git 初始化
- **2026-07-03**：代码结构整理
- **2026-07-17**：Claude 配置更新
- **2026-07-21**：首次提交，开源准备（README + LICENSE + CONTRIBUTING + .gitignore + 修复 2 个 P0 bug）

