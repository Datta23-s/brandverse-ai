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


class RegistryRuleInvalid(BrandverseError):
    """A sector registry rule cannot be applied (bad regex, unknown match type). Gates block."""


# ── LLM calls (Phase 2) ──────────────────────────────────────────────────────────────────────


class LLMError(BrandverseError):
    """A model call did not produce a usable, validated result."""


class CallRefused(LLMError):
    """The pre-flight check refused the call; the refusal and its reason are in llm_calls."""


class BudgetExceeded(CallRefused):
    """The run's caps (Rules.md C3: calls, list-price-equivalent USD) would be breached."""


class QuotaExhausted(CallRefused):
    """The model's daily request quota (Rules.md C9) is used up until the next reset."""


class LLMRateLimited(LLMError):
    """Rate-limit waits would exceed their cap (Rules.md C10); recorded as rate_limited."""


class LLMCallFailed(LLMError):
    """The provider call failed (not a rate limit); recorded as error, not retried."""


class StructuredOutputInvalid(LLMError):
    """The model's output did not validate against the schema, even after one repair."""


class StrongTierDisabled(CallRefused):
    """The strong tier is switched off (llm_allow_strong_tier is false): development, tests and
    smoke calls never spend its 20 requests/day."""


# ── Research (Phase 5) ───────────────────────────────────────────────────────────────────────


class SearchError(BrandverseError):
    """A search could not be completed. Research turns it into a partial result, never a crash."""


class SearchUnavailable(SearchError):
    """The search engine cannot be reached or did not answer with a result list."""


class SearchRateLimited(SearchError):
    """The search engine asked the caller to slow down."""


class FetchError(BrandverseError):
    """A page could not be fetched. Research drops the page and goes on with the others.

    `kind` names the specific reason, so a skipped page is visibly skipped and says why (the run
    report counts failures by it); None when the class says it all."""

    def __init__(self, message: str, *, kind: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind


class FetchRefused(FetchError):
    """The fetcher's own policy refused the URL before or while fetching it. `kind`: "scheme",
    "credentials", "host", "port", "address" (not on the public internet, at any hop of a redirect
    chain), "redirects" (too many), or "robots" (the site's robots.txt disallows it for this
    fetcher). Nothing was requested from the refused page or address."""


class FetchFailed(FetchError):
    """The page was requested and did not arrive: the network failed or timed out, the host did not
    resolve, robots.txt could not be retrieved, or the server answered with an error status
    (`status`, None when there was no HTTP answer)."""

    def __init__(self, message: str, *, status: int | None = None, kind: str | None = None) -> None:
        super().__init__(message, kind=kind)
        self.status = status


class FetchUnsupported(FetchError):
    """The server answered, or the URL says, that this is not something the pipeline takes as a
    page. `kind`: "pdf" (not supported yet: skipped on purpose, and counted so the cost of that is
    measured), "not_html", "no_content_type", or "too_large" (a body over the size cap)."""


class ExtractionFailed(BrandverseError):
    """A page yielded no usable main content (a login wall, a listing, boilerplate only)."""
