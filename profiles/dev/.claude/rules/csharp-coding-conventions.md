---
paths:
  - "**/*.cs"
---

# SpiGes C# coding conventions

## Version

| Version | Date | Details |
| --- | --- | --- |
| draft | 2026.03 | First draft |

## References

1. .NET Coding Conventions - C# | Microsoft Learn

## Purpose

These conventions define the default C# coding rules for SpiGes backend development.

Their goals are:

- to keep the codebase consistent
- to support readability and maintainability
- to reduce accidental coupling
- to provide a stable basis for code reviews, analyzers, and `.editorconfig`

These rules apply by default unless an explicit project decision states otherwise.

### Baseline conventions

Unless explicitly specified otherwise in this document, the baseline C# coding conventions for SpiGes are the Microsoft C# coding conventions.

This document extends, restricts, and overrides those baseline conventions where project-specific rules are required.

In case of conflict, this SpiGes coding convention document takes precedence.

## Scope

This document applies primarily to handwritten C# backend code in SpiGes, especially:

ASP.NET Core backend code
MediatR commands and handlers
services
repositories
entities and models
background jobs
unit tests

Unless explicitly stated otherwise, these conventions do not apply in full to:

generated code
Entity Framework migrations
third-party imported source files
files whose content is owned by an external generator or tool
short-lived technical prototypes or throwaway scripts that are not intended to become maintained product code

When such code is edited manually, these conventions should be applied only as far as practical and without fighting regeneration, tool ownership, or framework-generated structure.

## Structure of this document

This document is divided into two main parts:

1. conventions that cannot be expressed, or cannot be expressed sufficiently, in an `.editorconfig `2. conventions that can be expressed fully or partially in an `.editorconfig`

The first part takes precedence when a rule is architectural, semantic, or documentation-related.

### Rule Strength and Interpretation

This document uses the following rule strength levels:

must / must not: mandatory rules
should / should not: strong default rules that may be deviated from when there is a clear and justified reason
preferred: recommended defaults
may: allowed options

Mandatory rules are intended to be enforced consistently in implementation and review unless an explicit project decision states otherwise.

Strong default rules are expected to be followed in normal cases, but may be adapted when correctness, framework constraints, legacy constraints, or readability justify another choice.

Preferred rules express the default style direction of the project. They guide implementation and review but do not justify unnecessary churn by themselves.

When a rule conflicts with framework requirements, generated code constraints, or an established external contract, the constraint takes precedence and the deviation should remain local and justified.

When a rule conflicts with untouched legacy code, this chapter defines the expected change strategy.

## Part I. general conventions

### General principles

The following principles must guide the implementation:

correctness first
explicitness over implicitness
readability over compactness
consistency over personal preference
low coupling between features
clear business naming
targeted changes over broad rewrites

Code should be easy to read, easy to review, and easy to change safely.

### Language rules

All source code must be written in English.

All code comments must be written in English.

All exception messages explicitly written in code must be written in English.

All log messages of level `Information` and above must be written in English.

### Visibility rules

The most restrictive visibility possible must be used.

A member or type must not be exposed more broadly than required by its intended contract or architecture.

A member must not be made `internal` only to simplify testing.

If direct testing of a private implementation detail becomes necessary, prefer either:

testing through the public contract
extracting the logic into a dedicated type with an appropriate visibility

**Example**

```
internal sealed class ExportRequestService(
    IExportRepository repository,
    ILogger<ExportRequestService> logger
) : IExportRequestService
{
    public async Task MarkAsFinished(Guid integrationId, CancellationToken cancellationToken)
    {
        ExportRequest request = await repository.GetByIntegrationId(integrationId, cancellationToken);
        MarkRequestAsCompleted(request);
        logger.LogInformation(Logging.ExportRequestMarkedAsCompleted, integrationId);
    }
    private static void MarkRequestAsCompleted(ExportRequest request)
    {
        request.State = ExportState.Completed;
        request.FinishedAt = DateTimeOffset.UtcNow;
    }
    /// <summary>
    /// Defines logging messages.
    /// </summary>
    internal static class Logging
    {
        public const string ExportRequestMarkedAsCompleted = "Marked export request {IntegrationId} as completed.";
    }
}
```

### Architecture and layering rules

Controllers must stay thin.

