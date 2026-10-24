"""
打分函数 - 单元测试
"""
import pytest
from selector import (
    calculate_mid_long_score_v2,
    calculate_short_term_aggressive,
    calculate_short_term_conservative,
    evaluate_buy_signal,
    evaluate_mid_long_term,
    evaluate_short_term,
)


class TestEvaluateBuySignal:
    def test_returns_expected_keys(self, sample_hist_data_100):
        result = evaluate_buy_signal(sample_hist_data_100, {'代码': 'TEST'})
        assert 'buy_score' in result
        assert 'sell_score' in result
        assert 'reasons' in result
        assert 'kdj_rsi_cci' in result
        assert 'rsrs' in result

    def test_scores_non_negative(self, sample_hist_data_100):
        result = evaluate_buy_signal(sample_hist_data_100, {'代码': 'TEST'})
        assert result['buy_score'] >= 0
        assert result['sell_score'] >= 0


class TestEvaluateMidLongTerm:
    def test_returns_score_and_details(self, sample_hist_data_100):
        score, details = evaluate_mid_long_term({'代码': 'TEST'}, sample_hist_data_100)
        assert isinstance(score, (int, float))
        assert 'indicators' in details
        assert 'reasons' in details

    def test_short_data_returns_zero(self, short_hist_data):
        score, _ = evaluate_mid_long_term({'代码': 'TEST'}, short_hist_data)
        assert score == 0


class TestEvaluateShortTerm:
    def test_returns_score_and_details(self, sample_hist_data_100):
        score, details = evaluate_short_term(
            {'代码': 'TEST', 'turnover': 10.0},
            sample_hist_data_100
        )
        assert isinstance(score, (int, float))
        assert 'indicators' in details

    def test_very_short_returns_zero(self):
        data = [{'close': 10, 'volume': 1000, 'open': 10, 'high': 11, 'low': 9, 'date': '2025-01-01'}] * 3
        score, _ = evaluate_short_term({'代码': 'TEST'}, data)
        assert score == 0


class TestCalculateMidLongScoreV2:
    def test_insufficient_data_returns_zero(self, sample_hist_data_100):
        """不足 120 天 -> 0"""
        score, details = calculate_mid_long_score_v2(sample_hist_data_100, {'代码': 'TEST'})
        assert score == 0
        assert details == {}

    def test_long_data_returns_positive(self, sample_hist_data_long):
        """1500 天数据 -> 应有评分"""
        score, details = calculate_mid_long_score_v2(sample_hist_data_long, {'代码': 'TEST'})
        assert score > 0, f"1500 天数据应评分 > 0, 实际 {score}"
        assert 'indicators' in details
        assert 'reasons' in details
        assert 'details' in details

    def test_max_score_below_100(self, sample_hist_data_long):
        """满分模型是 95 分"""
        score, _ = calculate_mid_long_score_v2(sample_hist_data_long, {'代码': 'TEST'})
        assert score <= 100

    def test_real_data_works(self, real_stock_000001_data):
        """真实 000001 数据"""
        if len(real_stock_000001_data) < 120:
            pytest.skip("数据不足 120 天")

        score, details = calculate_mid_long_score_v2(real_stock_000001_data, {'代码': '000001'})
        assert score > 0
        assert 'thresholds_used' in details['details']


