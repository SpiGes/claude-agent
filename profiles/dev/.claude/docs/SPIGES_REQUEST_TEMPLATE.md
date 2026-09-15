# SpiGes Request Template

## 1. Purpose

This file defines the preferred way to ask for new SpiGes work with short but reliable prompts.

It is intended to reduce repetition while still providing enough information for accurate analysis, review, or development.

## 2. Short request format

Use this format when the feature is small or when the surrounding context is already known.

```md
Mode: analysis | review | development
Feature: <feature name>
Goal: <what must be achieved>
Backend scope: <controller/command/service/repository or module>
Frontend scope: <component/store/effect/screen>
Business scope: <location | hospital | enterprise | canton | country>
Input/output: <main input and expected output>
Constraints: <roles, validation, DB impact, async, no tests, etc.>
Reference examples: <files or existing feature names>
```

## 3. Full request format

Use this format when a new feature or larger refactoring must be generated.

### 3.1 Work mode

State the requested mode explicitly:
- analysis
- review
- development

### 3.2 Functional scope

Provide:
- user story
- acceptance criteria
- expected behavior
- error cases
- visibility rules
- permission rules

### 3.3 Technical anchor

Provide:
- target backend module or historical area
- target frontend screen or component area
- nearby reference files or example features
- whether the feature is synchronous or background-based

### 3.4 Data contract

Provide:
- request payload or expected body fields
- response shape or important returned fields
- validation rules
- whether generated API models already exist or must be created

### 3.5 Business scope

Provide the relevant scope explicitly:
- site / BUR
- hospital / BurGesv
- enterprise / EntId
- canton
- country-wide / wave year

If `UnitDescriptor` is relevant, say so explicitly.

### 3.6 Persistence impact

State one of the following clearly:
- no DB impact
- read existing data only
- add entity or relation
- add field
- add state or enum value
- add migration or SQL script

### 3.7 Testing expectations

State explicitly only when it differs from the project default (see the project CLAUDE.md,
`Project defaults`).

## 4. Minimal information required for reliable development

For a new full-stack feature, the minimum useful information is usually:
- the goal
- the target module or screen
- the main business scope
- the request and response shape
- the permissions or visibility rules
- the DB impact or confirmation that there is none
- one or two nearby reference examples

## 5. What to mention when the generated client is involved

When the frontend depends on generated API models or generated clients, it is useful to state:
- whether the relevant client already exists
- whether the request and response DTOs already exist
- whether the generated layer is visible or not in the shared material

The manual frontend pattern should still be described around that generated boundary.

## 6. Exception and non-generalization notes

When a local exception exists, state it explicitly.
Examples:
- a local Angular orchestration service exists but is specific to one area
- a legacy handler shape exists in one module but should not become the general rule
- a feature is older and should be treated as historical reference only

This information is very valuable because it prevents accidental over-generalization.

## 7. Recommended prompt examples

### 7.1 Analysis request

```md
Mode: analysis
Feature: EnterpriseClosure start process
Goal: explain the current backend flow and identify the main reusable patterns
Backend scope: EnterpriseClosure controller, command handler, service, repository
Business scope: enterprise
Constraints: no code generation, focus on current pattern and notable exceptions
Reference examples: StartEnterpriseClosureProcessCommandHandler, StartEnterpriseClosureService
```

### 7.2 Review request

```md
Mode: review
Feature: export filtering
Goal: review whether the new component follows the current SpiGes frontend pattern
Frontend scope: export filter components + ngrx actions/effects/reducer
Business scope: location and enterprise
Constraints: identify historical patterns vs preferred current pattern, no rewrite unless necessary
Reference examples: configs.component.ts, export.effects.ts, closing-overview.component.ts
```

### 7.3 Development request

```md
Mode: development
Feature: new enterprise-level action
Goal: add a new backend endpoint and frontend action to trigger the process and refresh the screen
Backend scope: EnterpriseClosure module
Frontend scope: closing overview screen + ngrx
Business scope: enterprise
Input/output: request contains waveId and entId, response returns full enterprise closure process
Constraints: direct component dispatch, generated client called from effect, no unit tests
Reference examples: StartClosure flow
```

## 8. Recommended default when requesting new code

When no stronger need exists, the most efficient request format is:
- one short functional goal
- one explicit business scope
- one backend anchor
- one frontend anchor
- one list of constraints
- one or two reference examples

This usually provides enough information to generate reliable SpiGes code without an unnecessarily long prompt.