---
name: diagrams
description: Generate Mermaid diagrams (class, ER, sequence, and similar) when explicitly requested or clearly required by a request. Use when the user asks for a diagram, a visual representation of a design, or a UML-style illustration.
---

# Diagrams — general conventions

- Generate diagrams only when they are explicitly requested or clearly required by the request.
- Give every relationship a label that explains its nature, except where the format already carries one (e.g. the messages of a sequence diagram).
- A label that only repeats the names of the two ends carries nothing and must be replaced by a useful wording.
- Keep the diagram semantically correct; relationships must not be simplified only for visual convenience.
- Provide the Mermaid code in a fenced code block with the `mermaid` language identifier, so that it is rendered by the tools that support it.

## Diagrams — SpiGes-specific

- Use current Mermaid syntax without restriction, since the code is only rendered by the claude.ai preview and by mermaid.live, which both follow the latest version.
- A diagram that uses subgraphs should start with the directive
  `%%{init: {'themeVariables': {'clusterBkg': 'transparent', 'clusterBorder': '#9e9e9e'}}}%%`,
  which removes the default background of the blocks and keeps only their border. No theme is
  fixed, so the rendering still follows the light or dark mode of the viewer.
- In a design document, a Markdown image reference to the exported diagram must be added right
  after the code block, so the diagram also appears where Mermaid isn't rendered (e.g. Confluence).
  The image is placed in a folder named after the document, with a file name of the form
  `diagram-01-short-name.png`, and the spaces of the path are written as `%20` in the reference.
- For UML-style diagrams: do not display `CancellationToken`; do not display `Task` (use the
  underlying type, or nothing if void); omit member/variable types unless omitting them would
  create ambiguity. (These three points are C#/.NET-specific.)
