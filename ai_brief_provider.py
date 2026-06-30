from __future__ import annotations

import os
import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from ai_brief_grounding import (
    GroundingError,
    brief_grounding_metadata,
    generation_allowed,
    verify_grounding_hash,
)


FEATURE_FLAG_ENV = "THESISOS_AI_BRIEF_ENABLED"
PROVIDER_ENV = "THESISOS_AI_BRIEF_PROVIDER"
MODEL_ENV = "THESISOS_AI_BRIEF_MODEL"
API_KEY_ENV = "THESISOS_AI_BRIEF_API_KEY"
GROQ_API_KEY_ENV = "THESISOS_AI_BRIEF_GROQ_API_KEY"
TIMEOUT_ENV = "THESISOS_AI_BRIEF_TIMEOUT_SECONDS"

DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_TIMEOUT_SECONDS = 60.0
MAX_TEXT_CHARS = 4000
MAX_LIST_ITEMS = 20

ALLOWED_SECTION_KEYS = {
    "executive_summary",
    "thesis",
    "strengths",
    "risks",
    "valuation",
    "technical",
    "portfolio_fit",
    "catalysts",
    "invalidation_signals",
    "next_review",
}

LIST_SECTION_KEYS = {
    "thesis",
    "strengths",
    "risks",
    "catalysts",
    "invalidation_signals",
}

TEXT_SECTION_KEYS = ALLOWED_SECTION_KEYS - LIST_SECTION_KEYS

ALLOWED_DECISION_ACTIONS = {
    "buy",
    "initiate_small",
    "accumulate",
    "hold",
    "watch",
    "reduce",
    "avoid",
    "sell",
    "unavailable",
}

ALLOWED_PROVIDER_CONTENT_KEYS = {
    "sections",
    "decision",
    "limitations",
}

PORTFOLIO_FIT_UNAVAILABLE = (
    "Unavailable: portfolio grounding is missing."
)
POSITION_SIZING_UNAVAILABLE = (
    "Unavailable: portfolio grounding is missing."
)
NEXT_REVIEW_UNAVAILABLE = (
    "Unavailable: no explicit grounded review trigger was found."
)
MISSING_PORTFOLIO_LIMITATION = (
    "Portfolio fit and position sizing are unavailable because "
    "portfolio grounding is missing."
)
UNSUPPORTED_CATALYST_LIMITATION = (
    "Unsupported model-generated catalysts were removed."
)
UNSUPPORTED_NEXT_REVIEW_LIMITATION = (
    "The model-proposed next review trigger was removed because "
    "it was not supported by grounding."
)

UNSUPPORTED_NARRATIVE_CLAIM_LIMITATION = (
    "Unsupported corporate-action statements were removed from "
    "narrative fields."
)
UNSUPPORTED_PORTFOLIO_LANGUAGE_LIMITATION = (
    "Portfolio-specific allocation or sizing language was removed "
    "from narrative fields because portfolio grounding is missing."
)
UNSUPPORTED_CLAIM_FALLBACK = (
    "Unavailable: no supported statement remained after grounding "
    "validation."
)

_CORPORATE_ACTION_PATTERN = re.compile(
    r"\b("
    r"share[- ]?repurchase(?: program)?|"
    r"repurchase program|"
    r"buyback|"
    r"dividend increase|"
    r"increase(?:d|s|ing)? dividend"
    r")\b",
    re.IGNORECASE,
)

_PORTFOLIO_ADVICE_PATTERN = re.compile(
    r"\b("
    r"defensive allocation|"
    r"cautious sizing|"
    r"position sizing|"
    r"sizable positions?|"
    r"full[- ]?size entry|"
    r"allocate(?:d|s|ing)?\b|"
    r"concentration limits?"
    r")\b",
    re.IGNORECASE,
)

_NARRATIVE_LIST_SECTIONS = (
    "thesis",
    "strengths",
    "risks",
    "invalidation_signals",
)

_NARRATIVE_TEXT_SECTIONS = (
    "executive_summary",
    "valuation",
    "technical",
)

_CLAIM_STOPWORDS = {
    "a", "an", "and", "after", "before", "could", "event",
    "events", "for", "from", "in", "into", "is", "latest",
    "may", "next", "of", "on", "or", "pending", "potential",
    "review", "the", "to", "with",
}


class AiBriefProviderError(RuntimeError):
    pass


class ProviderUnavailableError(AiBriefProviderError):
    pass


class ProviderGenerationError(AiBriefProviderError):
    pass


class ProviderContractError(AiBriefProviderError):
    pass