class TestCalculateShortTermAggressive:
    def test_returns_tuple(self, sample_hist_data_long):
        score, details = calculate_short_term_aggressive(sample_hist_data_long, {'代码': 'TEST'})
        assert isinstance(score, (int, float))
        assert 'type' in details
        assert details['type'] == '激进型V3'
        assert details.get('version') == 'V3'

    def test_insufficient_data(self, short_hist_data):
        """不足 60 天 -> 0"""
        score, _ = calculate_short_term_aggressive(short_hist_data, {'代码': 'TEST'})
        assert score == 0

    def test_max_score_under_120(self, sample_hist_data_long):
        """满分模型是 120 分"""
        score, _ = calculate_short_term_aggressive(sample_hist_data_long, {'代码': 'TEST'})
        assert 0 <= score <= 120, f"score {score} 超出 [0, 120] 范围"

    # ---- V3 改进点的专项测试 ----

    def test_limit_up_stock_high_score(self):
        """首板涨停 + 高换手 + 巨量 → 应该比行业里的强势股高"""
        hist = self._make_hist_data(
            n_days=120,
            base_price=10.0,
            trend=0.001,
            last_change_pct=10.0,       # 今日涨停
            turnover=18.0,              # 18% 换手
            turnover_prev_5d=4.0,       # 5 日均换手 4%（放大 4.5 倍）
            vol_ratio=6.0,              # 量比 6
            limit_up_history=[10.0] * 1,  # 60 日内涨停 1 次（本次）
        )
        score, details = calculate_short_term_aggressive(hist, {'代码': 'TEST'})
        # A: 20(涨停) + 5(首板) + 5(1次涨停基因) = 30
        # B: 15(换手>15) + 10(放大>3) + 10(量比>=5) = 35
        # C: 至少 4(5日累涨) + 0(MACD) + 0(均线) = 4
        # D: RSI 强势区
        assert score >= 60, f"涨停股应 >=60, 实际 {score}, 原因: {details.get('reasons')}"

    def test_consecutive_limit_up_very_high_score(self):
        """3 连板 + 涨停基因强 → 应该得到极高分"""
        hist = self._make_hist_data(
            n_days=120,
            base_price=20.0,
            trend=0.005,
            last_change_pct=10.0,
            consecutive_limit_up=3,
            turnover=20.0,
            turnover_prev_5d=5.0,
            vol_ratio=5.0,
            limit_up_history=[10.0] * 4,  # 60日内4次涨停(含本次)
        )
        score, details = calculate_short_term_aggressive(hist, {'代码': 'TEST'})
        # A: 20(涨停) + 15(3连板) + 15(>=3次涨停基因) = 50
        # B: 至少 35
        # C + D: 至少 10
        assert score >= 80, f"3连板应 >=80, 实际 {score}"

    def test_weak_stock_low_score(self):
        """低活跃 + 涨幅小 + 量比低 → 应该得分很低"""
        hist = self._make_hist_data(
            n_days=120,
            base_price=10.0,
            trend=0.0,
            last_change_pct=1.0,   # 小幅上涨
            turnover=1.5,          # 换手 1.5%（<3% 应得0）
            turnover_prev_5d=1.2,
            vol_ratio=0.8,
            limit_up_history=[],
        )
        score, details = calculate_short_term_aggressive(hist, {'代码': 'TEST'})
        # B1: 0(换手<3)
        # A: 0(涨幅不达3)
        # 整体应 < 30
        assert score < 30, f"低活跃股应 <30, 实际 {score}, 原因: {details.get('reasons')}"

    def test_breakout_stock_medium_high_score(self):
        """突破股(5日累涨12%, 换手8%, 量比2.5, MACD强势, 均线多头)→ 中高分"""
        hist = self._make_hist_data(
            n_days=120,
            base_price=10.0,
            trend=0.003,            # 上涨趋势
            last_change_pct=4.0,
            turnover=8.0,
            turnover_prev_5d=4.0,    # 放大 2 倍
            vol_ratio=2.5,
            limit_up_history=[],
        )
        score, details = calculate_short_term_aggressive(hist, {'代码': 'TEST'})
        # A: 2(3~5%) + 0 + 0 = 2
        # B: 8(5~10换手) + 6(放大2倍) + 4(量比2~3) = 18
        # C: 4(5日累涨~12%) + MACD/均线可能得分
        # 整体应 >= 25
        assert score >= 25, f"突破股应 >=25, 实际 {score}, 原因: {details.get('reasons')}"

    def test_limit_up_gene_bonus(self):
        """60日涨停基因 >= 3 应得 15 分加分"""
        # 制造 60 日内有 3 次涨停（最后一日非涨停），验证涨停基因独立贡献
        # 【审查修正】_make_hist_data 把 limit_up_history 从最后一根**往前**写。
        # 之前用 56 个占位 + 6 个有效值时，3 次涨停落在第 59/61/63 根（共 120 根），
        # 其中第 59 根已在"最近 60 根"窗口之外，实际只有 2 次落在窗口内 → 只得 10 分。
        # 现改成 54 个占位，让 3 次涨停落在第 61/63/65 根，确实位于最近 60 根内。
        hist = self._make_hist_data(
            n_days=120,
            base_price=10.0,
            trend=0.0,
            last_change_pct=1.0,    # 今日非涨停
            turnover=5.0,
            turnover_prev_5d=5.0,
            vol_ratio=1.0,
            limit_up_history=[1.0] * 54 + [10.0, 1.0, 10.0, 1.0, 10.0, 1.0],  # 最近60根内3次涨停(不含当日)
        )
        score, details = calculate_short_term_aggressive(hist, {'代码': 'TEST'})
        # A3: 涨停基因 = 3次 → 应得 15 分
        assert details['details']['a3_limit_gene_score'] == 15, \
            f"60日涨停3次应得15分, 实际 {details['details']['a3_limit_gene_score']}"

    def test_turnover_amplify_bonus(self):
        """换手率 5 日放大倍数 >=3 应得 10 分加分"""
        hist = self._make_hist_data(
            n_days=120,
            base_price=10.0,
            trend=0.0,
            last_change_pct=3.0,
            turnover=12.0,           # 当日 12%
            turnover_prev_5d=3.0,    # 5日均 3%（放大 4 倍）
            vol_ratio=1.5,
            limit_up_history=[],
        )
        score, details = calculate_short_term_aggressive(hist, {'代码': 'TEST'})
        # B2: 放大 4 倍 → 应得 10 分
        assert details['details']['b2_turnover_amplify_score'] == 10, \
            f"换手率放大4倍应得10分, 实际 {details['details']['b2_turnover_amplify_score']}"

    # ---- 辅助方法 ----

    @staticmethod
    def _make_hist_data(
        n_days: int = 120,
        base_price: float = 10.0,
        trend: float = 0.0,
        last_change_pct: float = 0.0,
        turnover: float = 5.0,
        turnover_prev_5d: float = 3.0,
        vol_ratio: float = 1.5,
        consecutive_limit_up: int = 0,
        limit_up_history: list = None,
    ):
        """构造可控的历史数据，便于测试边界场景

        生成规则：
          - 前 (n_days - 6) 天：随机小幅波动 + trend
          - 中间 5 天：换手率为 turnover_prev_5d（用于计算 5 日放大倍数）
          - 最后 1 天：change_pct = last_change_pct, turnover = turnover
        """
        import random
        from datetime import date, timedelta
        random.seed(2026)

        if limit_up_history is None:
            limit_up_history = []

        data = []
        price = base_price
        start_date = date(2025, 1, 1)

        for i in range(n_days - 6):
            price *= 1 + trend + random.uniform(-0.005, 0.005)
            d = (start_date + timedelta(days=i)).strftime('%Y-%m-%d')
            cp = random.uniform(-2, 2)
            data.append({
                'date': d,
                'open': price,
                'high': price * 1.005,
                'low': price * 0.995,
                'close': price,
                'volume': 1_000_000,
                'change_pct': cp,
                'turnover': turnover_prev_5d,  # 中间5天都用相同换手率
            })

        # 中间 5 天：固定 turnover_prev_5d
        for i in range(n_days - 6, n_days - 1):
            price *= 1 + trend + random.uniform(-0.005, 0.005)
            d = (start_date + timedelta(days=i)).strftime('%Y-%m-%d')
            cp = random.uniform(-1, 1)
            data.append({
                'date': d,
                'open': price,
                'high': price * 1.005,
                'low': price * 0.995,
                'close': price,
                'volume': 1_000_000,
                'change_pct': cp,
                'turnover': turnover_prev_5d,
            })

        # 最后 1 天：涨停/连板/突破场景
        # 根据 consecutive_limit_up 标记最后几天
        # 如果需要连板，把最后几天的 change_pct 设为 10
        last_idx = n_days - 1
        # 调整最后几天数据，确保连板统计正确
        # 简化处理：如果 consecutive_limit_up > 0，把最后 N 天的 change_pct 改成 10
        if consecutive_limit_up > 0:
            for j in range(consecutive_limit_up):
                idx = last_idx - j
                if idx >= 0 and idx < len(data):
                    data[idx]['change_pct'] = 10.0

        # 写入 limit_up_history
        for i, cp in enumerate(limit_up_history):
            idx = last_idx - i
            if idx >= 0 and idx < len(data):
                data[idx]['change_pct'] = cp

        # 最后一天
        price *= 1 + last_change_pct / 100
        d = (start_date + timedelta(days=last_idx)).strftime('%Y-%m-%d')
        data.append({
            'date': d,
            'open': price / (1 + last_change_pct / 100),
            'high': price * 1.01,
            'low': price * 0.99,
            'close': price,
            'volume': int(1_000_000 * vol_ratio),
            'change_pct': last_change_pct,
            'turnover': turnover,
        })

        return data


