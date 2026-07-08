"""Liquidity pools and sweeps detection module."""

from dataclasses import dataclass
from enum import Enum

from market_structure.structure_models import SwingGraph
from market_structure.swing_models import Swing, SwingType


class LiquidityType(Enum):
    """Supported liquidity types."""

    BUY_SIDE = "BUY_SIDE"
    SELL_SIDE = "SELL_SIDE"


@dataclass(frozen=True)
class LiquidityLevel:
    """Represents an identified liquidity pool zone.

    Attributes:
        id: Unique identifier format liq_{type}_{index}.
        price: Average price of the grouped equal highs/lows.
        type: LiquidityType (BUY_SIDE / SELL_SIDE).
        source_swing_ids: List of swing IDs that constitute the level.
        is_swept: True if subsequent price has breached this level.
    """

    id: str
    price: float
    type: LiquidityType
    source_swing_ids: list[str]
    is_swept: bool


class LiquidityDetector:
    """Identifies key buy-side and sell-side liquidity pools and sweep events."""

    def __init__(self, tolerance: float = 0.0001, min_swings: int = 2) -> None:
        """Initializes the LiquidityDetector.

        Args:
            tolerance: Maximum price difference between swings to group them as equal.
            min_swings: Minimum number of equal swings required to form a liquidity pool.
        """
        self.tolerance = tolerance
        self.min_swings = min_swings

    def _cluster_swings(self, swings: list[Swing]) -> list[list[Swing]]:
        """Groups swings whose price is within tolerance."""
        if not swings:
            return []

        # Sort by price
        sorted_swings = sorted(swings, key=lambda s: s.price)
        clusters = []
        current_cluster = [sorted_swings[0]]

        for s in sorted_swings[1:]:
            # Since sorted, checking distance to the first element in current cluster
            # ensures the total spread of the cluster is <= tolerance.
            if s.price - current_cluster[0].price <= self.tolerance:
                current_cluster.append(s)
            else:
                clusters.append(current_cluster)
                current_cluster = [s]

        if current_cluster:
            clusters.append(current_cluster)

        return clusters

    def find_liquidity_pools(self, swing_graph: SwingGraph) -> list[LiquidityLevel]:
        """Locates high-probability liquidity pools (equal highs/lows).

        Args:
            swing_graph: The swing graph containing detected high/low pivot nodes.

        Returns:
            A list of LiquidityLevel objects.
        """
        pools = []
        nodes = swing_graph.nodes

        # 1. Group Highs (BUY_SIDE Liquidity)
        highs = [s for s in nodes if s.type == SwingType.HIGH]
        high_clusters = self._cluster_swings(highs)

        for idx, cluster in enumerate(high_clusters):
            if len(cluster) < self.min_swings:
                continue

            avg_price = sum(s.price for s in cluster) / len(cluster)
            latest_idx = max(s.index for s in cluster)
            source_ids = [s.id for s in cluster]

            is_swept = False
            for s in reversed(nodes):
                if s.index <= latest_idx:
                    break
                if s.price >= avg_price:
                    is_swept = True
                    break

            pools.append(
                LiquidityLevel(
                    id=f"liq_buyside_{idx}",
                    price=avg_price,
                    type=LiquidityType.BUY_SIDE,
                    source_swing_ids=source_ids,
                    is_swept=is_swept,
                )
            )

        # 2. Group Lows (SELL_SIDE Liquidity)
        lows = [s for s in nodes if s.type == SwingType.LOW]
        low_clusters = self._cluster_swings(lows)

        for idx, cluster in enumerate(low_clusters):
            if len(cluster) < self.min_swings:
                continue

            avg_price = sum(s.price for s in cluster) / len(cluster)
            latest_idx = max(s.index for s in cluster)
            source_ids = [s.id for s in cluster]

            is_swept = False
            for s in reversed(nodes):
                if s.index <= latest_idx:
                    break
                if s.price <= avg_price:
                    is_swept = True
                    break

            pools.append(
                LiquidityLevel(
                    id=f"liq_sellside_{idx}",
                    price=avg_price,
                    type=LiquidityType.SELL_SIDE,
                    source_swing_ids=source_ids,
                    is_swept=is_swept,
                )
            )

        return pools
