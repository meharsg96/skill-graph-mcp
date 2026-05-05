---
name: leafygreen
description: >
  Produce React artifacts and web UIs that match MongoDB's LeafyGreen design system — the official
  component library used across Atlas, Charts, Compass, and all MongoDB products. Use this skill
  whenever the user asks for MongoDB-branded UI, LeafyGreen-styled components, Atlas-style interfaces,
  or any frontend that should look like an official MongoDB product. Also trigger when the user mentions
  'leafygreen', 'lg-', '@leafygreen-ui', 'mongodb UI', 'Atlas style', 'mongodb design system', or
  asks to build internal tools, dashboards, or demos that should match MongoDB's visual identity.
  This skill provides the complete design token set and component patterns so Claude can faithfully
  reproduce LeafyGreen components using Tailwind + CSS custom properties in artifact environments
  where npm packages are unavailable. Works standalone or as a design-system layer for the Imprint skill.
---

# LeafyGreen

MongoDB's design system, encoded as artifact-ready tokens and component patterns.

LeafyGreen uses Emotion CSS-in-JS and `@leafygreen-ui/*` npm packages. These cannot be imported
in Claude artifacts. This skill solves the problem by encoding the full design system — palette,
typography, spacing, shadows, interaction states, and 60+ component specs — as CSS custom properties
and React component patterns that reproduce LeafyGreen with pixel-level fidelity.

---

## Architecture

This skill separates **instructions** from **structured data** following the typed skill graph pattern.

```
leafygreen/
├── SKILL.md                → you are here (editorial instructions only)
├── graph/
│   ├── tokens.json         → design tokens per theme (palette, typography, spacing, shadows)
│   ├── components.json     → component catalog (variants, sizes, states, props)
│   └── contract.json       → skill ABI (input/output types, dependencies)
├── references/
│   └── patterns.md         → implementation patterns for artifact-compatible rendering
└── scripts/
    └── query_graph.py      → helper for efficient parameter lookups
```

**Critical rule**: Do NOT read entire graph files. Use the query helper to fetch only the
data needed for the current task. Building a dark-themed login form? Load dark theme tokens
and form components only.

---

## Step 0 · Query before building

Before writing ANY component code:

1. **Identify the theme** (light or dark). Query: `python scripts/query_graph.py tokens --theme light`
2. **Identify which components** are needed. Query: `python scripts/query_graph.py component button`
3. **Read** `references/patterns.md` for the artifact rendering strategy and implementation templates.
4. **Inject the CSS custom properties** from the queried tokens into the artifact's `<style>` block.

---

## Rendering Strategy

LeafyGreen components use Emotion CSS-in-JS, which is unavailable in artifact environments.
The rendering strategy is:

1. **Inject tokens as CSS custom properties** — All palette, typography, spacing, and interaction
   tokens become `var(--lg-*)` properties in a `:root` block.
2. **Build components with inline styles referencing tokens** — Every color, size, and spacing
   value references a CSS variable. Zero hardcoded hex values.
3. **Use hover/focus event handlers** — Since artifacts don't support full CSS pseudo-class
   styling, use `onMouseEnter`/`onMouseLeave`/`onFocus`/`onBlur` for interaction states.
4. **Fall back on system fonts** — Euclid Circular A isn't on CDN. The system stack
   `'Euclid Circular A', 'Helvetica Neue', Helvetica, Arial, sans-serif` matches its metrics.

---

## Semantic Color Rules

These are non-negotiable:

- **Green** = primary actions, success states, MongoDB brand accent
- **Blue** = links, focus indicators, informational states
- **Red** = error, danger, destructive actions
- **Yellow** = warning states only
- **Purple** = decorative only, never semantic
- **Gray** = neutral UI chrome, secondary content, borders, backgrounds

