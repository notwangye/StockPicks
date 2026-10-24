#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
选股主流程 - 中长线 / 超短线激进 / 超短线稳健 / RSRS 组合
所有函数都是纯函数：接收 fetcher + 参数，返回选股结果列表。

4 套策略：
  1. select_mid_long_term_stocks   - 中长线价值投资（4只）
  2. select_short_term_stocks       - 超短线（3只，激进/稳健可切换）
  3. select_with_rsrs_kdj_cci      - RSRS+KDJ组合（5只，超跌买入）
"""
from __future__ import annotations

import logging
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Literal, Optional, Tuple

from ._types import HistData, Indicators, MarketThresholds, StockDataList, StockPick
from .indicators import calculate_advanced_indicators, calculate_rsrs
from .scorer import (
    calculate_mid_long_score_v2,
    calculate_short_term_aggressive,
    calculate_short_term_conservative,
    evaluate_buy_signal,
)

logger = logging.getLogger(__name__)

# 类型别名
Fetcher = Any  # DataFetcher - 故意用 Any 避免循环引用
FiscalFetcher = Any  # FiscalDataFetcher
StrategyType = Literal["aggressive", "conservative"]


def _evaluate_mid_long_candidate(fetcher: Fetcher, stock: StockPick) -> Optional[StockPick]:
    """评估单只股票的中长线价值（用于并行计算）"""
    try:
        code: str = stock.get("代码", "")
        if not code or len(code) != 6:
            return None

        price: float = float(stock.get("最新价", 0))
        if price < 5 or price > 200:
            return None

        volume: float = float(stock.get("成交量", 0))
        if volume < 1e6:
            return None

        change_pct: float = float(stock.get("涨跌幅", 0))
        # 超跌股（如-10%）可能是极佳的买入机会，不应在评估阶段排除
        # 中长线要求：最短120交易日，获取360交易日
        hist_data: Optional[StockDataList] = fetcher.get_stock_historical(code, max_days=360)
        if not hist_data or len(hist_data) < 120:
            return None

        score: int
        details: Dict[str, Any]
        score, details = calculate_mid_long_score_v2(hist_data, stock)
        if score > 0:
            return {
                "code": code,
                "name": stock.get("名称", ""),
                "price": price,
                "change_pct": change_pct,
                "score": score,
                "details": details,
            }
    except Exception:
        pass
    return None


def select_mid_long_term_stocks(
    fetcher: Fetcher,
    fiscal_fetcher: FiscalFetcher,
    limit: int = 4,
    market_thresholds: Optional[MarketThresholds] = None,
) -> List[StockPick]:
    """筛选中长线股票（优化版：两阶段加载 + 并行评估 + 基本面筛选）

    Args:
        fetcher: DataFetcher 实例
        fiscal_fetcher: FiscalDataFetcher 实例
        limit: 返回股票数量（默认 4）
        market_thresholds: 市场自适应阈值（透传给 calculate_mid_long_score_v2）

    Returns:
        选股结果列表，按 (financial_score, score) 降序
    """
    logger.info("开始筛选中长线股票...")

    # 第一阶段：快速扫描股票列表（不加载历史）
    all_stocks: List[StockPick] = fetcher.get_stocks_from_historical(preload_data=False)
    logger.info(f"从历史数据获取到 {len(all_stocks)} 只股票")

    # 第二阶段：快速预筛选（不需要历史数据）
    candidates: List[StockPick] = []
    candidates_fallen: List[StockPick] = []
    for s in all_stocks:
        code: str = s.get("代码", "")
        if not code or len(code) != 6:
            continue
        price: float = float(s.get("最新价", 0))
        if price < 2 or price > 500:
            continue
        volume: float = float(s.get("成交量", 0))
        if volume < 5e5:
            continue
        change_pct: float = float(s.get("涨跌幅", 0))
        if change_pct < -15:
            continue
        elif change_pct < -8:
            candidates_fallen.append(s)
        else:
            candidates.append(s)

    if len(candidates) < limit * 2 and candidates_fallen:
        logger.info(f"候选股票较少({len(candidates)})，加入 {len(candidates_fallen[:20])} 只超跌股票")
        candidates.extend(candidates_fallen[:20])

    logger.info(f"预筛选出 {len(candidates)} 只候选股票，开始预加载历史数据...")

    # 第三阶段：预加载候选股票的历史数据
    fetcher.preload_candidates_history(candidates, min_days=120)
    logger.info(f"历史数据加载完成，缓存共 {fetcher._stock_data_cache.size()} 只")

    # 第四阶段：并行评估候选股票（硬过滤 + 评分）
    logger.info("开始并行评估...")
    results: List[StockPick] = []
    max_workers: int = min(16, os.cpu_count() or 8)

    _filter_stats: Dict[str, int] = {
        "total": 0,
        "ma_fail": 0,
        "rsi_fail": 0,
        "rsrs_fail": 0,
        "ch20_fail": 0,
        "ch5_fail": 0,
        "score_fail": 0,
        "pass": 0,
    }

    def evaluate(s: StockPick) -> Optional[StockPick]:
        try:
            code: str = s.get("代码", "")
            hist_data: Optional[StockDataList] = fetcher.get_stock_historical(code, max_days=360)
            if not hist_data or len(hist_data) < 120:
                return None

            indicators: Indicators = calculate_advanced_indicators(
                hist_data, None, code
            )
            if not indicators:
                return None

            # 硬过滤：均线完全多头排列
            if not (indicators["ma5"] > indicators["ma20"] > indicators["ma60"]):
                _filter_stats["ma_fail"] += 1
                return None

            rsi: float = indicators.get("rsi", 50.0)
            if not (40 < rsi < 70):
                _filter_stats["rsi_fail"] += 1
                return None

            rsrs: Dict[str, Any] = calculate_rsrs(hist_data)
            rsrs_val: float = rsrs.get("rsrs", 0.0)
            if rsrs_val <= 0.95:
                _filter_stats["rsrs_fail"] += 1
                return None

            change_20d: float = indicators.get("change_20d", 0.0)
            if change_20d <= 0:
                _filter_stats["ch20_fail"] += 1
                return None

            change_5d: float = indicators.get("change_5d", 0.0)
            if change_5d <= 0:
                _filter_stats["ch5_fail"] += 1
                return None

            score: int
            details: Dict[str, Any]
            score, details = calculate_mid_long_score_v2(
                hist_data, s, indicators, market_thresholds
            )
            _filter_stats["total"] += 1
            if score > 0:
                _filter_stats["pass"] += 1
                return {
                    "code": code,
                    "name": s.get("名称", ""),
                    "price": float(s.get("最新价", 0)),
                    "change_pct": float(s.get("涨跌幅", 0)),
                    "score": score,
                    "details": details,
                }
            _filter_stats["score_fail"] += 1
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(evaluate, s): s for s in candidates}
        for future in as_completed(futures):
            result: Optional[StockPick] = future.result()
            if result:
                results.append(result)

    total: int = _filter_stats["total"]
    logger.info(
        f"硬过滤统计: 总参与评分{total}只, 通过{_filter_stats['pass']}只 "
        f"(均线{_filter_stats['ma_fail']}, RSI{_filter_stats['rsi_fail']}, "
        f"RSRS{_filter_stats['rsrs_fail']}, 20日涨幅{_filter_stats['ch20_fail']}, "
        f"5日涨幅{_filter_stats['ch5_fail']}, 评分{_filter_stats['score_fail']})"
    )

    # 【审查修复】补上"代码"作为末位次级键：候选来自 as_completed()，
    # 同分股票的原始顺序取决于线程完成顺序，不加兜底键则结果不可复现。
    results.sort(
        key=lambda x: (-_to_int(x.get("score", 0), 0), str(x.get("code", "")))
    )

    # 第五阶段：基本面筛选
    logger.info("开始基本面筛选...")
    financial_filtered: List[StockPick] = []
    fin_filter_stats: Dict[str, int] = {
        "total": 0,
        "st_fail": 0,
        "roe_fail": 0,
        "profit_fail": 0,
        "score_fail": 0,
        "pass": 0,
    }

    for r in results:
        fin_filter_stats["total"] += 1
        fin_code: str = r.get("code", "")
        fin_name: str = r.get("name", "")

        fin_result: Dict[str, Any] = fiscal_fetcher.check_financial(
            fin_code, stock_type="mid_long"
        )

        # 【审查修复】ST 判定取"财务快照 + 最新名称"的并集，
        # 避免财务数据滞后期间新被 ST 的股票混进中长线候选（详见 _is_st_name）。
        if _is_st_result(fin_result, fin_name):
            fin_filter_stats["st_fail"] += 1
            logger.info(f"基本面淘汰: {fin_code} - ST股票")
            continue

        if not fin_result["pass"]:
            reason: str = (
                fin_result["reasons"][0] if fin_result["reasons"] else "未知原因"
            )
            if "ROE" in reason:
                fin_filter_stats["roe_fail"] += 1
            elif "净利润" in reason:
                fin_filter_stats["profit_fail"] += 1
            else:
                fin_filter_stats["score_fail"] += 1
            logger.info(f"基本面淘汰: {fin_code} - {reason}")
            continue

        fin_filter_stats["pass"] += 1
        r["financial_score"] = fin_result["score"]
        r["financial_reasons"] = fin_result["reasons"]
        r["financial_warnings"] = fin_result["warnings"]
        r["financial_details"] = fin_result["details"]
        financial_filtered.append(r)

    logger.info(
        f"基本面筛选: 参与{fin_filter_stats['total']}只, 通过{fin_filter_stats['pass']}只 "
        f"(ST{fin_filter_stats['st_fail']}, ROE{fin_filter_stats['roe_fail']}, "
        f"净利润{fin_filter_stats['profit_fail']}, 评分{fin_filter_stats['score_fail']})"
    )

    # 中长线：以财务分为优先（价值投资特性），但财务数据缺失（financial_score=0）
    # 的股票应回退到按技术分排序，避免错杀有技术形态但暂缺财务数据的标的
    # 【审查修复】末位补"代码"兜底键，保证排序结果唯一可复现（同分不再依赖线程顺序）
    financial_filtered.sort(
        key=lambda x: (
            -(
                _to_int(x.get("financial_score", 0), 0)
                if _to_int(x.get("financial_score", 0), 0) > 0
                else -1
            ),
            -_to_int(x.get("score", 0), 0),
            str(x.get("code", "")),
        ),
    )

    # 第六阶段：行业分散约束
    selected: List[StockPick] = []
    industry_count: Dict[str, int] = {}
    max_per_industry: int = max(2, limit // 3)

    for r in financial_filtered:
        if len(selected) >= limit:
            break
        pseudo_industry: str = r["code"][:3]
        count: int = industry_count.get(pseudo_industry, 0)
        if count < max_per_industry:
            selected.append(r)
            industry_count[pseudo_industry] = count + 1
        elif len(selected) < limit * 0.7:
            r["overweight"] = True
            selected.append(r)
            industry_count[pseudo_industry] = count + 1

    logger.info(
        f"筛选出 {len(selected)} 只中长线股票，分布于 {len(industry_count)} 个行业"
    )
    return selected


def _to_int(value: Any, default: int = 0) -> int:
    """安全转 int（脏数据/None/字符串都回落到默认值）"""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ST / 退市风险股名称特征（与 fiscal.check_financial 里的规则保持一致）
_ST_NAME_PATTERN = re.compile(r"(ST|S\*ST|\*ST|退市?|暂停上市)")


def _is_st_name(name: Any) -> bool:
    """按股票名称判断是否为 ST / 退市风险股

    【审查修复】ST 判定此前只依赖财务文件里的 name，而财务数据是每周/每月
    快照（实测当前快照落后 7 周）。这段时间内新被 ST 的股票（如 000078
    海王生物 -> ST海王）不会被识别，而超短线选股里 ST 是唯一的硬性风险
    闸门 —— 漏判就会把 ±5% 限幅的高风险票放进交易候选。

    现在改为"两个来源取并集"：
      - 财务文件的 name（快照，可能滞后，但能反映已摘帽的股票）
      - 股票列表的 name（每日更新的 K 线文件，反映最新状态）
    实测两者对 23 只股票的判定不一致（9 只新增 ST、14 只已摘帽），
    取并集后排除数从 242 增至 251（+0.16%），代价可忽略，
    换来的是"不会漏掉近期被 ST 的股票"。
    """
    if not name:
        return False
    return bool(_ST_NAME_PATTERN.search(str(name)))


def _is_st_result(fin_result: Dict[str, Any], fresh_name: Any) -> bool:
    """综合财务检查结果与最新名称，判断是否属于 ST 风险股"""
    if _is_st_name(fresh_name):
        return True
    details: Any = fin_result.get("details")
    if isinstance(details, dict) and details.get("is_st"):
        return True
    if not fin_result.get("pass", False):
        reasons: Any = fin_result.get("reasons") or []
        if isinstance(reasons, (list, tuple)):
            for reason in reasons:
                if "ST" in str(reason):
                    return True
    return False


def _short_term_sort_key(item: StockPick) -> Tuple[int, float, int, str]:
    """超短线选股排序键（确定性）

    【审查修复】原先只按 score 排序，而候选来自 ThreadPoolExecutor 的
    as_completed() —— 同分股票的先后取决于**线程完成顺序**，导致同一份数据
    每次跑出来的选股结果可能不同（可复现性问题）。

    这里补充确定性的次级键：技术分 → 换手异动倍数 → 连板数 → 股票代码。
    全部是技术/资金面指标，**不引入任何财务因子**，保持"超短线不看重财务"
    的设计意图；最后用代码做兜底，保证排序结果唯一且稳定。
    """
    indicators: Dict[str, Any] = {}
    details: Any = item.get("details")
    if isinstance(details, dict):
        raw_indicators: Any = details.get("indicators")
        if isinstance(raw_indicators, dict):
            indicators = raw_indicators

    turnover_ratio: float = 0.0
    try:
        turnover_ratio = float(indicators.get("turnover_ratio_5d", 0.0) or 0.0)
    except (TypeError, ValueError):
        turnover_ratio = 0.0
    if not math.isfinite(turnover_ratio):
        turnover_ratio = 0.0

    consecutive: int = _to_int(indicators.get("consecutive_limit_up", 0), 0)
    score: int = _to_int(item.get("score", 0), 0)

    return (-score, -turnover_ratio, -consecutive, str(item.get("code", "")))


def _evaluate_short_term_candidate(
    fetcher: Fetcher,
    stock: StockPick,
    strategy: StrategyType,
    fiscal_fetcher: Optional[FiscalFetcher] = None,
) -> Optional[StockPick]:
    """评估单只超短线股票（用于并行计算）

    Args:
        fetcher: DataFetcher 实例
        stock: 股票基本信息
        strategy: 'aggressive' / 'conservative'
        fiscal_fetcher: 仅用于取真实流通股本以计算换手率（不参与评分）
    """
    try:
        code: str = stock.get("代码", "")
        if not code or len(code) != 6:
            return None

        min_days: int = 60
        max_days: int = 180
        hist_data: Optional[StockDataList] = fetcher.get_stock_historical(
            code, max_days=max_days
        )
        if not hist_data or len(hist_data) < min_days:
            return None

        # 【修复】本地 K 线没有 turnover 字段，不传真实股本的话换手率会被
        # 高估 2 倍以上（98% 的股票），且"资金异动"维度恒为 0 分。
        float_shares: Optional[float] = None
        if fiscal_fetcher is not None:
            try:
                float_shares = fiscal_fetcher.get_float_shares(code)
            except Exception:
                float_shares = None

        if strategy == "aggressive":
            score: int
            details: Dict[str, Any]
            score, details = calculate_short_term_aggressive(
                hist_data, stock, float_shares
            )
        else:
            score, details = calculate_short_term_conservative(
                hist_data, stock, float_shares
            )

        if score > 0:
            return {
                "code": code,
                "name": stock.get("名称", ""),
                "price": stock.get("最新价", 0),
                "change_pct": stock.get("涨跌幅", 0),
                "score": score,
                "details": details,
            }
    except Exception:
        pass
    return None


def select_short_term_stocks(
    fetcher: Fetcher,
    fiscal_fetcher: FiscalFetcher,
    limit: int = 3,
    strategy: StrategyType = "aggressive",
) -> List[StockPick]:
    """筛选超短线股票（并行优化版 + 基本面筛选）

    Args:
        fetcher: DataFetcher 实例
        fiscal_fetcher: FiscalDataFetcher 实例
        limit: 返回股票数量（默认 3）
        strategy: 'aggressive' 激进型 / 'conservative' 稳健型

    Returns:
        选股结果列表，按技术分降序（财务仅用于剔除 ST 风险）
    """
    logger.info(f"开始筛选超短线股票({strategy})...")
    all_stocks: List[StockPick] = fetcher.get_stocks_from_historical()

    candidates: List[StockPick] = []
    max_workers: int = min(32, os.cpu_count() or 8)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _evaluate_short_term_candidate, fetcher, s, strategy, fiscal_fetcher
            ): s
            for s in all_stocks
        }
        for future in as_completed(futures):
            result: Optional[StockPick] = future.result()
            if result:
                candidates.append(result)

    logger.info(f"技术面候选 {len(candidates)} 只，开始基本面筛查...")
    financial_filtered: List[StockPick] = []
    fin_filter_stats: Dict[str, int] = {
        "total": 0,
        "st_fail": 0,
        "fin_weak": 0,
        "pass": 0,
    }

    for r in candidates:
        fin_filter_stats["total"] += 1
        _code: str = r.get("code", "")
        _name: str = r.get("name", "")

        fin_result: Dict[str, Any] = fiscal_fetcher.check_financial(
            _code, stock_type="short_term"
        )

        # 超短线只做风险排除，不做基本面筛选：
        # 激进型/稳健型抓的是"资金异动 + 技术形态"，ST 属于交易风险必须挡掉；
        # 其余情况（财务评分低、ROE 一般、净利润下滑、甚至无财务数据）
        # 都不该淘汰——短线赚的是波动而非价值，财务差但资金疯狂的票正是目标。
        #
        # 【审查修复】ST 判定改为"财务快照 + 最新名称"取并集，
        # 避免财务数据滞后期间新被 ST 的股票混进超短线候选（详见 _is_st_name）。
        if _is_st_result(fin_result, _name):
            fin_filter_stats["st_fail"] += 1
            logger.info(f"风险排除: {_code} - ST股票")
            continue

        if not fin_result["pass"]:
            reason: str = fin_result["reasons"][0] if fin_result["reasons"] else "未知"
            # 非 ST 的基本面问题：仅记录，不淘汰
            fin_filter_stats["fin_weak"] = (
                fin_filter_stats.get("fin_weak", 0) + 1
            )
            logger.debug(f"基本面偏弱但保留(短线不淘汰): {_code} - {reason}")
            # 非 ST 的基本面问题：仅记录，不淘汰
            fin_filter_stats["fin_weak"] = (
                fin_filter_stats.get("fin_weak", 0) + 1
            )
            logger.debug(f"基本面偏弱但保留(短线不淘汰): {_code} - {reason}")

        fin_filter_stats["pass"] += 1
        r["financial_score"] = fin_result["score"]
        r["financial_reasons"] = fin_result["reasons"]
        r["financial_details"] = fin_result["details"]
        financial_filtered.append(r)

    logger.info(
        f"基本面筛查: 参与{fin_filter_stats['total']}只, 保留{fin_filter_stats['pass']}只 "
        f"(ST排除{fin_filter_stats['st_fail']}, 财务偏弱保留{fin_filter_stats.get('fin_weak', 0)})"
    )

    # 超短线排序：纯技术分 + 确定性次级键。
    # 【修复】原来把 financial_score 作为第二排序键，导致技术分相同的股票
    # 会因财务好坏被重新排序——短线是波动交易，不该由财务决定名次。
    # 【审查修复】进一步改为 _short_term_sort_key：去掉财务权重后，
    # 同分股票会退化成"按线程完成顺序"排列，结果不可复现；
    # 现补上换手异动/连板/代码三个确定性次级键（均为技术面，不含财务）。
    # ST 已在上方硬排除，这里无需再考虑。
    financial_filtered.sort(key=_short_term_sort_key)
    selected: List[StockPick] = financial_filtered[:limit]

    logger.info(f"筛选出 {len(selected)} 只超短线股票({strategy})")
    return selected


def _evaluate_rsrs_candidate(
    fetcher: Fetcher, stock: StockPick
) -> Optional[Dict[str, Any]]:
    """评估单只股票的 RSRS 买入信号（用于并行计算）"""
    try:
        code: str = stock.get("代码", "")
        if not code or len(code) != 6:
            return None

        hist_data: Optional[StockDataList] = fetcher.get_stock_historical(
            code, max_days=360
        )
        if not hist_data or len(hist_data) < 120:
            return None

        evaluation: Dict[str, Any] = evaluate_buy_signal(hist_data, stock)

        if evaluation["buy_score"] >= 30:
            return {
                "code": code,
                "name": stock.get("名称", ""),
                "price": stock.get("最新价", 0),
                "change_pct": stock.get("涨跌幅", 0),
                "buy_score": evaluation["buy_score"],
                "sell_score": evaluation["sell_score"],
                "reasons": evaluation["reasons"],
                "kdj_rsi_cci": evaluation["kdj_rsi_cci"],
                "rsrs": evaluation["rsrs"],
            }
    except Exception:
        pass
    return None


def select_with_rsrs_kdj_cci(
    fetcher: Fetcher, fiscal_fetcher: FiscalFetcher, limit: int = 5
) -> List[Dict[str, Any]]:
    """使用 RSRS + KDJ/RSI/CCI 组合筛选超跌买入机会

    Args:
        fetcher: DataFetcher 实例
        fiscal_fetcher: FiscalDataFetcher 实例
        limit: 返回股票数量（默认 5）

    Returns:
        选股结果列表，按 (buy_score, financial_score) 降序
    """
    logger.info("开始RSRS/KDJ/RSI/CCI组合筛选...")
    all_stocks: List[StockPick] = fetcher.get_stocks_from_historical()

    candidates: List[Dict[str, Any]] = []
    max_workers: int = min(32, os.cpu_count() or 8)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_evaluate_rsrs_candidate, fetcher, s): s
            for s in all_stocks
        }
        for future in as_completed(futures):
            result: Optional[Dict[str, Any]] = future.result()
            if result:
                candidates.append(result)

    logger.info(f"技术面候选 {len(candidates)} 只，开始基本面筛选...")
    financial_filtered: List[Dict[str, Any]] = []
    fin_filter_stats: Dict[str, int] = {
        "total": 0,
        "st_fail": 0,
        "roe_fail": 0,
        "pass": 0,
    }

    for r in candidates:
        fin_filter_stats["total"] += 1
        _code: str = r.get("code", "")

        fin_result: Dict[str, Any] = fiscal_fetcher.check_financial(
            _code, stock_type="mid_long"
        )

        if not fin_result["pass"]:
            reason: str = fin_result["reasons"][0] if fin_result["reasons"] else "未知"
            if "ST" in reason:
                fin_filter_stats["st_fail"] += 1
            elif "ROE" in reason:
                fin_filter_stats["roe_fail"] += 1
            logger.info(f"基本面淘汰: {_code} - {reason}")
            continue

        fin_filter_stats["pass"] += 1
        r["financial_score"] = fin_result["score"]
        r["financial_reasons"] = fin_result["reasons"]
        r["financial_details"] = fin_result["details"]
        financial_filtered.append(r)

    logger.info(
        f"基本面筛选: 参与{fin_filter_stats['total']}只, 通过{fin_filter_stats['pass']}只 "
        f"(ST{fin_filter_stats['st_fail']}, ROE{fin_filter_stats['roe_fail']})"
    )

    # 【审查修复】末位补"代码"兜底键，保证同分时结果可复现
    financial_filtered.sort(
        key=lambda x: (
            -_to_int(x.get("buy_score", 0), 0),
            -_to_int(x.get("financial_score", 0), 0),
            str(x.get("code", "")),
        ),
    )
    selected: List[Dict[str, Any]] = financial_filtered[:limit]

    logger.info(f"组合筛选出 {len(selected)} 只股票")
    return selected