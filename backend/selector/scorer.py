#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
打分函数 - 中长线、超短线、RSRS 等综合评分

5 套打分函数：
  1. evaluate_buy_signal            - 超买超卖综合信号 (买入/卖出评分)
  2. evaluate_mid_long_term         - 中长线评分 (V1 简化版)
  3. evaluate_short_term            - 超短线评分 (V1 简化版)
  4. calculate_mid_long_score_v2    - 中长线 V2 (满分 95,五大维度)
  5. calculate_short_term_aggressive - 超短线激进 (满分 100)
  6. calculate_short_term_conservative - 超短线稳健 (满分 85)
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from ._types import HistData, Indicators, ScoreResult, StockDataList
from .indicators import (
    calculate_advanced_indicators,
    calculate_kdj_rsi_cci,
    calculate_rsrs,
    calculate_technical_indicators,
)

# 类型别名: market_thresholds 用于市场自适应评分
MarketThresholds = Dict[str, float]


def evaluate_buy_signal(hist_data: StockDataList, stock_info: Dict[str, Any]) -> Dict[str, Any]:
    """超买超卖综合评估（KDJ + RSI + CCI + RSRS）

    Args:
        hist_data: 历史K线列表
        stock_info: 股票基本信息 (来自 get_stocks_from_historical)

    Returns:
        {
            'buy_score': int,         # 买入信号总分
            'sell_score': int,        # 卖出信号总分
            'reasons': List[str],     # 触发原因
            'kdj_rsi_cci': dict,
            'rsrs': dict
        }
    """
    kdj_rsi_cci: Dict[str, Any] = calculate_kdj_rsi_cci(hist_data)
    rsrs: Dict[str, Any] = calculate_rsrs(hist_data)

    buy_score: int = 0
    sell_score: int = 0
    reasons: list[str] = []

    # KDJ 超卖信号
    if kdj_rsi_cci.get("kdj_oversold") and kdj_rsi_cci.get("kdj_golden_cross"):
        buy_score += 20
        reasons.append("KDJ低位金叉")

    # RSI 超卖信号
    if kdj_rsi_cci.get("rsi_oversold"):
        buy_score += 15
        reasons.append("RSI超卖")

    if kdj_rsi_cci.get("rsi_overbought"):
        sell_score += 15
        reasons.append("RSI超买")

    # CCI 超卖信号
    if kdj_rsi_cci.get("cci_oversold"):
        buy_score += 10
        reasons.append("CCI超卖")

    if kdj_rsi_cci.get("cci_overbought"):
        sell_score += 10
        reasons.append("CCI超买")

    # RSRS 信号
    if rsrs.get("signal") == "strong_up":
        buy_score += 15
        reasons.append("RSRS强势向上")
    elif rsrs.get("signal") == "up":
        buy_score += 10

    if rsrs.get("signal") == "strong_down":
        sell_score += 15
        reasons.append("RSRS强势向下")

    return {
        "buy_score": buy_score,
        "sell_score": sell_score,
        "reasons": reasons,
        "kdj_rsi_cci": kdj_rsi_cci,
        "rsrs": rsrs,
    }