@dataclass(frozen=True)
class ProviderConfig:
    enabled: bool
    provider: str | None
    model: str | None
    timeout_seconds: float
    api_key_configured: bool

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "ProviderConfig":
        source = os.environ if environ is None else environ

        enabled = _parse_bool(source.get(FEATURE_FLAG_ENV))
        provider = _clean_optional(source.get(PROVIDER_ENV))
        model = _clean_optional(source.get(MODEL_ENV))
        timeout_seconds = _parse_timeout(
            source.get(TIMEOUT_ENV)
        )
        provider_name = str(provider or "").strip().lower()
        api_key_env = (
            GROQ_API_KEY_ENV
            if provider_name == "groq"
            else API_KEY_ENV
        )
        api_key_configured = bool(
            _clean_optional(source.get(api_key_env))
        )

        return cls(
            enabled=enabled,
            provider=provider,
            model=model,
            timeout_seconds=timeout_seconds,
            api_key_configured=api_key_configured,
        )


@dataclass(frozen=True)
class ProviderResult:
    content: Mapping[str, Any]
    provider: str
    model: str
    request_id: str | None = None


class AiBriefProvider(Protocol):
    def generate(
        self,
        grounding_bundle: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> ProviderResult:
        ...


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None

    cleaned = str(value).strip()
    return cleaned or None


def _parse_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }


def _parse_timeout(value: Any) -> float:
    if value is None or str(value).strip() == "":
        return DEFAULT_TIMEOUT_SECONDS

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS

    if parsed <= 0:
        return DEFAULT_TIMEOUT_SECONDS

    return min(parsed, MAX_TIMEOUT_SECONDS)


def provider_configuration(
    environ: Mapping[str, str] | None = None,
) -> dict:
    config = ProviderConfig.from_env(environ)

    return {
        "enabled": config.enabled,
        "provider": config.provider,
        "model": config.model,
        "timeout_seconds": config.timeout_seconds,
        "api_key_configured": config.api_key_configured,
    }


def _text(value: Any) -> str | None:
    if value is None:
        return None

    cleaned = str(value).strip()
    return cleaned[:MAX_TEXT_CHARS] if cleaned else None


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []

    result = []

    for item in list(value)[:MAX_LIST_ITEMS]:
        cleaned = _text(item)

        if cleaned:
            result.append(cleaned)

    return result


def _meaningful(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _normalized_text(value: Any) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    without_marks = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )
    return " ".join(without_marks.casefold().split())


def _claim_tokens(value: Any) -> set[str]:
    tokens = set()
    for token in re.findall(r"[a-z0-9]+", _normalized_text(value)):
        if token in _CLAIM_STOPWORDS:
            continue
        if token.isdigit() or len(token) >= 4:
            tokens.add(token)
    return tokens


def _walk_strings(value: Any):
    if isinstance(value, Mapping):
        for child in value.values():
            yield from _walk_strings(child)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_strings(child)
        return
    if isinstance(value, str) and value.strip():
        yield value


def _grounded_event_strings(
    bundle: Mapping[str, Any],
) -> list[str]:
    grounded = bundle.get("grounded_data")
    if not isinstance(grounded, Mapping):
        grounded = {}

    evidence = grounded.get("evidence")
    candidates: list[Any] = []

    if isinstance(evidence, Mapping):
        for key in (
            "events",
            "material_events",
            "latest_event",
            "latest_official_filing",
            "review_reason",
        ):
            candidates.append(evidence.get(key))

    references = bundle.get("evidence_refs")
    if isinstance(references, list):
        for reference in references:
            if not isinstance(reference, Mapping):
                continue
            candidates.extend(
                (reference.get("title"), reference.get("as_of"))
            )

    result = []
    observed = set()

    for candidate in candidates:
        for text in _walk_strings(candidate):
            cleaned = text.strip()
            if cleaned in observed:
                continue
            observed.add(cleaned)
            result.append(cleaned)

    return result


def _claim_supported(
    claim: Any,
    sources: list[str],
) -> bool:
    cleaned = _text(claim)
    if not cleaned:
        return False

    normalized_claim = _normalized_text(cleaned)
    claim_tokens = _claim_tokens(cleaned)
    if not claim_tokens:
        return False

    numeric_tokens = {
        token for token in claim_tokens if token.isdigit()
    }

    for source in sources:
        normalized_source = _normalized_text(source)

        if (
            len(normalized_claim) >= 12
            and normalized_claim in normalized_source
        ):
            return True

        if (
            len(normalized_source) >= 12
            and normalized_source in normalized_claim
        ):
            return True

        source_tokens = _claim_tokens(source)

        if (
            numeric_tokens
            and not numeric_tokens.issubset(source_tokens)
        ):
            continue

        matched = claim_tokens & source_tokens
        required = 1 if len(claim_tokens) == 1 else 2
        ratio = len(matched) / len(claim_tokens)

        if len(matched) >= required and ratio >= 0.60:
            return True

    return False


