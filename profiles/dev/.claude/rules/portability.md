---
paths: ["**/*.cs", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx", "**/*.py", "**/*.java", "**/*.go"]
---

# Code portability

## 1. Principle

Code and tests are written to behave the same on Windows and Linux, as far as possible.
Developers usually work on Windows, while CI pipelines, containers and deployed services
usually run on Linux. A test that only passes on one platform is a defect, not a local issue.

When full portability isn't possible (e.g. a platform-specific API or tool), the limitation is
stated explicitly in a code comment and in the reply, rather than left implicit.

## 2. Line endings

- Git stores text files with LF, and the checked-out line endings depend on the local
  `core.autocrlf` setting: usually CR LF on Windows, LF on Linux CI agents
- Code that reads text (files, streams, test resources, multi-line strings) doesn't assume a
  line ending. A line reader or a normalization is used (e.g. `StreamReader.ReadLine` or
  `ReplaceLineEndings` in .NET, `split(/\r?\n/)` in TypeScript), never a split on `"\r\n"` or
  on the platform newline for content that comes from outside the process
- The platform newline (`Environment.NewLine`, `os.EOL`) is only used for output meant for the
  local platform. When a file format defines its line ending, that line ending is written
  explicitly
- Multi-line expected values in tests (including raw string or template literals, which take the
  line endings of the source file) are compared after normalization
- When a test resource must keep its exact bytes (e.g. a sample that reproduces a CR LF file
  format), it's either marked `-text` in `.gitattributes`, or its content is rebuilt in the
  expected format by the test

## 3. Paths and file names

- Paths are built with the platform path API (`Path.Combine`, `path.join`), never with a
  hard-coded `\` or `/` separator
- File systems on Linux are case-sensitive: file names, resource names, imports and paths in
  configuration match the exact case of the file
- No absolute path specific to one machine or platform (drive letters, `/home/...`) is written in
  code, tests or committed configuration

## 4. Encoding, culture and time zone

- The encoding is always given explicitly when reading or writing text; the platform default is
  never relied on
- Parsing and formatting that must not depend on the machine use an invariant culture
  (`CultureInfo.InvariantCulture` in .NET)
- Time zones are resolved by IANA ID (e.g. `Europe/Zurich`), which works on both platforms

## 5. Verification

When a test reads a text resource or compares multi-line text, it's also run once with LF line
endings (e.g. an LF copy of the resource made with `dos2unix`), to reproduce a Linux checkout,
before it's considered done.
