"""Domain exceptions (Rules.md §3.1: never raise bare Exception)."""


class BrandverseError(Exception):
    """Base class for all domain errors."""


class ApprovalNotPermitted(BrandverseError):
    """The actor may not approve this campaign (Rules.md T6)."""


class AuthenticationFailed(BrandverseError):
    """The request carries no valid Supabase access token. Maps to 401."""


class RunEventCorrupt(BrandverseError):
    """A run-progress stream holds an entry that is not a valid run event."""


class JobTimedOut(BrandverseError):
    """A run's job reached its own deadline (just inside ARQ's); the run was ended `failed`.
    Deliberately not a `TimeoutError`: that is classified `network_error`, and a job timeout is
    `process_crash` in the attempt rule (app/core/infrastructure.py)."""


class StepTimeout(BrandverseError):
    """A pipeline step exceeded its own timeout; the message names the step."""


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


class ProviderUnavailable(LLMCallFailed):
    """The provider answered with a server-side unavailability (503 "high demand", 500, 502, 504,
    any 5xx) on every attempt the gateway was allowed. The fifth infrastructure-failure kind,
    `provider_unavailable` (amendment of 2026-11-05, docs/evaluation-plan.md): a property of the
    provider, not of the arm being measured. Recorded as provider_unavailable, bounded-retried."""


class ProviderCircuitOpen(LLMError):
    """Three consecutive provider-unavailable responses in one run: the run is aborted as an
    infrastructure failure instead of grinding through its budget against an outage (2026-11-05:
    35 errored calls in 20 minutes). Raised for every later call in that run as well."""


class StructuredOutputInvalid(LLMError):
    """The model's output did not validate against the schema, even after one repair."""


class StrongTierDisabled(CallRefused):
    """The strong tier is switched off (llm_allow_strong_tier is false), or enabled for another
    organisation only (llm_strong_tier_org_id): development, tests and smoke calls never spend its
    20 requests/day."""


class StrongTierCapReached(CallRefused):
    """The organisation has made all the strong-tier calls it was approved for
    (llm_strong_tier_max_calls). Refused at admission, under the organisation's lock, so two
    concurrent calls cannot both take the last place."""


class DevBudgetRefused(CallRefused):
    """The development ceiling (config/dev_budget.yaml) refused the call: the billing condition is
    not confirmed, the database is not registered, or the ceiling is spent. `kind` says which."""

    def __init__(self, message: str, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


class EvaluationBudgetRefused(DevBudgetRefused):
    """The EVALUATION's own budget (config/evaluation_budget.yaml) refused the call. A subclass of
    the development refusal so that every site that already ends a run on that refusal ends it on
    this one, but a different scope: the development ceiling never raises it."""


# What must END a run instead of being turned into a "declared gap" by a node that catches a
# failed agent. ONE name, used at every catch site in the orchestrator, so a new abort cannot be
# swallowed at a site that forgot it (tests/test_provider_unavailable.py scans for the old form).
ABORTS: tuple[type[BaseException], ...] = (BudgetExceeded, ProviderCircuitOpen)


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