Controllers may:

receive HTTP input
perform boundary-level validation or routing
map request DTOs to commands
dispatch through MediatR
map and return the HTTP response

Controllers must not contain business logic.

The modular structure described in this document is the default target design for new features and for significant refactoring work.

SpiGes also contains a historical core project, `Bfs.Sis.SpiGes`, which predates the current modular organization. The module structure rules in this chapter apply directly to dedicated feature modules. For the historical core project, they should be used as the direction of travel when code is moved or substantially redesigned, but they are not a rewrite mandate for untouched legacy areas.

In the main project of a module, all type definitions must be declared `internal` by default, including classes and interfaces.

The only default exceptions are:

controller classes
the static module class that contains registration and configuration methods for the module

Public contracts that must be consumed outside the module, such as commands, module-specific interfaces, payloads, or similar shared definitions, must be placed in the module abstraction project rather than in the main module project.

Broader visibility in the main module project must remain exceptional and must be justified explicitly by architecture or cross-module contract requirements.

Handlers should orchestrate the use case.

Services should host reusable business logic or workflow logic.

Repositories must encapsulate `DbContext` usage and database access.

`DbContext` must not be used outside repositories.

EF entities are persistence types.

EF entities must never be used in controllers.

EF entities must never be used in API DTOs.

A mapping step must be introduced when data crosses from persistence-oriented models to API contracts.

#### Entity Framework mapping rules

Entity Framework database mapping and schema configuration must be defined through Fluent API in dedicated IEntityTypeConfiguration<T> implementations.

This includes in particular:

table names
column names
key definitions
indexes
relationships
property lengths
precision and scale
conversions
delete behaviors
constraints relevant to persistence mapping

Entity classes must not define Entity Framework mapping through attributes except in rare and justified cases.

The default rule is therefore:

persistence mapping belongs to Entity Framework configuration classes
entity classes should remain free of persistence mapping attributes

This rule exists to keep persistence concerns centralized, explicit, and easier to maintain.

When an exception is made, it should remain local, rare, and justified by a clear technical constraint.

**Example**

```
internal sealed class ExportRequestEntityTypeConfiguration : IEntityTypeConfiguration<ExportRequest>
{
    public void Configure(EntityTypeBuilder<ExportRequest> builder)
    {
        builder.ToTable("SpiGes_ExportRequests");
        builder.HasKey(x => x.Id);
        builder.Property(x => x.UserIdentifier)
            .HasMaxLength(GlobalConstraints.MaxUserIdentifierLength)
            .IsRequired();
        builder.HasIndex(x => new
        {
            x.ExportConfigurationId,
            x.WaveId,
            x.UserIdentifier
        });
    }
}
```

### Module Rules

A new feature must be defined in a new module when all of the following conditions are met:

the feature is sufficiently independent from existing features
the feature has low direct coupling with unrelated business flows
the feature has a clear functional boundary
the feature has realistic reuse potential
the feature introduces a distinct business capability or subdomain

A new module should not be created for small local variations that do not justify an isolated boundary.

#### Standard structure of a module abstraction project

The abstraction project of a module must expose only contracts and shared public types intended to be consumed outside the main module project.

The standard structure is:

```
Bfs.Sis.SpiGes.Modules.<Feature>.Abstractions
 Api
    Requests
    Responses
 Application
    Commands
    Results
    Notifications
 Contracts
 Models
```

The directories above should contain the following kinds of types:

`Api/Requests`: endpoint request payloads such as `StartEnterpriseClosureRequest`
`Api/Responses`: endpoint response payloads such as `EnterpriseClosureProcessResponse`
`Application/Commands`: public MediatR commands such as `StartEnterpriseClosureCommand`
`Application/Results`: application-level result models such as `EnterpriseClosureProcessResult`
`Application/Notifications`: public notifications such as `EnterpriseClosureStartedNotification`
`Contracts`: public interfaces exposed by the module such as `IEnterpriseClosureModuleContract`
`Models`: shared public supporting models and enums used by commands, requests, responses, results, or notifications, such as `EnterpriseClosurePhase`, `EnterpriseClosureStatus`, or `ClosureActorLevel`

The `Models` directory in a module abstraction project must not contain commands, requests, responses, results, notifications, or service contracts.

