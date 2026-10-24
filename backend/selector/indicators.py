#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
技术指标计算 - MA/MACD/RSI/BOLL/RSRS/KDJ/CCI 等

所有函数都是"纯函数"——输入 hist_data，输出指标 dict，便于测试和组合。
"""
from __future__ import annotations

import warnings
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from ._types import HistData, Indicators, StockDataList

# ================== 全局常量 ==================

# "60 日涨停基因"统计窗口（交易日）
LIMIT_UP_WINDOW_DAYS: int = 60

# "相对 250 日位置"统计窗口（交易日）
POSITION_WINDOW_DAYS: int = 250

# ================== 基础技术指标 ==================


def calculate_technical_indicators(hist_data: StockDataList) -> Indicators:
    """计算基础技术指标 (MA / 均线位置 / 量比 / 波动率 / 近期涨幅)

    Args:
        hist_data: 历史K线列表，按日期正序，至少 20 根

    Returns:
        {ma5, ma10, ma20, ma60, current_price, position, vol_ratio,
         volatility, change_5d, change_20d, volume} - 数据不足时返回 {}
    """
    if not hist_data or len(hist_data) < 20:
        return {}

    df = pd.DataFrame(hist_data)
    close: np.ndarray = df["close"].values

    ma5: float = float(np.mean(close[-5:]))
    ma10: float = float(np.mean(close[-10:]))
    ma20: float = float(np.mean(close[-20:]))
    ma60: float = float(np.mean(close[-60:])) if len(close) >= 60 else ma20

    current_price: float = float(close[-1])
    # 【审查修复】与 calculate_advanced_indicators 保持一致："相对 250 日位置"
    # 只取最近 250 根 K 线，不足 250 根时退化为全样本。
    position_window: np.ndarray = (
        close[-POSITION_WINDOW_DAYS:]
        if len(close) > POSITION_WINDOW_DAYS
        else close
    )
    high_250: float = float(np.max(position_window))
    low_250: float = float(np.min(position_window))
    position: float = (
        (current_price - low_250) / (high_250 - low_250)
        if high_250 != low_250
        else 0.5
    )

    vol_ma5: float = float(np.mean(df["volume"].values[-5:]))
    vol_ma20: float = float(np.mean(df["volume"].values[-20:]))
    vol_ratio: float = vol_ma5 / vol_ma20 if vol_ma20 > 0 else 1.0

    returns: np.ndarray = np.diff(np.log(close))
    volatility: float = (
        float(np.std(returns) * np.sqrt(250)) if len(returns) > 0 else 0.0
    )

    change_5d: float = (
        float((close[-1] / close[-6] - 1) * 100) if len(close) >= 6 else 0.0
    )
    change_20d: float = (
        float((close[-1] / close[-21] - 1) * 100) if len(close) >= 21 else 0.0
    )

    return {
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "current_price": current_price,
        "position": position,
        "vol_ratio": vol_ratio,
        "volatility": volatility,
        "change_5d": change_5d,
        "change_20d": change_20d,
        "volume": float(df["volume"].values[-1]),
    }


# ================== 高级技术指标 ==================


def _limit_up_threshold(code: Optional[str]) -> float:
    """按板块返回涨停判定阈值

    主板(60/00) 10%，创业板(30)/科创板(688) 20%，北交所(8/920) 30%。
    统一用 9.9% 会把科创板 10~15% 的普通上涨误判成涨停。

    阈值取略低于名义涨停价（10%→9.9%），以容忍四舍五入误差。
    """
    if not code or len(code) < 3:
        return 9.9
    prefix: str = code[:3]
    if prefix in ("688", "689"):
        return 19.5   # 科创板 20%
    if prefix.startswith("30"):
        return 19.5   # 创业板 20%（300/301/302…）
    if prefix.startswith("8") or prefix.startswith("920"):
        return 29.5   # 北交所 30%
    return 9.9       # 主板 10%（600/601/603/605/000/001/002/003）


def calculate_advanced_indicators(
    hist_data: StockDataList,
    float_shares: Optional[float] = None,
    code: Optional[str] = None,
) -> Indicators:
    """计算高级技术指标 (MACD/RSI/BOLL/均线形态/换手率/涨停基因)

    Args:
        hist_data: 历史K线列表，至少 60 根
        float_shares: 流通股本（股）。本地 K 线没有 turnover 字段，
            没有真实股本就只能用固定 1e8 股本粗估换手率，导致 98% 的股票
            换手率被高估 2 倍以上。传入真实流通股本可消除该误差。
        code: 股票代码，用于按板块区分涨停阈值（主板10%/双创20%/北交所30%）

    Returns:
        包含 macd/rsi/bollinger/ma*/position/vol_ratio/turnover/volatility/change_*
        和 涨停基因 (limit_up_count_60d/consecutive_limit_up) 的 dict
    """
    if not hist_data or len(hist_data) < 60:
        return {}

    df = pd.DataFrame(hist_data)
    close: np.ndarray = df["close"].values
    volume: np.ndarray = df["volume"].values

    indicators: Indicators = {}

    # === MACD ===
    try:
        ema12: pd.Series = pd.Series(close).ewm(span=12, adjust=False).mean()
        ema26: pd.Series = pd.Series(close).ewm(span=26, adjust=False).mean()
        dif: pd.Series = ema12 - ema26
        dea: pd.Series = pd.Series(dif).ewm(span=9, adjust=False).mean()
        macd_hist: pd.Series = 2 * (dif - dea)

        indicators["macd"] = {
            "dif": float(dif.iloc[-1]),
            "dea": float(dea.iloc[-1]),
            "hist": float(macd_hist.iloc[-1]),
            "dif_above_zero": bool(dif.iloc[-1] > 0),
            "golden_cross": bool(
                dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-2] <= dea.iloc[-2]
            ),
        }
    except Exception:
        indicators["macd"] = {
            "dif": 0.0,
            "dea": 0.0,
            "hist": 0.0,
            "dif_above_zero": False,
            "golden_cross": False,
        }

    # === RSI ===
    try:
        delta: pd.Series = pd.Series(close).diff()
        gain: pd.Series = delta.where(delta > 0, 0).rolling(14).mean()
        loss: pd.Series = (-delta.where(delta < 0, 0)).rolling(14).mean()
        if loss.iloc[-1] == 0 or pd.isna(loss.iloc[-1]):
            indicators["rsi"] = 100.0
        else:
            rs: pd.Series = gain / loss
            rsi: pd.Series = 100 - (100 / (1 + rs))
            indicators["rsi"] = (
                float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0
            )
    except Exception:
        indicators["rsi"] = 50.0

    # === 布林带 ===
    try:
        ma20_series: pd.Series = pd.Series(close).rolling(20).mean()
        std20: pd.Series = pd.Series(close).rolling(20).std()
        upper_band: pd.Series = ma20_series + 2 * std20
        lower_band: pd.Series = ma20_series - 2 * std20

        boll_position: float = (
            (close[-1] - lower_band.iloc[-1])
            / (upper_band.iloc[-1] - lower_band.iloc[-1])
            if upper_band.iloc[-1] != lower_band.iloc[-1]
            else 0.5
        )

        indicators["bollinger"] = {
            "upper": float(upper_band.iloc[-1]),
            "middle": float(ma20_series.iloc[-1]),
            "lower": float(lower_band.iloc[-1]),
            "position": float(boll_position),
        }
    except Exception:
        indicators["bollinger"] = {
            "upper": 0.0,
            "middle": 0.0,
            "lower": 0.0,
            "position": 0.5,
        }

    # === 均线 ===
    indicators["ma5"] = float(np.mean(close[-5:]))
    indicators["ma10"] = float(np.mean(close[-10:]))
    indicators["ma20"] = float(np.mean(close[-20:]))
    indicators["ma60"] = (
        float(np.mean(close[-60:])) if len(close) >= 60 else indicators["ma20"]
    )

    indicators["ma_bullish"] = bool(
        indicators["ma5"] > indicators["ma10"] > indicators["ma20"] > indicators["ma60"]
    )
    indicators["ma_partial_bullish"] = bool(
        indicators["ma5"] > indicators["ma20"] > indicators["ma60"]
    )

    # === 相对位置 ===
    # 【审查修复】变量名与评分文档都写明"相对 250 日位置"，但实现取的是整条
    # 数组的极值。调用方给中长线传 360 根 K 线，于是实际算的是"1.5 年位置"，
    # 与设计口径不符（实测 15% 的股票位置值偏移 >0.1，足以跨过 0.25/0.75 阈值）。
    # 这里显式裁剪到最近 250 根，使实现与"250 日位置"的定义一致；
    # 数据不足 250 根时退化为全样本（与原行为一致）。
    position_window: np.ndarray = (
        close[-POSITION_WINDOW_DAYS:]
        if len(close) > POSITION_WINDOW_DAYS
        else close
    )
    high_250: float = float(np.max(position_window))
    low_250: float = float(np.min(position_window))
    indicators["position"] = float(
        (close[-1] - low_250) / (high_250 - low_250) if high_250 != low_250 else 0.5
    )
    indicators["position_window_days"] = int(len(position_window))

    # === 量比 ===
    vol_ma5: float = float(np.mean(volume[-5:]))
    vol_ma20: float = float(np.mean(volume[-20:]))
    indicators["vol_ratio"] = float(vol_ma5 / vol_ma20) if vol_ma20 > 0 else 1.0

    # === 换手率 ===
    # 【修复】本地 K 线不含 turnover 字段。原实现用固定 1e8 股本粗估，
    # 实测 98% 的股票换手率被高估 2 倍以上（真实中位 0.26% 却被估成 1.91%），
    # 激进型评分的"换手率"15分因此几乎完全失真。
    # 优先用真实流通股本；确实拿不到时才退回粗估。
    turnover_series: Optional[np.ndarray] = None
    if "turnover" in df.columns:
        turnover_series = df["turnover"].values.astype(float)
        indicators["turnover"] = float(turnover_series[-1])
        indicators["turnover_estimated"] = False  # 数据源自带换手率，非估算
    elif float_shares and close[-1] > 0:
        indicators["turnover"] = float(volume[-1] / (close[-1] * float_shares) * 100)
        indicators["turnover_estimated"] = False
        # 【审查修复】用 np.where 屏蔽非正收盘价，避免除零产生 inf/nan
        # 污染整条换手率序列（进而让"换手放大倍数"变成 inf 而虚增评分）。
        turnover_series = np.where(
            close > 0,
            volume / np.where(close > 0, close, 1.0) / float_shares * 100.0,
            0.0,
        ).astype(float)
    else:
        indicators["turnover"] = (
            float(volume[-1] / (close[-1] * 1e8) * 100) if close[-1] > 0 else 0.0
        )
        indicators["turnover_estimated"] = True  # 股本缺失，标记为粗估值

    # === 换手率 5 日放大倍数（资金异动核心信号）===
    # 计算当日换手率 / 近 5 日均换手率，反映"突然放量"
    # 【修复】原来在没有 turnover 字段时直接置 1.0，导致"资金异动"10分恒为 0，
    # 该维度对所有股票完全失效。现在用成交量序列同样可以算出该比值
    # （换手率比值 = 成交量比值，与股本无关），因此不再失效。
    if turnover_series is not None and len(turnover_series) >= 6:
        turnover_ma5: float = float(np.mean(turnover_series[-6:-1]))
        if turnover_ma5 > 0:
            ratio_5d: float = float(indicators["turnover"] / turnover_ma5)
            # 【审查修复】非有限值（inf/nan）一律回落到 1.0，
            # 避免异常数据让"资金异动"维度凭空拿到满分。
            indicators["turnover_ratio_5d"] = (
                ratio_5d if np.isfinite(ratio_5d) else 1.0
            )
        else:
            indicators["turnover_ratio_5d"] = 1.0
    else:
        indicators["turnover_ratio_5d"] = 1.0

    # === 波动率 ===
    returns: np.ndarray = np.diff(np.log(close))
    indicators["volatility"] = (
        float(np.std(returns) * np.sqrt(250)) if len(returns) > 0 else 0.0
    )

    # === 近期涨幅 ===
    indicators["change_5d"] = (
        float((close[-1] / close[-6] - 1) * 100) if len(close) >= 6 else 0.0
    )
    indicators["change_10d"] = (
        float((close[-1] / close[-11] - 1) * 100) if len(close) >= 11 else 0.0
    )
    indicators["change_20d"] = (
        float((close[-1] / close[-21] - 1) * 100) if len(close) >= 21 else 0.0
    )

    # === 涨停基因 ===
    # 【修复】本地 K 线不含 change_pct 字段，原实现直接置 0，
    # 导致"连板"(15分) + "60日涨停基因"(15分) 这 30 分对所有股票恒为 0，
    # 激进型 120 分里有 25% 完全失效。这里用收盘价自行推算涨跌幅。
    if "change_pct" in df.columns:
        limit_ups: np.ndarray = df["change_pct"].values.astype(float)
    else:
        # 由收盘价推算：涨跌幅% = (今收/昨收 - 1) * 100，首根无前值故置 0
        limit_ups = np.zeros_like(close, dtype=float)
        if len(close) >= 2:
            prev: np.ndarray = close[:-1]
            valid: np.ndarray = prev > 0
            limit_ups[1:][valid] = (close[1:][valid] / prev[valid] - 1.0) * 100.0

    # 涨停阈值需区分板块：创业板/科创板 20%，北交所 30%，主板 10%
    threshold: float = _limit_up_threshold(code)
    indicators["limit_up_threshold"] = threshold

    # 【审查修复】"60 日涨停基因"必须只统计最近 60 个交易日。
    # 调用方传入的是 max_days=360 根 K 线（strategy 里统一 360），
    # 直接对整条数组统计等于按 1.5 年统计：实测中位涨停数 4 次(360日)
    # vs 0 次(真60日)，83% 的股票被高估，导致 >=3 次的 15 分几乎人人可得，
    # 该维度彻底丧失区分度。这里显式裁剪窗口。
    limit_up_window: np.ndarray = (
        limit_ups[-LIMIT_UP_WINDOW_DAYS:] if len(limit_ups) > LIMIT_UP_WINDOW_DAYS
        else limit_ups
    )
    indicators["limit_up_count_60d"] = int(
        sum(1 for c in limit_up_window if c >= threshold)
    )

    # 连板数从最新一根往回数，遇非涨停即停（与窗口长度无关，天然只反映"当下"）
    consecutive: int = 0
    for c in reversed(limit_ups):
        if c >= threshold:
            consecutive += 1
        else:
            break
    indicators["consecutive_limit_up"] = consecutive

    # === 强势回踩专用指标 (用于稳健型选股) ===

    # 高点回撤幅度: 当前价距 10 日内最高价的回撤比例
    if len(close) >= 10:
        high_10d: float = float(np.max(close[-10:]))
        indicators["pullback_from_10d_high"] = float(
            (close[-1] - high_10d) / high_10d * 100 if high_10d > 0 else 0.0
        )
    else:
        indicators["pullback_from_10d_high"] = 0.0

    # 均线偏离度: 当前价对 MA5/MA10/MA20 的偏离百分比(正值=站上,负值=跌破)
    indicators["ma5_deviation"] = float(
        (close[-1] - indicators["ma5"]) / indicators["ma5"] * 100
        if indicators["ma5"] > 0 else 0.0
    )
    indicators["ma10_deviation"] = float(
        (close[-1] - indicators["ma10"]) / indicators["ma10"] * 100
        if indicators["ma10"] > 0 else 0.0
    )
    indicators["ma20_deviation"] = float(
        (close[-1] - indicators["ma20"]) / indicators["ma20"] * 100
        if indicators["ma20"] > 0 else 0.0
    )

    # 均线回踩检测: 当前价与 MA5/MA10/MA20 距离的最小绝对值(越小越接近均线)
    indicators["nearest_ma_distance"] = float(min(
        abs(indicators["ma5_deviation"]),
        abs(indicators["ma10_deviation"]),
        abs(indicators["ma20_deviation"]),
    ))

    # 量能收缩比: 当前 vol_ratio 与近 10 日平均 vol_ratio 的比值
    # < 1 表示当前阶段量能较前期萎缩(回踩典型特征)
    vol_series: np.ndarray = volume
    if len(vol_series) >= 20:
        vol_ma10_recent: float = float(np.mean(vol_series[-10:]))
        vol_ma10_prev: float = float(np.mean(vol_series[-20:-10]))
        indicators["volume_shrink_ratio"] = float(
            vol_ma10_recent / vol_ma10_prev if vol_ma10_prev > 0 else 1.0
        )
    else:
        indicators["volume_shrink_ratio"] = 1.0

    # MACD 绿柱缩短 (止跌信号): 比较最近 2 根 MACD 柱状图
    # 前期为绿柱(hist<0), 现在绿柱变短, 表示下跌动能衰减
    if len(close) >= 30:
        try:
            ema12_s: pd.Series = pd.Series(close).ewm(span=12, adjust=False).mean()
            ema26_s: pd.Series = pd.Series(close).ewm(span=26, adjust=False).mean()
            dif_s: pd.Series = ema12_s - ema26_s
            dea_s: pd.Series = pd.Series(dif_s).ewm(span=9, adjust=False).mean()
            hist_s: pd.Series = 2 * (dif_s - dea_s)
            if len(hist_s) >= 3:
                hist_now: float = float(hist_s.iloc[-1])
                hist_prev: float = float(hist_s.iloc[-2])
                # 绿柱缩短: 之前为负且绝对值在变小(趋向零轴)
                indicators["macd_green_shortening"] = bool(
                    hist_prev < 0 and hist_now > hist_prev and hist_now <= 0
                )
                # MACD 金叉/死叉预警(0轴附近)
                indicators["macd_about_to_cross"] = bool(
                    abs(hist_now) < abs(hist_prev) and abs(hist_now) < 0.1
                )
            else:
                indicators["macd_green_shortening"] = False
                indicators["macd_about_to_cross"] = False
        except Exception:
            indicators["macd_green_shortening"] = False
            indicators["macd_about_to_cross"] = False
    else:
        indicators["macd_green_shortening"] = False
        indicators["macd_about_to_cross"] = False

    # RSI 超卖回升 (止跌信号): RSI_6 < 40 且近期由更低值回升
    if len(close) >= 14:
        try:
            delta_s: pd.Series = pd.Series(close).diff()
            gain_s: pd.Series = delta_s.where(delta_s > 0, 0).rolling(6).mean()
            loss_s: pd.Series = (-delta_s.where(delta_s < 0, 0)).rolling(6).mean()
            if len(gain_s) >= 3 and loss_s.iloc[-1] not in (0, None):
                if loss_s.iloc[-1] > 0:
                    rs_now: float = float(gain_s.iloc[-1] / loss_s.iloc[-1])
                    rsi_now: float = 100 - (100 / (1 + rs_now))
                    # 3 日前 RSI
                    if loss_s.iloc[-3] > 0:
                        rs_3ago: float = float(gain_s.iloc[-3] / loss_s.iloc[-3])
                        rsi_3ago: float = 100 - (100 / (1 + rs_3ago))
                        # RSI 超卖区(<=40) 且从更低位回升
                        indicators["rsi_bottom_rebound"] = bool(
                            rsi_now <= 40 and rsi_now > rsi_3ago
                        )
                    else:
                        indicators["rsi_bottom_rebound"] = False
                else:
                    indicators["rsi_bottom_rebound"] = False
            else:
                indicators["rsi_bottom_rebound"] = False
        except Exception:
            indicators["rsi_bottom_rebound"] = False
    else:
        indicators["rsi_bottom_rebound"] = False

    # MA5 拐头向上 (趋势重启信号): MA5 当日值 > 昨日值
    if len(close) >= 6:
        ma5_now: float = float(np.mean(close[-5:]))
        ma5_yesterday: float = float(np.mean(close[-6:-1]))
        indicators["ma5_turning_up"] = bool(ma5_now > ma5_yesterday)
    else:
        indicators["ma5_turning_up"] = False

    # === 今日涨幅 ===
    if "change_pct" in df.columns:
        indicators["change_pct"] = float(df["change_pct"].values[-1])
    else:
        prev_close: float = (
            float(df["close"].values[-2]) if len(df) >= 2 else float(df["close"].values[-1])
        )
        curr_close: float = float(df["close"].values[-1])
        indicators["change_pct"] = (
            float((curr_close - prev_close) / prev_close * 100) if prev_close > 0 else 0.0
        )

    return indicators


# ================== RSRS 阻力支撑相对强度 ==================


def calculate_rsrs(hist_data: StockDataList, period: int = 18) -> Dict[str, Any]:
    """计算 RSRS 指标（阻力支撑相对强度）

    RSRS = 斜率值，通过 N 日最高价与最低价的线性回归获得
    斜率 > 0 表示趋势向上，斜率越大趋势越强

    Args:
        hist_data: 历史K线列表，至少 period+1 根
        period: 回归窗口（默认 18）

    Returns:
        {
            'rsrs': float,        # 当前周期 RSRS 值(同 slope)
            'slope': float,       # 线性回归斜率(供调用方直接使用)
            'rsrs_ma': float,     # RSRS 5 日均值
            'signal': str         # 'strong_up' / 'up' / 'neutral' / 'down' / 'strong_down'
        }
        数据不足时返回带默认值的 dict（避免 NoneType 错误）
    """
    # 默认返回结构 - 包含全部字段，避免上游 .get('slope', 0) 险责
    empty: Dict[str, Any] = {
        "rsrs": 1.0,
        "slope": 1.0,
        "rsrs_ma": 1.0,
        "signal": "neutral",
    }

    if not hist_data or len(hist_data) < period + 1:
        return empty

    df = pd.DataFrame(hist_data)
    close: np.ndarray = df["close"].values
    high: np.ndarray = df["high"].values
    low: np.ndarray = df["low"].values

    results: list[float] = []
    for i in range(period, len(close)):
        high_n: np.ndarray = high[i - period : i]
        low_n: np.ndarray = low[i - period : i]

        # 线性回归 y = kx + b, y=最高价, x=最低价
        try:
            x: np.ndarray = np.array(low_n)
            y: np.ndarray = np.array(high_n)
            if len(x) > 0 and len(y) > 0:
                # 压制 polyfit 的所有警告（低价股数据变化极小时可能触发）
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    k: float = float(np.polyfit(x, y, 1)[0])
                results.append(k)
            else:
                results.append(1.0)
        except Exception:
            results.append(1.0)

    if not results:
        return empty

    rsrs_current: float = results[-1]
    rsrs_ma: float = float(np.mean(results[-5:])) if len(results) >= 5 else rsrs_current

    # 信号判断
    signal: str = "neutral"
    if rsrs_current > 1.05 and rsrs_ma > 1.0:
        signal = "strong_up"
    elif rsrs_current > 1.0 and rsrs_ma > 0.98:
        signal = "up"
    elif rsrs_current < 0.95 and rsrs_ma < 1.0:
        signal = "down"
    elif rsrs_current < 0.9:
        signal = "strong_down"

    return {
        "rsrs": float(rsrs_current),
        "slope": float(rsrs_current),  # slope与rsrs相同，都是线性回归斜率
        "rsrs_ma": float(rsrs_ma),
        "signal": signal,
    }


# ================== KDJ / RSI / CCI 组合 ==================


def calculate_kdj_rsi_cci(hist_data: StockDataList) -> Dict[str, Any]:
    """计算 KDJ / RSI / CCI 组合指标（用于超买超卖判断）

    Args:
        hist_data: 历史K线列表，至少 20 根

    Returns:
        {
            'kdjk','kdjd','kdjj': float,           # KDJ 三个值
            'kdj_oversold','kdj_overbought': bool, # KDJ 超买超卖
            'kdj_golden_cross','kdj_death_cross': bool,
            'rsi_6','rsi_12': float,               # RSI 双周期
            'rsi_oversold','rsi_overbought': bool,
            'rsi_oversold_12','rsi_overbought_12': bool,
            'cci': float,
            'cci_oversold','cci_overbought': bool,
            'cci_calculation_failed': bool
        }
        数据不足时返回 {}
    """
    if not hist_data or len(hist_data) < 20:
        return {}

    df = pd.DataFrame(hist_data)
    close: np.ndarray = df["close"].values
    high: np.ndarray = df["high"].values
    low: np.ndarray = df["low"].values

    result: Dict[str, Any] = {}

    # === KDJ ===
    period: int = 9
    k_values: list[float] = []
    d_values: list[float] = []
    j_values: list[float] = []

    for i in range(period, len(close)):
        n_high: np.ndarray = high[i - period + 1 : i + 1]
        n_low: np.ndarray = low[i - period + 1 : i + 1]
        rsv: float = (
            (close[i] - np.min(n_low)) / (np.max(n_high) - np.min(n_low)) * 100
            if np.max(n_high) != np.min(n_low)
            else 50.0
        )

        k: float = (
            2 / 3 * 50 + 1 / 3 * rsv
            if len(k_values) == 0
            else 2 / 3 * k_values[-1] + 1 / 3 * rsv
        )
        d: float = (
            2 / 3 * 50 + 1 / 3 * k
            if len(d_values) == 0
            else 2 / 3 * d_values[-1] + 1 / 3 * k
        )
        j: float = 3 * k - 2 * d

        k_values.append(k)
        d_values.append(d)
        j_values.append(j)

    if k_values:
        result["kdjk"] = float(k_values[-1])
        result["kdjd"] = float(d_values[-1])
        result["kdjj"] = float(j_values[-1])
        result["kdj_oversold"] = bool(k_values[-1] < 20)
        result["kdj_overbought"] = bool(k_values[-1] > 80)
        # 【修复P0-3】KDJ金叉/死叉信号均补全
        result["kdj_golden_cross"] = bool(
            len(k_values) >= 2
            and k_values[-1] > d_values[-1]
            and k_values[-2] <= d_values[-2]
        )
        result["kdj_death_cross"] = bool(
            len(k_values) >= 2
            and k_values[-1] < d_values[-1]
            and k_values[-2] >= d_values[-2]
        )
    else:
        result["kdjk"] = None
        result["kdjd"] = None
        result["kdjj"] = None
        result["kdj_oversold"] = False
        result["kdj_overbought"] = False
        result["kdj_golden_cross"] = False
        result["kdj_death_cross"] = False

    # === RSI ===
    try:
        delta: pd.Series = pd.Series(close).diff()
        gain: pd.Series = delta.where(delta > 0, 0).rolling(6).mean()
        loss: pd.Series = (-delta.where(delta < 0, 0)).rolling(6).mean()

        # 安全计算RSI，处理零除情况
        rsi_6_val: float = 50.0
        if not (pd.isna(gain.iloc[-1]) or pd.isna(loss.iloc[-1])):
            if loss.iloc[-1] == 0:
                rsi_6_val = 100.0 if gain.iloc[-1] > 0 else 50.0
            elif gain.iloc[-1] == 0 and loss.iloc[-1] > 0:
                rsi_6_val = 0.0
            else:
                rs: float = gain.iloc[-1] / loss.iloc[-1]
                rsi_6_val = 100.0 - (100.0 / (1 + rs))
        result["rsi_6"] = rsi_6_val

        gain12: pd.Series = delta.where(delta > 0, 0).rolling(12).mean()
        loss12: pd.Series = (-delta.where(delta < 0, 0)).rolling(12).mean()

        rsi_12_val: float = 50.0
        if not (pd.isna(gain12.iloc[-1]) or pd.isna(loss12.iloc[-1])):
            if loss12.iloc[-1] == 0:
                rsi_12_val = 100.0 if gain12.iloc[-1] > 0 else 50.0
            elif gain12.iloc[-1] == 0 and loss12.iloc[-1] > 0:
                rsi_12_val = 0.0
            else:
                rs12: float = gain12.iloc[-1] / loss12.iloc[-1]
                rsi_12_val = 100.0 - (100.0 / (1 + rs12))
        result["rsi_12"] = rsi_12_val
    except Exception:
        # 【修复P2-9】RSI计算失败时返回None，与RSI=50（中性正常值）区分
        result["rsi_6"] = None
        result["rsi_12"] = None

    # 【修复P2-9】RSI超买超卖（双周期），安全处理None
    rsi_6: Optional[float] = result["rsi_6"]
    rsi_12: Optional[float] = result["rsi_12"]
    result["rsi_oversold"] = rsi_6 is not None and rsi_6 < 30
    result["rsi_overbought"] = rsi_6 is not None and rsi_6 > 70
    result["rsi_oversold_12"] = rsi_12 is not None and rsi_12 < 30
    result["rsi_overbought_12"] = rsi_12 is not None and rsi_12 > 70

    # === CCI ===
    try:
        tp: np.ndarray = (high + low + close) / 3
        sma_tp: pd.Series = pd.Series(tp).rolling(14).mean()
        mad: pd.Series = pd.Series(tp).rolling(14).apply(
            lambda x: np.abs(x - x.mean()).mean()
        )
        cci: pd.Series = (tp - sma_tp) / (0.015 * mad)
        cci_val: Any = cci.iloc[-1]
        if pd.isna(cci_val):
            result["cci"] = np.nan
            result["cci_calculation_failed"] = True
        else:
            result["cci"] = float(cci_val)
            result["cci_calculation_failed"] = False
    except Exception:
        result["cci"] = np.nan
        result["cci_calculation_failed"] = True

    cci_now: float = result["cci"]
    result["cci_oversold"] = bool(cci_now < -100) if not pd.isna(cci_now) else False
    result["cci_overbought"] = bool(cci_now > 100) if not pd.isna(cci_now) else False

    return result