def _append_limitation(
    limitations: list[str],
    value: str,
) -> None:
    if value not in limitations:
        limitations.append(value)


def _split_sentences(value: Any) -> list[str]:
    cleaned = _text(value)

    if not cleaned:
        return []

    return [
        sentence.strip()
        for sentence in re.split(
            r"(?<=[.!?])\s+",
            cleaned,
        )
        if sentence.strip()
    ]


def _sanitize_narrative_text(
    value: Any,
    support_strings: list[str],
    *,
    portfolio_available: bool,
) -> tuple[str, bool, bool]:
    retained = []
    removed_corporate = False
    removed_portfolio = False

    for sentence in _split_sentences(value):
        if (
            _CORPORATE_ACTION_PATTERN.search(sentence)
            and not _claim_supported(
                sentence,
                support_strings,
            )
        ):
            removed_corporate = True
            continue

        if (
            not portfolio_available
            and _PORTFOLIO_ADVICE_PATTERN.search(sentence)
        ):
            removed_portfolio = True
            continue

        retained.append(sentence)

    return (
        " ".join(retained).strip(),
        removed_corporate,
        removed_portfolio,
    )


def _sanitize_generated_narratives(
    sections: dict,
    decision: dict,
    limitations: list[str],
    support_strings: list[str],
    *,
    portfolio_available: bool,
) -> tuple[dict, dict, list[str]]:
    removed_corporate = False
    removed_portfolio = False

    for key in _NARRATIVE_LIST_SECTIONS:
        retained_items = []

        for item in sections.get(key) or []:
            (
                cleaned,
                item_corporate,
                item_portfolio,
            ) = _sanitize_narrative_text(
                item,
                support_strings,
                portfolio_available=portfolio_available,
            )

            removed_corporate = (
                removed_corporate or item_corporate
            )
            removed_portfolio = (
                removed_portfolio or item_portfolio
            )

            if cleaned:
                retained_items.append(cleaned)

        sections[key] = retained_items

    for key in _NARRATIVE_TEXT_SECTIONS:
        (
            cleaned,
            item_corporate,
            item_portfolio,
        ) = _sanitize_narrative_text(
            sections.get(key),
            support_strings,
            portfolio_available=portfolio_available,
        )

        removed_corporate = (
            removed_corporate or item_corporate
        )
        removed_portfolio = (
            removed_portfolio or item_portfolio
        )

        if not cleaned and (
            item_corporate or item_portfolio
        ):
            cleaned = UNSUPPORTED_CLAIM_FALLBACK

        sections[key] = cleaned

    for key in ("rationale", "entry_zone"):
        (
            cleaned,
            item_corporate,
            item_portfolio,
        ) = _sanitize_narrative_text(
            decision.get(key),
            support_strings,
            portfolio_available=portfolio_available,
        )

        removed_corporate = (
            removed_corporate or item_corporate
        )
        removed_portfolio = (
            removed_portfolio or item_portfolio
        )

        if not cleaned and (
            item_corporate or item_portfolio
        ):
            cleaned = UNSUPPORTED_CLAIM_FALLBACK

        decision[key] = cleaned

    if removed_corporate:
        _append_limitation(
            limitations,
            UNSUPPORTED_NARRATIVE_CLAIM_LIMITATION,
        )

    if removed_portfolio:
        _append_limitation(
            limitations,
            UNSUPPORTED_PORTFOLIO_LANGUAGE_LIMITATION,
        )

    return sections, decision, limitations

