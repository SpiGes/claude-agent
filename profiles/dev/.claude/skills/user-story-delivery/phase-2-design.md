# Phase 2 - Design

Goal: a design document, based on the functional specification, validated by the user.

## Steps

1. The `design-documents` skill is loaded, and its structure and conventions are followed. The
   `diagrams` skill is loaded for the diagrams.
2. The existing design document is updated, or a new one is created, in the specs repository
   (`design/...`).
3. Each decision with alternatives is written as "Choice 1 / Choice 2 / Choice and rationale", with
   what each choice brings and costs.
4. Points not decided yet are split into deferred design points and open points, as defined in the
   `design-documents` skill.
5. The chapter "Modifications to existing" lists the impacts, their risk, and their mitigation,
   in particular on the behaviour that must stay unchanged.

## Rules

- The functional specification is the reference. A design choice that changes the functional
  behaviour is reported to the user, and the functional specification is updated after validation.
- Choices that depend on the user (e.g. an architecture preference) are asked, numbered, with a
  recommendation.

## End of the phase

A recap is given: main choices, open points, impacts on existing behaviour. The user validates the
design, or asks for changes.