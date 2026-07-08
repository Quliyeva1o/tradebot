"""Break of Structure (BOS) identification module."""

from market_structure.structure_models import BreakType, StructureBreak


def get_bos_events(breaks_history: list[StructureBreak]) -> list[StructureBreak]:
    """Filters break history to BOS (trend-continuation) events only.

    Args:
        breaks_history: A list of all detected StructureBreak objects.

    Returns:
        A list containing only the StructureBreak objects of type BreakType.BOS.
    """
    return [b for b in breaks_history if b.break_type == BreakType.BOS]
