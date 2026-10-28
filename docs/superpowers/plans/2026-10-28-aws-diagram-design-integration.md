# AWS Diagram Design Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the user's Claude Code `aws-diagram-design` skill to the visual page planner and every individual drawing call.

**Architecture:** Bundle the installed plugin's design documents in `agent/skills/aws-diagram-design`. A small runtime adapter selects planning guidance or common drawing guidance plus the chosen type reference. Keep the existing JSON → inline SVG → page renderer pipeline.

**Tech Stack:** Python, existing LangChain model interface, Markdown skill documents, pytest.

**Spec:** The scope and constraints below implement the conversation's agreed integration with `aws-diagram-design`, including the earlier sensor-location and robot-motion examples.

## Global Constraints

- Source the Claude Code installation of `aws-diagram-design@hi-aws-skills`, plugin version 2.1.3, commit `873e1a0c7c516e2e96af3b32a1db47d41e553415`.
- Preserve the original design documents and MIT license; identify TransMeet-specific additions separately.
- Load the configured skills directory, never a developer's plugin cache, in production.
- Retain inline SVG, theme variables, mobile typography, accessibility, and the existing validator.
- Pass only the selected type's reference to a drawing call; preserve legacy figure kinds.
- Supplement the upstream catalog with physical illustrations, coverage, cutaways, exploded views, motion paths, and storyboards without inventing physical specifications.

## Review Focus

- Both overview variants must receive the same selected design type.
- A topic must receive its own type reference, not another topic's.
- Old or malformed kind values must not become filesystem paths.
- A custom workspace skills directory must reach the planner and drawer.
- Standalone-SVG, font-embedding, shell, and interactive instructions in upstream references must not override the page's output contract.

## Task 1: Bundle and connect the skill

**Files:** Create `agent/runtime/diagram_design.py`, `agent/skills/aws-diagram-design/`, and `agent/tests/test_visual_design.py`; modify `agent/runtime/visual.py`, `agent/runtime/graph.py`, and `agent/runtime/prompts/page_{plan,draw}.md`.

**Interfaces:** `planning_context(skills_dir: Path) -> str`, `drawing_context(skills_dir: Path, kind: object) -> str`; `VisualInputs.skills_dir` carries the configured bundle root; drawing jobs carry `figure_kind` independently of their page slot.

- [x] Write integration tests using a temporary skill bundle with recognizable type-reference markers; inspect the actual model request and validated SVG result.
- [x] Run the new tests and confirm they fail because the runtime does not yet load the skill.
- [x] Copy the pinned upstream documents and license, add a page adapter and physical-illustration guidance, then implement stage-specific loading.
- [x] Update the planner's type selection and carry its selection through both overview and topic drawing jobs.
- [x] Run the focused tests, including legacy kinds, invalid kinds, and workspace configuration.

## Task 2: Verify the generated output

- [x] Run the agent test suite.
- [x] Generate a small synthetic robot example with the existing model configuration, validate its SVG, and inspect the rendered result if model access is available.
- [x] Review the diff for unrelated changes, provenance, unnecessary prompt content, and deployment packaging.
- [x] Report verified results and any remaining limitations; do not deploy as part of this task.

## Validation record

- Before implementation: 12 new tests failed because the visual pipeline had no skill-directory input.
- After integration: the complete agent suite passed, **163 tests**.
- All 35 routed figure kinds loaded successfully from the bundled documents.
- Independent read-only review found no critical or important defect and verified the 30 upstream documents and license byte-for-byte.
- Review follow-ups incorporated: a graph-level custom-bundle regression test and an accessible SVG fixture. Large type references are intentionally loaded only for their selected type.
- The real planner selected `annotated`, `coverage`, `storyboard`, and `cutaway` for the synthetic robot discussion. All four generated SVG types passed the existing validator.
- Browser inspection exposed a sensor leader hidden behind the opaque body. A hit test reproduced the occlusion; the technical-illustration adapter now explicitly draws leaders after physical parts. Regeneration and hit tests confirmed all four leaders remain visible.
- Desktop (1000px), mobile (390px), and dark-mode previews were inspected. The four figures have no duplicate IDs or horizontal page overflow.
- Implementation verification completed before release. Deployment uses the committed agent image; the self-contained example is available at `aws-diagram-design-example.html` in the project root.
