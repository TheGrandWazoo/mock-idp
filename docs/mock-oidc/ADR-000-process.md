# ADR-000: Architecture Decision Record Process

**Date:** 2026-05-17
**Status:** Accepted
**Deciders:** Platform team

---

## Context

Architecture Decision Records (ADRs) capture significant design decisions,
the context that drove them, the alternatives considered, and the
consequences. Without a defined lifecycle, ADRs accumulate without clear
status and lose their value as a reference.

This project treats ADRs like RFCs: they are drafted, proposed for review,
then accepted or rejected. When a decision changes, a new ADR is created
and the old one is marked Superseded — the historical record is never
deleted.

---

## Lifecycle

```
Draft → Proposed → Accepted
                 → Rejected
                 → Withdrawn

Any Accepted ADR → Superseded (by a later ADR)
```

### Status definitions

| Status | Meaning |
|---|---|
| **Draft** | Being written. Not ready for review. Content may be incomplete. |
| **Proposed** | Complete and open for review. The decision has not yet been made. |
| **Accepted** | Decision made and ratified. Implementation may proceed. |
| **Rejected** | Considered and declined. Kept for historical context. |
| **Withdrawn** | Author withdrew before a decision was made. |
| **Superseded** | A later ADR changes or reverses this decision. The ADR header names the successor. |

---

## Format

Every ADR follows this template:

```markdown
# ADR-NNN: Short Title

**Date:** YYYY-MM-DD
**Status:** Draft | Proposed | Accepted | Rejected | Withdrawn | Superseded by ADR-NNN
**Deciders:** <team or person>
**Supersedes:** ADR-NNN (if applicable)

---

## Context

What is the problem or situation that requires a decision?
Include constraints, requirements, and relevant background.

---

## Decision

What was decided? State it clearly and directly.

---

## Alternatives considered

What other options were evaluated and why were they not chosen?

---

## Consequences

**Positive:** What does this decision make easier or better?
**Negative:** What does this decision make harder or worse?
**Neutral:** What changes without being clearly better or worse?

---

## References

Links to related ADRs, issues, or external documents.
```

---

## Numbering

ADRs are numbered sequentially starting at 001. ADR-000 is this process
document. Numbers are never reused. When an ADR is Superseded, the old
number remains in place — only the status line changes.

---

## When to write an ADR

Write an ADR when:
- A technology choice affects the project long-term (library, protocol, schema)
- A design decision would surprise a new contributor
- A decision was actively debated before being settled
- You are reversing or significantly changing a previous decision

Do NOT write an ADR for:
- Routine implementation choices (variable names, file layout within a module)
- Decisions that can be reversed with one PR and no migration

---

## Process steps

1. Author creates `ADR-NNN-short-title.md` with **Status: Draft**
2. Author changes status to **Proposed** when ready for review
3. Team discusses (GitHub PR, issue, or meeting)
4. Status changes to **Accepted** or **Rejected** with a brief rationale note
5. If a future decision supersedes this one:
   - New ADR is created (new number)
   - Old ADR status line changes to `Superseded by ADR-NNN`
   - New ADR header includes `Supersedes: ADR-NNN`

---

## Consequences

**Positive:**
- Clear status makes it easy to know which decisions are current
- Historical record preserved — Superseded ADRs explain why decisions changed
- RFC-style flow gives team members a chance to weigh in before decisions are locked

**Neutral:**
- Small overhead per decision — justified for significant choices, skip for trivial ones
