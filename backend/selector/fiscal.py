#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
基本面数据获取器 - 从本地财务文件读取数据

CANSLIM 风格评分（C=Current 净利润, A=Annual 增长, etc）
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


# ============================================================
# 财务记录解析辅助函数
#
# 历史数据里 period 字段格式不统一：最新季度写成 '06-30'（带横杠），
# 历史季度写成 '0630'（不带横杠）。直接用 period 做字符串匹配会漏掉
# 几乎所有股票，因此统一改为"季度月份 + 年份"解析。
# ============================================================

# 季度月份 -> 年化系数（归母净利润为年初至今累计值，需年化才是全年 ROE）
_ANNUALIZE_FACTOR: Dict[int, float] = {3: 4.0, 6: 2.0, 9: 4.0 / 3.0, 12: 1.0}

# report_date 解析：兼容 "2026-06-30" / "2026-6-30" / "20260630" / "2026年6月30日"
# 【审查修复】原实现用固定下标 rd[5:7] 取月份，遇到不补零的 "2026-6-30" 会取到
# "6-" 而解析失败（月份退化为 None → 年化系数丢失 → ROE 量级差 2~4 倍）。
_REPORT_DATE_RE = re.compile(r"^\s*(\d{4})\s*[-/.年]?\s*(\d{1,2})")


def _parse_report_date(report_date: Any) -> Tuple[Optional[int], Optional[int]]:
    """解析 report_date，返回 (年, 月)；解析不出来返回 (None, None)"""
    rd: str = str(report_date or "")
    matched = _REPORT_DATE_RE.match(rd)
    if not matched:
        return (None, None)
    return (int(matched.group(1)), int(matched.group(2)))


def _quarter_month(rec: Dict[str, Any]) -> Optional[int]:
    """从财务记录中解析季度月份（3/6/9/12），兼容 report_date / period 各种写法"""
    rd: str = str(rec.get("report_date", "") or "")
    _year, rd_month = _parse_report_date(rd)
    if rd_month is not None and rd_month in _ANNUALIZE_FACTOR:
        return rd_month

    period: str = str(rec.get("period", "") or "")
    digits: str = "".join(ch for ch in period if ch.isdigit())
    if len(digits) >= 2:
        mm: int = int(digits[:2])
        if mm in _ANNUALIZE_FACTOR:
            return mm

    if period.upper().startswith("Q"):
        try:
            q: int = int(period[1])
            if 1 <= q <= 4:
                return q * 3
        except ValueError:
            pass
    return None


def _quarter_year(rec: Dict[str, Any]) -> Optional[int]:
    """从财务记录中解析年份"""
    y: Any = rec.get("year")
    if isinstance(y, int):
        return y
    if isinstance(y, str) and y.isdigit():
        return int(y)

    rd: str = str(rec.get("report_date", "") or "")
    rd_year, _month = _parse_report_date(rd)
    if rd_year is not None:
        return rd_year
    return None