#### Standard structure of a main module project

The main project of a module must contain the internal implementation of the module.

The standard structure is:

```
Bfs.Sis.SpiGes.Modules.<Feature>
 Api
    Controllers
    Mappers
 Application
    Commands
       Public
       Internal
    Jobs
    Abstractions
    Notifications
    Mappers
 Domain
    Models
    Services
    Abstractions
    Policies
    Rules
    Exceptions
 Infrastructure
    Repositories
    HttpClients
    Mappers
 <Feature>Module.cs
```

The directories above should contain the following kinds of types:

- `Api/Controllers`: controllers such as `EnterpriseClosureController`
- `Api/Mappers`: API mapping helpers such as `EnterpriseClosureResponseMapper`
- `Application/Commands/Public`: handlers for commands defined in the abstraction project, such as `StartEnterpriseClosureCommandHandler`
- `Application/Commands/Internal`: internal-only commands, handlers, and validators such as `EnsureHospitalClosuresCommand`, `EnsureHospitalClosuresCommandHandler`, or `EnsureHospitalClosuresCommandValidator`
- `Application/Jobs`: Hangfire jobs such as `StartEnterpriseClosureJob`
- `Application/Notifications`: internal application notifications and related handlers
- `Application/Mappers`: orchestration-level mappers such as `StartEnterpriseClosureCommandMapper`
- `Application/Abstractions`: interfaces whose implementation isn't under the responsibility of the Application layer.
- `Domain/Models`: internal business models such as `EnterpriseClosureReadiness`
- `Domain/Services`: business services such as `EnterpriseClosureReadinessService`
- `Domain/Abstractions`: Interface whose implementation isn't under the responsibility of the Domain layer, such as `IEnterpriseClosureRepository`
- `Domain/Policies`: business policies such as `EnterpriseClosureAuthorizationPolicy`
- `Domain/Rules`: business rules such as `EnterpriseClosureEligibilityRule`
- `Domain/Exceptions`: module-specific business exceptions when justified
- `Infrastructure/Repositories`: internal repository abstractions used by the module, such as `EnterpriseClosureRepository`
- `Infrastructure/Mappers`: persistence-to-domain mapping helpers such as `EnterpriseClosureEntityMapper`
- `<Feature>Module.cs`: the module registration and configuration entry point

#### Commands exposed versus commands internal to the module

A command that must be consumed outside the main module project must be defined in the abstraction project.

Its implementation must be placed in `Application/Commands/Public` in the main module project.

A command that is only an internal orchestration mechanism of the module must be defined only in `Application/Commands/Internal` of the main module project.

**Example**

```
Bfs.Sis.SpiGes.Modules.EnterpriseClosure.Abstractions
 Application
     Commands
         StartEnterpriseClosureCommand.cs
Bfs.Sis.SpiGes.Modules.EnterpriseClosure
 Application
     Commands
         Public
            StartEnterpriseClosure
                StartEnterpriseClosureCommandHandler.cs
         Internal
             EnsureHospitalClosures
                 EnsureHospitalClosuresCommand.cs
                 EnsureHospitalClosuresCommandHandler.cs
                 EnsureHospitalClosuresCommandValidator.cs
```

#### Namespace rules for module abstraction projects

Namespaces in a module abstraction project must not contain the term `Abstractions`.

Namespaces must reflect the functional boundary and the folder structure rather than the technical project suffix.

**Correct examples**

```
namespace Bfs.Sis.SpiGes.Modules.EnterpriseClosure.Api.Requests;
namespace Bfs.Sis.SpiGes.Modules.EnterpriseClosure.Application.Commands;
namespace Bfs.Sis.SpiGes.Modules.EnterpriseClosure.Models;
```

**Incorrect example**

```
namespace Bfs.Sis.SpiGes.Modules.EnterpriseClosure.Abstractions.Api.Requests;
```

#### Shared constraints and constants

Constraints and constants that must be reused across several layers or technical representations must be centralized instead of being duplicated.

This applies in particular when the same value is used in more than one of the following places:

HTTP request validation
business validation
persistence configuration
entity type configuration
mapping or serialization rules
application contracts

A shared module-level constraint or constant should be defined once in the abstraction project of the module when it must be consumed from multiple parts of the module or from multiple projects related to that module.

