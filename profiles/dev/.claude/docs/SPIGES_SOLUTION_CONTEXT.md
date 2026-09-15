# SpiGes Solution Context

## 1. Global Context

SpiGes is one service of the SIS microservice infrastructure.

SpiGes is composed of four main parts:
- an Angular frontend;
- an Angular model generated from C#, including DTO definitions used as backend input and output models;
- an ASP.NET Core backend;
- a persistence layer based on both a SpiGes-specific PostgreSQL database and a shared Oracle database used by several microservices.

Authentication is handled by an external service outside the SIS infrastructure.
The frontend is responsible for initiating the authentication flow.
Identity data is carried by tokens.
The backend does not participate in the authentication flow itself.
Authentication data is received by the backend through controllers.

## 2. Business Domain Structure

The central business entity of the domain is the `Unit`.

A `Unit` represents one hospital site.
In business terms, this corresponds to one location.
A `Unit` is identified by a `BUR`.

A hospital is identified by a `BurGesv`.
A hospital is composed of one or more `Unit` instances.
All `Unit` instances that belong to the same hospital are located in the same canton, which is the canton responsible for managing that hospital.

An enterprise is identified by an `EntId`.
An enterprise is composed of one or more hospitals.
The hospitals that belong to the same enterprise may be managed by different cantons.

This creates the following main business hierarchy:
- one enterprise contains one or more hospitals;
- one hospital contains one or more `Unit` instances;
- one `Unit` represents one hospital site.

The solution includes several shared types that model and identify these business scopes.

`UnitDescriptor` is the main record used to identify a business scope in a uniform way.
Depending on its content, it can represent:
- one site through a `Unit`;
- one hospital;
- one enterprise;
- one canton, understood as the set of sites belonging to that canton;
- the national scope, identified by the survey wave year.

`GroupType` contains the constants used to identify the kind of scope represented by a `UnitDescriptor` or by a group of `Unit` instances.

`Unit` is the persistence entity representing one site in the database.

`Wave` is the model representing one survey wave.
The national scope is tied to the year of a `Wave`, while the lower scopes are tied to enterprise, hospital, canton, or site identifiers.

## 3. Architectural Implications

When code is generated or reviewed, the following implications should be kept in mind:
- module isolation is a design goal on the backend side;
- shared abstractions must not be confused with module-specific abstractions;
- entity ownership may be module-specific even when persistence is technically centralized;
- infrastructure projects should host technical concerns, not feature business logic;
- `Bfs.Sis.SpiGes` may contain historical feature logic that behaves like a large legacy module.

Backend-internal project structure (which project hosts persistence, the module/abstractions/entities split, and so on) is documented in `.claude/rules/backend.md`, not here, since it only matters when writing backend code.