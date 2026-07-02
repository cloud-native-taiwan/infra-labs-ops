# ADR-0025: Nova vTPM for Windows 11 guests

- **Status:** Reverted
- **Date:** 2026-05-01
- **Deciders:** CNTUG ops

## Context

Windows 11 guests can use a vTPM (TPM 2.0) for BitLocker and attestation. Kolla
2026.1 added swtpm support, and the Debian image bundles `swtpm` and
`tpm2-tools`.

## Decision

(Later reverted.) Enable `enable_nova_swtpm` and attach a TPM 2.0 device via
flavor properties, accepting that vTPM state is persisted in plaintext (deemed
acceptable for general Windows 11 use, to revisit if compliance-grade
attestation is needed). This decision was subsequently reverted.

## Alternatives considered

- **No vTPM** — the state after the revert.
- **Compliance-grade sealed attestation** — not pursued; plaintext state is
  insufficient for it.

## Consequences

Recorded as a reversed decision. vTPM is not currently enabled. Revisit if a
concrete Windows 11 / BitLocker requirement returns, with a plan for state
confidentiality.

## References

- commit 0094af1 (enable nova vTPM), reverted by a498915
