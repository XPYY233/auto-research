# Auto Research 1.2 workbench layout audit

Status: frozen design and usability contract. This is not a release claim.

The audit compares the installed Fusion workbench, the 0.5.1 rollback UI, the
approved `fusion-concept.html`, and the user's real Mac screenshots. It treats
the workbench as a scientific desktop tool: layout exists to keep the current
research object, its source and the next action visible. Split panes are an
optional comparison capability, not the default information architecture.

## Round 1: global shell and pane budget

The current wide layout can show the activity bar, context sidebar, primary
editor, secondary editor and inspector at the same time. At a normal desktop
width this spends roughly 650 px before the two editors receive any space. It
produces narrow columns, wrapped titles and hidden actions even though every
separator can technically be dragged to zero.

The frozen projection is:

| Task state | Docked surfaces | Temporarily released surfaces |
|---|---|---|
| Browse/list | Activity, context, primary editor | Inspector, secondary editor |
| Inspect one entity | Activity, context, primary editor, inspector when space permits | Unrelated secondary editor |
| Compare two documents | Activity, primary editor, secondary editor | Inspector; context if either editor becomes narrow |
| Read PDF | Activity, PDF editor | Context, inspector, unrelated editor |
| Librarian chat | Activity, history when space permits, chat | Global inspector and unrelated editor |
| Librarian results | Activity, chat, clickable result navigator | History, global inspector and unrelated editor |
| Experiment grid | Activity, sheet context, grid; column inspector only after selection | Unrelated editor |
| Package center | Activity, package destinations, current task | Empty inspector and unrelated editor |
| Settings | Activity, settings categories, settings page | Inspector and secondary editor |

Rules:

- Never render the five-column wall.
- A task-scoped release of a pane must preserve its tabs, requests, scroll and
  the user's saved size. It is not a persisted preference change.
- At least one editor always remains visible.
- Pane restore buttons must be visible in the title bar; the command palette is
  an additional path, not the only path.
- Comfortable density is the default. Compact density may reduce spacing but
  must not shrink scientific values or body text below readable sizes.

## Round 2: page hierarchy and visible primary actions

### Literature

The title/author/DOI filter and paper list stay in the context sidebar. The
editor's top command bar always exposes `导入 PDF`, `开始提取与核验`,
`打开原文` and the review queue when applicable. Extraction progress and the
latest receipt stay beside the current paper instead of disappearing after the
request finishes.

### Search and Librarian

The query is the widest control in the first row. `精确检索` and `图书管理员`
stay beside it. CSV/XLSX exports are secondary to searching and may use one
clearly labelled `导出` control or a dedicated result action row; they must not
compress the query. Librarian is a full editor conversation. The composer is a
comfortable, persistent bottom dock, not a thin footer. Its clickable citations
and recommended papers appear in a right result navigator only when results
exist.

### Evidence detail and evidence AI

Evidence AI exists only after opening a concrete evidence document. Metadata,
visual/PDF source and conversation belong to that document. The conversation is
a full-height panel or tab with multi-turn history, not the bottom strip of the
global inspector. Selecting a list row alone never opens or calls AI.

### Experiment

The empty state and top command bar expose file selection. After a table is
selected, the visible action sequence becomes `AI 辅助预填` then
`确认并导入一次`; the interface does not ask the user to understand internal
draft states. Grid space wins over forms and inspectors. Column details appear
only when a column or cell is selected.

### Package center and dataset export

Official package import, literature package export, personal experiment export,
dataset export and user-package import are destinations in the context sidebar.
The central editor shows one selected workflow at a time with its plan, rights,
start action, job and receipt. `生成计划` is explicitly a preview step; the
file-producing action remains visible immediately below the plan. Long stacked
sections are not the primary navigation.

### Settings

Settings has one category sidebar and one reading-width content page. It never
shows an inspector. Provider connection, Harness state and the four business
capabilities remain distinct. Credential actions are visible in the AI category
and never placed in a global footer.

## Round 3: size, density and continuous workflows

### Width projection

- `>= 1600 px`: context plus one editor and inspector, or context plus two
  editors. Two editors and inspector never dock together.
- `1200–1599 px`: context plus one editor. Inspector is a drawer. Opening a
  second editor temporarily releases context if required to keep both readable.
- `900–1199 px`: one editor. Context and inspector are drawers.
- `< 900 px`: one editor group; tabs retain their identities and can be revisited.
- `< 640 px`: bottom activity navigation; scientific tables may scroll
  horizontally instead of shrinking text into unreadability.

### Comfortable density floor

- Primary toolbar: 52–56 px; primary controls: 34–38 px.
- UI text: normally 12–13 px; explanatory and body text: 13–16 px.
- List rows: 44 px minimum; paper rows use automatic height and 8–12 px gaps.
- Reading column: roughly 760–920 px with 24–44 px content padding.
- Blue is semantic: selection, focus, active tab and active separators. Borders
  remain quiet neutrals with a restrained blue tint; no gradients, glow or card
  wall.

### Continuous acceptance journeys

1. Import PDF, find the new paper, start extraction, leave the page, return and
   see the same job/receipt.
2. Search, open several evidence documents, keep one pinned on the right, return
   focus to results and ask the pinned evidence a question without losing it.
3. Open source PDF, fit/zoom, reveal highlight, close and recover the originating
   tab, scroll and focus.
4. Choose a real CSV/XLSX, inspect real rows, run optional AI prefill, edit column
   meaning and confirm once.
5. Import a package or export a dataset, observe a real job and receive a durable
   receipt and next action.

## Deletion and architecture gate

- Fusion remains the sole production DOM and navigation owner.
- `PaneLayoutController` remains the sole stored geometry authority.
- Remove empty inspector projections, static second-editor facsimiles, dead
  column-editor stubs, duplicate page-level action listeners and style rules
  that preserve the five-column wall.
- Do not restore the 0.5 DOM, direct model routes, browser workbench or old
  navigation. Reuse only its proven service and feedback behavior.
- Layout tests must assert readable projections and stable state, not merely the
  presence of CSS strings.