The default class name for such definitions should be `Constraints` when the values represent business or technical limits.

The `Constraints `class should be placed in a location such as` Models/Constraints.cs` in the module abstraction project when the constants are part of the public shared contract of the module.

Constants must not be hardcoded repeatedly in validators, persistence configuration, or other technical layers when a single shared definition is appropriate.

A constant should remain local to the consuming type when it is only used once or when centralization would not improve consistency or readability.

When the same constant is relevant beyond a single module, it should be placed in an appropriate shared location rather than copied into several modules, e.g. in `Bfs.Sis.SpiGes.Abstractions`.

**Example**

```
/// <summary>
/// Defines various constraints used in the enterprise closure feature.
/// </summary>
public static class Constraints
{
    /// <summary>
    /// The maximum length for comments.
    /// </summary>
    public const int MaxCommentLength = 4000;
}
```

**Example**

```
/// <summary>
/// Defines the model for enterprise closure limits.
/// </summary>
/// <remarks>
/// This model is intended to be used as a singleton service.
/// </remarks>
public sealed class EnterpriseClosureLimits
{
    /// <summary>
    /// Maximum length for comments.
    /// </summary>
    public int MaxCommentLength => Constraints.MaxCommentLength;
}
```

**Example**

```
/// <summary>
/// Defines a validator for the command <see cref="AcceptInspectionReportCommand"/>.
/// </summary>
internal sealed class AcceptInspectionReportCommandValidator : AbstractValidator<AcceptInspectionReportCommand>
{
    /// <summary>
    /// Constructs an instance of <see cref="AcceptInspectionReportCommandValidator"/>.
    /// </summary>
    public AcceptInspectionReportCommandValidator()
    {
        ...
        RuleFor(command => command.Comment)
            .MaximumLength(Constraints.MaxCommentLength)
            .When(x => x.Comment is not null)
            .WithMessage(x => string.Format(ValidationErrorMessages.Common.InvalidCommentLength, x.Comment?.Length, Constraints.MaxCommentLength));
    }
}
```

**Example**

```
/// <summary>
/// Defines the entity type configuration for <see cref="HospitalClosure"/>.
/// </summary>
public sealed class HospitalClosureEntityTypeConfiguration : OracleEntityTypeConfiguration<HospitalClosure>
{
    /// <inheritdoc/>
    public override void Configure(EntityTypeBuilder<HospitalClosure> builder)
    {
        ...
        builder.Property(x => x.CantonCode)
            .IsRequired()
            .HasMaxLength(GlobalConstraints.MaxCantonCodeLength);
    }
}
```

### Type and file design rules

#### One main top-level type per file

Each file must contain one main top-level type.

Only the following limited exceptions are allowed:

an `enum` tightly coupled to the main type
a nested helper type
a nested `Errors `or` Logging` type
a `partial` declaration of the same main type

#### File-scoped namespaces

File-scoped namespaces must be used.

They may be avoided only in limited cases where the file shape or generated code makes them impractical.

#### Sealed by default

A concrete non-abstract class must be declared `sealed` unless inheritance is intentionally part of the design.

**Example**

```
internal sealed class ProcessingRequestService : IProcessingRequestService
{
}
```

**Example**

```
internal sealed class GetHospitalBusinessOverviewsCommandHandler(
    IRrdSqlDbContextProvider contextProvider,
    IUnitAuthorizationPredicateService authorizationService,
    ILogger<GetHospitalBusinessOverviewsCommandHandler> logger
) : IRequestHandler<GetHospitalBusinessOverviewsCommand, IEnumerable<HospitalBusinessOverview>>
{
}
```

**Example**

```
internal sealed class ExportProcessForCrossUnitExecutionJob(
    IServiceProvider serviceProvider,
    ILogger<ExportProcessForCrossUnitExecutionJob> logger
)
{
}
```

#### Records and record structs

`record `and `readonly record struct` should be preferred for immutable, non-EF models that primarily represent data rather than behavior.

This applies in particular to:

API payloads
commands
results
read models
simple value objects

Positional records should be reserved for small immutable types whose meaning remains immediately clear without named property initialization.

`readonly record struct` should be preferred when a model:

is immutable
expresses a payload, a command, a result, or a simple value object
contains only primitive-like properties or other small value-like properties
contains no specific behavior beyond representation

