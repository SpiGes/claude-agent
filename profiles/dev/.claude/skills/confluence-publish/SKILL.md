---
name: confluence-publish
description: Publish a Markdown document (design document, guide, and similar) to a Confluence Data Center page through the Confluence MCP tools, together with its images. Use when the user asks to create or update a Confluence page from a Markdown file. Not for the manual copy-paste workflow, which is covered by the doc-confluence skill.
---

# Publishing a Markdown document to Confluence (MCP)

## Images of a document

- The images of a document are stored in a `resources/` folder at the same level as the
  document, e.g. `design/<feature>/spec.md` and `design/<feature>/resources/`.
- An image belongs to the document only if the document contains a Markdown image reference to
  it, of the form `![<alt text>](resources/<file>)` or `![<alt text>](./resources/<file>)`. Only
  those images are published with the page.

## Checks before publishing

- Every image reference of the document points to an existing file under `resources/`. A
  missing file stops the publication: it is reported, or rendered first when it is a Mermaid
  diagram (see "Rendering to images" in the `diagrams` skill). It is never skipped silently.
- Every image reference has a meaningful alt text, since it is kept on the Confluence page.
- A file of `resources/` that no reference of the document uses is not uploaded. It is
  reported, since it may be a leftover or belong to another document.

## Page content

The page body is prepared directly from the Markdown source, without the `doc-confluence` HTML
conversion:
- The top-level title is removed from the body, since it is passed as the page title.
- The remaining headings are moved up one level, so the highest one becomes a level-1 heading.
- The Mermaid code blocks are removed: the rendered image is attached to the page, and the
  source stays in the versioned Markdown file.
- Each image reference is rewritten to the bare file name, `![<alt text>](<file>)`, which the
  MCP server resolves to the page attachment of the same name.
- The body is passed with `content_format: "markdown"`, through `content_file` when it is long.

## Publication steps

1. The page is created (`confluence_create_page`) or updated (`confluence_update_page`).
2. All the images referenced by the document are uploaded as attachments of that page, in a
   single `confluence_upload_attachments` call with the page id. An attachment that already
   exists with the same name gets a new version, so an image changed in the repository replaces
   the old one on the page.
3. For a new page, the page is updated once more with the same body, so that the image
   references resolve against the attachments that now exist.
4. The published page is read back (`confluence_get_page`) to check that every image is shown
   as an attachment image, not as a broken image or plain text. The result is reported with the
   list of uploaded images, and the unreferenced files found in `resources/`, if any.