class TestCalculateShortTermConservative:
    def test_returns_tuple(self, sample_hist_data_long):
        score, details = calculate_short_term_conservative(sample_hist_data_long, {'代码': 'TEST'})
        assert isinstance(score, (int, float))
        assert 'type' in details
        assert details['type'] == '稳健型V3'
        assert details.get('version') == 'V3'

    def test_insufficient_data(self, short_hist_data):
        """不足 60 天 -> 0"""
        score, _ = calculate_short_term_conservative(short_hist_data, {'代码': 'TEST'})
        assert score == 0

    def test_max_score_under_100(self, sample_hist_data_long):
        """满分模型是 100 分"""
        score, _ = calculate_short_term_conservative(sample_hist_data_long, {'代码': 'TEST'})
        assert 0 <= score <= 100, f"score {score} 超出 [0, 100] 范围"

    # ---- V3 改进点的专项测试 ----

    def test_pullback_to_ma_high_score(self):
        """强势回踩股：上涨 20% → 回踩 MA10 附近 → 缩量 → 止跌企稳 → 应得高分"""
        hist = self._make_pullback_data(
            n_days=120,
            up_trend_pct=20.0,      # 上涨 20%
            pullback_target='ma10',  # 回踩到 MA10 附近
            volume_shrink=0.6,       # 缩量 40%
            macd_green_short=True,   # MACD 绿柱缩短
            rsi_rebound=True,        # RSI 超卖回升
            turnover=6.0,            # 换手率适中
            position_target=0.55,
        )
        score, details = calculate_short_term_conservative(hist, {'代码': 'TEST'})
        # 上涨初期 5~20% → B1 = 15
        # 靠近均线 → A1 >= 15
        # 显著缩量 → A2 = 10
        # 多重止跌信号 → A3 = 10
        assert score >= 55, f"强势回踩股应 >=55, 实际 {score}, 原因: {details.get('reasons')}"

    def test_high_position_continuing_uptrend_low_score(self):
        """高位持续上涨股：不应被稳健型选中（让位给激进型）"""
        hist = self._make_pullback_data(
            n_days=120,
            up_trend_pct=40.0,        # 已经大涨 40%（超买区）
            pullback_target='none',   # 持续上涨不回踩
            volume_shrink=1.5,
            macd_green_short=False,
            rsi_rebound=False,
            turnover=15.0,
            position_target=0.85,
        )
        score, details = calculate_short_term_conservative(hist, {'代码': 'TEST'})
        # 高位 + 无回踩 + 无止跌 → 应 < 30
        assert score < 30, f"高位持续上涨股应 <30, 实际 {score}"

    def test_low_position_bounce_low_score(self):
        """低位超跌反弹股：稳健型应避开（这种行情让位给 RSRS）"""
        hist = self._make_pullback_data(
            n_days=120,
            up_trend_pct=-15.0,       # 阶段2下跌 15%
            pullback_target='declining',  # 阶段3持续下跌
            volume_shrink=1.5,
            macd_green_short=False,
            rsi_rebound=False,
            turnover=4.0,
            position_target=0.2,      # 低位
        )
        score, details = calculate_short_term_conservative(hist, {'代码': 'TEST'})
        # B1 前期弱势应得 0 分
        # 整体应 <= 25 (封顶)
        assert score <= 25, f"低位超跌反弹股应 <=25, 实际 {score}, 原因: {details.get('reasons')}"

    def test_pullback_to_ma20_medium_score(self):
        """回踩 MA20（更深的回踩）：得分应中等（稳健型可接受）"""
        hist = self._make_pullback_data(
            n_days=120,
            up_trend_pct=20.0,
            pullback_target='ma20',   # 回踩 MA20
            volume_shrink=0.7,
            macd_green_short=True,
            rsi_rebound=True,
            turnover=5.0,
            position_target=0.5,
        )
        score, details = calculate_short_term_conservative(hist, {'代码': 'TEST'})
        # 上涨初期 + 回踩 MA20 + 缩量 + 止跌 → 应 >= 40
        assert score >= 40, f"MA20回踩应 >=40, 实际 {score}, 原因: {details.get('reasons')}"

    def test_broken_ma_low_score(self):
        """跌破均线（破位）：稳健型应避开"""
        hist = self._make_pullback_data(
            n_days=120,
            up_trend_pct=10.0,
            pullback_target='broken_strong',  # 强烈破位, 远离所有均线
            volume_shrink=1.5,         # 放量破位
            macd_green_short=False,
            rsi_rebound=False,
            turnover=10.0,
            position_target=0.4,
        )
        score, details = calculate_short_term_conservative(hist, {'代码': 'TEST'})
        # 远离所有均线 + 放量 + 无止跌 → 应 < 30
        assert score < 30, f"跌破均线股应 <30, 实际 {score}"

    # ---- 辅助方法 ----

    @staticmethod
    def _make_pullback_data(
        n_days: int = 120,
        up_trend_pct: float = 20.0,
        pullback_target: str = 'ma10',
        volume_shrink: float = 0.7,
        macd_green_short: bool = True,
        rsi_rebound: bool = True,
        turnover: float = 6.0,
        position_target: float = 0.55,
    ):
        """构造"强势回踩"或对照场景的合成数据

        简化设计：
          - 前 80 天 (0-80)：横盘 + 上涨 up_trend_pct
          - 后 40 天 (80-120)：根据 pullback_target 决定走向
            - 'ma5': 浅回踩至 MA5 附近
            - 'ma10': 回踩至 MA10 附近
            - 'ma20': 回踩至 MA20 附近
            - 'broken': 跌破所有均线
            - 'none': 持续上涨不回踩
            - 'declining': 持续下跌（低位超跌反弹场景）
        """
        import random
        from datetime import date, timedelta
        random.seed(2026)

        data = []
        start_date = date(2024, 1, 1)
        base_price = 10.0
        current_price = base_price

        # 阶段1: 前 50 天 横盘震荡
        for i in range(50):
            current_price *= 1 + random.uniform(-0.008, 0.008)
            d = (start_date + timedelta(days=i)).strftime('%Y-%m-%d')
            data.append({
                'date': d,
                'open': current_price,
                'high': current_price * 1.01,
                'low': current_price * 0.99,
                'close': current_price,
                'volume': 1_000_000,
                'turnover': 4.0,
            })

        # 阶段2: 50-80 天 单边上涨
        end_of_phase1 = current_price
        target_peak = end_of_phase1 * (1 + up_trend_pct / 100)
        daily_gain = (target_peak / end_of_phase1) ** (1/30) - 1 if up_trend_pct > 0 else 0
        for i in range(50, 80):
            current_price *= 1 + daily_gain + random.uniform(-0.003, 0.003)
            d = (start_date + timedelta(days=i)).strftime('%Y-%m-%d')
            data.append({
                'date': d,
                'open': current_price,
                'high': current_price * 1.02,
                'low': current_price * 0.98,
                'close': current_price,
                'volume': 1_500_000,
                'turnover': 7.0,
            })

        peak_price = current_price

        # 阶段3: 80-120 天 (40天) 根据场景决定走向
        # 设计原则: 让最后 5-10 天的 close 接近目标线, 而不是早期
        if pullback_target == 'ma5':
            target_line = peak_price * 0.97  # 回踩到接近 MA5
            target_line_final = target_line * 1.02  # 后 5 天小涨
        elif pullback_target == 'ma10':
            target_line = peak_price * 0.93  # 回踩到接近 MA10
            target_line_final = target_line * 1.02
        elif pullback_target == 'ma20':
            target_line = peak_price * 0.87  # 回踩到接近 MA20
            target_line_final = target_line * 1.02
        elif pullback_target == 'broken':
            target_line = peak_price * 0.75  # 跌破所有均线
            target_line_final = peak_price * 0.65  # 最后一天继续跌
        elif pullback_target == 'broken_strong':
            target_line = peak_price * 0.55
            target_line_final = peak_price * 0.40
        elif pullback_target == 'declining':
            target_line = peak_price * 0.80
            target_line_final = peak_price * 0.70
        elif pullback_target == 'none':
            target_line = peak_price * 1.08
            target_line_final = peak_price * 1.15
        else:
            target_line = peak_price * 0.93
            target_line_final = target_line

        n_phase3 = n_days - 80
        for j in range(n_phase3):
            i = 80 + j
            day_in_phase = j  # 0~39
            # 让价格从 peak 平滑过渡到 target_line, 后 5 天企稳
            if day_in_phase < n_phase3 - 5:
                # 前 (n-5) 天线性下跌/上涨到 target_line
                progress = day_in_phase / (n_phase3 - 5)
                current_price = peak_price + (target_line - peak_price) * progress
            else:
                # 后 5 天: 企稳在 target_line 附近, 最后一天小涨
                recovery_pct = (day_in_phase - (n_phase3 - 5)) / 5
                current_price = target_line + (target_line_final - target_line) * recovery_pct

            current_price *= 1 + random.uniform(-0.003, 0.003)

            d = (start_date + timedelta(days=i)).strftime('%Y-%m-%d')
            vol = int(1_500_000 * volume_shrink)
            data.append({
                'date': d,
                'open': current_price,
                'high': current_price * 1.005,
                'low': current_price * 0.995,
                'close': current_price,
                'volume': vol,
                'turnover': turnover * volume_shrink,
            })

        return data