`record` should be preferred when the model is immutable but contains non-primitive properties.

`class` should remain preferred when the type:

is an EF entity
relies on reference identity rather than value equality
is intentionally mutable
depends on inheritance, proxying, or framework lifecycle behavior
contains significant behavior and is not primarily a data carrier

`record `or `readonly record struct` should not be introduced mechanically when their equality semantics or generated members do not match the intended domain meaning.

**Example**

```
public sealed record CancelEnterpriseClosureRequest
{
    public required Guid ProcessId { get; init; }
    public required ClosureActorLevel Level { get; init; }
    public string? CantonCode { get; init; }
}
```

**Example**

```
public readonly record struct EnterpriseIdentifier(uint Value);
```

### Naming rules

Names must express intent clearly.

Business vocabulary already established in SpiGes must be reused instead of being replaced by generic terminology.

Method names must describe the action or the condition clearly.

Predicate members should read naturally as boolean checks.

Method names should remain normal business or technical names.

A method name should not be forced into a special throwing form only because the method may raise an exception.

When a method can throw an explicit business exception, the expected exception behavior should be documented through XML documentation rather than encoded artificially in the method name.

`Ensure...` remains acceptable when the method purpose is genuinely to verify or guarantee a condition.

**Example**

```
/// <summary>
/// Gets the user's email or throws a <see cref="ValidationException"/> (BadRequest) if not present.
/// </summary>
/// <param name="cancellationToken">A cancellation token.</param>
/// <returns>The email address.</returns>
/// <exception cref="ValidationException">Thrown if the email isn't found in the claims.</exception>
Task<string> GetEmail(CancellationToken cancellationToken);
...
var userIdentifier = await _userIdentityService.GetEmail(cancellationToken);
`` `The absence of the `Async `suffix is a SpiGes-specific naming convention and is an intentional deviation from common .NET guidance.` Async` should not be used as a suffix by default.

The `Async` suffix is allowed only when both an asynchronous and a non-asynchronous method with the same semantic purpose coexist and the suffix is necessary to avoid ambiguity.

The project convention above must not force unnatural renaming when a member is constrained by:

a framework contract
an external library contract
an interface or base member that already defines the name
an established public API where the `Async` suffix is already part of the contract

### Type naming and suffix rules

The following suffixes should be used consistently when applicable:

`Service `for business or orchestration services` Repository `for persistence access abstractions and implementations` Controller `for API controllers` Command `for MediatR commands` Handler `or` CommandHandler `for MediatR handlers` Job `for background executable jobs` Configuration `for configuration models` Options `only when a type is explicitly used as an options-binding object` Exception` for exception types

Interfaces must use the `I` prefix.

### Exception rules

Exception usage must be explicit and predictable.

For execution flows initiated by an HTTP request, the following mapping must be applied:

`NotFoundException `HTTP 404` ValidationException `HTTP 400` UnprocessableRequestException `HTTP 422` UnauthorizedAccessException `HTTP 401` AccessDeniedException `HTTP 403` ConflictException`  HTTP 409

The same business exceptions may also be used in Hangfire jobs when they describe the same business failure semantics.

`InvalidOperationException` should be reserved for internal invariant violations or impossible states that do not represent a normal business-facing validation case.

All exception messages explicitly written in code must:

be in English
be stable enough for tests and diagnostics
be declared in an inner `internal static `class named` Errors` by default

The `Errors `class must be documented as follows:` ``
/// <summary>
/// Defines error messages.
/// </summary>
`` `The `Errors `class may be placed in another file through` partial` declarations when this improves readability.

When the constants are defined in a base class and reused by derived classes, another name may be used if `Errors` is not appropriate in that inheritance context.

**Example**

```
internal sealed partial class ExportRequestService : IExportRequestService
{
    /// <summary>
    /// Defines error messages.
    /// </summary>
    internal static class Errors
    {
        public const string ExportRequestNotFound = "Export request was not found.";
    }
}
```

### Logging rules

Logs should be structured, meaningful, and useful for diagnosis.

Logging is expected for:

important workflow transitions
state changes
relevant warnings
background job milestones
ignored or fallback behaviors when diagnosis matters

Noise logs should be avoided.