def evaluate_mid_long_term(
    stock_data_or_hist: Any,
    hist_data_or_stock: Any = None,
) -> Tuple[int, ScoreResult]:
    """评估中长线投资价值（V1 简化版,满分 100）

    为了向后兼容，同时支持两种调用顺序：
        - 旧 API: evaluate_mid_long_term(stock_data, hist_data)
        - 新 API: evaluate_mid_long_term(hist_data) 或 evaluate_mid_long_term(hist_data, stock_data)
    内部按“哪个是 list”自动判断。

    Args:
        stock_data_or_hist: 股票基本信息 dict 或历史数据 list
        hist_data_or_stock: 反之（可选）

    Returns:
        (score, result_dict) - score 是 0-100 的评分
    """
    # 参数顺序智能判定
    if isinstance(stock_data_or_hist, list):
        hist_data: StockDataList = stock_data_or_hist
        stock_data: Optional[Dict[str, Any]] = hist_data_or_stock
    elif isinstance(hist_data_or_stock, list):
        hist_data = hist_data_or_stock
        stock_data = stock_data_or_hist
    else:
        return 0, {}

    if not hist_data or len(hist_data) < 60:
        return 0, {}

    indicators: Indicators = calculate_technical_indicators(hist_data)
    if not indicators:
        return 0, {}

    score: int = 0
    reasons: list[str] = []

    # 1. 相对低位 (30%)
    position: float = indicators["position"]
    if position < 0.3:
        score += 30
        reasons.append(f"相对低位({position:.1%})")
    elif position < 0.5:
        score += 15

    # 2. 均线多头排列 (20%)
    if indicators["ma5"] > indicators["ma20"] > indicators["ma60"]:
        score += 20
        reasons.append("均线多头排列")
    elif indicators["ma20"] > indicators["ma60"]:
        score += 10

    # 3. 成交量配合 (20%)
    if indicators["vol_ratio"] > 1.5:
        score += 20
    elif indicators["vol_ratio"] > 1.0:
        score += 10

    # 4. 波动率适中 (15%)
    volatility: float = indicators["volatility"]
    if 0.2 < volatility < 0.5:
        score += 15
    elif volatility < 0.2:
        score += 5

    # 5. 近期表现 (15%)
    change: float = indicators["change_20d"]
    if -10 < change < 20:
        score += 15

    return score, {"indicators": indicators, "reasons": reasons}


def evaluate_short_term(
    stock_data_or_hist: Any,
    hist_data_or_stock: Any = None,
) -> Tuple[int, ScoreResult]:
    """评估超短线机会（V1 简化版,满分 90）

    同时支持两种调用顺序：
        - 旧 API: evaluate_short_term(stock_data, hist_data)
        - 新 API: evaluate_short_term(hist_data) 或 evaluate_short_term(hist_data, stock_data)

    Args:
        stock_data_or_hist: 股票基本信息 dict 或历史数据 list
        hist_data_or_stock: 反之（可选）

    Returns:
        (score, result_dict) - score 是 0-90 的评分
    """
    # 参数顺序智能判定
    if isinstance(stock_data_or_hist, list):
        hist_data: StockDataList = stock_data_or_hist
        stock_data: Dict[str, Any] = hist_data_or_stock or {}
    elif isinstance(hist_data_or_stock, list):
        hist_data = hist_data_or_stock
        stock_data = stock_data_or_hist if isinstance(stock_data_or_hist, dict) else {}
    else:
        return 0, {}

    if not hist_data or len(hist_data) < 5:
        return 0, {}

    indicators: Indicators = calculate_technical_indicators(hist_data)
    if not indicators:
        return 0, {}

    score: int = 0
    reasons: list[str] = []

    # 1. 换手率 (25%)
    turnover: float = stock_data.get("turnover", 0) if isinstance(stock_data, dict) else 0.0
    if turnover > 15:
        score += 25
    elif turnover > 8:
        score += 15
    elif turnover > 3:
        score += 10

    # 2. 成交量放大 (25%)
    vol_ratio: float = indicators["vol_ratio"]
    if vol_ratio > 3:
        score += 25
    elif vol_ratio > 2:
        score += 20
    elif vol_ratio > 1.5:
        score += 15

    # 3. 价格位置 (20%)
    position: float = indicators["position"]
    if 0.6 < position < 0.9:
        score += 20
    elif position > 0.9:
        score += 10

    # 4. 短期爆发力 (20%)
    change_5d: float = indicators["change_5d"]
    if 5 < change_5d < 15:
        score += 20
    elif change_5d > 0:
        score += 10

    return score, {"indicators": indicators, "reasons": reasons}


