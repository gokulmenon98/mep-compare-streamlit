"""
Match sheets across two drawing sets by drawing number.

Produces three lists:
  - matched:   (v1_page_idx, v2_page_idx) pairs to actually compare
  - added:     drawing numbers present in v2 but not v1
  - removed:   drawing numbers present in v1 but not v2
  - unmatched: pages whose drawing number couldn't be extracted

The matching step is intentionally separate from the comparison step.
You should always look at the match report before trusting any diff
output — if half your sheets are "unmatched" because of bad calibration,
no amount of clever diffing will save the result.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from .identify import SheetID


@dataclass
class MatchReport:
    matched: list[tuple[SheetID, SheetID]] = field(default_factory=list)
    added_in_v2: list[SheetID] = field(default_factory=list)
    removed_from_v1: list[SheetID] = field(default_factory=list)
    unmatched_v1: list[SheetID] = field(default_factory=list)  # extraction failed
    unmatched_v2: list[SheetID] = field(default_factory=list)
    duplicate_in_v1: list[str] = field(default_factory=list)   # same dwg # on multiple pages
    duplicate_in_v2: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Matched pairs:        {len(self.matched)}",
            f"Added in v2:          {len(self.added_in_v2)}",
            f"Removed from v1:      {len(self.removed_from_v1)}",
            f"Unmatched (v1 fail):  {len(self.unmatched_v1)}",
            f"Unmatched (v2 fail):  {len(self.unmatched_v2)}",
        ]
        if self.duplicate_in_v1:
            lines.append(f"Duplicates in v1:     {', '.join(self.duplicate_in_v1)}")
        if self.duplicate_in_v2:
            lines.append(f"Duplicates in v2:     {', '.join(self.duplicate_in_v2)}")
        return "\n".join(lines)


def match_sheets(v1: list[SheetID], v2: list[SheetID]) -> MatchReport:
    """Match sheets between two sets by drawing number."""
    report = MatchReport()

    # Partition extraction failures up front.
    v1_ok = [s for s in v1 if s.drawing_number]
    v2_ok = [s for s in v2 if s.drawing_number]
    report.unmatched_v1 = [s for s in v1 if not s.drawing_number]
    report.unmatched_v2 = [s for s in v2 if not s.drawing_number]

    # Build lookup dicts; track duplicates as a data-quality signal.
    # Duplicates usually mean the calibration is grabbing a referenced
    # drawing number from the sheet body instead of the title block.
    def build_index(sheets: list[SheetID]) -> tuple[dict[str, SheetID], list[str]]:
        index: dict[str, SheetID] = {}
        dupes: list[str] = []
        for s in sheets:
            assert s.drawing_number is not None
            if s.drawing_number in index:
                dupes.append(s.drawing_number)
            else:
                index[s.drawing_number] = s
        return index, dupes

    v1_idx, report.duplicate_in_v1 = build_index(v1_ok)
    v2_idx, report.duplicate_in_v2 = build_index(v2_ok)

    v1_keys = set(v1_idx)
    v2_keys = set(v2_idx)

    for dwg in sorted(v1_keys & v2_keys):
        report.matched.append((v1_idx[dwg], v2_idx[dwg]))
    report.added_in_v2 = [v2_idx[k] for k in sorted(v2_keys - v1_keys)]
    report.removed_from_v1 = [v1_idx[k] for k in sorted(v1_keys - v2_keys)]

    return report