**Accessibility**: Green (#00ED64) NEVER used for text on white (fails WCAG AA).
Use green.dark2 (#00684A) for text. Green.base only as fills on dark backgrounds.

---

## Typography Rules

LeafyGreen typography has defining visual traits:

- **H1 and H2 use regular weight (400)**, not bold. This is signature LeafyGreen.
- **H3 uses medium (500)**. Subtitle uses semiBold (600).
- **Overline** is always uppercase with 0.4px letter-spacing, 12px, semiBold.
- **Body2** (16px/28px) is the default body. **Body1** (13px/20px) is compact/secondary.
- **Code** uses Source Code Pro at 13px or 15px.

---

## Integration with Imprint

When used alongside the Imprint skill, LeafyGreen acts as a **flavour override**:

1. Replace Imprint's accent color with green.dark2 (#00684A)
2. Replace Imprint's font stack with the LeafyGreen default font family
3. Apply the LeafyGreen spacing scale instead of Imprint's rhythm
4. Use LeafyGreen component patterns for all standard controls
5. Imprint's craft principles (rhythm, accent economy, datum alignment) still apply

This combination produces interfaces that are both brand-compliant and craft-grade.

---

## MongoDB Leaf SVG

For the MongoDB leaf icon:
```svg
<svg viewBox="0 0 15 32" xmlns="http://www.w3.org/2000/svg">
  <path d="M7.30466 31.2036C7.30466 31.2036 7.61066 30.956 7.75366 30.7896C11.5437 26.4936 12.2837 19.0296 8.54466 13.2696C7.33966 11.5656 5.89666 10.1016 4.74166 8.39958C4.16266 7.54758 3.64666 6.64758 3.25966 5.69358C2.80066 4.56258 3.07766 3.55458 3.43366 2.43558C2.91766 3.31558 2.38966 4.16758 1.98766 5.08758C0.539661 8.37558 0.0316613 11.9376 0.787661 15.4296C1.87366 20.4456 4.97566 24.3696 7.15366 28.9656C7.15366 28.9656 7.28666 29.2776 7.30466 31.2036Z" fill="#00ED64"/>
  <path d="M8.30666 31.4957C8.30666 31.4957 8.41166 30.9117 8.47866 30.6357C8.61166 30.0837 8.76266 29.5317 8.94266 28.9877C9.63966 27.0617 10.7637 25.3497 11.8577 23.6237C14.4257 19.5837 15.5937 15.1437 14.7857 10.2477C14.3417 7.55568 13.1857 5.19168 11.6837 2.93268L11.1977 2.26368C11.1057 2.89968 11.0357 3.40968 10.9357 3.91068C10.6397 5.33568 10.0297 6.65268 9.29466 7.89768C8.47566 9.28368 7.47366 10.5537 6.59166 11.8937C4.93166 14.4177 3.99966 17.1417 4.12266 20.2077C4.23266 22.9437 5.08366 25.4557 6.51966 27.7677C6.82766 28.2657 7.15266 28.7517 7.48066 29.2497L7.74066 29.6157C7.84066 29.7537 8.10466 30.4017 8.30666 31.4957Z" fill="#00684A"/>
  <path d="M7.55366 32C7.55366 32 7.21866 32 7.04766 31.7156C6.84766 31.4516 6.83766 29.8396 6.81766 29.5916C6.77966 29.0596 6.53766 28.5676 6.38766 28.0556C6.37266 28.0076 6.36866 27.9596 6.37466 27.9116C6.38266 27.8636 6.39966 27.8156 6.42766 27.7756C6.73566 27.3436 6.92166 26.8956 7.14966 26.3956C7.26066 26.6956 7.32066 26.9196 7.40466 27.1316C7.50966 27.3996 7.61766 27.6716 7.73366 27.9396C7.76466 28.0076 7.77366 28.0876 7.75266 28.1596C7.60266 28.6116 7.53666 29.0876 7.50066 29.5636C7.46466 30.0036 7.50066 30.4556 7.50066 30.9116C7.50066 30.9576 7.54366 31.1596 7.55366 32Z" fill="#1C2D38"/>
</svg>
```
