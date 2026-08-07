"""Hardened v2 reason codes (additive to the frozen v1 codes).

v2 keeps every v1 reason code unchanged (mandatory checks combine by logical
AND with v1) and adds semantic-suite codes below. Codes are plain strings in
records; the constant names are the canonical spellings.

Outcome semantics (hardening §6):
- Controlled rewards remain exactly ``0.0`` or ``1.0``.
- A failed semantic check turns a v1 pass into reward ``0.0``.
- Infrastructure/semantic-infrastructure outcomes carry null reward and are
  never presented as a solution rejection.
"""

from __future__ import annotations

SEMANTIC_TESTS_FAILED = "semantic_tests_failed"
SEMANTIC_COLLECTION_MISMATCH = "semantic_collection_mismatch"
SEMANTIC_EVIDENCE_MISSING = "semantic_evidence_missing"
SEMANTIC_SUITE_TIMEOUT = "semantic_suite_timeout"
SEMANTIC_INFRASTRUCTURE_ERROR = "semantic_infrastructure_error"

V2_REASON_CODES = frozenset(
    {
        SEMANTIC_TESTS_FAILED,
        SEMANTIC_COLLECTION_MISMATCH,
        SEMANTIC_EVIDENCE_MISSING,
        SEMANTIC_SUITE_TIMEOUT,
        SEMANTIC_INFRASTRUCTURE_ERROR,
    }
)