All log messages of level `Information` and above must:

be in English
be stable enough for tests and diagnostics
be declared in an inner `internal static `class named` Logging` by default

The `Logging `class must be documented as follows:` ``
/// <summary>
/// Defines logging messages.
/// </summary>
`` `The `Logging `class may be placed in another file through` partial` declarations when this improves readability.

When the constants are defined in a base class and reused by derived classes, another name may be used if `Logging` is not appropriate in that inheritance context.

In unit tests, all `Information`, `Warning`, and `Error` log messages that are expected by design must be verified.

**Example**

```
internal sealed partial class ProcessingRequestService : IProcessingRequestService
{
    /// <summary>
    /// Defines logging messages.
    /// </summary>
    internal static class Logging
    {
        public const string ProcessingStarted = "Started processing request {ProcessingRequestId}.";
    }
}
```

### Resource lifetime rules

All disposable resources must be disposed.

This requirement applies both to nominal flows and to error paths.

`using `and` await using `must be preferred when applicable.` try/finally `must be used when deterministic disposal cannot be expressed cleanly with` using `or` await using`.

Ownership of disposable resources must remain explicit. Disposal responsibility must not remain ambiguous.

Well-scoped ownership patterns should be used consistently to guarantee disposal and reduce the risk of memory leaks.

### XML documentation rules

XML documentation is mandatory for all non-private handwritten types and members.

The only exceptions are:

public constants declared inside `Errors `or `Logging` classes; those constants do not require XML documentation because their names are
expected to be self-descriptive
unit test methods; those methods do not require XML documentation because their names are expected to be self-descriptive
generated code
Entity Framework migrations
third-party imported source files
files whose content is primarily owned by an external generator or tool

Documentation must be written in English.

Documentation should describe:

the purpose of the type or member
important semantics
non-obvious behavior
important constraints when relevant

Documentation must not restate the obvious mechanically.

A property summary must not start with forms such as:

`Gets or sets ...` `Gets or initializes ...`
equivalent boilerplate forms

Such automatic verbal forms should be avoided.

At a minimum, the property name or the represented concept must appear clearly in the summary.

All method parameters must be documented.

The return value of a method must be documented, except for a non-generic `Task`.

An empty `<returns></returns>` element is tolerated when the return description adds no meaningful information, but a meaningful return description should be preferred whenever it improves clarity.

All exceptions explicitly thrown by the written code must be documented with `<exception>`.

Each documented exception must describe the condition under which it is thrown.

Exceptions that are only implicitly thrown by called APIs or the runtime must not be documented.

When a maintained internal helper or test support type is not self-explanatory, XML documentation should still be added even if the type is not externally visible.

**Example**

```
/// <summary>
/// The identifier as an unsigned integer for hospital-related group types,
/// otherwise <see langword="null"/>.
/// </summary>
/// <returns>
/// The numeric identifier when the descriptor represents a hospital-related scope; otherwise <see langword="null"/>.
/// </returns>
public uint? IdentifierAsNumeric => ...;
```

**Example**

```
/// <summary>
/// Marks the export request as finished.
/// </summary>
/// <param name="integrationId">The integration identifier of the export request.</param>
/// <param name="cancellationToken">The cancellation token.</param>
/// <exception cref="NotFoundException">Thrown when the export request was not found.</exception>
public Task MarkAsFinished(Guid integrationId, CancellationToken cancellationToken)
{
    ...
}
```

### Unit test rules

Unit tests must use:

NUnit
FluentAssertions

A unit test class file must be named after the tested class, with the suffix `Test`.

A unit test project must be named after the tested project, with the suffix `.Tests`.

The test class name must follow:

```
<class-to-test>Test
```

The test method name must follow:

```
<method-to-test>_Should<expected>_When<context>
```

Each test must contain:

```
// Arrange
// Act
// Assert
`` `All test fields must start with .`_ `A `CancellationTokenSource `field named` _theoreticalCts` must be used when a token is required.

All mock interactions must be verified.

`VerifyNoOtherCalls()` must be used on all mocks.

All exception messages must be asserted in unit tests when the scenario checks an explicit exception thrown by the written code.

All `Information`, `Warning`, and `Error` log messages that are expected by design must be asserted in unit tests when they are part of the scenario.

