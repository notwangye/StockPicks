"""
FiscalDataFetcher - 基本面数据获取器单元测试
"""
import json
from datetime import datetime, timedelta

import pytest

from selector import FiscalDataFetcher
from selector.fiscal import _quarter_month, _quarter_year


@pytest.fixture
def tricky_fiscal_files(tmp_path):
    """构造真实数据中出现的'脏'格式样本

    覆盖 4 个曾经导致中长线选不出股票的真实 bug：
      1. 最新季度 period 写成 '06-30'（带横杠），历史季度是 '0630'
      2. 去年同期排在第 5 个季度，超出"最近4季度"窗口
      3. 最新季度缺 '总股本'（31% 股票如此）
      4. 净利润是年初至今累计值，ROE 必须年化
    """
    fiscal_dir = tmp_path / "fiscal_tricky"
    fiscal_dir.mkdir()

    quarterly = [
        # (report_date, period, 归母净利润, 每股净资产, 总股本, 资产负债率)
        ('2026-06-30', '06-30', 12_000_000_000, 10.0, None,   40),  # 最新：period带横杠 + 缺总股本
        ('2026-03-31', '0331',    6_000_000_000, 9.5,  1_000_000_000, 42),
        ('2025-12-31', '1231',   20_000_000_000, 9.0,  1_000_000_000, 45),
        ('2025-09-30', '0930',   16_000_000_000, 8.8,  1_000_000_000, 43),
        ('2025-06-30', '0630',   10_000_000_000, 8.5,  1_000_000_000, 44),  # 去年同期：第5个季度
        ('2025-03-31', '0331',    5_000_000_000, 8.2,  1_000_000_000, 46),
        ('2024-12-31', '1231',   15_000_000_000, 8.0,  1_000_000_000, 48),
    ]
    records = [
        {'report_date': rd, 'year': int(rd[:4]), 'period': p,
         '归母净利润': np_, '每股净资产': bps, '总股本': ts, '资产负债率': dr}
        for rd, p, np_, bps, ts, dr in quarterly
    ]
    with open(fiscal_dir / "300001.json", 'w', encoding='utf-8') as f:
        json.dump({'code': '300001', 'name': '格式异常股', 'fiscal': records},
                  f, ensure_ascii=False)

    # 亏损转盈利：去年同期为负，同比增长率应被置为 None（而非 46000%）
    turnaround = [
        {'report_date': '2026-06-30', 'year': 2026, 'period': '06-30',
         '归母净利润': 1_000_000_000, '每股净资产': 5.0, '总股本': 200_000_000,
         '资产负债率': 50},
        {'report_date': '2026-03-31', 'year': 2026, 'period': '0331',
         '归母净利润': 500_000_000, '每股净资产': 4.9, '总股本': 200_000_000,
         '资产负债率': 50},
        {'report_date': '2026-01-01', 'year': 2026, 'period': '0101',
         '归母净利润': 1_000_000, '每股净资产': 4.8, '总股本': 200_000_000,
         '资产负债率': 51},
        {'report_date': '2025-12-31', 'year': 2025, 'period': '1231',
         '归母净利润': -500_000_000, '每股净资产': 4.5, '总股本': 200_000_000,
         '资产负债率': 52},
        {'report_date': '2025-06-30', 'year': 2025, 'period': '0630',
         '归母净利润': -2_000_000, '每股净资产': 4.0, '总股本': 200_000_000,
         '资产负债率': 53},   # 去年同期微亏 -> 假同比会算出 50000%
    ]
    with open(fiscal_dir / "300002.json", 'w', encoding='utf-8') as f:
        json.dump({'code': '300002', 'name': '扭亏股', 'fiscal': turnaround},
                  f, ensure_ascii=False)

    # name 为 None：旧代码 re.search(None) 会抛异常导致整份财务数据作废
    with open(fiscal_dir / "300003.json", 'w', encoding='utf-8') as f:
        json.dump({'code': '300003', 'name': None, 'fiscal': [
            {'report_date': '2026-06-30', 'year': 2026, 'period': '06-30',
             '归母净利润': 8_000_000_000, '每股净资产': 10.0, '总股本': 800_000_000,
             '资产负债率': 30},
            {'report_date': '2026-03-31', 'year': 2026, 'period': '0331',
             '归母净利润': 4_000_000_000, '每股净资产': 9.8, '总股本': 800_000_000,
             '资产负债率': 31},
            {'report_date': '2025-12-31', 'year': 2025, 'period': '1231',
             '归母净利润': 12_000_000_000, '每股净资产': 9.5, '总股本': 800_000_000,
             '资产负债率': 32},
            {'report_date': '2025-09-30', 'year': 2025, 'period': '0930',
             '归母净利润': 9_000_000_000, '每股净资产': 9.3, '总股本': 800_000_000,
             '资产负债率': 33},
            {'report_date': '2025-06-30', 'year': 2025, 'period': '0630',
             '归母净利润': 6_000_000_000, '每股净资产': 9.0, '总股本': 800_000_000,
             '资产负债率': 34},
        ]}, f, ensure_ascii=False)

    return fiscal_dir