def _enforce_grounding_contract(
    grounding_bundle: Mapping[str, Any],
    sections: dict,
    decision: dict,
    limitations: list[str],
) -> tuple[dict, dict, list[str]]:
    grounded = grounding_bundle.get("grounded_data")
    if not isinstance(grounded, Mapping):
        grounded = {}

    if not _meaningful(grounded.get("portfolio")):
        sections["portfolio_fit"] = PORTFOLIO_FIT_UNAVAILABLE
        decision["position_sizing"] = POSITION_SIZING_UNAVAILABLE
        _append_limitation(
            limitations,
            MISSING_PORTFOLIO_LIMITATION,
        )

    support_strings = _grounded_event_strings(grounding_bundle)
    retained_catalysts = []
    removed_catalysts = 0

    for catalyst in sections.get("catalysts") or []:
        if _claim_supported(catalyst, support_strings):
            retained_catalysts.append(catalyst)
        else:
            removed_catalysts += 1

    sections["catalysts"] = retained_catalysts

    if removed_catalysts:
        _append_limitation(
            limitations,
            UNSUPPORTED_CATALYST_LIMITATION,
        )

    next_review = sections.get("next_review")

    if not _claim_supported(next_review, support_strings):
        sections["next_review"] = NEXT_REVIEW_UNAVAILABLE
        if next_review:
            _append_limitation(
                limitations,
                UNSUPPORTED_NEXT_REVIEW_LIMITATION,
            )

    portfolio_available = _meaningful(
        grounded.get("portfolio")
    )

    sections, decision, limitations = (
        _sanitize_generated_narratives(
            sections,
            decision,
            limitations,
            support_strings,
            portfolio_available=portfolio_available,
        )
    )

    return sections, decision, limitations

def _empty_sections() -> dict:
    return {
        "executive_summary": None,
        "thesis": [],
        "strengths": [],
        "risks": [],
        "valuation": None,
        "technical": None,
        "portfolio_fit": None,
        "catalysts": [],
        "invalidation_signals": [],
        "next_review": None,
    }


def _empty_decision() -> dict:
    return {
        "action": "unavailable",
        "confidence": 0,
        "rationale": None,
        "position_sizing": None,
        "entry_zone": None,
    }


def _uncertainty(bundle: Mapping[str, Any]) -> dict:
    coverage = bundle.get("coverage") or {}
    missing_critical = list(
        coverage.get("missing_critical_fields") or []
    )
    missing_optional = list(
        coverage.get("missing_optional_fields") or []
    )

    reasons = []

    if missing_critical:
        reasons.append(
            "Critical ThesisOS grounding fields are missing."
        )

    if missing_optional:
        reasons.append(
            "Some optional ThesisOS grounding fields are missing."
        )

    if not reasons:
        reasons.append(
            "No critical or optional grounding fields are missing."
        )

    return {
        "level": coverage.get(
            "uncertainty_level",
            "unknown",
        ),
        "reasons": reasons,
        "missing_fields": sorted(
            {
                *missing_critical,
                *missing_optional,
            }
        ),
    }


def _base_brief(
    bundle: Mapping[str, Any],
    *,
    status: str,
    generated_at: str | None,
    error: dict | None,
    model: dict | None,
    limitations: list[str],
) -> dict:
    timestamp = (
        _clean_optional(generated_at)
        or _clean_optional(
            bundle.get("analysis_generated_at")
        )
        or "1970-01-01T00:00:00Z"
    )

    payload_hash = str(bundle["payload_sha256"])

    return {
        "schema_version": "1.0",
        "brief_id": f"brief-{payload_hash[:20]}",
        "status": status,
        "asset": deepcopy(bundle["asset"]),
        "generated_at": timestamp,
        "grounding": brief_grounding_metadata(bundle),
        "uncertainty": _uncertainty(bundle),
        "sections": _empty_sections(),
        "decision": _empty_decision(),
        "model": model,
        "error": error,
        "limitations": limitations,
    }


def _disabled_brief(
    bundle: Mapping[str, Any],
    generated_at: str | None,
) -> dict:
    return _base_brief(
        bundle,
        status="disabled",
        generated_at=generated_at,
        error=None,
        model=None,
        limitations=[
            "AI Investment Brief is disabled by configuration.",
            "No provider was called.",
        ],
    )


def _insufficient_data_brief(
    bundle: Mapping[str, Any],
    generated_at: str | None,
) -> dict:
    return _base_brief(
        bundle,
        status="insufficient_data",
        generated_at=generated_at,
        error=None,
        model=None,
        limitations=[
            "Critical ThesisOS grounding data is missing.",
            "No provider was called.",
        ],
    )


def _error_brief(
    bundle: Mapping[str, Any],
    *,
    status: str,
    generated_at: str | None,
    code: str,
    message: str,
    retryable: bool,
) -> dict:
    return _base_brief(
        bundle,
        status=status,
        generated_at=generated_at,
        error={
            "code": code,
            "message": _text(message)
            or "AI Brief provider error.",
            "retryable": bool(retryable),
        },
        model=None,
        limitations=[
            "No provider output was accepted.",
            "The ThesisOS grounding bundle remains available.",
        ],
    )