**Example**

```
/// <summary>
/// Unit tests for <see cref="xportRequestService"/>.
/// </summary>
[Category(Common.Testing.TestCategory.UnitTest)]
internal sealed class ExportRequestServiceTest
{
    private Mock<IExportRepository> _exportRepositoryMock = default!;
    private Mock<ILogger<ExportRequestService>> _loggerMock = default!;
    private CancellationTokenSource _theoreticalCts = default!;
    private ExportRequestService _service = default!;

    /// <summary>
    /// Sets up each unit test.
    /// </summary>
    [SetUp]
    public void SetUp()
    {
        _exportRepositoryMock = new();
        _loggerMock = new Mock();
        _theoreticalCts = new CancellationTokenSource();
        _service = new ExportRequestService(_exportRepositoryMock.Object, _loggerMock.Object);
    }

    /// <summary>
    /// Tears down each test.
    /// </summary>
    [TearDown]
    public void TearDown()
    {
        _theoreticalCts.Dispose();
    }
    [Test]
    public async Task MarkAsFinished_ShouldThrowNotFoundException_WhenExportRequestDoesNotExist()
    {
        // Arrange
        Guid theoreticalIntegrationId = Guid.NewGuid();
        _exportRepositoryMock
            .Setup(x => x.GetByIntegrationId(theoreticalIntegrationId, _theoreticalCts.Token))
            .ThrowsAsync(new NotFoundException(ExportRequestService.Errors.ExportRequestNotFound));
        // Act
        Func<Task> action = async () => await _service.MarkAsFinished(theoreticalIntegrationId, _theoreticalCts.Token);
        // Assert
        NotFoundException exception = await action
            .Should()
            .ThrowAsync<NotFoundException>();
        exception.Which.Message.Should().Be(ExportRequestService.Errors.ExportRequestNotFound);

        _exportRepositoryMock.VerifyNoOtherCalls();
        _loggerMock.VerifyNoOtherCalls();
    }
}
```

## Part II. conventions suitable for .editorconfig

### Formatting rules

#### Braces

Allman style must be used.

Opening braces must be placed on the next line for:

type declarations
method declarations
property accessors when expanded
control blocks

**Example**

```
public void EnsureValidDescriptor()
{
    if (!IsValid)
    {
        throw new InvalidDataException(string.Format(Errors.InvalidData, Identifier, GroupType));
    }
}
```

#### Blank lines

A blank line should be kept:

between fields and the next constructor or method
between methods
between logical code blocks when readability is improved

Blank lines should not be multiplied unnecessarily.

#### Indentation

Four spaces must be used for indentation.

Tabs must not be used in source code.

#### Long lines

Lines should remain reasonably short for readability.

The maximum line length is 160 characters.

### Using directives

Using directives must be kept minimal and ordered consistently.

The order must be:

`System` namespaces
non-`System `namespaces` using static`
alias directives

### Typing rules

Explicit typing is preferred over `var` whenever reasonably possible.

The concrete type should normally appear on the left side of `new`. `var` is allowed:

when the type is anonymous
when the concrete type is excessively long and writing it harms readability
when the right-hand side already makes the type immediately obvious and repeating it would be heavy without adding value

`var` should be avoided:

when the actual type is not obvious

when multiple similar APIs return different concrete types
when readability becomes worse
when it hides an important business or technical type

**Preferred example**

```
UnitDescriptor descriptor = new()
{
    Identifier = "42",
    GroupType = GroupType.HospitalLocation
};
```

**Allowed example**

```
var projection = items.Select(x => new { x.Id, x.Name });
```

### Member syntax rules

Primary constructors should be encouraged when they improve clarity and no specific constructor logic is needed.

This applies in particular to services, handlers, controllers, jobs, and similar types where constructor injection is the primary purpose of the constructor.

Classic constructors should be used when specific constructor logic is required.

When a declaration contains more than three constructor parameters, more than three implemented interfaces, or more than three inherited/implemented type elements, the elements should be split across multiple lines and aligned vertically for readability.

**Example**

