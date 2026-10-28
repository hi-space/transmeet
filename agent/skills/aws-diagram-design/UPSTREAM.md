# Bundled AWS Diagram Design

This is a design-document subset of the user's installed Claude Code plugin
`aws-diagram-design@hi-aws-skills`.

- Repository: https://github.com/hi-space/hi-aws-skills
- Plugin version: **2.1.3**
- Installed commit: `873e1a0c7c516e2e96af3b32a1db47d41e553415`
- Imported on: 2026-10-28
- License: [MIT](LICENSE)

`SKILL.md`, all 27 `references/type-*.md` documents,
`references/semantic-patterns.md`, and `references/primitive-annotation.md`
are unchanged copies. The upstream `SKILL.md` metadata still says `2.0.0`;
the installed plugin manifest identifies this distribution as `2.1.3`.

TransMeet adds these files:

- `references/transmeet.md`: adaptation to the existing tool-free planner,
  inline SVG drawer, page theme, and validator.
- `references/technical-illustration.md`: physical objects, sensor locations
  and fields, cutaways, exploded views, motion, and sequential scenes.
- `references/comparison.md`: compatibility with the existing comparison
  and matrix figure kinds.

`runtime/diagram_design.py` loads the source skill's selection sections for
planning and its drawing sections plus the chosen type's reference for drawing.
It appends the TransMeet adaptation; the stage's output prompt follows last.
The main skill and reference files are not expected to be executed as a
standalone Claude Code workflow inside this application.

The plugin's export scripts, fonts, icon assets, and unrelated import/onboarding
references are not bundled. Their commands and file links in the preserved
documents are upstream context, not runtime capabilities. The page adapter
requires existing CSS variables and supported SVG primitives, and does not
permit fabricated official AWS icons.

Deployment uses the existing `COPY skills/ ./skills/` in `agent/Dockerfile`.
No home directory, Claude Code installation, network lookup, or new dependency
is required to load these documents.

To update, replace the unchanged source documents and license from the desired
installed plugin version, update this provenance, keep the three TransMeet
references, and run `cd agent && .venv/bin/python -m pytest`.