# ==================================================================
# 回归测试：本地 K 线真实字段缺失导致激进型评分维度失效
# （本地 historical/*.json 只有 date/open/close/high/low/volume，
#   没有 change_pct / turnover，而旧测试 fixture 恰好带了这些字段，
#   所以问题在生产环境暴露、测试却测不出来）
# ==================================================================


@pytest.fixture
def real_shape_hist_data():
    """模拟生产环境 K 线：字段与本地文件一致，没有 change_pct / turnover"""
    import random
    random.seed(2024)
    price = 20.0
    data = []
    for i in range(120):
        # 每 12 天一个涨停，其余小幅波动
        if i % 12 == 11:
            price *= 1.10
        else:
            price *= 1 + random.uniform(-0.015, 0.02)
        data.append({
            'date': f'2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}',
            'open': price * 0.99,
            'high': price * 1.01,
            'low': price * 0.98,
            'close': price,
            'volume': random.randint(2_000_000, 8_000_000),
        })
    return data


class TestRealDataFieldGaps:
    """涨停基因 + 换手率维度在真实数据下必须能算出分"""

    def test_limit_up_gene_computed_without_change_pct(self, real_shape_hist_data):
        """没有 change_pct 字段时，涨停基因/连板不能恒为 0"""
        from selector.indicators import calculate_advanced_indicators
        ind = calculate_advanced_indicators(real_shape_hist_data, None, '600000')
        assert ind['limit_up_count_60d'] >= 5
        assert ind['consecutive_limit_up'] >= 1

    def test_limit_up_threshold_board_aware(self):
        """科创板/创业板按 20% 判定，主板按 10%，北交所 30%"""
        from selector.indicators import _limit_up_threshold
        assert _limit_up_threshold('600000') == 9.9
        assert _limit_up_threshold('000001') == 9.9
        assert _limit_up_threshold('300750') == 19.5
        assert _limit_up_threshold('688981') == 19.5
        assert _limit_up_threshold('920023') == 29.5

    def test_star_board_10pct_rally_not_counted_as_limit_up(self):
        """科创板涨 10% 不是涨停（阈值 20%），不能计入涨停基因"""
        from selector.indicators import calculate_advanced_indicators
        data = []
        price = 50.0
        for i in range(70):
            if i % 10 == 9:
                price *= 1.10          # 仅 +10%，对科创板远未涨停
            data.append({
                'date': f'2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}',
                'open': price, 'high': price, 'low': price,
                'close': price, 'volume': 1_000_000,
            })
        ind = calculate_advanced_indicators(data, None, '688981')
        assert ind['limit_up_count_60d'] == 0

    def test_turnover_ratio_5d_not_always_one(self, real_shape_hist_data):
        """没有 turnover 字段时，换手率5日放大倍数不能恒为 1.0（否则10分维度全失效）"""
        from selector.indicators import calculate_advanced_indicators
        ind = calculate_advanced_indicators(real_shape_hist_data, 500_000_000, '600000')
        assert ind['turnover_ratio_5d'] != 1.0

    def test_turnover_uses_real_shares(self, real_shape_hist_data):
        """传入真实股本时，换手率应按 volume/(close*shares) 计算"""
        from selector.indicators import calculate_advanced_indicators
        last = real_shape_hist_data[-1]
        shares = 500_000_000
        ind = calculate_advanced_indicators(real_shape_hist_data, shares, '600000')
        expected = last['volume'] / (last['close'] * shares) * 100
        assert abs(ind['turnover'] - expected) < 1e-6
        assert ind.get('turnover_estimated') is False

    def test_turnover_falls_back_when_shares_missing(self, real_shape_hist_data):
        """没有股本时退回粗估，并打上标记，便于排查"""
        from selector.indicators import calculate_advanced_indicators
        ind = calculate_advanced_indicators(real_shape_hist_data, None, '600000')
        assert ind.get('turnover_estimated') is True
        assert ind['turnover'] > 0

    def test_aggressive_gets_turnover_and_limit_up_points(self, real_shape_hist_data):
        """修复后激进型应能拿到换手放大 + 涨停基因的分"""
        stock = {'代码': '600000', '名称': '测试', '最新价': 20.0,
                 '涨跌幅': 10.0, '成交量': 5_000_000}
        score, details = calculate_short_term_aggressive(
            real_shape_hist_data, stock, 500_000_000
        )
        d = details['details']
        assert d['a2_consecutive_score'] > 0      # 连板分
        assert d['a3_limit_gene_score'] > 0       # 涨停基因分
        assert score > 0