def _validate_provider_content(
    content: Mapping[str, Any],
) -> tuple[dict, dict, list[str]]:
    if not isinstance(content, Mapping):
        raise ProviderContractError(
            "provider content must be a mapping"
        )

    extra = set(content) - ALLOWED_PROVIDER_CONTENT_KEYS

    if extra:
        raise ProviderContractError(
            "provider content contains unsupported fields"
        )

    raw_sections = content.get("sections")

    if not isinstance(raw_sections, Mapping):
        raise ProviderContractError(
            "provider sections must be a mapping"
        )

    if set(raw_sections) - ALLOWED_SECTION_KEYS:
        raise ProviderContractError(
            "provider sections contain unsupported fields"
        )

    sections = _empty_sections()

    for key in LIST_SECTION_KEYS:
        sections[key] = _text_list(raw_sections.get(key))

    for key in TEXT_SECTION_KEYS:
        sections[key] = _text(raw_sections.get(key))

    if not sections["executive_summary"]:
        raise ProviderContractError(
            "provider executive summary is required"
        )

    raw_decision = content.get("decision")

    if not isinstance(raw_decision, Mapping):
        raise ProviderContractError(
            "provider decision must be a mapping"
        )

    action = _clean_optional(raw_decision.get("action"))

    if action not in ALLOWED_DECISION_ACTIONS:
        raise ProviderContractError(
            "provider decision action is invalid"
        )

    confidence = raw_decision.get("confidence")

    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, int)
        or not 0 <= confidence <= 100
    ):
        raise ProviderContractError(
            "provider confidence must be an integer from 0 to 100"
        )

    decision = {
        "action": action,
        "confidence": confidence,
        "rationale": _text(raw_decision.get("rationale")),
        "position_sizing": _text(
            raw_decision.get("position_sizing")
        ),
        "entry_zone": _text(
            raw_decision.get("entry_zone")
        ),
    }

    limitations = _text_list(content.get("limitations"))

    return sections, decision, limitations


def generate_ai_brief(
    grounding_bundle: Mapping[str, Any],
    config: ProviderConfig,
    provider: AiBriefProvider | None = None,
    *,
    generated_at: str | None = None,
) -> dict:
    if not isinstance(grounding_bundle, Mapping):
        raise ProviderContractError(
            "grounding bundle must be a mapping"
        )

    if not verify_grounding_hash(grounding_bundle):
        raise ProviderContractError(
            "grounding bundle hash is invalid"
        )

    if not config.enabled:
        return _disabled_brief(
            grounding_bundle,
            generated_at,
        )

    if not generation_allowed(grounding_bundle):
        return _insufficient_data_brief(
            grounding_bundle,
            generated_at,
        )

    if (
        not config.provider
        or not config.model
        or not config.api_key_configured
        or provider is None
    ):
        return _error_brief(
            grounding_bundle,
            status="provider_unavailable",
            generated_at=generated_at,
            code="provider_not_configured",
            message=(
                "AI Brief provider configuration is incomplete."
            ),
            retryable=False,
        )

    try:
        result = provider.generate(
            deepcopy(dict(grounding_bundle)),
            timeout_seconds=config.timeout_seconds,
        )
    except ProviderUnavailableError as exc:
        return _error_brief(
            grounding_bundle,
            status="provider_unavailable",
            generated_at=generated_at,
            code="provider_unavailable",
            message=str(exc),
            retryable=True,
        )
    except Exception as exc:
        return _error_brief(
            grounding_bundle,
            status="generation_failed",
            generated_at=generated_at,
            code="generation_failed",
            message=str(exc),
            retryable=False,
        )

    try:
        sections, decision, limitations = (
            _validate_provider_content(result.content)
        )
        sections, decision, limitations = (
            _enforce_grounding_contract(
                grounding_bundle,
                sections,
                decision,
                limitations,
            )
        )
    except ProviderContractError as exc:
        return _error_brief(
            grounding_bundle,
            status="generation_failed",
            generated_at=generated_at,
            code="invalid_provider_response",
            message=str(exc),
            retryable=False,
        )

    coverage = grounding_bundle.get("coverage") or {}
    status = (
        "partial"
        if coverage.get("status") == "partial"
        else "ready"
    )

    brief = _base_brief(
        grounding_bundle,
        status=status,
        generated_at=generated_at,
        error=None,
        model={
            "provider": _text(result.provider)
            or config.provider,
            "model": _text(result.model)
            or config.model,
            "request_id": _text(result.request_id),
        },
        limitations=limitations,
    )

    brief["sections"] = sections
    brief["decision"] = decision

    return brief
