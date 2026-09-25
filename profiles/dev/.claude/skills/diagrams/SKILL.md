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
- A diagram that uses subgraphs or class diagram namespaces should start with the directive
  `%%{init: {'themeVariables': {'clusterBkg': 'transparent', 'clusterBorder': '#9e9e9e'}}}%%`,
  which removes the default background of the blocks and keeps only their border. No theme is
  fixed, so the rendering still follows the light or dark mode of the viewer.
- A class diagram whose namespace names contain dots (e.g. `Company.Product.Module`) also sets
  `'class': {'hierarchicalNamespaces': false}` in the same directive:
  `%%{init: {'class': {'hierarchicalNamespaces': false}, 'themeVariables': {'clusterBkg': 'transparent', 'clusterBorder': '#9e9e9e'}}}%%`.
  Without it, each segment of the name becomes its own nested box, the text becomes too small
  to read, and a class can be drawn outside its namespace. The full namespace names are kept in
  the code.
- The default layout engine is used. `layout: elk` was tested on class diagrams and misdraws
  the realization arrows (`<|..`), so it isn't used, even when the default layout routes an
  arrow behind another class.

## C#/.NET UML-style diagrams

- Do not display `CancellationToken`.
- Do not display `Task`: use the underlying type, or nothing if void.
- Omit member/variable types unless omitting them would create ambiguity.

## Image files in design documents

- A Markdown image reference to the exported diagram must be added right after the code block,
  so the diagram also appears where Mermaid isn't rendered (e.g. Confluence).
- The image is placed in the `resources/` folder at the same level as the document, and
  referenced as `![<alt text>](resources/<file>)` (see the `confluence-publish` skill). What
  matters is that the reference points to an existing file.
- A newly created image is named `<type>-diagram-<short-name>.png`, so that a person can tell it
  apart without opening it, e.g. `sequence-diagram-report.png` or
  `component-diagram-overview.png`; an existing image keeps its name.

## Rendering to images

- Use current Mermaid syntax, within what the Mermaid version bundled with the image's `mmdc`
  supports (`mmdc --version`), since the same code is rendered by `mmdc` for the exported images,
  as well as by the claude.ai preview and mermaid.live.
- In a design document, every Mermaid diagram is rendered to the PNG file its image reference
  points to, each time its code block is written or changed, so that the code and the image never
  diverge.
- The rendering is done with `mmdc` (`@mermaid-js/mermaid-cli`, installed in the image). Its
  Puppeteer configuration is written outside the repository, never in the working tree, and the
  output folder is created first, since `mmdc` doesn't create it:
  ```bash
  echo '{ "args": ["--no-sandbox"] }' > /tmp/puppeteer-config.json
  mkdir -p "<document folder>/resources"
  mmdc -i /tmp/diagram.mmd -o "<document folder>/resources/<type>-diagram-<short-name>.png" -p /tmp/puppeteer-config.json -s 2
  ```
  `--no-sandbox` is required, since the container has no unprivileged user namespaces for
  Chromium's own sandbox. `-s 2` doubles the resolution, so the text stays readable once the
  image is scaled down in Confluence.
- The `.mmd` input is a temporary file and is never committed: the Mermaid code block in the
  Markdown document stays the only source of the diagram.
- The generated PNG is opened and checked (text visible, no Mermaid error rendered in place of
  the diagram, readable layout) before the diagram is reported as done.
- A PNG has a fixed look: the default `mmdc` theme on a white background, which is the expected
  rendering for Confluence. It doesn't follow the viewer's light or dark mode, unlike the live
  Mermaid rendering; no `-t` or `-b` option is set.
- A rendering failure is reported together with the `mmdc` error message. The image reference is
  kept in the document, so that the `doc-confluence` skill still marks the place of the missing
  diagram.