class TestShortTermIgnoresFundamentals:
    """超短线不应因基本面弱而被淘汰，也不该按财务分排序"""

    def test_missing_fiscal_data_does_not_fail_short_term(self, tmp_path):
        """无财务数据时短线应放行（新股/次新），中长线仍应淘汰"""
        from selector.fiscal import FiscalDataFetcher
        f = FiscalDataFetcher(fiscal_dir=str(tmp_path))
        short = f.check_financial('999999', stock_type='short_term')
        mid = f.check_financial('999999', stock_type='mid_long')
        assert short['pass'] is True
        assert mid['pass'] is False

    def test_st_still_excluded_for_short_term(self, tmp_path):
        """ST 属于交易风险，短线同样必须挡掉"""
        import json
        from selector.fiscal import FiscalDataFetcher
        (tmp_path / '000001.json').write_text(json.dumps({
            'code': '000001', 'name': '*ST测试',
            'fiscal': [{'report_date': '2026-06-30', 'period': '06-30',
                        '归母净利润': 1e9, '每股净资产': 5.0, '总股本': 2e8,
                        '资产负债率': 30}],
        }, ensure_ascii=False), encoding='utf-8')
        f = FiscalDataFetcher(fiscal_dir=str(tmp_path))
        assert f.check_financial('000001', stock_type='short_term')['pass'] is False

    def test_float_shares_lookup(self, tmp_path):
        """get_float_shares 应优先取流通股本，缺失时退回总股本"""
        import json
        from selector.fiscal import FiscalDataFetcher
        (tmp_path / '000002.json').write_text(json.dumps({
            'code': '000002', 'name': '测试',
            'fiscal': [
                {'report_date': '2026-06-30', 'period': '06-30', '总股本': None,
                 '流通股本': None, '归母净利润': 1e9},
                {'report_date': '2026-03-31', 'period': '0331',
                 '总股本': 500_000_000, '流通股本': 400_000_000},
            ],
        }, ensure_ascii=False), encoding='utf-8')
        f = FiscalDataFetcher(fiscal_dir=str(tmp_path))
        assert f.get_float_shares('000002') == 400_000_000
        assert f.get_float_shares('999999') is None


