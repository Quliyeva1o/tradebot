"""Change of Character (CHoCH) identification module."""

from market_structure.structure_models import BreakType, StructureBreak


def get_choch_events(breaks_history: list[StructureBreak]) -> list[StructureBreak]:
    """Filters break history to CHoCH (trend-reversal) events only.

    Args:
        breaks_history: A list of all detected StructureBreak objects.

    Returns:
        A list containing only the StructureBreak objects of type BreakType.CHoCH.
    """
    return [b for b in breaks_history if b.break_type == BreakType.CHoCH]