def calculate_mid_long_score_v2(
    hist_data: StockDataList,
    stock_info: Dict[str, Any],
    indicators: Optional[Indicators] = None,
    market_thresholds: Optional[MarketThresholds] = None,
) -> Tuple[int, ScoreResult]:
    """中长线股票优化评分模型 V2（五大维度,满分 95 分）

    维度：
        - 趋势指标 (30): MACD + 均线
        - 动量指标 (25): RSI + 近期涨幅
        - 位置指标 (15): 相对 250 日位置
        - 量价配合 (15): 量比 + 换手率
        - 波动质量 (10): 波动率 + 涨停基因

    Args:
        hist_data: 历史K线列表
        stock_info: 股票基本信息
        indicators: 预计算的指标(可选,避免重复算)
        market_thresholds: 市场自适应阈值 dict(由调用方传入,避免函数变成隐藏的IO调用)

    Returns:
        (score, result_dict)
    """
    if not hist_data or len(hist_data) < 120:
        return 0, {}

    if indicators is None:
        # 【审查修复】中长线路径此前不传 code，导致创业板/科创板沿用主板 10% 涨停阈值，
        # 10~15% 的普通上涨会被误记为涨停，虚增"涨停基因"分。
        stock_code: str = (
            str(stock_info.get("代码") or stock_info.get("code") or "")
            if isinstance(stock_info, dict)
            else ""
        )
        indicators = calculate_advanced_indicators(hist_data, None, stock_code)
    if not indicators:
        return 0, {}

    # 默认阈值(无 market_thresholds 参数时使用)
    if market_thresholds is None:
        market_thresholds = {
            "vol_factor": 1.0,
            "rsi_oversold": 35.0,
            "rsi_overbought": 75.0,
            "vol_ratio_threshold": 1.5,
            "position_low": 0.25,
            "position_high": 0.75,
        }

    score: int = 0
    reasons: list[str] = []
    details: Dict[str, Any] = {}

    # === 趋势指标 (30分) ===
    macd: Dict[str, Any] = indicators.get("macd", {})
    macd_score: int = 0
    if macd.get("golden_cross") and macd.get("dif_above_zero"):
        macd_score = 15
        reasons.append("MACD零轴上方金叉")
    elif macd.get("dif_above_zero"):
        macd_score = 5
    score += macd_score
    details["macd_score"] = macd_score

    ma_score: int = 0
    if indicators.get("ma_bullish"):
        ma_score = 15
        reasons.append("均线多头排列")
    elif indicators.get("ma_partial_bullish"):
        ma_score = 10
    elif indicators["ma5"] > indicators["ma20"]:
        ma_score = 5
    score += ma_score
    details["ma_score"] = ma_score

    # === 动量指标 (25分) - 使用自适应阈值 ===
    rsi: float = indicators.get("rsi", 50.0)
    rsi_low: float = market_thresholds["rsi_oversold"]
    rsi_high: float = market_thresholds["rsi_overbought"]
    rsi_score: int = (
        15
        if rsi_low <= rsi <= rsi_high
        else 10
        if rsi_low - 10 <= rsi <= rsi_high + 10
        else 5
    )
    if rsi_score >= 10:
        reasons.append(f"RSI健康区间({rsi:.1f})")
    score += rsi_score
    details["rsi_score"] = rsi_score
    details["thresholds_used"] = market_thresholds  # 记录使用的阈值

    change_20d: float = indicators.get("change_20d", 0.0)
    momentum_score: int = 10 if 0 <= change_20d <= 15 else 5 if -10 <= change_20d < 0 else 3
    score += momentum_score
    details["momentum_score"] = momentum_score

    # === 位置指标 (15分) - 使用自适应阈值 ===
    position: float = indicators.get("position", 0.5)
    pos_low: float = market_thresholds["position_low"]
    pos_high: float = market_thresholds["position_high"]
    position_score: int = (
        15
        if pos_low <= position <= pos_high
        else 8
        if pos_high < position <= pos_high + 0.15
        else 5
    )
    if position_score >= 10:
        reasons.append(f"相对低位({position:.1%})")
    score += position_score
    details["position_score"] = position_score

    # === 量价配合 (15分) - 使用自适应阈值 ===
    vol_ratio: float = indicators.get("vol_ratio", 1.0)
    vol_thresh: float = market_thresholds["vol_ratio_threshold"]
    vol_score: int = 10 if vol_ratio > vol_thresh * 1.5 else 7 if vol_ratio > vol_thresh else 4
    score += vol_score
    details["vol_score"] = vol_score

    turnover: float = indicators.get("turnover", 0.0)
    turnover_score: int = 5 if turnover > 5 else 3 if turnover > 2 else 0
    score += turnover_score
    details["turnover_score"] = turnover_score

    # === 波动质量 (10分) ===
    volatility: float = indicators.get("volatility", 0.0)
    vol_quality_score: int = 5 if 0.15 <= volatility <= 0.4 else 3
    score += vol_quality_score
    details["volatility_score"] = vol_quality_score

    limit_up_count: int = indicators.get("limit_up_count_60d", 0)
    limit_gene_score: int = 5 if limit_up_count >= 2 else 3 if limit_up_count >= 1 else 0
    score += limit_gene_score

    return score, {
        "score": score,
        "reasons": reasons,
        "indicators": indicators,
        "details": details,
        "trend": "上升" if indicators.get("ma_bullish") else "反弹" if indicators.get("ma_partial_bullish") else "震荡",
    }


