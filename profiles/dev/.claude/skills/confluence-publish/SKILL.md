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
- Every relative link to another file of the repository (see "Links to other files of the
  repository") is listed with its target. A target that doesn't exist on the default branch of the
  remote stops the publication, like a missing image: it is reported, never published as a broken
  link.
- A table of contents written by hand in the document is reported (see "Table of contents").
- The parent page of the document is resolved from the folders of the repository (see "Place of
  the page in the page tree"). An ambiguous parent, or a parent document that isn't published yet,
  stops the publication until the person decides; a missing folder page is created.

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
- A blank line is inserted before a list item that directly follows an item of a deeper nesting
  level (bulleted or numbered): without it, the Markdown converter of the server merges the outer
  item into the text of the nested one (e.g. `4. The updated Excel file...` shown at the end of the
  last sub-item). Only the published copy is changed.
- Each relative link to another file of the repository is rewritten to an absolute URL (see
  "Links to other files of the repository").
- A page published from a versioned document starts with a note naming the repository and the
  file, which is the reference: changes are made there, then published again (see "Source note").
- The Confluence table of contents macro follows the note (see "Table of contents").
- The body is passed with `content_format: "markdown"`, through `content_file` when it is long.

### Body preparation

All the changes above are made by the step that generates the published copy, each one on the
element it targets (a link, a list item, a heading). The body is never patched afterwards with a
global text replacement (e.g. `sed` on the whole file): a replacement meant for one place also
changes the others, e.g. a link URL rewritten together with the note. When the generated body is
wrong, the preparation step is fixed and the body generated again.

### Source note

The note is the first line of the body, a blockquote written from this template, with the path of
the file from the repository root, the name of the repository, and its default branch:

```
> **Note:** this page is published from the file `<path of the file>` of the repository <repository name> (branch <branch>), which is the reference. Changes are made in that file, then the page is published again.
```

- The name of the repository is plain text, without a link: the note names the reference, the
  links of the page lead to the content.
- The wording is kept as it is, since the note is also used to find a page already published (see
  "Links to other files of the repository" and "Place of the page in the page tree"): a page with
  another wording isn't found by the search.

### Links to other files of the repository

A relative link to another file of the repository (e.g. `[ITAR_K re-import](../../../itark-upload/spec.md)`)
can't be resolved by Confluence: it is shown as a broken link (red cross). The rule applies to
every link whose target isn't an absolute URL (with a scheme such as `https:` or `mailto:`), isn't
an anchor of the same document (`#...`), and isn't a file of `resources/` (covered above).

- The target is resolved from the folder of the document to a path from the repository root.
- The target is checked to exist on the default branch of the remote (`origin/HEAD`, after a
  `git fetch`), e.g. `git cat-file -e origin/<branch>:<path>`. A file that exists only locally or
  on the current branch would give a dead link.
- When the target document is already published on Confluence, the link is rewritten to its page
  URL (`<Confluence base URL>/pages/viewpage.action?pageId=<page id>`). The page is found from the
  conversation, or by a search in the space for the note that names the same repository and file;
  more than one match isn't guessed: the repository URL is used and the case is reported.
- Otherwise the link is rewritten to the URL of the file on the default branch of the remote, built
  from `git remote get-url origin` without any user information:
  - Azure DevOps: `<repository URL>?path=/<path>&version=GB<branch>`
  - GitHub: `<repository URL without .git>/blob/<branch>/<path>`
  - Bitbucket Data Center: `<server>/projects/<project>/repos/<repository>/browse/<path>?at=refs/heads/<branch>`
- An anchor of the link (`<file>#<heading>`) is dropped: neither target keeps the heading ids of
  the Markdown file. The link text is kept as it is.
- Only the published copy is changed, never the versioned file.

### Table of contents

The page gets the Confluence table of contents macro, which builds the table from the headings of
the page. It is inserted as a single line of storage XML, on its own line with a blank line before
and after it, right after the note that names the repository and the file:

```
<ac:structured-macro ac:name="toc" ac:schema-version="1"><ac:parameter ac:name="outline">false</ac:parameter><ac:parameter ac:name="style">none</ac:parameter></ac:structured-macro>
```

This line goes through the Markdown converter of the server unchanged and is stored as a real
macro. A full update of the page body replaces the macro of an earlier version, so it is inserted
at each publication.

`outline` stays `false`: the chapters of a design document are already numbered in their headings
(`design-documents` skill), and `outline=true` makes Confluence number the entries again from the
heading levels, so each entry would show two numbers (e.g. `1 1. Versions`).

A table of contents written by hand in the document (a heading such as `Table of contents` or
`Contents` followed by a list of the chapters) is removed from the published copy, together with
its heading. It is reported to the person, since it goes against the `design-documents` skill (a
table of contents comes from the publishing platform, never by hand); the versioned file isn't
changed by this skill.

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

## Place of the page in the page tree

The page tree mirrors the folders of the repository below the root folder of the documents (e.g.
`design/`), which matches the root page given by the person (e.g. Design-Spezificationen). A
document is never published flat under the root page when it sits in a sub-folder.

- Each folder between the root folder and the folder of the document is a parent page:
  - a folder without a `spec.md` of its own (e.g. `design/ks-upload/`, which only holds the
    sub-folders `ks-upload-import/` and `ks-upload-input-file/`) gets a folder page. Its title is
    the folder name as it is (`ks-upload`). Its body is a short note naming the folder and the
    repository, followed by the `children` macro, passed with `content_format: "storage"`:

    ```
    <p>Design documents of <topic>, published from the folder <code><folder path></code> of the repository <repository> (branch <branch>).</p><ac:structured-macro ac:name="children" ac:schema-version="2"><ac:parameter ac:name="all">false</ac:parameter></ac:structured-macro>
    ```

  - a folder with a `spec.md` of its own (e.g. `design/export/spec.md`, the general export design,
    whose sub-designs are in `design/export/<sub-folder>/`) gets no folder page: the page of that
    document is the parent page of the documents of its sub-folders. When that document isn't
    published yet, it's reported, and the person decides whether it's published first.
- An existing parent page is reused, never created twice. It's found from the conversation, or by
  a search in the space for its note (the folder path, or the file path of its `spec.md`, and the
  repository); more than one match isn't guessed and is reported. A page of the expected title
  without the note (e.g. a page created by hand before the rule) isn't reused silently: it's
  reported, and the person decides.
- The parent pages are resolved, and the missing folder pages created, from the root page down to
  the folder of the document, before the page of the document is created.
- A page published at the wrong level is moved with `confluence_update_page` and `parent_id`. The
  call needs the full body, so the body is generated again, and the update is a minor edit.
- Existing pages that don't follow the rule (published before it, or from a document not migrated
  to the repository yet) aren't moved unless the person asks for it.

## Publication steps

1. A new page is created first (`confluence_create_page`), with the `parent_id` of its parent page
   (see "Place of the page in the page tree"), to get its id. An existing page keeps its title,
   since other pages may link to it by title.
2. All the images and linked files of the document are uploaded as attachments of that page, in a
   single `confluence_upload_attachments` call with the page id and the paths of their copies in
   the drop folder. An attachment that already exists with the same name gets a new version, so
   a file changed in the repository replaces the old one on the page. The result is checked with
   its `uploaded` and `failed` lists, not with its message, whose count can be wrong (e.g.
   "Uploaded 5 attachment(s)" for 25 files).
3. The page is updated (`confluence_update_page`) with the body, so that the image references
   resolve against the attachments that now exist.
4. The published page is read back (`confluence_get_page`) to check that:
   - every image is shown as an attachment image, not as a broken image or plain text
   - the nested lists keep their levels, and no outer list item is merged into the text of a
     nested item
   - no link is broken: no red cross marker, no `<a>` without `href`, no relative `href` left
   - the source note is the first element of the page, stored as a blockquote
     (`<blockquote><p><strong>Note:</strong> ...`), with the wording of the template
   - the table of contents macro is there, stored as a real macro
     (`ac:structured-macro ac:name="toc"` with an `ac:macro-id`), with `outline` set to `false`.
     The Markdown view of the page only shows the parameter values as plain text (e.g. `truenone`),
     so the check is made on the storage format (`convert_to_markdown: false`)
   - the page is a child of the expected parent page (`confluence_get_page_children` of the
     parent), and each folder page created holds the `children` macro

   The result is reported with the place of the page in the page tree (parent pages, folder pages
   created), the list of uploaded files, the rewritten links with their new target, the
   hand-written table of contents removed, if any, and the unreferenced files found in
   `resources/`, if any.