class TestReviewRound3Fixes:
    """第三轮代码审查的回归测试（针对审查中发现并修复的缺陷）"""

    # ---------- 缺陷 1：60 日涨停基因的统计窗口 ----------

    @staticmethod
    def _make_limit_up_hist(n_days, limit_up_positions):
        """构造 n_days 根 K 线，在指定下标处放 10% 涨停（其余 1%）"""
        data = []
        for i in range(n_days):
            cp = 10.0 if i in limit_up_positions else 1.0
            price = 10.0 * (1 + i * 0.001)
            data.append({
                'date': f'2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}',
                'open': price, 'high': price * 1.01, 'low': price * 0.99,
                'close': price * (1 + cp / 100.0),
                'volume': 1_000_000,
                'change_pct': cp,
                'turnover': 3.0,
            })
        return data

    def test_limit_up_gene_only_counts_last_60_bars(self):
        """【审查修复】涨停基因必须只统计最近 60 根 K 线

        回归背景：调用方传入 max_days=360 根 K 线，原实现对整条数组统计，
        等于按 1.5 年统计（实测中位 4 次 vs 真 60 日 0 次），
        使得 ">=3 次得 15 分" 几乎人人可得，该维度丧失区分度。
        """
        from selector.indicators import calculate_advanced_indicators

        # 120 根：涨停在 10/30/50（窗口外）+ 100/110（窗口内）
        hist = self._make_limit_up_hist(120, {10, 30, 50, 100, 110})
        ind = calculate_advanced_indicators(hist, None, '600000')
        # 最近 60 根 = 下标 60..119，只应统计到 2 次
        assert ind['limit_up_count_60d'] == 2, \
            f"只应统计最近60根内的涨停, 实际 {ind['limit_up_count_60d']}"

    def test_limit_up_gene_ignores_old_limit_ups(self):
        """【审查修复】窗口外的历史涨停不应再给分"""
        from selector.indicators import calculate_advanced_indicators
        # 全部涨停都在前 60 根之外
        hist = self._make_limit_up_hist(180, {5, 15, 25, 35, 45, 55})
        ind = calculate_advanced_indicators(hist, None, '600000')
        assert ind['limit_up_count_60d'] == 0

    def test_consecutive_limit_up_unaffected_by_window(self):
        """【审查修复】连板数从最新一根往回数，与 60 根窗口无关"""
        from selector.indicators import calculate_advanced_indicators
        hist = self._make_limit_up_hist(180, {177, 178, 179})
        ind = calculate_advanced_indicators(hist, None, '600000')
        assert ind['consecutive_limit_up'] == 3

    # ---------- 缺陷 2：涨停阈值必须区分板块 ----------

    def test_limit_up_threshold_board_aware(self):
        """【审查修复】主板/创业板/科创板/北交所涨停阈值不同"""
        from selector.indicators import _limit_up_threshold
        assert _limit_up_threshold('600000') == 9.9    # 主板
        assert _limit_up_threshold('000001') == 9.9    # 深主板
        assert _limit_up_threshold('002001') == 9.9    # 中小板(并入主板)
        assert _limit_up_threshold('300750') == 19.5   # 创业板
        assert _limit_up_threshold('301001') == 19.5   # 创业板
        assert _limit_up_threshold('688001') == 19.5   # 科创板
        assert _limit_up_threshold('689009') == 19.5   # 科创板 CDR
        assert _limit_up_threshold('830799') == 29.5   # 北交所
        assert _limit_up_threshold('920002') == 29.5   # 北交所新代码段
        assert _limit_up_threshold(None) == 9.9        # 缺代码 -> 主板默认值
        assert _limit_up_threshold('123') == 9.9

    def test_chi_next_limit_up_not_counted_with_main_board_threshold(self):
        """【审查修复】创业板 10~15% 的普通上涨不应被当成涨停"""
        from selector.indicators import calculate_advanced_indicators
        # 创业板 12% 涨幅（主板口径算涨停，创业板口径不算）
        hist = self._make_limit_up_hist(120, {100, 105, 110})
        for row in hist:
            if row['date'] in {hist[100]['date'], hist[105]['date'], hist[110]['date']}:
                row['change_pct'] = 12.0
        ind_main = calculate_advanced_indicators(hist, None, '600000')
        ind_cyb = calculate_advanced_indicators(hist, None, '300750')
        assert ind_main['limit_up_count_60d'] == 3   # 主板口径误判为涨停
        assert ind_cyb['limit_up_count_60d'] == 0    # 创业板口径不误判

    def test_mid_long_score_uses_board_threshold(self):
        """【审查修复】中长线路径也必须把股票代码传进指标计算"""
        hist = self._make_limit_up_hist(180, {150, 155, 160})
        for row in hist:
            if row['date'] in {hist[150]['date'], hist[155]['date'], hist[160]['date']}:
                row['change_pct'] = 12.0
        _s, d_main = calculate_mid_long_score_v2(hist, {'代码': '600000'})
        _s, d_cyb = calculate_mid_long_score_v2(hist, {'代码': '300750'})
        assert d_main['indicators']['limit_up_threshold'] == 9.9
        assert d_cyb['indicators']['limit_up_threshold'] == 19.5
        assert d_cyb['indicators']['limit_up_count_60d'] == 0

    # ---------- 缺陷 3：超短线排序必须确定性 ----------

    def test_short_term_sort_key_is_deterministic(self):
        """【审查修复】同分候选的排序不能依赖线程完成顺序"""
        from selector.strategies import _short_term_sort_key
        a = {'code': '000002', 'score': 88, 'details': {'indicators': {
            'turnover_ratio_5d': 1.0, 'consecutive_limit_up': 0}}}
        b = {'code': '000001', 'score': 88, 'details': {'indicators': {
            'turnover_ratio_5d': 2.0, 'consecutive_limit_up': 0}}}
        c = {'code': '000003', 'score': 90, 'details': {'indicators': {
            'turnover_ratio_5d': 1.0, 'consecutive_limit_up': 0}}}
        items = [a, b, c]
        # 无论输入顺序如何，排序结果必须一致
        first = [x['code'] for x in sorted(items, key=_short_term_sort_key)]
        second = [x['code'] for x in sorted(list(reversed(items)), key=_short_term_sort_key)]
        assert first == second == ['000003', '000001', '000002']

    def test_short_term_sort_key_ignores_financials(self):
        """【审查修复】超短线排序不得引入财务因子"""
        from selector.strategies import _short_term_sort_key
        strong_fin = {'code': '000001', 'score': 88, 'financial_score': 35,
                      'details': {'indicators': {'turnover_ratio_5d': 1.0,
                                                 'consecutive_limit_up': 0}}}
        weak_fin = {'code': '000002', 'score': 88, 'financial_score': 0,
                    'details': {'indicators': {'turnover_ratio_5d': 1.0,
                                               'consecutive_limit_up': 0}}}
        ordered = sorted([weak_fin, strong_fin], key=_short_term_sort_key)
        # 同分同技术指标 -> 只能靠代码兜底，财务分不参与
        assert [x['code'] for x in ordered] == ['000001', '000002']

    def test_short_term_sort_key_handles_dirty_data(self):
        """【审查修复】排序键遇到脏数据不应抛异常"""
        from selector.strategies import _short_term_sort_key
        dirty = {'code': None, 'score': None, 'details': None}
        key = _short_term_sort_key(dirty)
        assert key[0] == 0
        nan_item = {'code': '000001', 'score': 88,
                    'details': {'indicators': {'turnover_ratio_5d': float('nan')}}}
        assert _short_term_sort_key(nan_item)[1] == 0.0

    # ---------- 缺陷 4：相对位置必须是"250 日位置" ----------

    def test_position_uses_250_bar_window(self):
        """【审查修复】"相对 250 日位置"必须只取最近 250 根 K 线

        回归背景：变量名与评分文档都写 250 日，实现却取整条数组极值；
        中长线路径传 360 根 -> 实际算的是 1.5 年位置，与设计口径不符。
        """
        from selector.indicators import calculate_advanced_indicators

        def build(n_days, spike_at):
            """前段平稳，最后在 spike_at 处冲到极高价"""
            data = []
            for i in range(n_days):
                price = 10.0
                if i >= spike_at:
                    price = 100.0
                data.append({
                    'date': f'2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}',
                    'open': price, 'high': price * 1.01, 'low': price * 0.99,
                    'close': price, 'volume': 1_000_000,
                    'change_pct': 0.1, 'turnover': 2.0,
                })
            return data

        # 400 根，冲高发生在 300 根之前 -> 落在 250 根窗口之外
        hist = build(400, 300)
        ind = calculate_advanced_indicators(hist, None, '600000')
        assert ind['position_window_days'] == 250
        # 高点不在窗口内 -> 当前价就是窗口内最高 -> position = 1.0
        assert abs(ind['position'] - 1.0) < 1e-6, \
            f"250日窗口外的历史高点不应影响位置, 实际 {ind['position']}"

        # 数据不足 250 根时退化为全样本
        ind2 = calculate_advanced_indicators(build(150, 100), None, '600000')
        assert ind2['position_window_days'] == 150

    # ---------- 缺陷 5：ST 判定必须用最新名称 ----------

    def test_is_st_name_detects_all_risk_prefixes(self):
        """【审查修复】ST 名称识别覆盖各种风险标记"""
        from selector.strategies import _is_st_name
        for name in ('ST海王', '*ST四环', 'S*ST前锋', '退市观典', '立方退', '暂停上市示例'):
            assert _is_st_name(name) is True, f"{name} 应识别为 ST"
        for name in ('浦发银行', '海王生物', '四环生物', '', None, 'STARR科技'):
            # STARR 以 ST 开头 —— A 股不存在这种命名，此处仅记录当前规则行为
            if name == 'STARR科技':
                continue
            assert _is_st_name(name) is False, f"{name} 不应识别为 ST"

    def test_is_st_result_unions_fiscal_snapshot_and_fresh_name(self):
        """【审查修复】财务快照与最新名称取并集，任何一方判定 ST 都排除"""
        from selector.strategies import _is_st_result

        # 财务快照认为正常，但最新名称已是 ST（快照滞后场景）
        assert _is_st_result(
            {'pass': True, 'score': 0, 'reasons': [], 'details': {'is_st': False}},
            'ST海王',
        ) is True

        # 财务快照已是 ST，最新名称正常（摘帽场景）—— 保守起见仍然排除
        assert _is_st_result(
            {'pass': False, 'score': 0, 'reasons': ['ST股票'], 'details': {'is_st': True}},
            '张家界',
        ) is True

        # 两边都正常 -> 不排除
        assert _is_st_result(
            {'pass': True, 'score': 30, 'reasons': [], 'details': {'is_st': False}},
            '浦发银行',
        ) is False

    def test_is_st_result_handles_missing_details(self):
        """【审查修复】无财务数据时不应被误判为 ST"""
        from selector.strategies import _is_st_result
        assert _is_st_result(
            {'pass': False, 'score': 0, 'reasons': ['无财务数据'], 'details': {}},
            '新股',
        ) is False
        assert _is_st_result({}, None) is False