def _as_float(value: Any) -> Optional[float]:
    """把脏数据（None / '' / 'None' / 非数字 / NaN / Inf）安全转成 float

    【审查修复】原实现会接受 'nan' / 'inf' 并原样返回：
    NaN 参与任何大小比较都是 False，会让该股"看起来有数据但全部判定失败"，
    且写进 JSON 会产生非标准 JSON。这里统一把非有限值判为脏数据。
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped: str = value.strip()
        if not stripped or stripped.lower() in (
            "none",
            "nan",
            "inf",
            "-inf",
            "+inf",
            "infinity",
            "-infinity",
        ):
            return None
    try:
        result: float = float(value)
    except (ValueError, TypeError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def get_fiscal_data_dir() -> Path:
    """获取基本面数据目录(跨平台)

    优先级:
        1. 环境变量 STOCK_PICKS_FISCAL
        2. <项目根>/data/fiscal
        3. Windows + 老项目 D:/stock-picks/data/fiscal (向后兼容)
    """
    primary = Path(
        os.environ.get(
            "STOCK_PICKS_FISCAL",
            str(Path(__file__).parent.parent / "data" / "fiscal"),
        )
    )
    legacy = Path("D:/stock-picks/data/fiscal")

    # Windows: 兼容老项目数据(如存在)
    if os.name == "nt" and legacy.exists():
        return legacy

    if not primary.exists() and not primary.is_junction():
        try:
            primary.mkdir(parents=True, exist_ok=True)
        except FileExistsError:
            pass
    return primary


class FiscalDataFetcher:
    """基本面数据获取器 - 从本地财务文件读取数据

    Attributes:
        fiscal_dir: 财务 JSON 文件目录
        _cache: 财务数据缓存 {symbol: data}
        _cache_time: 缓存时间戳 {symbol: datetime}
        cache_validity: 缓存有效期（秒），默认 6 小时
    """

    def __init__(self, fiscal_dir: Optional[Union[str, Path]] = None) -> None:
        # 允许测试时传入临时目录
        self.fiscal_dir: Path = (
            Path(fiscal_dir) if fiscal_dir else get_fiscal_data_dir()
        )
        self._cache: Dict[str, Dict[str, Any]] = {}  # 缓存财务数据
        self._cache_time: Dict[str, datetime] = {}
        self._shares_cache: Dict[str, Optional[float]] = {}  # 缓存流通股本
        self.cache_validity: int = 3600 * 6  # 6小时缓存

    def _is_cache_valid(self, symbol: str) -> bool:
        """检查缓存是否有效

        当文件比缓存更新时也认为失效（强制重读）
        """
        if symbol not in self._cache or symbol not in self._cache_time:
            return False
        fpath: Path = self.fiscal_dir / f"{symbol}.json"
        if fpath.exists():
            file_mtime: datetime = datetime.fromtimestamp(fpath.stat().st_mtime)
            if file_mtime > self._cache_time[symbol]:
                return False
        return (
            datetime.now() - self._cache_time[symbol]
        ).total_seconds() < self.cache_validity

    def get_fiscal_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """获取股票基本面数据

        Args:
            symbol: 股票代码，如 "600519"

        Returns:
            dict 包含 ROE、净利润增长率、资产负债率等指标；文件不存在时返回 None
            {
              'code', 'name', 'roe', '净利润', '资产负债率', '净利润增长率', 'is_st', 'source'
            }
        """
        if self._is_cache_valid(symbol):
            return self._cache[symbol]

        fpath: Path = self.fiscal_dir / f"{symbol}.json"
        if not fpath.exists():
            return None

        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content: Dict[str, Any] = json.load(f)

            fiscal_list: List[Dict[str, Any]] = content.get("fiscal", [])
            if not fiscal_list:
                return None

            # 按 report_date 降序排列（最新季度排前面）
            fiscal_list = sorted(
                fiscal_list,
                key=lambda x: x.get("report_date", ""),
                reverse=True,
            )
            # 【修复】原来只取最近4个季度，而"去年同期"最早出现在第5位，
            # 导致同比增长率永远算不出来。放宽到8个季度以覆盖去年同期。
            recent: List[Dict[str, Any]] = fiscal_list[:8]

            def get_q(qidx: int, field: str, default: Optional[float] = None) -> Optional[float]:
                """取指定季度字段值（自动转 float，处理 'None'/'' 等脏数据）"""
                if 0 <= qidx < len(recent):
                    return _as_float(recent[qidx].get(field))
                return default

            # 计算 ROE = 归母净利润 / 净资产总计
            bps: Optional[float] = get_q(0, "每股净资产")
            total_share: Optional[float] = get_q(0, "总股本")

            # 【修复】约31%的股票最新季度缺"总股本"字段，但更早的季度通常有。
            # 回溯查找最近一个可用值，避免 ROE 直接算不出来（丢掉15分）。
            if total_share is None:
                for q in recent[1:]:
                    total_share = _as_float(q.get("总股本"))
                    if total_share is not None:
                        break

            net_assets: Optional[float] = (
                bps * total_share if bps and total_share else None
            )
            net_profit: Optional[float] = get_q(0, "归母净利润")
            roe: Optional[float] = (
                (net_profit / net_assets * 100)
                if (net_profit is not None and net_assets and net_assets > 0)
                else None
            )

            # 【修复】归母净利润是"年初至今累计值"（半年报=上半年累计），
            # 而 ROE 的评分阈值（20%/15%/10%）是年度口径。
            # 不年化会导致量级差 2~4 倍，中位 ROE 仅 2.45%，几乎没人能拿到 ROE 分。
            roe_annualized: Optional[float] = None
            q0_month: Optional[int] = _quarter_month(recent[0])
            if roe is not None and q0_month is not None:
                roe_annualized = roe * _ANNUALIZE_FACTOR[q0_month]

            # 资产负债率
            debt_ratio: Optional[float] = get_q(0, "资产负债率")

            # ST 状态（【修复】name 可能为 None，直接 re.search(None) 会抛异常）
            stock_name: str = content.get("name") or ""
            st_pattern = re.compile(r"(ST|S\*ST|\*ST|退市?|暂停上市)")
            is_st: bool = bool(st_pattern.search(stock_name))

            # 计算净利润同比增长率
            # 按时间降序：q0=最新季度，q1=去年同季度
            # 【修复】原来按 period 字符串匹配，但最新季度 period 写成 '06-30'
            # 而历史季度是 '0630'，格式不一致导致永远匹配不上。改为解析季度月份比较。
            def same_period_q1() -> Optional[Dict[str, Any]]:
                q0_month_m: Optional[int] = _quarter_month(recent[0])
                q0_year_m: Optional[int] = _quarter_year(recent[0])
                if q0_month_m is None or q0_year_m is None:
                    return None
                for q in recent[1:]:
                    if (
                        _quarter_month(q) == q0_month_m
                        and _quarter_year(q) == q0_year_m - 1
                    ):
                        return q
                return None

            q1: Optional[Dict[str, Any]] = same_period_q1()
            net_profit_yoy: Optional[float] = None

            if q1 and net_profit is not None:
                np1: Optional[float] = _as_float(q1.get("归母净利润"))
                # 去年同期为负(亏损)或 0 时，同比增长率在财务上没有意义
                # （亏损转盈利会算出 46000% 这类假象），此时置为 None 不参与评分。
                if np1 and np1 > 0:
                    net_profit_yoy = (net_profit - np1) / abs(np1) * 100

            data: Dict[str, Any] = {
                "code": symbol,
                "name": stock_name,
                "roe": roe,
                "roe_annualized": roe_annualized,
                "净利润": net_profit,
                "report_period_month": q0_month,
                "资产负债率": debt_ratio,
                "净利润增长率": net_profit_yoy,
                "is_st": is_st,
                "source": "local_fiscal",
            }

            self._cache[symbol] = data
            self._cache_time[symbol] = datetime.now()
            return data

        except Exception as e:
            logger.warning(f"读取财务文件失败 {symbol}: {e}")
            return None

    def get_float_shares(self, symbol: str) -> Optional[float]:
        """获取流通股本（股），用于计算真实换手率

        本地 K 线数据不含 turnover 字段，若用固定 1e8 股本粗估换手率，
        实测 98% 的股票会被高估 2 倍以上。这里从财务文件取真实股本。

        取值口径（按优先级）：
            1. 流通股本 —— 换手率的标准口径（成交量/流通股本）
            2. 总股本   —— 仅当**所有季度都没有**流通股本时才退而求其次

        【审查修复】原实现按季度从新到旧遍历，每个季度先取流通股本、再取总股本，
        一旦最新季度有"总股本"就立即返回，会漏掉更早季度里可用的"流通股本"。
        例：000002 最新季度流通股本为 None、总股本 1.19e10，更早季度流通股本
        1.19e10 —— 口径混用会导致换手率被系统性低估。现在改为"先把所有季度的
        流通股本扫完，一个都没有再用总股本"，保证口径统一。

        Args:
            symbol: 股票代码

        Returns:
            流通/总股本（股），取不到返回 None
        """
        if symbol in self._shares_cache:
            return self._shares_cache[symbol]

        fpath: Path = self.fiscal_dir / f"{symbol}.json"
        result: Optional[float] = None

        if fpath.exists():
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content: Dict[str, Any] = json.load(f)
                fiscal_list: List[Dict[str, Any]] = sorted(
                    content.get("fiscal", []),
                    key=lambda x: x.get("report_date", ""),
                    reverse=True,
                )
                # 第一轮：只认"流通股本"（换手率标准口径），按时间从新到旧
                for q in fiscal_list:
                    shares: Optional[float] = _as_float(q.get("流通股本"))
                    if shares and shares > 0:
                        result = shares
                        break
                # 第二轮：所有季度都没有流通股本时，才退回"总股本"
                if result is None:
                    for q in fiscal_list:
                        shares = _as_float(q.get("总股本"))
                        if shares and shares > 0:
                            result = shares
                            break
            except Exception as e:
                logger.warning(f"读取股本失败 {symbol}: {e}")

        self._shares_cache[symbol] = result
        return result

    def check_financial(
        self, symbol: str, stock_type: str = "mid_long"
    ) -> Dict[str, Any]:
        """检查股票基本面是否通过筛选

        Args:
            symbol: 股票代码
            stock_type: 'mid_long' 中长线（严格财务筛选）
                       / 其他（超短线，仅做风险排除）

        Returns:
            {
              'pass': bool,           # 是否通过
              'score': int,           # CANSLIM 财务评分（满分 35）
              'reasons': list,        # 加分理由
              'warnings': list,       # 风险提示
              'details': dict         # 原始财务数据
            }
        """
        data: Optional[Dict[str, Any]] = self.get_fiscal_data(symbol)

        # 超短线不做基本面筛选：没有财务数据不代表公司有问题（新股/次新/数据缺失），
        # 短线交易不应因缺少财务文件而被淘汰。ST 风险检查仍照常做。
        if not data:
            return {
                "pass": stock_type != "mid_long",
                "score": 0,
                "reasons": [] if stock_type != "mid_long" else ["无财务数据"],
                "warnings": [],
                "details": {},
            }

        score: int = 0
        reasons: List[str] = []
        warnings: List[str] = []

        # ST 股票直接淘汰（对中长线和超短线都是硬风险）
        if data.get("is_st"):
            return {
                "pass": False,
                "score": 0,
                "reasons": ["ST股票"],
                "warnings": [],
                "details": data,
            }

        # 提取核心指标
        # ROE 评分统一使用"年化 ROE"：财务数据里归母净利润是年初至今累计值，
        # 而评分阈值(20%/15%/10%)是年度口径，不年化会导致量级偏低而被误杀。
        roe: Optional[float] = data.get("roe_annualized")
        if roe is None:
            roe = data.get("roe")
        profit_growth: Optional[float] = data.get("净利润增长率")

        # 中长线硬性淘汰条件
        if stock_type == "mid_long":
            if roe is not None and roe < 0:
                return {
                    "pass": False,
                    "score": 0,
                    "reasons": [f"ROE为负({roe:.1f}%)"],
                    "warnings": [],
                    "details": data,
                }
            if profit_growth is not None and profit_growth < -50:
                return {
                    "pass": False,
                    "score": 0,
                    "reasons": [f"净利润增长异常({profit_growth:.1f}%)"],
                    "warnings": [],
                    "details": data,
                }

        # ========== 财务评分（CANSLIM风格）==========
        # 1. ROE 评分（15分）
        if roe is not None:
            if roe >= 20:
                score += 15
                reasons.append(f"ROE优秀({roe:.1f}%)")
            elif roe >= 15:
                score += 12
                reasons.append(f"ROE良好({roe:.1f}%)")
            elif roe >= 10:
                score += 8
                reasons.append(f"ROE尚可({roe:.1f}%)")
            elif roe >= 5:
                score += 4
            elif roe >= 0:
                warnings.append(f"ROE偏低({roe:.1f}%)")

        # 2. 净利润增长率（15分）
        if profit_growth is not None:
            if profit_growth >= 100:
                score += 15
                reasons.append(f"净利润爆发增长({profit_growth:.1f}%)")
            elif profit_growth >= 50:
                score += 13
                reasons.append(f"净利润高速增长({profit_growth:.1f}%)")
            elif profit_growth >= 30:
                score += 11
                reasons.append(f"净利润较快增长({profit_growth:.1f}%)")
            elif profit_growth >= 20:
                score += 9
                reasons.append(f"净利润稳健增长({profit_growth:.1f}%)")
            elif profit_growth >= 10:
                score += 6
                reasons.append(f"净利润小幅增长({profit_growth:.1f}%)")
            elif profit_growth >= 0:
                score += 3
            elif profit_growth >= -20:
                warnings.append(f"净利润小幅下滑({profit_growth:.1f}%)")
            else:
                warnings.append(f"净利润下滑较大({profit_growth:.1f}%)")

        # 3. 资产负债率（5分）
        debt_ratio: Optional[float] = data.get("资产负债率")
        if debt_ratio is not None:
            if debt_ratio <= 30:
                score += 5
                reasons.append(f"负债率低({debt_ratio:.1f}%)")
            elif debt_ratio <= 50:
                score += 3
                reasons.append(f"负债率适中({debt_ratio:.1f}%)")
            elif debt_ratio <= 70:
                score += 1
            else:
                warnings.append(f"负债率偏高({debt_ratio:.1f}%)")

        # 中长线：总分<15硬淘汰
        if stock_type == "mid_long" and score < 15:
            return {
                "pass": False,
                "score": score,
                "reasons": [f"财务评分过低({score}分)"],
                "warnings": warnings,
                "details": data,
            }

        return {
            "pass": True,
            "score": score,
            "reasons": reasons,
            "warnings": warnings,
            "details": data,
        }