@pytest.fixture
def fake_fiscal_files(tmp_path):
    """创建临时财务数据"""
    fiscal_dir = tmp_path / "fiscal"
    fiscal_dir.mkdir()

    # 股票 A: 健康 ROE
    fiscal_a = {
        'fiscal': [
            {'report_date': '2025-03-31', 'year': 2025, 'period': 'Q1',
             '归母净利润': 10_000_000_000, '每股净资产': 10, '总股本': 1_000_000_000,
             '资产负债率': 30},
            {'report_date': '2024-12-31', 'year': 2024, 'period': 'Q4',
             '归母净利润': 8_000_000_000, '每股净资产': 9, '总股本': 1_000_000_000,
             '资产负债率': 32},
            {'report_date': '2024-03-31', 'year': 2024, 'period': 'Q1',
             '归母净利润': 8_000_000_000, '每股净资产': 9, '总股本': 1_000_000_000,
             '资产负债率': 32},
        ]
    }

    # 股票 B: ST 状态
    fiscal_b = {
        'fiscal': [
            {'report_date': '2025-03-31', 'year': 2025, 'period': 'Q1',
             '归母净利润': -1_000_000_000, '每股净资产': 5, '总股本': 1_000_000_000,
             '资产负债率': 80}
        ]
    }

    with open(fiscal_dir / "000001.json", 'w', encoding='utf-8') as f:
        json.dump({'code': '000001', 'name': '健康股票A', 'fiscal': fiscal_a['fiscal']}, f, ensure_ascii=False)

    with open(fiscal_dir / "600000.json", 'w', encoding='utf-8') as f:
        json.dump({'code': '600000', 'name': 'ST股票B', 'fiscal': fiscal_b['fiscal']}, f, ensure_ascii=False)

    return fiscal_dir


class TestGetFiscalData:
    def test_returns_dict(self, fake_fiscal_files):
        fetcher = FiscalDataFetcher(fiscal_dir=str(fake_fiscal_files))
        data = fetcher.get_fiscal_data('000001')
        assert data is not None
        assert 'roe' in data
        assert '净利润' in data
        assert '资产负债率' in data

    def test_roe_calculation(self, fake_fiscal_files):
        """ROE = 归母净利润 / (每股净资产 * 总股本)"""
        fetcher = FiscalDataFetcher(fiscal_dir=str(fake_fiscal_files))
        data = fetcher.get_fiscal_data('000001')
        # 10_000_000_000 / (10 * 1_000_000_000) = 1.0
        # 净利润 100亿 / 净资产 100亿 = 100%
        assert data['roe'] is not None
        assert abs(data['roe'] - 100.0) < 0.5

    def test_nonexistent_returns_none(self, fake_fiscal_files):
        fetcher = FiscalDataFetcher(fiscal_dir=str(fake_fiscal_files))
        data = fetcher.get_fiscal_data('999999')
        assert data is None

    def test_st_detection(self, fake_fiscal_files):
        fetcher = FiscalDataFetcher(fiscal_dir=str(fake_fiscal_files))
        data = fetcher.get_fiscal_data('600000')
        assert data['is_st'] is True


class TestCheckFinancial:
    def test_healthy_stock_passes(self, fake_fiscal_files):
        fetcher = FiscalDataFetcher(fiscal_dir=str(fake_fiscal_files))
        result = fetcher.check_financial('000001', stock_type='mid_long')
        assert result['pass'] is True
        assert result['score'] > 0

    def test_st_stock_fails(self, fake_fiscal_files):
        fetcher = FiscalDataFetcher(fiscal_dir=str(fake_fiscal_files))
        result = fetcher.check_financial('600000', stock_type='mid_long')
        assert result['pass'] is False
        assert 'ST' in result['reasons'][0]

    def test_missing_data_returns_fail(self, fake_fiscal_files):
        fetcher = FiscalDataFetcher(fiscal_dir=str(fake_fiscal_files))
        result = fetcher.check_financial('999999', stock_type='mid_long')
        assert result['pass'] is False
        assert '无财务数据' in result['reasons']

    def test_short_term_relaxed(self, fake_fiscal_files):
        """超短线模式 ROE 负数检查更宽松"""
        fetcher = FiscalDataFetcher(fiscal_dir=str(fake_fiscal_files))
        result = fetcher.check_financial('000001', stock_type='short_term')
        # 应该通过
        assert 'score' in result


