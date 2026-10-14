"""Domain exceptions (Rules.md §3.1: never raise bare Exception)."""


class BrandverseError(Exception):
    """Base class for all domain errors."""


class ApprovalNotPermitted(BrandverseError):
    """The actor may not approve this campaign (Rules.md T6)."""
