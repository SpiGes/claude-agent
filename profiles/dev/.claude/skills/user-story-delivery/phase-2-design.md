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
6. For each configuration setting the flow depends on (output format, feature flag, mapping, limit),
   its value is compared in the backend `appsettings.json` and in the Helm settings of each
   environment. A setting whose values lead to different behaviours (e.g. an output format that
   produces one file or several) is a design point: the supported values are decided and written
   down, and the behaviour for the other values is defined.

## Rules

- The functional specification is the reference. A design choice that changes the functional
  behaviour is reported to the user, and the functional specification is updated after validation.
- Choices that depend on the user (e.g. an architecture preference) are asked, numbered, with a
  recommendation.

## End of the phase

A recap is given: main choices, open points, impacts on existing behaviour. The user validates the
design, or asks for changes.