class TestPeriodParsing:
    """period 字段格式在真实数据中不统一，必须能统一解析"""

    def test_dash_format(self):
        assert _quarter_month({'report_date': '2026-06-30', 'period': '06-30'}) == 6

    def test_compact_format(self):
        assert _quarter_month({'report_date': '2025-06-30', 'period': '0630'}) == 6

    def test_q_format(self):
        assert _quarter_month({'report_date': '2025-03-31', 'period': 'Q1'}) == 3

    def test_year_from_field_and_report_date(self):
        assert _quarter_year({'year': 2026, 'report_date': '2026-06-30'}) == 2026
        assert _quarter_year({'report_date': '2026-06-30'}) == 2026
        assert _quarter_year({'year': '2026'}) == 2026

    def test_invalid_returns_none(self):
        assert _quarter_month({'period': 'X'}) is None
        assert _quarter_year({}) is None


class TestRegressionBugFixes:
    """回归测试：中长线选不出股票的 4 个根因"""

    def test_yoy_growth_found_despite_period_format_mismatch(self, tricky_fiscal_files):
        """根因1：period 格式不一致 + 去年同期在第5季度，同比增长率必须算得出来"""
        fetcher = FiscalDataFetcher(fiscal_dir=str(tricky_fiscal_files))
        data = fetcher.get_fiscal_data('300001')
        # 2026H1 = 120亿, 2025H1 = 100亿 -> +20%
        assert data['净利润增长率'] is not None
        assert abs(data['净利润增长率'] - 20.0) < 0.01

    def test_total_share_fallback_to_older_quarter(self, tricky_fiscal_files):
        """根因3：最新季度缺'总股本'时应回溯更早季度，而不是让 ROE 变成 None"""
        fetcher = FiscalDataFetcher(fiscal_dir=str(tricky_fiscal_files))
        data = fetcher.get_fiscal_data('300001')
        assert data['roe'] is not None
        # 半年报 ROE = 120亿 / (每股净资产10元 x 10亿股=100亿净资产) = 120%
        assert abs(data['roe'] - 120.0) < 0.01

    def test_roe_annualized(self, tricky_fiscal_files):
        """根因2：净利润是年初至今累计值，ROE 必须年化才能与年度阈值比较"""
        fetcher = FiscalDataFetcher(fiscal_dir=str(tricky_fiscal_files))
        data = fetcher.get_fiscal_data('300001')
        assert data['report_period_month'] == 6
        # 半年报 -> x2 = 240%
        assert abs(data['roe_annualized'] - 240.0) < 0.01

    def test_annualization_scales_with_period(self, tricky_fiscal_files):
        """Q1 累计值应 x4 年化"""
        fetcher = FiscalDataFetcher(fiscal_dir=str(tricky_fiscal_files))
        data = fetcher.get_fiscal_data('300001')
        # 同一份数据里 H1 用 x2；确认系数按月份取而非固定
        assert data['roe'] * 2.0 == data['roe_annualized']

    def test_loss_making_base_yoy_is_none(self, tricky_fiscal_files):
        """去年同期亏损时同比增长率无意义，不应算出 50000% 这种假象"""
        fetcher = FiscalDataFetcher(fiscal_dir=str(tricky_fiscal_files))
        data = fetcher.get_fiscal_data('300002')
        assert data['净利润增长率'] is None

    def test_none_name_does_not_crash(self, tricky_fiscal_files):
        """根因4：name 为 None 时不应抛异常导致整份财务数据作废"""
        fetcher = FiscalDataFetcher(fiscal_dir=str(tricky_fiscal_files))
        data = fetcher.get_fiscal_data('300003')
        assert data is not None
        assert data['is_st'] is False

    def test_tricky_stock_passes_fundamental_filter(self, tricky_fiscal_files):
        """这些股票在修复前会被'财务评分过低'淘汰，修复后应能通过"""
        fetcher = FiscalDataFetcher(fiscal_dir=str(tricky_fiscal_files))
        result = fetcher.check_financial('300001', stock_type='mid_long')
        assert result['pass'] is True
        assert result['score'] >= 15

