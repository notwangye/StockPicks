#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
季度财务数据下载任务 V2

功能：
  - 主源：baostock（稳定免费）
  - 兜底：akshare（覆盖更全）
  - 范围：所有 A 股（5000+）
  - 触发：5/9/11 月 10 号 02:00（每年 3 次）
    * 5 月：年报 + 一季报补全
    * 9 月：半年报补全
    * 11 月：三季报补全

设计要点：
  - 只下载缺失的 report_date（不重复）
  - 文件锁防并发写
  - 复用 selector/fiscal.py 的 FiscalDataFetcher 数据格式
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# 直接复用 selector/fiscal.py 的路径解析（已支持 STOCK_PICKS_FISCAL 环境变量 + 老项目兼容）
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from selector.fiscal import get_fiscal_data_dir  # noqa: E402

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

FISCAL_DIR: Path = get_fiscal_data_dir()
# 兼容 NTFS junction（参考 news_job.py 修复 #3451da6）：
# 在 Windows 下 FISCAL_DIR 可能指向老项目的 junction，
# 此时 exists() 返回 False 但 mkdir(exist_ok=True) 仍会触发 WinError 183。
if not FISCAL_DIR.exists() and not FISCAL_DIR.is_junction():
    try:
        FISCAL_DIR.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        pass

# 历史数据目录（用于获取股票代码列表 + 名称）
HISTORICAL_DIR: Path = Path(os.environ.get(
    "STOCK_PICKS_DATA",
    str(Path(__file__).parent.parent.parent / "data" / "historical")
))

# 并发控制
#   修复: 实测 baostock 在并发 / 网络抖动下大量 "接收数据异常 / 服务器连接失败"
#   主动降低并发 + 默认走 akshare 主源（一次性返回所有季度，更稳定）
WORKERS = 8             # 股票并发数
INNER_WORKERS = 5       # 单股票内部季度并发数（baostock 每线程独立连接）
FILE_LOCK_TIMEOUT = 30  # 文件锁超时秒数

# 数据源策略
#   "akshare_first": akshare 优先（默认；baostock 兜底补字段缺失）
#   "baostock_first": baostock 优先（兜底 akshare）
PRIMARY_SOURCE = "akshare_first"

# 线程本地 baostock 连接
_tls = threading.local()


# ============================================================
# 季度日期生成
# ============================================================

# A 股财报披露季度及对应截止日
QUARTER_END_DATES = {
    1: "03-31",   # 一季报
    2: "06-30",   # 半年报
    3: "09-30",   # 三季报
    4: "12-31",   # 年报
}


