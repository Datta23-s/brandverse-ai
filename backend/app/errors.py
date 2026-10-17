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