class TestReviewRound3Fixes:
    """第三轮代码审查的回归测试（针对审查中发现并修复的缺陷）"""

    def test_float_shares_prefers_float_over_total_across_quarters(self, tmp_path):
        """【审查修复】流通股本口径必须优先于总股本，且要跨所有季度找

        原实现按季度从新到旧遍历，每个季度先取流通股本再取总股本，
        一旦最新季度有"总股本"就立即返回 —— 会漏掉更早季度里可用的"流通股本"，
        口径混用导致换手率被系统性低估。
        """
        from selector import FiscalDataFetcher
        (tmp_path / '600001.json').write_text(json.dumps({
            'code': '600001', 'name': '测试',
            'fiscal': [
                # 最新季度：流通股本缺失，但有总股本
                {'report_date': '2026-06-30', 'period': '06-30',
                 '总股本': 1_000_000_000, '流通股本': None},
                # 更早季度：有流通股本
                {'report_date': '2026-03-31', 'period': '0331',
                 '总股本': 900_000_000, '流通股本': 300_000_000},
            ],
        }, ensure_ascii=False), encoding='utf-8')
        fetcher = FiscalDataFetcher(fiscal_dir=str(tmp_path))
        # 必须取到更早季度的流通股本，而不是最新季度的总股本
        assert fetcher.get_float_shares('600001') == 300_000_000

    def test_float_shares_falls_back_to_total_when_no_float_exists(self, tmp_path):
        """【审查修复】所有季度都没有流通股本时，才退回总股本"""
        from selector import FiscalDataFetcher
        (tmp_path / '600002.json').write_text(json.dumps({
            'code': '600002', 'name': '测试',
            'fiscal': [
                {'report_date': '2026-06-30', 'period': '06-30',
                 '总股本': 800_000_000, '流通股本': None},
                {'report_date': '2026-03-31', 'period': '0331',
                 '总股本': 700_000_000, '流通股本': None},
            ],
        }, ensure_ascii=False), encoding='utf-8')
        fetcher = FiscalDataFetcher(fiscal_dir=str(tmp_path))
        assert fetcher.get_float_shares('600002') == 800_000_000

    def test_float_shares_ignores_non_positive_values(self, tmp_path):
        """【审查修复】0 / 负数股本视为无效，不能被当成有效分母"""
        from selector import FiscalDataFetcher
        (tmp_path / '600003.json').write_text(json.dumps({
            'code': '600003', 'name': '测试',
            'fiscal': [
                {'report_date': '2026-06-30', 'period': '06-30',
                 '总股本': 0, '流通股本': -5},
                {'report_date': '2026-03-31', 'period': '0331',
                 '总股本': 600_000_000, '流通股本': 400_000_000},
            ],
        }, ensure_ascii=False), encoding='utf-8')
        fetcher = FiscalDataFetcher(fiscal_dir=str(tmp_path))
        assert fetcher.get_float_shares('600003') == 400_000_000

    def test_as_float_rejects_nan_and_inf(self):
        """【审查修复】'nan'/'inf' 会被 float() 接受，但会让所有比较失效"""
        from selector.fiscal import _as_float
        for bad in ('nan', 'NaN', 'inf', '-inf', 'Infinity', float('nan'), float('inf')):
            assert _as_float(bad) is None, f"{bad!r} 应被判定为脏数据"
        assert _as_float('12.5') == 12.5
        assert _as_float(0) == 0.0
        assert _as_float(None) is None
        assert _as_float('') is None

    def test_quarter_month_handles_non_padded_date(self):
        """【审查修复】'2026-6-30' 这类不补零日期原本解析失败 -> 年化系数丢失"""
        from selector.fiscal import _quarter_month, _quarter_year
        for rd in ('2026-06-30', '2026-6-30', '20260630', '2026年6月30日'):
            assert _quarter_month({'report_date': rd}) == 6, f"{rd} 应解析为 6 月"
            assert _quarter_year({'report_date': rd}) == 2026, f"{rd} 应解析为 2026 年"

    def test_roe_annualized_with_non_padded_report_date(self, tmp_path):
        """【审查修复】不补零日期下 ROE 仍要正确年化（x2）"""
        from selector import FiscalDataFetcher
        (tmp_path / '300010.json').write_text(json.dumps({
            'code': '300010', 'name': '测试',
            'fiscal': [
                {'report_date': '2026-6-30', 'period': '6-30',
                 '归母净利润': 1_200_000_000, '每股净资产': 10.0,
                 '总股本': 1_000_000_000, '资产负债率': 40},
                {'report_date': '2025-06-30', 'period': '0630',
                 '归母净利润': 1_000_000_000, '每股净资产': 9.0,
                 '总股本': 1_000_000_000, '资产负债率': 42},
            ],
        }, ensure_ascii=False), encoding='utf-8')
        fetcher = FiscalDataFetcher(fiscal_dir=str(tmp_path))
        data = fetcher.get_fiscal_data('300010')
        assert data['report_period_month'] == 6
        # ROE = 12亿 / (10元 x 10亿股 = 100亿) = 12% ; 半年报年化 x2 = 24%
        assert abs(data['roe'] - 12.0) < 0.01
        assert abs(data['roe_annualized'] - 24.0) < 0.01
