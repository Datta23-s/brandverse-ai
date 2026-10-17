"""Domain exceptions (Rules.md §3.1: never raise bare Exception)."""


class BrandverseError(Exception):
    """Base class for all domain errors."""


class ApprovalNotPermitted(BrandverseError):
    """The actor may not approve this campaign (Rules.md T6)."""


class AuthenticationFailed(BrandverseError):
    """The request carries no valid Supabase access token. Maps to 401."""


class RunEventCorrupt(BrandverseError):
    """A run-progress stream holds an entry that is not a valid run event."""


class JobNotEnqueued(BrandverseError):
    """ARQ refused to enqueue a job (a job with the same ID already exists)."""
