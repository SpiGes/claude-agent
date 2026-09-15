---
name: doc-confluence
description: Convert a file or piece of content into a standalone HTML file formatted for copy-pasting into the Confluence Data Center 9.2.10 visual editor. Use when the user asks to "convert for Confluence", says "Doc Confluence", "Confluence doc", "Convertis pour Confluence", or simply "Confluence" together with a file, or asks to prepare a document/file for pasting into Confluence.
---

# Confluence content conversion

Convert the given content (any file type: markdown, source code, existing HTML, plain text, etc.) into a standalone `.html` file intended for this workflow:

```
Source file -> HTML file -> open in browser -> Ctrl+A / Ctrl+C -> paste into Confluence editor
```

## Rules

- Extract the main document title and report it separately as the Confluence page title. The main title must **not** appear in the generated HTML body (it may appear in `<title>`).
- Normalize the remaining content headings so the highest remaining level starts at `<h1>`.
- Preserve the source content as faithfully as possible: paragraphs, emphasis, lists, nested lists, tables, links, quotes, separators, inline code, and code blocks.
- Do not summarize, rewrite, complete, or silently correct the source content.
- By default, preserve the exact language of the source content; do not translate.
- If a translation is explicitly requested (e.g. "en anglais technique"), translate the content according to that explicit instruction, while still applying the structural rules of this skill (headings, tables, code blocks, etc.).
- Convert code blocks to `<pre><code>...</code></pre>`, preserving their content exactly.
- Remove the Mermaid code blocks. The diagram is attached to the page as an image, and the source stays in the versioned Markdown file, so keeping both makes the page longer without adding anything for a reviewer.
- Replace an image reference whose target doesn't exist yet by a visible marker of the form `[Diagram to insert: <alt text>]`, rendered as a grey line. An `<img>` tag pointing to a relative path would paste as a broken image, and the marker tells where each image goes.
- Convert tables to semantic HTML tables (`<table>`, `<thead>`, `<tbody>`).
- Use only simple HTML and minimal inline/embedded CSS compatible with copy-paste into the Confluence Data Center 9.2.10 visual editor.
- Do not generate Confluence Storage Format, Wiki Markup, macros, JavaScript, or complex CSS.

## Output

Unless told otherwise, produce:
1. The Confluence page title (as text, separate from the file).
2. The generated `.html` file, saved to disk for the user.
