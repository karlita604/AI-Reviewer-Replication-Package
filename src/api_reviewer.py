"""
API-based reviewer for closed, production-grade models (Anthropic Claude).

Mirrors src/reviewer.py's `Reviewer` interface — review(task, description, diff)
-> ReviewResult — using the SAME system prompt, the SAME context builder, the
SAME structured JSON schema, and the SAME parsing logic, so a closed-model review
is byte-for-byte comparable in format to an open-weight one. The only real
differences are that inference happens over the Anthropic API instead of a local
HuggingFace pipeline, and that Anthropic takes the system prompt as a top-level
argument rather than as a message with role "system".

Design choices are locked in docs/FRONTIER-PLAN.md:
  - full set of four conditions, 5 runs, temperature 0.7;
  - the verdict-free bug-question probe is NOT run for the API model (the API
    does not expose token log-probabilities), so this module implements the
    review path only;
  - the Anthropic API accepts no seed, so individual trials are not
    bit-reproducible. run_experiment writes the full response to the raw tree,
    which keeps every trial auditable; the five runs per cell still estimate the
    per-cell approval probability and separate framing from sampling noise.

Requires: `pip install anthropic`, and ANTHROPIC_API_KEY in the environment.
"""

from __future__ import annotations

import json
import random
import re
import time

from omegaconf import DictConfig

# Reuse the exact prompts, context builder, and result type from the open-weight
# reviewer so the two paths cannot drift apart.
from src.reviewer import (
    ANSWER_ORDER_PROMPTS,
    build_context,
    ReviewResult,
)


def parse_review(raw: str, valid_verdicts: set[str]) -> ReviewResult:
    """
    Identical to Reviewer._parse in src/reviewer.py: pull the JSON object out of
    the model's response (it may be wrapped in prose or markdown), normalise the
    verdict against the allowed set, and join the problems list into a string for
    CSV compatibility. Kept as a module function so both reviewers share it.
    """
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group())
            verdict = str(data.get("verdict", "")).strip().lower().replace(" ", "_")
            if verdict not in valid_verdicts:
                verdict = "insufficient_information"
            problems = data.get("problems_found", [])
            if isinstance(problems, list):
                problems = "; ".join(problems)
            return ReviewResult(
                verdict=verdict,
                confidence=str(data.get("confidence", "")).strip().lower(),
                problems_found=str(problems),
                main_reason=str(data.get("main_reason", "")),
                suggested_changes=str(data.get("suggested_changes", "") or ""),
                raw_output=raw,
            )
        except json.JSONDecodeError:
            pass

    return ReviewResult(
        verdict="insufficient_information",
        confidence="",
        problems_found="",
        main_reason="[parse error — see raw_output]",
        suggested_changes="",
        raw_output=raw,
    )


class APIReviewer:
    """Anthropic-API reviewer with the same interface as Reviewer."""

    def __init__(self, cfg: DictConfig):
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover - environment guard
            raise ImportError(
                "APIReviewer needs the 'anthropic' package: pip install anthropic"
            ) from e

        m = cfg.model
        self._anthropic = anthropic
        # Own the retry loop ourselves (below) for visible logging/backoff, so
        # disable the SDK's built-in retries to avoid compounding the two.
        # Anthropic() reads ANTHROPIC_API_KEY from the environment.
        self.client = anthropic.Anthropic(max_retries=0)
        self.model_name = m.name
        self.max_tokens = int(m.get("max_new_tokens", 512))
        self.temperature = float(m.get("temperature", 0.7))
        self.valid_verdicts = set(cfg.experiment.valid_verdicts)

        # Retry only transient failures (connection drops, timeouts, 429s, 5xx
        # incl. 529 overloaded). Terminal errors — a 400 credit-balance message,
        # 401 auth, 403 permission — are re-raised immediately so the run stops
        # cleanly rather than spinning; already_done makes the resume cheap.
        self.max_retries = int(m.get("api_max_retries", 5))
        self.retry_base_delay = float(m.get("api_retry_base_delay", 2.0))
        self._retryable = (
            anthropic.APIConnectionError,   # includes APITimeoutError
            anthropic.RateLimitError,       # 429
            anthropic.InternalServerError,  # 5xx, includes 529 overloaded
        )

        # The closed-model extension uses the main-study neutral prompt only; the
        # answer-order and mitigation prompt variants are open-weight experiments.
        order = cfg.experiment.get("answer_order", "verdict_first")
        if order != "verdict_first":
            raise ValueError(
                f"APIReviewer is locked to answer_order=verdict_first; got {order!r}"
            )
        self.system_prompt = ANSWER_ORDER_PROMPTS[order]
        self.context_order = cfg.experiment.get("context_order", "description_first")
        # The frontier extension always shows the description (no diff-only arm).
        self.include_description = True
        print(
            f"[api-reviewer] ready. model={self.model_name} "
            f"temperature={self.temperature} answer_order={order} "
            f"context_order={self.context_order}",
            flush=True,
        )

    def _create_with_retry(self, user: str):
        """
        Call the Anthropic API, retrying transient failures with exponential
        backoff + jitter. Terminal errors propagate so the run aborts cleanly
        (and resumes via already_done once the cause — e.g. credits — is fixed).
        """
        attempt = 0
        while True:
            try:
                return self.client.messages.create(
                    model=self.model_name,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    system=self.system_prompt,        # top-level, not a message role
                    messages=[{"role": "user", "content": user}],
                )
            except self._retryable as e:
                attempt += 1
                if attempt > self.max_retries:
                    print(
                        f"  [api-retry] giving up after {self.max_retries} retries: "
                        f"{type(e).__name__}: {e}",
                        flush=True,
                    )
                    raise
                delay = self.retry_base_delay * (2 ** (attempt - 1))
                delay += random.uniform(0, delay)  # full jitter
                print(
                    f"  [api-retry] {type(e).__name__} (attempt {attempt}/"
                    f"{self.max_retries}); sleeping {delay:.1f}s",
                    flush=True,
                )
                time.sleep(delay)

    def review(self, task: str, description: str, diff: str) -> ReviewResult:
        user = build_context(
            task,
            description,
            diff,
            order=self.context_order,
            include_description=self.include_description,
        )
        resp = self._create_with_retry(user)
        raw = "".join(
            block.text for block in resp.content
            if getattr(block, "type", None) == "text"
        )
        return parse_review(raw, self.valid_verdicts)


def make_reviewer(cfg: DictConfig):
    """
    Factory used by run_experiment: return the API reviewer when the model config
    sets `api`, otherwise the local HuggingFace reviewer. This keeps every
    open-weight code path untouched.
    """
    if cfg.model.get("api"):
        return APIReviewer(cfg)
    from src.reviewer import Reviewer
    return Reviewer(cfg)
