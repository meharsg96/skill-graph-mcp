# Adding a parameterized skill to the graph

This is the question every reader will ask after Blog 1–3: *"OK, how do I
actually create a parameterized skill? That sounds like a lot of work."*

Six stages. Three are mostly automatable, two need editorial judgement,
one is the eval loop. The right target is **"does the resulting artifact
look like it was built by someone who uses this library daily?"** — not
"minimum tokens loaded." Tokens are a proxy; native-feel is the real
goal. A pipeline that optimizes the proxy alone (the Blog 1 metric)
loses the editorial quality that makes a skill actually produce good
artifacts. Programme, not algorithm.

The LeafyGreen skill (`skill:leafygreen-ui`, shipped in
[v2.1.0](https://github.com/meharsg96/skill-graph-mcp/releases/tag/v2.1.0))
was produced by exactly this pipeline. We use it as the worked example
throughout.

---

## Stage 1 — Source acquisition

**Input:** a target (npm package name, GitHub URL, design-system docs URL).
**Output:** raw source files on disk.

| Source kind | How to acquire |
|---|---|
| React component library on GitHub | sparse `git clone` of the relevant `packages/*` paths |
| Design system with a docs site | scripted web fetch of token reference pages |
| API client | OpenAPI / `swagger.json` |
| CSS framework | clone the `dist/` and the source SCSS |

For LeafyGreen: cloned [`mongodb/leafygreen-ui`](https://github.com/mongodb/leafygreen-ui),
read each component package's TypeScript source.

**Automatable.** Standard fetch / clone scripts.

---

## Stage 2 — Token extraction

**Output:** `graph/tokens.json` in a normalized schema:
`palette`, `typography`, `spacing`, `borderRadius`, `breakpoints`,
`transitions`, `themes.light`, `themes.dark`.

| Source kind | Approach |
|---|---|
| TypeScript const exports (`tokens.ts`, `palette.ts`) | AST parse, find `Record`-shaped exports |
| CSS custom properties | parse `:root { --foo: …; }` declarations |
| Design tokens W3C format | direct JSON load |
| Sass variables | scrape `$foo: …` declarations |

For LeafyGreen: the `@leafygreen-ui/tokens` package was parsed for
`palette`, `spacing`, `typography`, `borderRadius`, `transitions`. Theme
blocks (`background`, `border`, `text`, `shadow`, `focusRing`,
`hoverRing`) were extracted from the `@leafygreen-ui/palette` theme
exports.

**Fully automatable.** String parsing with known patterns.

---

## Stage 3 — Component cataloging

**Output:** `graph/components.json` (variants, sizes, states, props per
component) and `graph/contract.json` (the skill ABI: input/output types,
dependencies, parameter sources).

The categorization step matters: **classify by prop signature, not name**.

| Prop signature | Category |
|---|---|
| has `onChange` + `value` + `disabled` | `form` |
| has `variant: 'info' \| 'warning' \| 'success' \| 'danger'` | `feedback` |
| has `as` + nested children + breakpoints | `layout` |
| has `selected` + `onClick` + tab/anchor semantics | `navigation` |
| has `data` array + `columns` | `dataDisplay` |
| has nothing visual but a hook signature | `utility` |

For LeafyGreen: categorized into `form`, `layout`, `feedback`,
`navigation`, `dataDisplay`, `utility`. ~60 components total. The
contract declared `input.type=ui_requirements`,
`output.type=react_artifact`, both with versioned schema strings.

**Semi-automatable.** Parsing is mechanical. Classification benefits
from one editorial pass — naming-convention shortcuts mostly work, edge
cases (e.g. `Combobox` straddles form + utility) need a human.

---

## Stage 4 — Constraint analysis

**This is where judgement enters.** What in this library is available in
the artifact environment, and what needs shimming?

For LeafyGreen, three real constraints:

1. **Emotion CSS-in-JS unavailable** in artifact sandboxes → shim with
   CSS custom properties. The `:root { --lg-* }` block at the top of
   every artifact is the workaround.
2. **Euclid Circular A not on CDN** → system font stack matched to its
   metrics (`-apple-system, BlinkMacSystemFont, …`).
3. **Accessibility constraint** that survives the migration:
   `green.base #00ED64` must NEVER be used for text on white — fails
   WCAG AA. Use `green.dark2 #00684A` for text. This becomes a load-bearing
   `accessibility_rules` entry on the skill — same data the eventual
   `validate_artifact` tool (Blog 3) reads.

Other libraries will have their own list:

- **Is it on `esm.sh` / unpkg?** If yes, can import directly.
- **Peer dependencies that won't resolve?** Inline them or shim.
- **Browser APIs unavailable in the sandbox** (e.g. `IntersectionObserver` polyfill needed)?
- **Bundle size too large to ship inline?** Subset the export.

**Editorial.** The output is a *rendering strategy* — the core of what
goes into `SKILL.md`.

---

## Stage 5 — Artifact generation

**Output:** the four canonical files of a parameterized skill.

```
<skill-name>/
├── SKILL.md                    editorial instructions ONLY
├── graph/
│   ├── contract.json           skill ABI (Stage 3 output)
│   ├── tokens.json             design tokens (Stage 2 output)
│   └── components.json         component catalog (Stage 3 output)
├── references/
│   └── patterns.md             implementation templates derived from Stages 3+4
└── scripts/
    └── query_graph.py          lookup helper
```

**`SKILL.md`** — instructions only. State the rendering strategy
(Stage 4), the semantic rules ("primary CTA always green.dark2"), and
the integration boundary ("works alongside Imprint as a flavour
override"). **Never embed the JSON tables here** — that's the bloat the
whole architecture exists to avoid.

**`references/patterns.md`** — artifact-compatible React templates
generated from the component specs + rendering strategy. The token
injection block, the standard component implementations, the common
gotchas. Editorial craft — templates that look hand-written.

**`scripts/query_graph.py`** — the lookup helper. **Mostly templatable**:
the subcommand structure (`tokens --theme dark`, `components --category form`)
is the same for every skill. Only the file paths and field names
change. This script is also the thing agents bypass — its existence
proves the file-read trap; its irrelevance under the typed-graph design
proves the architecture's win.

**Semi-automatable.** Query script is mechanical. Patterns require
editorial craft. SKILL.md requires real writing.

---

## Stage 6 — Package and validate

Two parts:

**Package** — pull the bundle into the typed graph:

1. Add the skill row to `schema/skills.json` (using the contract from
   Stage 3 as the source of truth).
2. Add the parameter doc to `schema/parameters.json` with the tokens +
   components from Stages 2–3.
3. `python scripts/seed.py` to load.
4. `python scripts/route.py <output_type>` to verify it shows up in
   routing.
5. `python scripts/measure_baseline.py` to see how the file-read
   baseline shifts (LeafyGreen pushed it from 15,492 → 60,135 tokens —
   that delta is the architectural win made literal).

**Validate** — the eval loop:

1. Generate test prompts for the skill ("build a dark Atlas-style login
   form", "create a data table with sorting", "make a stats dashboard
   for a MongoDB cluster").
2. Run them against the live MCP server with `claude mcp add` and a
   tagged `SESSION_ID`.
3. `python scripts/analyze.py --session $SESSION_ID --all` after.
4. Eyeball the artifacts. Are they visually indistinguishable from
   what a developer using the source library daily would have written?
   That's the real target. Token efficiency is necessary but not
   sufficient.

For LeafyGreen, the in-progress R2 manifest entries are
`atlas-style dark login (LeafyGreen)` and
`WCAG check on LeafyGreen primary` — both already encoded in
`scripts/measure_baseline.py`'s manifest.

**Eval loop.** Mechanical to run, editorial to judge.

---

## Reading the LeafyGreen skill against this pipeline

| Stage | Where in the repo |
|---|---|
| 1 — source | `https://github.com/mongodb/leafygreen-ui` (external; clone happened pre-repo) |
| 2 — tokens | `skills/leafygreen/graph/tokens.json` (5KB) |
| 3 — components + contract | `skills/leafygreen/graph/components.json` (16KB), `graph/contract.json` (2KB) |
| 4 — rendering strategy | `skills/leafygreen/SKILL.md` (the prose), accessibility rule on `skill:leafygreen-ui.domain_fields` |
| 5 — artifact templates | `skills/leafygreen/references/patterns.md`, `skills/leafygreen/scripts/query_graph.py` |
| 6 — package + eval | `schema/skills.json`, `schema/parameters.json`, the v2.1.0 commit, R1/R2 in `notes/r1-results.md` |

---

## Could this be a meta-skill?

Yes. Stages 1–3 would run as automated scripts. Stage 4 would be a
structured prompt to Claude. Stage 5 would be template-based generation
with an editorial pass. Stage 6 would use existing eval infrastructure.
A meta-skill (working name: `Fabrica` or `Forge`) takes a source target
as input and produces a parameterized `.skill` bundle as output.

That's a different system from the one this repo demonstrates. This repo
is *"agents need typed contracts to compose well."* A meta-skill factory
would be *"here's how to manufacture skills that have those contracts."*
Different question, different reader mode. Plausibly its own series
later — not part of the current arc.

For now, this document captures the process so the next person doesn't
have to rediscover it.