def calculate_short_term_aggressive(
    hist_data: StockDataList,
    stock_info: Dict[str, Any],
    float_shares: Optional[float] = None,
) -> Tuple[int, ScoreResult]:
    """超短线激进型评分模型 V3（追涨停 / 抓连板 / 捕捉资金异动,满分 120）

    设计理念：聚焦"可能大涨、涨停、连续涨停"的强势股，回避低活跃、无量、
    涨幅温和的"伪强势"。

    维度（满分 120）：
        - A. 涨停基因 (50): 今日涨幅 + 连板 + 60 日涨停基因
        - B. 资金动能 (35): 换手率绝对值 + 换手率 5 日放大倍数 + 量比
        - C. 强势上涨动能 (25): 5 日累计涨幅 + MACD 强势 + 均线多头
        - D. 质量过滤 (10): RSI 强势但未超买

    与 V2 相比的关键改进：
        1. 涨停基因权重 35→50，凸显"涨停/连板/涨停基因"的核心地位
        2. 新增"换手率 5 日放大倍数"维度，捕捉资金突然异动
        3. 新增"5 日累计涨幅 + MACD 强势 + 均线多头"组合，体现突破与动量
        4. RSI 双区间过滤，避免追在超买区（>=90 不得分）

    Args:
        hist_data: 历史K线列表
        stock_info: 股票基本信息
        float_shares: 流通股本（股），用于计算真实换手率

    Returns:
        (score, result_dict)
    """
    if not hist_data or len(hist_data) < 60:
        return 0, {}

    stock_code = (
        str(stock_info.get("代码") or stock_info.get("code") or "")
        if isinstance(stock_info, dict)
        else ""
    )
    indicators: Indicators = calculate_advanced_indicators(
        hist_data, float_shares, stock_code
    )
    if not indicators:
        return 0, {}

    score: int = 0
    reasons: list[str] = []
    details: Dict[str, Any] = {}

    # ====== A. 涨停基因 (50 分) ======
    today_change: float = indicators.get("change_pct", 0.0)

    # A1. 今日涨幅 (20 分)
    if today_change >= 9.9:
        a1_score: int = 20
        reasons.append(f"今日涨停(+{today_change:.2f}%)")
    elif today_change >= 7.0:
        a1_score = 12
        reasons.append(f"今日大阳(+{today_change:.2f}%)")
    elif today_change >= 5.0:
        a1_score = 6
        reasons.append(f"今日中阳(+{today_change:.2f}%)")
    elif today_change >= 3.0:
        a1_score = 2
    else:
        a1_score = 0
    score += a1_score
    details["a1_today_change_score"] = a1_score

    # A2. 连板奖励 (15 分)
    consecutive: int = indicators.get("consecutive_limit_up", 0)
    if consecutive >= 3:
        a2_score = 15
        reasons.append(f"{consecutive}连板")
    elif consecutive == 2:
        a2_score = 10
        reasons.append("2连板")
    elif consecutive == 1:
        a2_score = 5
        reasons.append("首板涨停")
    else:
        a2_score = 0
    score += a2_score
    details["a2_consecutive_score"] = a2_score

    # A3. 60 日涨停基因 (15 分)
    limit_up_count_60d: int = indicators.get("limit_up_count_60d", 0)
    if limit_up_count_60d >= 3:
        a3_score = 15
        reasons.append(f"60日涨停{limit_up_count_60d}次(涨停基因强)")
    elif limit_up_count_60d == 2:
        a3_score = 10
        reasons.append("60日涨停2次")
    elif limit_up_count_60d == 1:
        a3_score = 5
    else:
        a3_score = 0
    score += a3_score
    details["a3_limit_gene_score"] = a3_score

    # ====== B. 资金动能 (35 分) ======
    turnover: float = indicators.get("turnover", 0.0)
    turnover_ratio_5d: float = indicators.get("turnover_ratio_5d", 1.0)
    vol_ratio: float = indicators.get("vol_ratio", 1.0)

    # B1. 换手率绝对值 (15 分)
    if turnover >= 15.0:
        b1_score = 15
        reasons.append(f"换手率{turnover:.1f}%(高活跃)")
    elif turnover >= 10.0:
        b1_score = 12
        reasons.append(f"换手率{turnover:.1f}%")
    elif turnover >= 5.0:
        b1_score = 8
    elif turnover >= 3.0:
        b1_score = 4
    else:
        b1_score = 0  # 换手率 < 3% 直接 0 分（低活跃股不考虑）
    score += b1_score
    details["b1_turnover_score"] = b1_score

    # B2. 换手率 5 日放大倍数 (10 分) — 资金异动核心信号
    if turnover_ratio_5d >= 3.0:
        b2_score = 10
        reasons.append(f"换手率5日放大{turnover_ratio_5d:.1f}倍(资金异动)")
    elif turnover_ratio_5d >= 2.0:
        b2_score = 6
        reasons.append(f"换手率5日放大{turnover_ratio_5d:.1f}倍")
    elif turnover_ratio_5d >= 1.5:
        b2_score = 3
    else:
        b2_score = 0
    score += b2_score
    details["b2_turnover_amplify_score"] = b2_score

    # B3. 量比 (10 分)
    if vol_ratio >= 5.0:
        b3_score = 10
        reasons.append(f"量比{vol_ratio:.1f}(巨量)")
    elif vol_ratio >= 3.0:
        b3_score = 7
        reasons.append(f"量比{vol_ratio:.1f}")
    elif vol_ratio >= 2.0:
        b3_score = 4
    elif vol_ratio >= 1.5:
        b3_score = 2
    else:
        b3_score = 0
    score += b3_score
    details["b3_vol_ratio_score"] = b3_score

    # ====== C. 强势上涨动能 (25 分) ======
    change_5d: float = indicators.get("change_5d", 0.0)

    # C1. 5 日累计涨幅 (10 分)
    if change_5d >= 15.0:
        c1_score = 10
        reasons.append(f"5日累涨{change_5d:.1f}%")
    elif change_5d >= 10.0:
        c1_score = 7
        reasons.append(f"5日累涨{change_5d:.1f}%")
    elif change_5d >= 5.0:
        c1_score = 4
    elif change_5d >= 0:
        c1_score = 1
    else:
        c1_score = 0
    score += c1_score
    details["c1_change_5d_score"] = c1_score

    # C2. MACD 强势 (8 分)
    macd: Dict[str, Any] = indicators.get("macd", {})
    dif: float = float(macd.get("dif", 0.0))
    dea: float = float(macd.get("dea", 0.0))
    hist_val: float = float(macd.get("hist", 0.0))
    if dif > dea and dif > 0 and hist_val > 0:
        c2_score = 8
        reasons.append("MACD零轴上方强势")
    elif dif > dea and dif > 0:
        c2_score = 5
        reasons.append("MACD零轴上方")
    elif dif > dea:
        c2_score = 2
    else:
        c2_score = 0
    score += c2_score
    details["c2_macd_score"] = c2_score

    # C3. 均线多头 (7 分)
    ma5: float = float(indicators.get("ma5", 0.0))
    ma10: float = float(indicators.get("ma10", 0.0))
    ma20: float = float(indicators.get("ma20", 0.0))
    ma60: float = float(indicators.get("ma60", 0.0))
    if ma5 > ma10 > ma20 > ma60:
        c3_score = 7
        reasons.append("均线完全多头")
    elif ma5 > ma10 > ma20:
        c3_score = 5
        reasons.append("均线多头")
    elif ma5 > ma10:
        c3_score = 3
    elif ma5 > ma20:
        c3_score = 1
    else:
        c3_score = 0
    score += c3_score
    details["c3_ma_score"] = c3_score

    # ====== D. 质量过滤 (10 分) — RSI 强势但未超买 ======
    rsi: float = float(indicators.get("rsi", 50.0))
    if 60.0 <= rsi < 80.0:
        d_score = 10
        reasons.append(f"RSI强势区({rsi:.1f})")
    elif 50.0 <= rsi < 60.0:
        d_score = 5
    elif 80.0 <= rsi < 90.0:
        d_score = 3  # 较强但已偏超买
        reasons.append(f"RSI偏高({rsi:.1f})")
    elif 40.0 <= rsi < 50.0:
        d_score = 2
    else:
        # rsi < 40 或 >= 90: 动能不足 / 严重超买
        d_score = 0
    score += d_score
    details["d_rsi_score"] = d_score

    return (
        score,
        {
            "score": score,
            "reasons": reasons,
            "indicators": indicators,
            "details": details,
            "type": "激进型V3",
            "version": "V3",
        },
    )


