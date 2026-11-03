#!/usr/bin/env python3
"""Provider-neutral table sizing helpers for bounded PDF layouts."""

from __future__ import annotations


class TableLayoutError(ValueError):
    """Raised when a table cannot be sized within its declared content area."""


def bounded_column_widths(
    requested: list[float] | tuple[float, ...],
    *,
    available_width: float,
    minimum_width: float = 36.0,
) -> list[float]:
    """Scale requested widths to one content frame while retaining proportions.

    Cell values still need wrapping (for ReportLab, use ``Paragraph``).  This
    helper prevents the table grid itself from exceeding the page frame.
    """
    if not requested or available_width <= 0 or minimum_width <= 0:
        raise TableLayoutError("table widths and available width must be positive")
    widths = [float(value) for value in requested]
    if any(value <= 0 for value in widths):
        raise TableLayoutError("every requested column width must be positive")
    if minimum_width * len(widths) > available_width:
        raise TableLayoutError("the minimum column widths exceed the available content width")
    total = sum(widths)
    if total <= available_width:
        return widths
    scale = available_width / total
    scaled = [value * scale for value in widths]
    if min(scaled) >= minimum_width:
        return scaled

    fixed = {index for index, value in enumerate(scaled) if value < minimum_width}
    while fixed:
        remaining = available_width - minimum_width * len(fixed)
        flexible_total = sum(widths[index] for index in range(len(widths)) if index not in fixed)
        if flexible_total <= 0:
            raise TableLayoutError("table has no flexible width remaining")
        candidate = {
            index: widths[index] * remaining / flexible_total
            for index in range(len(widths)) if index not in fixed
        }
        newly_fixed = {index for index, value in candidate.items() if value < minimum_width}
        if not newly_fixed:
            return [minimum_width if index in fixed else candidate[index]
                    for index in range(len(widths))]
        fixed.update(newly_fixed)
    raise TableLayoutError("could not fit table columns within the content width")
