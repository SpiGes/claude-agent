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
- A non-image file of `resources/` (e.g. `.xsd`, `.wsdl`, `.docx`) belongs to the document when the
  document links to it, `[<text>](resources/<file>)`. It is published as a page attachment too.

## Checks before publishing

- Every image reference of the document points to an existing file under `resources/`. A
  missing file stops the publication: it is reported, or rendered first when it is a Mermaid
  diagram (see "Rendering to images" in the `diagrams` skill). It is never skipped silently.
- The alt text of an image isn't kept on the Confluence page: the MCP server uses the file name.
  It still matters in the Markdown file.
- A file of `resources/` that no image reference or link of the document uses is not uploaded. It
  is reported, since it may be a leftover or belong to another document.

## Page content

The page body is prepared directly from the Markdown source, without the `doc-confluence` HTML
conversion:
- The top-level title is removed from the body, since it is passed as the page title.
- The remaining headings are moved up one level, so the highest one becomes a level-1 heading.
- The Mermaid code blocks are removed: the rendered image is attached to the page, and the
  source stays in the versioned Markdown file.
- Each image reference is rewritten to the bare file name, `![<alt text>](<file>)`, which the
  MCP server resolves to the page attachment of the same name.
- Each link to a non-image file of `resources/` is rewritten to the absolute download URL of the
  page attachment, `<Confluence base URL>/download/attachments/<page id>/<URL-encoded file name>`:
  the server doesn't resolve a bare file name in a link.
- Each nesting level of a list is indented by 4 spaces, including the continuation lines of an
  item: the Markdown converter of the server flattens a nested list indented by 2 or 3 spaces.
  Only the published copy is re-indented, never the versioned file.
- A page published from a versioned document starts with a note naming the repository and the
  file, which is the reference: changes are made there, then published again.
- The body is passed with `content_format: "markdown"`, through `content_file` when it is long.

## Drop folder

The Confluence MCP server runs in a container of its own: it only reads files under the drop
folder `/shared/mcp-confluence`, which is mounted read only there at the same path. A path
outside it (e.g. in a repository under `/workspace`) is refused by the server.

- Before the publication, the page body (when passed through `content_file`) and the files to
  upload are copied to `/shared/mcp-confluence/<document name>/`, a folder emptied first, so
  that no file of an earlier publication is uploaded by mistake.
- `content_file` and `file_paths` give the absolute paths of these copies.
- The person can open these files on the host before approving the write calls on the MCP
  gateway.
- The folder of the document is deleted once the publication is checked.

## Publication steps

1. A new page is created first (`confluence_create_page`), to get its id. An existing page keeps
   its title, since other pages may link to it by title.
2. All the images and linked files of the document are uploaded as attachments of that page, in a
   single `confluence_upload_attachments` call with the page id and the paths of their copies in
   the drop folder. An attachment that already exists with the same name gets a new version, so
   a file changed in the repository replaces the old one on the page. The result is checked with
   its `uploaded` and `failed` lists, not with its message, whose count can be wrong (e.g.
   "Uploaded 5 attachment(s)" for 25 files).
3. The page is updated (`confluence_update_page`) with the body, so that the image references
   resolve against the attachments that now exist.
4. The published page is read back (`confluence_get_page`) to check that every image is shown
   as an attachment image, not as a broken image or plain text, and that the nested lists keep
   their levels. The result is reported with the list of uploaded files, and the unreferenced
   files found in `resources/`, if any.