def calculate_short_term_conservative(
    hist_data: StockDataList,
    stock_info: Dict[str, Any],
    float_shares: Optional[float] = None,
) -> Tuple[int, ScoreResult]:
    """超短线稳健型评分模型 V3（强势回踩,满分 100）

    设计理念：捕捉"强势股短期回踩、准备继续上攻"的买点。
    特别关注"上涨初期回踩"（20日涨幅 5~20%，趋势刚启动）。

    关键特征：
      1. 前期已确立强势上涨（10/20日累计涨幅为正）
      2. 当前处于健康回踩（股价靠近 MA5/MA10/MA20）
      3. 量能萎缩（缩量回踩，避免放量破位）
      4. 出现止跌企稳信号（KDJ金叉 / MACD绿柱缩短 / RSI超卖回升）

    维度（满分 100）：
        - A. 回踩形态识别 (40): 均线回踩 + 缩量回踩 + 止跌企稳
        - B. 上涨初期确认 (30): 前期已强势 + 趋势刚启动 + 涨停基因
        - C. 量价配合 (20): 换手率适中 + 资金未撤离
        - D. 质量过滤 (10): 位置适中 + RSI 健康

    与 V2 相比的关键改进：
        1. 核心场景从"低位反弹" → "强势股回踩"
        2. 新增"均线回踩检测"(距离 MA5/10/20 最小偏离)
        3. 新增"量能收缩比"(识别缩量回踩)
        4. 新增"止跌企稳信号"(KDJ金叉/MACD绿柱缩短/RSI超卖回升)
        5. 新增"MA5 拐头向上"(趋势重启信号)
        6. 特别优待"上涨初期回踩"(10日涨幅 5~20%)

    Args:
        hist_data: 历史K线列表
        stock_info: 股票基本信息
        float_shares: 流通股本（股），用于计算真实换手率

    Returns:
        (score, result_dict)
    """
    if not hist_data or len(hist_data) < 60:
        return 0, {}

    stock_code = (
        str(stock_info.get("代码") or stock_info.get("code") or "")
        if isinstance(stock_info, dict)
        else ""
    )
    indicators: Indicators = calculate_advanced_indicators(
        hist_data, float_shares, stock_code
    )
    if not indicators:
        return 0, {}

    score: int = 0
    reasons: list[str] = []
    details: Dict[str, Any] = {}

    # ====== A. 回踩形态识别 (40 分) ======
    nearest_ma_dist: float = float(indicators.get("nearest_ma_distance", 100.0))
    vol_shrink: float = float(indicators.get("volume_shrink_ratio", 1.0))
    macd_green_short: bool = bool(indicators.get("macd_green_shortening", False))
    macd_cross: bool = bool(indicators.get("macd_about_to_cross", False))
    rsi_rebound: bool = bool(indicators.get("rsi_bottom_rebound", False))

    # A1. 均线回踩 (20 分)
    # 重要: "下跌趋势中跟随下跌" 看似贴均线但不是有效回踩
    # 必须确保中期(60日)累计涨幅 > 0 (整体趋势上涨), 否则不算有效回踩
    # 直接用 60 日涨幅计算: 取更长时间窗口
    # 【审查修复】change_60d_real 原来在 if/else 两个分支里各标注了一次，
    # mypy 报 [no-redef] 会让 CI 的 typecheck job 失败。改为只标注一次。
    close_prices = [bar.get("close", 0) for bar in hist_data] if hist_data else []
    change_60d_real: float = 0.0
    if len(close_prices) >= 60:
        change_60d_real = float((close_prices[-1] / close_prices[-60] - 1) * 100)
    has_uptrend: bool = change_60d_real > 0.0  # 60 日涨幅 > 0 才算有上涨基础(放宽条件)

    if not has_uptrend:
        # 中期无上涨, "贴均线"可能是下跌中继, 不算回踩
        a1_score = 0
    elif nearest_ma_dist <= 1.0:
        a1_score = 20
        reasons.append(f"紧贴均线回踩({nearest_ma_dist:.2f}%)")
    elif nearest_ma_dist <= 2.0:
        a1_score = 15
        reasons.append(f"靠近均线({nearest_ma_dist:.2f}%)")
    elif nearest_ma_dist <= 3.0:
        a1_score = 10
        reasons.append(f"接近均线({nearest_ma_dist:.2f}%)")
    elif nearest_ma_dist <= 5.0:
        a1_score = 5
    else:
        a1_score = 0
    score += a1_score
    details["a1_pullback_to_ma_score"] = a1_score
    details["a1_has_uptrend"] = has_uptrend

    # A2. 缩量回踩 (10 分)
    if vol_shrink <= 0.6:
        a2_score = 10
        reasons.append(f"显著缩量({vol_shrink:.2f}倍)")
    elif vol_shrink <= 0.8:
        a2_score = 7
        reasons.append(f"温和缩量({vol_shrink:.2f}倍)")
    elif vol_shrink <= 1.0:
        a2_score = 3
    else:
        a2_score = 0
    score += a2_score
    details["a2_volume_shrink_score"] = a2_score

    # A3. 止跌企稳 (10 分)
    # 至少满足 2 个独立信号才算强企稳信号
    bottom_signals = sum([
        macd_green_short,
        macd_cross,
        rsi_rebound,
    ])
    if bottom_signals >= 2:
        a3_score = 10
        reasons.append("多重止跌信号共振")
    elif bottom_signals == 1:
        if macd_cross:
            a3_score = 8
            reasons.append("MACD接近金叉")
        elif rsi_rebound:
            a3_score = 7
            reasons.append("RSI超卖回升")
        elif macd_green_short:
            a3_score = 6
            reasons.append("MACD绿柱缩短")
        else:
            a3_score = 6
    else:
        a3_score = 0
    score += a3_score
    details["a3_bottom_signal_score"] = a3_score

    # ====== B. 上涨初期确认 (30 分) ======
    change_10d: float = float(indicators.get("change_10d", 0.0))
    ma5_turning: bool = bool(indicators.get("ma5_turning_up", False))

    # B1. 前期已确立强势 (15 分) — 上涨初期回踩特别优待
    if 5.0 <= change_10d <= 20.0:
        b1_score = 15
        reasons.append(f"上涨初期回踩(10d+{change_10d:.1f}%)")
    elif 0.0 <= change_10d < 5.0:
        b1_score = 12
        reasons.append(f"温和上涨(10d+{change_10d:.1f}%)")
    elif 20.0 < change_10d <= 35.0:
        b1_score = 8
        reasons.append(f"强势上涨(10d+{change_10d:.1f}%)")
    elif change_10d > 35.0:
        # 涨幅过大, 风险升高
        b1_score = 3
    else:  # change_10d < 0
        # 前期下跌, 不符合"上涨后回踩"特征
        b1_score = 0
    score += b1_score
    details["b1_prior_strength_score"] = b1_score

    # B2. 趋势刚启动 (10 分)
    ma5: float = float(indicators.get("ma5", 0.0))
    ma10: float = float(indicators.get("ma10", 0.0))
    ma20: float = float(indicators.get("ma20", 0.0))
    ma60: float = float(indicators.get("ma60", 0.0))
    if ma5 > ma10 > ma20 > ma60 and ma5_turning:
        b2_score = 10
        reasons.append("均线完全多头+MA5拐头")
    elif ma5 > ma10 > ma20 > ma60:
        b2_score = 7
        reasons.append("均线完全多头")
    elif ma5 > ma10 > ma20:
        b2_score = 5
        reasons.append("均线多头")
    elif ma5 > ma10 and ma5_turning:
        b2_score = 4
        reasons.append("MA5拐头向上")
    elif ma5 > ma10:
        b2_score = 2
    elif ma20 > ma60:
        b2_score = 1
    else:
        b2_score = 0
    score += b2_score
    details["b2_trend_start_score"] = b2_score

    # B3. 涨停基因 (5 分) — 历史涨停反映主力资金活跃
    limit_up_count_60d: int = int(indicators.get("limit_up_count_60d", 0))
    if limit_up_count_60d >= 3:
        b3_score = 5
        reasons.append(f"60日涨停{limit_up_count_60d}次(主力活跃)")
    elif limit_up_count_60d >= 2:
        b3_score = 3
    elif limit_up_count_60d >= 1:
        b3_score = 1
    else:
        b3_score = 0
    score += b3_score
    details["b3_limit_gene_score"] = b3_score

    # ====== C. 量价配合 (20 分) ======
    turnover: float = float(indicators.get("turnover", 0.0))
    vol_ratio: float = float(indicators.get("vol_ratio", 1.0))

    # C1. 换手率适中 (10 分)
    # 稳健型偏好 3~12% 的活跃区间, 避免过低/过高
    if 3.0 <= turnover <= 8.0:
        c1_score = 10
        reasons.append(f"换手率适中({turnover:.1f}%)")
    elif 8.0 < turnover <= 12.0:
        c1_score = 8
    elif 12.0 < turnover <= 20.0:
        c1_score = 5
    elif 2.0 <= turnover < 3.0:
        c1_score = 3
    else:
        # < 2% 或 > 20% 不适合稳健型
        c1_score = 0
    score += c1_score
    details["c1_turnover_score"] = c1_score

    # C2. 资金未撤离 (10 分) — 量能参考(配合 vol_shrink 已用, 这里用 vol_ratio)
    # vol_ratio 适度即可(0.8~2.0), 过高可能是放量破位
    if 0.8 <= vol_ratio <= 2.0:
        c2_score = 10
        reasons.append(f"量能稳定({vol_ratio:.2f})")
    elif 2.0 < vol_ratio <= 3.0:
        c2_score = 5
    elif vol_ratio > 3.0:
        c2_score = 2  # 放量破位风险
    else:
        c2_score = 3  # 量能过低
    score += c2_score
    details["c2_volume_stable_score"] = c2_score

    # ====== D. 质量过滤 (10 分) ======
    position: float = float(indicators.get("position", 0.5))
    rsi: float = float(indicators.get("rsi", 50.0))

    # D1. 位置适中 (5 分)
    # 稳健型偏好 0.3~0.7(趋势中段), 避免过高(高位回踩可能续跌)
    if 0.35 <= position <= 0.65:
        d1_score = 5
        reasons.append(f"位置适中({position:.1%})")
    elif 0.25 <= position < 0.35 or 0.65 < position <= 0.75:
        d1_score = 3
    elif 0.15 <= position < 0.25:
        d1_score = 2
    else:
        d1_score = 0
    score += d1_score
    details["d1_position_score"] = d1_score

    # D2. RSI 健康 (5 分)
    # 稳健型偏好 40~65(已止跌但未超买)
    if 40.0 <= rsi <= 65.0:
        d2_score = 5
        reasons.append(f"RSI健康({rsi:.1f})")
    elif 30.0 <= rsi < 40.0:
        d2_score = 3  # 仍偏超卖, 但配合金叉信号可接受
    elif 65.0 < rsi <= 75.0:
        d2_score = 2  # 偏强但接近超买
    else:
        # RSI < 30(严重超卖, 可能是下跌中继) 或 RSI > 75(已超买)
        d2_score = 0
    score += d2_score
    details["d2_rsi_score"] = d2_score

    # ====== 总分门槛(防止"伪回踩"骗分) ======
    # 只有 A 维度(回踩形态)得分 >= 15 才认为是有效的回踩买点
    # 否则即使是其他维度都好, 也不符合"强势回踩"的核心场景
    pullback_score_total: int = a1_score + a2_score + a3_score
    details["pullback_score_total"] = pullback_score_total

    if pullback_score_total < 15:
        # 没有形成有效的回踩买点, 整体得分封顶(降权)
        # 目的是: 让稳健型严格只选出"真正的回踩股"
        cap_score: int = min(score, 25)
        details["cap_applied"] = True
        details["cap_reason"] = "回踩形态不足,总分封顶25"
        score = cap_score

    return (
        score,
        {
            "score": score,
            "reasons": reasons,
            "indicators": indicators,
            "details": details,
            "type": "稳健型V3",
            "version": "V3",
            "scenario": "强势回踩",
        },
    )