```
internal sealed class CancelEnterpriseClosureService(
    IClosureReadinessEnterpriseClosureService closureReadinessEnterpriseClosureService,
    IEnterpriseClosureRepository enterpriseClosureRepository,
    IUnitRepository unitRepository,
    IUserIdentityService userIdentityService,
    IAuthorizationService authorizationService,
    ILogger<CancelEnterpriseClosureService> logger
) : EnterpriseClosureService(
        closureReadinessEnterpriseClosureService,
        enterpriseClosureRepository,
        unitRepository,
        userIdentityService,
        authorizationService,
        logger
    ),
    ICancelEnterpriseClosureService
{
    ...
}
```

Expression-bodied members should be limited to members whose definition fits on a single line and remains immediately readable.

Auto-properties should be preferred when no specific accessor logic is required.

Pure pass-through asynchronous methods should not use async and await when they do nothing other than return the task produced by another call.

In such cases, the task should be returned directly.

This avoids introducing an unnecessary async state machine and keeps the method simpler.

This rule applies only when the method is a true pass-through.

async and await remain appropriate when the method needs, for example, to:

handle exceptions with try/catch
guarantee cleanup with try/finally
use await using
perform additional work after the awaited call
transform the result
add logging or business handling around the awaited operation

**Preferred example**

```
public Task<ExportRequest> GetByIntegrationId(Guid integrationId, CancellationToken cancellationToken)
    => _exportRepository.GetByIntegrationId(integrationId, cancellationToken);
```

**Avoid**

```
public async Task<ExportRequest> GetByIntegrationId(Guid integrationId, CancellationToken cancellationToken)
    => await _exportRepository.GetByIntegrationId(integrationId, cancellationToken);
```

#### Task blocking rules

Asynchronous code must be awaited by default.

Blocking on a task with `.Wait()`, `.Result`, `.GetAwaiter().GetResult()`, or similar patterns should be avoided.

These patterns must not be used except in rare and explicitly justified cases.

They are discouraged because they may:

block threads unnecessarily
hide an async flow behind synchronous waiting
increase the risk of deadlocks depending on the execution context
make exception behavior less clear
reduce scalability

When synchronous waiting cannot be avoided because of a strict synchronous boundary, the reason should be explicit and the blocking should remain local.

Such exceptions should remain rare.

### Collections and modern C# syntax

Empty collections must be expressed with `[]` whenever the language version and context support it.

Collection expressions such as `[.. collection]` should be preferred over `ToArray()`, `ToList()`, or similar conversions when this improves clarity.

**Example**

```
public ICollection<Participant> Participants { get; set; } = [];
```

**Example**

```
model.LocationOverviews = [.. status.LocationOverviews.Where(x => x.BurGesv == unit.BurGesv)]
```

### Member order rules

Members inside a type should be ordered as follows:

constants
static fields
instance fields

constructors
properties
public methods
internal and protected methods
private methods
nested types
`Errors` `Logging`

When `Errors `or` Logging `are extracted to another file through` partial` declarations, the logical order above still applies to the owning type design.

### Comment rules

Comments should be rare, useful, and written only when they add information that is not obvious from the code itself.

Comments must not paraphrase the implementation mechanically.

XML documentation remains mandatory where required by this document.

### Change strategy

When existing code is modified:

targeted changes should be preferred
unrelated refactoring should be avoided
architecture should not be degraded
new coupling should not be introduced only for convenience

Consistency with the existing solution is more important than applying a personal style preference.

New or substantially reworked code should follow these conventions.

Untouched legacy code is not a rewrite mandate.

### Review checklist

During review, the following points should be checked:

is the naming explicit?
is the visibility minimal?
is the type in the right layer?
is `DbContext` kept inside repositories?
is an EF entity leaking into a controller or API DTO?
is the chosen exception mapped correctly?
are explicit exception and log messages declared centrally?
is `var` used only where justified?
is XML documentation present and complete on non-private members?
is the code readable without guessing intent?
are disposables disposed in nominal and error paths?
does the change justify a new module?
are the business terms consistent with SpiGes vocabulary?
is the choice between `class`, `record`, and `readonly record struct` semantically correct?
does async naming follow the project convention without breaking framework or external contracts?
is Entity Framework mapping defined in `IEntityTypeConfiguration<T>` classes rather than through entity attributes?
is `async`/`await` avoided for pure pass-through task-returning methods?
does the code avoid blocking on tasks with `.Wait()`, `.Result`, or equivalent patterns?