def get_target_quarters(current_date: Optional[datetime] = None) -> List[Tuple[int, int]]:
    """根据当前日期返回本次下载应补的 (year, quarter) 列表

    规则：
      - 5 月 10 号: 触发当年的 (year, 1) 一季报 + 上年的 (year-1, 4) 年报
      - 9 月 10 号: 触发当年的 (year, 2) 半年报
      - 11 月 10 号: 触发当年的 (year, 3) 三季报
      - 其他月份（手动调用）：返回最近一个未完成披露的季度
    """
    now = current_date or datetime.now()
    month = now.month
    year = now.year

    if month == 5:
        return [(year, 1), (year - 1, 4)]
    elif month == 9:
        return [(year, 2)]
    elif month == 11:
        return [(year, 3)]
    else:
        # 手动调用兜底：返回上一季度
        last_q = ((month - 1) // 3) or 4
        if last_q == 4:
            return [(year - 1, 4)]
        return [(year, last_q)]


def _fmt_date(year: int, quarter: int) -> str:
    """(year, quarter) → 'YYYY-MM-DD'"""
    return f"{year}-{QUARTER_END_DATES[quarter]}"


# ============================================================
# baostock 数据源
# ============================================================

def _baocode(code: str) -> str:
    """A 股代码 → baostock 格式（sh/sz/bj）"""
    if code[0] in "569":
        return "sh." + code
    elif code[0] in "02":
        return "sz." + code
    elif code.startswith("8") or code.startswith("4"):
        return "bj." + code
    return "sz." + code


def _bs():
    """线程本地 baostock 连接（每线程独立 login）"""
    if not hasattr(_tls, "bs"):
        import baostock as bs
        bs.login()
        _tls.bs = bs
    return _tls.bs


def _query_bs(code: str, year: int, quarter: int, tbl: str) -> Dict[str, dict]:
    """baostock 单表查询，返回 {statDate: row}

    带重试：实测 baostock 在并发 / 网络抖动时会随机失败
      ('接收数据异常，请稍后再试', '服务器连接失败', WinError 10057)
    失败时重新 login 再查，最多重试 2 次
    """
    fn_map = {
        "profit": "query_profit_data",
        "balance": "query_balance_data",
        "dupont": "query_dupont_data",
    }
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            bs = _bs()
            fn = getattr(bs, fn_map[tbl])
            rs = fn(code=code, year=str(year), quarter=str(quarter))
            rows = {}
            while rs.next():
                r = dict(zip(rs.fields, rs.get_row_data()))
                rows[r.get("statDate", "")] = r
            return rows
        except Exception as e:
            last_err = e
            logger.debug(f"baostock {code} {year}Q{quarter} {tbl} 第{attempt+1}次失败: {e}")
            # 重连 baostock
            try:
                import baostock as _bs_mod
                _bs_mod.logout()
            except Exception:
                pass
            if hasattr(_tls, "bs"):
                del _tls.bs
            time.sleep(0.5 * (attempt + 1))
    if last_err:
        raise last_err
    return {}


def _parse_bs_records(
    tbl_rows: Dict[Tuple[int, int], Dict[str, Dict[str, dict]]],
    year: int,
    quarter: int,
) -> Optional[dict]:
    """把 baostock 三表合并成单条 fiscal 记录

    Args:
        tbl_rows: {(year, quarter): {tbl_name: {statDate: row}}}
        year, quarter: 当前处理的季度
    """
    rp = _fmt_date(year, quarter)
    yq_tables = tbl_rows.get((year, quarter), {})
    prows = yq_tables.get("profit", {}).get(rp)
    if not prows:
        return None

    bdata = yq_tables.get("balance", {}).get(rp, {})
    drows = yq_tables.get("dupont", {}).get(rp, {})

    def _f(v):
        if not v:
            return None
        try:
            return round(float(v), 4)
        except (ValueError, TypeError):
            return None

    net_profit = _f(prows.get("netProfit"))
    mb_revenue = _f(prows.get("MBRevenue"))
    total_share = _f(prows.get("totalShare"))
    liqa_share = _f(prows.get("liqaShare"))
    debt_ratio = _f(bdata.get("liabilityToAsset"))

    # 修复: baostock 的 MBRevenue / netProfit / totalShare / liqaShare 字段
    # 单位均为"元"（已验证 000001 平安银行 MBRevenue=70617000000=706.17 亿），
    # 早期 _parse_bs_records 误乘 1e6 导致 188 只股票半年报营收被放大 100 万倍。
    # 不要乘以 1e6，直接使用原值。
    total_rev = mb_revenue

    naps = None
    if net_profit and total_share:
        try:
            if total_share > 0:
                naps = round(net_profit / total_share, 4)
        except Exception:
            pass

    rec = {
        "report_date": rp,
        "year": year,
        "period": QUARTER_END_DATES[quarter],
        "营业总收入": total_rev,
        "归母净利润": net_profit,
        "每股净资产": naps,
        "资产负债率": round(debt_ratio * 100, 4) if debt_ratio else None,
        "总股本": _f(total_share),
        "流通股本": _f(liqa_share),
    }
    return {k: v for k, v in rec.items() if v is not None}


def fetch_from_baostock(
    code: str,
    targets: List[Tuple[int, int]],
) -> Dict[str, dict]:
    """从 baostock 拉一只股票的多个季度数据，返回 {report_date: record}"""
    bcode = _baocode(code)
    records: Dict[str, dict] = {}

    # 每只股票内部每季度三表并发
    with ThreadPoolExecutor(max_workers=INNER_WORKERS) as ex:
        futs = {}
        for y, q in targets:
            for tbl in ("profit", "balance", "dupont"):
                futs[ex.submit(_query_bs, bcode, y, q, tbl)] = (y, q, tbl)

        # {(year, quarter, tbl): {statDate: row}}
        tbl_rows: Dict[Tuple[int, int, str], Dict[str, dict]] = {}
        for fu in as_completed(futs):
            try:
                rows = fu.result()
                tbl_rows[futs[fu]] = rows
            except Exception as e:
                logger.debug(f"baostock {code} {futs[fu]} 异常: {e}")

    # 按 (year, quarter) 合并三表
    for y, q in targets:
        # 重组为 {(year, quarter): {tbl: {date: row}}}
        # _parse_bs_records 只关心三个表，需传入 {(y, q): {"profit": {}, "balance": {}, "dupont": {}}}
        yq_tables: Dict[Tuple[int, int], Dict[str, Dict[str, dict]]] = {}
        for (yy, qq, tbl), rows in tbl_rows.items():
            if (yy, qq) == (y, q):
                yq_tables.setdefault((y, q), {})[tbl] = rows

        rec = _parse_bs_records(yq_tables, y, q)
        if rec:
            records[rec["report_date"]] = rec

    return records


# ============================================================
# akshare 兜底（老板 7 项财务指标全覆盖）
# ============================================================

# akshare 抽象指标接口 -> 老板 7 项字段映射
# 来源：ak.stock_financial_abstract(symbol=code) 返回 DataFrame
#   columns: ['选项', '指标', 'YYYYMMDD', ...] (所有季度作为列)
AKSHARE_ABSTRACT_MAP = {
    "营业总收入": ("常用指标", "营业总收入"),
    "归母净利润": ("常用指标", "归母净利润"),
    "扣非净利润": ("常用指标", "扣非净利润"),  # 备用，实测 abstract 中未必含
    "每股净资产": ("每股指标", "每股净资产"),
    "资产负债率": ("财务风险", "资产负债率"),
    # 总股本/流通股本 走 balance 接口
    # 扣非净利润 备选走 stock_profit_sheet_by_report_em 的 DEDUCT_PARENT_NETPROFIT
}


def fetch_from_akshare(
    code: str,
    targets: List[Tuple[int, int]],
) -> Dict[str, dict]:
    """从 akshare 拉指定季度的财务数据（兜底数据源）

    两个接口：
      1. stock_financial_abstract - 一次返回所有季度，拿 5 个常用指标
      2. stock_balance_sheet_by_report_em - 拿 SHARE_CAPITAL (总股本)
    流通股本：akshare 无直接接口，留空（依赖 baostock 或后续接其他数据源）
    """
    try:
        import akshare as ak
    except ImportError:
        logger.debug("akshare 未安装，跳过")
        return {}

    target_dates = {_fmt_date(y, q) for y, q in targets}
    records: Dict[str, dict] = {}

    # ---- 接口 1: stock_financial_abstract ----
    # akshare 抽象指标中日期列格式: '20251231'
    target_date_compact = {
        _fmt_date(y, q).replace("-", "") for y, q in targets
    }

    try:
        df_abstract = ak.stock_financial_abstract(symbol=code)
    except Exception as e:
        logger.debug(f"akshare abstract {code} 异常: {e}")
        df_abstract = None

    if df_abstract is not None and not df_abstract.empty:
        # 为每个目标季度收集数据
        per_quarter: Dict[str, dict] = {d: {} for d in target_dates}

        for field_name, (expected_opt, indicator) in AKSHARE_ABSTRACT_MAP.items():
            rows = df_abstract[df_abstract["指标"] == indicator]
            if rows.empty:
                continue
            row = rows.iloc[0]
            for dc in target_date_compact:
                if dc not in df_abstract.columns:
                    continue
                v = row.get(dc)
                if v is None or (isinstance(v, float) and v != v):  # NaN
                    continue
                # 转回标准日期格式
                rp = f"{dc[:4]}-{dc[4:6]}-{dc[6:8]}"
                if rp in per_quarter:
                    try:
                        per_quarter[rp][field_name] = round(float(v), 4)
                    except (ValueError, TypeError):
                        pass

        # ---- 接口 2: stock_balance_sheet_by_report_em 拿总股本 ----
        try:
            market_prefix = (
                "sh" if code[0] in "569"
                else "sz" if code[0] in "02"
                else "bj"
            )
            df_bs = ak.stock_balance_sheet_by_report_em(
                symbol=f"{market_prefix}{code}"
            )
            if df_bs is not None and not df_bs.empty and "SHARE_CAPITAL" in df_bs.columns:
                # 按 REPORT_DATE 匹配
                for _, row in df_bs.iterrows():
                    rd = row.get("REPORT_DATE")
                    if rd is None:
                        continue
                    # datetime -> 'YYYY-MM-DD'
                    if hasattr(rd, "strftime"):
                        rd_str = rd.strftime("%Y-%m-%d")
                    else:
                        rd_str = str(rd)[:10]
                    if rd_str in per_quarter:
                        sc = row.get("SHARE_CAPITAL")
                        if sc is not None and not (isinstance(sc, float) and sc != sc):
                            try:
                                per_quarter[rd_str]["总股本"] = round(float(sc), 0)
                            except (ValueError, TypeError):
                                pass
        except Exception as e:
            logger.debug(f"akshare balance {code} 异常: {e}")

        # ---- 接口 3: stock_profit_sheet_by_report_em 补扣非净利润 ----
        # abstract 表中未必含扣非净利润，profit_sheet 一定有 DEDUCT_PARENT_NETPROFIT
        if any("扣非净利润" not in r for r in per_quarter.values()):
            try:
                market_prefix = (
                    "sh" if code[0] in "569"
                    else "sz" if code[0] in "02"
                    else "bj"
                )
                df_ps = ak.stock_profit_sheet_by_report_em(
                    symbol=f"{market_prefix}{code}"
                )
                if df_ps is not None and not df_ps.empty and "DEDUCT_PARENT_NETPROFIT" in df_ps.columns:
                    for _, row in df_ps.iterrows():
                        rd = row.get("REPORT_DATE")
                        if rd is None:
                            continue
                        rd_str = rd.strftime("%Y-%m-%d") if hasattr(rd, "strftime") else str(rd)[:10]
                        if rd_str in per_quarter and "扣非净利润" not in per_quarter[rd_str]:
                            v = row.get("DEDUCT_PARENT_NETPROFIT")
                            if v is not None and not (isinstance(v, float) and v != v):
                                try:
                                    per_quarter[rd_str]["扣非净利润"] = round(float(v), 4)
                                except (ValueError, TypeError):
                                    pass
            except Exception as e:
                logger.debug(f"akshare profit_sheet {code} 异常: {e}")

        # 过滤空记录
        for rp, rec in per_quarter.items():
            if rec:
                rec["report_date"] = rp
                # year/period 推断
                parts = rp.split("-")
                rec["year"] = int(parts[0])
                rec["period"] = f"{parts[1]}-{parts[2]}"
                records[rp] = rec

    return records


# ============================================================
# 文件锁 + 写入
# ============================================================

LOCK_DIR: Path = FISCAL_DIR / ".locks"
if not LOCK_DIR.exists() and not LOCK_DIR.is_junction():
    try:
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        pass


# ============================================================
# 数据校验（防止异常值写入）
# ============================================================

# A 股字段合理范围（参考东方财富历史数据极值）
#   营收: [-1e11, 1e12] (亏损 -100 亿 ~ 1 万亿)
#   净利: [-1e11, 1e11] (-100 亿 ~ 100 亿)
#   总股本: [1e7, 1e12] (1000 万股 ~ 1 万亿股)
#   每股净资产: [-100, 1000] 元
#   资产负债率: [0, 100] %
_VALID_RANGES = {
    "营业总收入": (-1e11, 1e12),
    "归母净利润": (-1e11, 1e11),
    "扣非净利润": (-1e11, 1e11),
    "每股净资产": (-100.0, 1000.0),
    "总股本": (1e7, 1e12),
    "流通股本": (0, 1e12),
    "资产负债率": (0.0, 100.0),
}


def _sanitize_record(code: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    """过滤单条记录中的异常字段值

    校验规则：
      1. 字段值在 _VALID_RANGES 范围内 → 保留
      2. 字段值超出范围 → 删除该字段并记录警告
      3. 字段值非数值（字符串/None）→ 跳过校验保留原值

    Returns:
        清洗后的 dict
    """
    cleaned = {}
    for k, v in rec.items():
        if k in _VALID_RANGES and isinstance(v, (int, float)):
            lo, hi = _VALID_RANGES[k]
            if lo <= v <= hi:
                cleaned[k] = v
            else:
                logger.warning(
                    f"[数据校验] {code} {k}={v} 超出合理范围 [{lo}, {hi}], 丢弃该字段"
                )
        else:
            cleaned[k] = v
    return cleaned


def _safe_json_load(path: Path) -> Optional[Dict[str, Any]]:
    """安全加载 JSON 文件，损坏时返回 None 而非抛异常"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.warning(f"[JSON 损坏] {path.name}: {e}; 备份为 .broken 后跳过")
        # 备份损坏文件，避免下次写入覆盖丢失
        try:
            broken_path = path.with_suffix(f".json.broken.{int(time.time())}")
            path.rename(broken_path)
            logger.warning(f"[JSON 损坏] 已备份至 {broken_path.name}")
        except Exception as rename_err:
            logger.warning(f"[JSON 损坏] 备份失败: {rename_err}")
        return None
    except Exception as e:
        logger.warning(f"[JSON 损坏] {path.name}: {e}")
        return None


def _acquire_file_lock(lock_path: Path, timeout: int = FILE_LOCK_TIMEOUT) -> bool:
    start = time.time()
    while True:
        try:
            lock_path.touch(exist_ok=False)
            return True
        except FileExistsError:
            if time.time() - start > timeout:
                return False
            time.sleep(0.1)
        except Exception:
            if time.time() - start > timeout:
                return False
            time.sleep(0.1)


def _load_name(code: str) -> str:
    """从 historical 加载股票名称"""
    hp = HISTORICAL_DIR / f"{code}.json"
    if hp.exists():
        try:
            return json.loads(hp.read_text(encoding="utf-8")).get("name", code)
        except Exception:
            pass
    return code


def _existing_dates(code: str) -> Set[str]:
    """读取现有 fiscal 文件中已存在的 report_date 集合"""
    fp = FISCAL_DIR / f"{code}.json"
    if not fp.exists():
        return set()
    try:
        d = json.loads(fp.read_text(encoding="utf-8"))
        return {q["report_date"] for q in d.get("fiscal", [])}
    except Exception:
        return set()


def _save(code: str, records: Dict[str, dict]) -> int:
    """合并写入，新增条数

    流程：
      1. 数据校验：每条 record 经 _sanitize_record 过滤异常值
      2. 安全加载：JSON 损坏时备份为 .broken.*，不抛异常
      3. 增量写入：仅追加不存在的 report_date
      4. UTF-8 写入：ensure_ascii=False，特殊字符不破坏 JSON
    """
    if not records:
        return 0

    # 数据校验（防止异常值写入）
    sanitized: Dict[str, dict] = {}
    for rp, rec in records.items():
        clean = _sanitize_record(code, rec)
        if clean:
            sanitized[rp] = clean
    if not sanitized:
        return 0

    fpath = FISCAL_DIR / f"{code}.json"
    lock_path = LOCK_DIR / f"{code}.lock"
    if not _acquire_file_lock(lock_path):
        logger.warning(f"{code} 文件锁超时，跳过")
        return 0
    try:
        if fpath.exists():
            fd = _safe_json_load(fpath)
            if fd is None:
                # JSON 损坏已被备份为 .broken.*，从空文件重建
                fd = {
                    "code": code,
                    "name": _load_name(code),
                    "update_date": "",
                    "fiscal": [],
                }
        else:
            fd = {
                "code": code,
                "name": _load_name(code),
                "update_date": "",
                "fiscal": [],
            }

        exist = {q["report_date"] for q in fd.get("fiscal", [])}
        added = 0
        for rp, rec in sanitized.items():
            if rp not in exist:
                fd["fiscal"].append(rec)
                added += 1

        if added:
            fd["update_date"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            # 关键：ensure_ascii=False 避免中文名/单位字符串被 ASCII 转义；
            # default=str 让 numpy/non-serializable 类型降级为 str 而非崩溃
            fpath.write_text(
                json.dumps(fd, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        return added
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


# ============================================================
# 股票列表 + 主入口
# ============================================================

def get_codes() -> List[str]:
    """获取所有 A 股代码（从 historical 目录读）"""
    if not HISTORICAL_DIR.exists():
        logger.warning(f"历史数据目录不存在: {HISTORICAL_DIR}")
        return []
    return sorted(
        f.stem
        for f in HISTORICAL_DIR.iterdir()
        if f.suffix == ".json" and f.stem.isdigit()
    )


def run_fiscal_update(
    current_date: Optional[datetime] = None,
    codes: Optional[List[str]] = None,
) -> Dict[str, int]:
    """季度财务数据补全主入口

    Args:
        current_date: 当前日期（用于决定下载哪个季度；测试时传固定值）
        codes: 股票代码列表（None = 自动获取）

    Returns:
        {"total_added": N, "errors": M, "skipped": K}
    """
    targets = get_target_quarters(current_date)
    logger.info(
        f"目标季度: {[(y, q, _fmt_date(y, q)) for y, q in targets]} "
        f"数据源: baostock → akshare 兜底"
    )

    if codes is None:
        codes = get_codes()
    logger.info(f"待处理股票: {len(codes)} 只")

    stats = {"total_added": 0, "errors": 0, "skipped": 0}
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {
            ex.submit(_process_one, code, targets): code for code in codes
        }
        for i, fu in enumerate(as_completed(futures), 1):
            code = futures[fu]
            try:
                added = fu.result()
                stats["total_added"] += added
                if added == 0:
                    stats["skipped"] += 1
            except Exception as e:
                stats["errors"] += 1
                logger.warning(f"[{i}/{len(codes)}] {code} 异常: {e}")

            if i % 100 == 0 or i == len(codes):
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 0
                logger.info(
                    f"进度: {i}/{len(codes)} "
                    f"新增 {stats['total_added']} "
                    f"跳过 {stats['skipped']} "
                    f"失败 {stats['errors']} "
                    f"速度 {rate:.1f} 只/s"
                )

    elapsed = time.time() - t0
    logger.info(
        f"完成: 新增 {stats['total_added']} 条, "
        f"跳过 {stats['skipped']}, 失败 {stats['errors']}, "
        f"耗时 {elapsed:.1f}s"
    )
    return stats


def _process_one(code: str, targets: List[Tuple[int, int]]) -> int:
    """单股票处理：按 PRIMARY_SOURCE 决定主源 → 另一源补字段 → 写入

    字段合并策略：
      1. 主源拉一轮（覆盖大部分字段）
      2. 备源拉一轮（只补主源缺失的字段，不覆盖已有）
      3. 写入磁盘

    注意：akshare_first 默认不调用 baostock（避免 baostock 高并发限流
    拖慢整体速度），但若 akshare 缺关键字段（营业总收入/总股本/归母净利润）
    会**自动**启用 baostock 兜底补缺。

    设置环境变量 FISCAL_DUAL_SOURCE=always 强制每只股票都双源。
    """
    target_dates = {_fmt_date(y, q) for y, q in targets}
    force_dual = os.environ.get("FISCAL_DUAL_SOURCE", "").lower() == "always"
    # 关键字段（缺失时自动启用 baostock 兜底）
    KEY_FIELDS = ("营业总收入", "归母净利润", "总股本")

    def _needs_fallback(recs: Dict[str, dict]) -> bool:
        """检查 records 是否缺失关键字段"""
        for rp in target_dates:
            rec = recs.get(rp)
            if not rec:
                continue
            if any(rec.get(k) is None for k in KEY_FIELDS):
                return True
        return False

    if PRIMARY_SOURCE == "akshare_first":
        primary_records = fetch_from_akshare(code, targets)
        primary_records = {rp: r for rp, r in primary_records.items() if rp in target_dates}
        records = primary_records

        # 自动兜底：缺关键字段 或 强制双源
        if force_dual or _needs_fallback(primary_records):
            try:
                bs_records = fetch_from_baostock(code, targets)
                bs_records = {rp: r for rp, r in bs_records.items() if rp in target_dates}
                for rp, bs_rec in bs_records.items():
                    existing_rec = records.get(rp, {})
                    for k, v in bs_rec.items():
                        if k not in existing_rec:
                            existing_rec[k] = v
                    records.setdefault(rp, existing_rec)
                    records[rp] = existing_rec
                logger.debug(f"{code} baostock 兜底补字段")
            except Exception as e:
                logger.debug(f"baostock 兜底 {code} 失败: {e}")
    else:
        # baostock 优先：旧逻辑保留
        bs_records = fetch_from_baostock(code, targets)
        bs_records = {rp: r for rp, r in bs_records.items() if rp in target_dates}
        aks_records = fetch_from_akshare(code, targets)
        for rp, aks_rec in aks_records.items():
            if rp not in target_dates:
                continue
            existing_rec = bs_records.get(rp, {})
            for k, v in aks_rec.items():
                if k not in existing_rec:
                    existing_rec[k] = v
            bs_records.setdefault(rp, existing_rec)
            bs_records[rp] = existing_rec
        records = bs_records

    return _save(code, records)


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="季度财务数据补全")
    parser.add_argument(
        "--month", type=int, default=None,
        help="模拟当前月份（1-12），用于测试不同月份触发的季度",
    )
    parser.add_argument(
        "--codes", type=str, default=None,
        help="指定股票代码列表（逗号分隔），否则全部",
    )
    args = parser.parse_args()

    current = datetime.now()
    if args.month:
        # 修复: replace(month=X) 在当前 day 超出目标月份天数时会抛 ValueError
        #   例: 8 月 31 日调用 --month 2 → 2 月没有 31 日 → ValueError
        # 改用 date() 构造再合并时间
        from datetime import date as _date
        today = current.date()
        try:
            new_day = min(today.day, 28)  # 28 日任何月份都安全
            current = datetime(today.year, args.month, new_day, current.hour, current.minute, current.second)
        except ValueError:
            current = datetime(today.year, args.month, 1, current.hour, current.minute, current.second)
    code_list = args.codes.split(",") if args.codes else None

    run_fiscal_update(current_date=current, codes=code_list)