"""Tests for nearest/most-recent OB and FVG selection in continuation strategies (Bug #10).

Previously, BullishContinuationStrategy/BearishContinuationStrategy picked the
first matching unmitigated OB/FVG found in list order. These tests verify the
replacement selection: nearest to price first, most-recent (highest
bar_index/end_index) as a tiebreak, and that mitigated zones are still
excluded regardless of how well they'd otherwise rank.
"""

from smc.fvg import FairValueGap, FVGDirection
from smc.order_block import OBDirection, OrderBlock
from strategy.continuation import BullishContinuationStrategy
from tests.test_strategy_engine import create_valid_bullish_market_state


class TestOrderBlockSelection:
    """BullishContinuationStrategy's OB candidates always have distance=0 to
    price (the match condition is "price inside the OB zone"), so recency
    (highest bar_index) is the sole discriminator among ties.
    """

    def test_picks_most_recent_among_overlapping_order_blocks(self) -> None:
        state = create_valid_bullish_market_state()
        base_ob = state.smc_state.order_blocks[0]  # bar_index=15, 1.0990-1.1010
        assert base_ob.bar_index == 15

        newer_ob = OrderBlock(
            id="ob_18_bullish",
            bar_index=18,
            high=1.1015,
            low=1.0985,
            direction=OBDirection.BULLISH,
            timestamp=state.bars[18].timestamp,
            is_mitigated=False,
        )
        state.smc_state.order_blocks.append(newer_ob)

        strategy = BullishContinuationStrategy()
        setup = strategy.evaluate(state)

        assert setup is not None
        assert setup.related_order_block.id == "ob_18_bullish"

    def test_mitigated_order_block_excluded_even_if_more_recent(self) -> None:
        state = create_valid_bullish_market_state()
        base_ob = state.smc_state.order_blocks[0]  # bar_index=15

        mitigated_newer_ob = OrderBlock(
            id="ob_25_bullish_mitigated",
            bar_index=25,
            high=1.1015,
            low=1.0985,
            direction=OBDirection.BULLISH,
            timestamp=state.bars[25].timestamp,
            is_mitigated=True,
        )
        state.smc_state.order_blocks.append(mitigated_newer_ob)

        strategy = BullishContinuationStrategy()
        setup = strategy.evaluate(state)

        assert setup is not None
        assert setup.related_order_block.id == base_ob.id

    def test_wrong_direction_order_block_excluded(self) -> None:
        state = create_valid_bullish_market_state()
        base_ob = state.smc_state.order_blocks[0]

        bearish_newer_ob = OrderBlock(
            id="ob_20_bearish",
            bar_index=20,
            high=1.1015,
            low=1.0985,
            direction=OBDirection.BEARISH,
            timestamp=state.bars[20].timestamp,
            is_mitigated=False,
        )
        state.smc_state.order_blocks.append(bearish_newer_ob)

        strategy = BullishContinuationStrategy()
        setup = strategy.evaluate(state)

        assert setup is not None
        assert setup.related_order_block.id == base_ob.id


class TestFairValueGapSelection:
    def test_picks_nearer_fvg_over_more_recent_farther_one(self) -> None:
        state = create_valid_bullish_market_state()
        base_fvg = state.smc_state.fair_value_gaps[0]  # 20 pips away, end_index=13
        assert base_fvg.id == "fvg_12_bullish"

        # Price (1.1000) is literally inside this one -> distance 0, nearer than base.
        nearer_fvg = FairValueGap(
            id="fvg_20_bullish_nearer",
            start_index=20,
            end_index=22,
            upper_price=1.1005,
            lower_price=1.0995,
            direction=FVGDirection.BULLISH,
            timestamp=state.bars[20].timestamp,
            is_mitigated=False,
        )
        state.smc_state.fair_value_gaps.append(nearer_fvg)

        strategy = BullishContinuationStrategy()
        setup = strategy.evaluate(state)

        assert setup is not None
        assert setup.related_fvg.id == "fvg_20_bullish_nearer"

    def test_equal_distance_ties_broken_by_recency(self) -> None:
        state = create_valid_bullish_market_state()
        # Clear the base FVG so both candidates are symmetric around price (1.1000).
        state.smc_state.fair_value_gaps.clear()

        older_equal_fvg = FairValueGap(
            id="fvg_older_equal",
            start_index=10,
            end_index=12,
            upper_price=1.0980,
            lower_price=1.0960,  # distance = |1.1000 - 1.0980| = 0.0020
            direction=FVGDirection.BULLISH,
            timestamp=state.bars[10].timestamp,
            is_mitigated=False,
        )
        newer_equal_fvg = FairValueGap(
            id="fvg_newer_equal",
            start_index=20,
            end_index=22,
            upper_price=1.0980,
            lower_price=1.0960,  # identical distance, later end_index
            direction=FVGDirection.BULLISH,
            timestamp=state.bars[20].timestamp,
            is_mitigated=False,
        )
        state.smc_state.fair_value_gaps.append(older_equal_fvg)
        state.smc_state.fair_value_gaps.append(newer_equal_fvg)

        strategy = BullishContinuationStrategy()
        setup = strategy.evaluate(state)

        assert setup is not None
        assert setup.related_fvg.id == "fvg_newer_equal"

    def test_mitigated_fvg_excluded_even_if_nearer(self) -> None:
        state = create_valid_bullish_market_state()
        base_fvg = state.smc_state.fair_value_gaps[0]

        mitigated_nearer_fvg = FairValueGap(
            id="fvg_mitigated_nearer",
            start_index=20,
            end_index=22,
            upper_price=1.1005,
            lower_price=1.0995,
            direction=FVGDirection.BULLISH,
            timestamp=state.bars[20].timestamp,
            is_mitigated=True,
        )
        state.smc_state.fair_value_gaps.append(mitigated_nearer_fvg)

        strategy = BullishContinuationStrategy()
        setup = strategy.evaluate(state)

        assert setup is not None
        assert setup.related_fvg.id == base_fvg.id

    def test_fvg_outside_proximity_threshold_excluded(self) -> None:
        state = create_valid_bullish_market_state()
        base_fvg = state.smc_state.fair_value_gaps[0]

        far_fvg = FairValueGap(
            id="fvg_too_far",
            start_index=5,
            end_index=7,
            upper_price=1.0000,  # 1000 pips away, far beyond default 50-pip threshold
            lower_price=0.9950,
            direction=FVGDirection.BULLISH,
            timestamp=state.bars[5].timestamp,
            is_mitigated=False,
        )
        state.smc_state.fair_value_gaps.append(far_fvg)

        strategy = BullishContinuationStrategy()
        setup = strategy.evaluate(state)

        assert setup is not None
        assert setup.related_fvg.id == base_fvg.id
