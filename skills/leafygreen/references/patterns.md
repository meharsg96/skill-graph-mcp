# LeafyGreen Implementation Patterns

Artifact-compatible React implementations for LeafyGreen components.
All components use CSS custom properties injected from `graph/tokens.json`.

---

## Token Injection Template

Every LeafyGreen artifact starts with this shell. The `lgTokens` string is generated from
`graph/tokens.json` — query for the needed theme first, then build the `:root` block.

```jsx
import { useState } from "react";

const lgTokens = `
:root {
  /* ── Palette (from graph/tokens.json → palette) ── */
  --lg-white: #FFFFFF;
  --lg-black: #001E2B;
  --lg-gray-dark4: #112733;
  --lg-gray-dark3: #1C2D38;
  --lg-gray-dark2: #3D4F58;
  --lg-gray-dark1: #5C6C75;
  --lg-gray-base: #889397;
  --lg-gray-light1: #C1C7C6;
  --lg-gray-light2: #E8EDEB;
  --lg-gray-light3: #F9FBFA;
  --lg-green-dark3: #023430;
  --lg-green-dark2: #00684A;
  --lg-green-dark1: #00A35C;
  --lg-green-base: #00ED64;
  --lg-green-light1: #71F6BA;
  --lg-green-light2: #C0FAE6;
  --lg-green-light3: #E3FCF7;
  --lg-blue-dark3: #0C2657;
  --lg-blue-dark2: #083C90;
  --lg-blue-dark1: #1254B7;
  --lg-blue-base: #016BF8;
  --lg-blue-light1: #0498EC;
  --lg-blue-light2: #C3E7FE;
  --lg-blue-light3: #E1F7FF;
  --lg-purple-dark3: #2D0B59;
  --lg-purple-dark2: #5E0C9E;
  --lg-purple-base: #B45AF2;
  --lg-purple-light2: #F1D4FD;
  --lg-purple-light3: #F9EBFF;
  --lg-yellow-dark3: #4C2100;
  --lg-yellow-dark2: #944F01;
  --lg-yellow-base: #FFC010;
  --lg-yellow-light2: #FFEC9E;
  --lg-yellow-light3: #FEF7DB;
  --lg-red-dark3: #5B0000;
  --lg-red-dark2: #970606;
  --lg-red-base: #DB3030;
  --lg-red-light1: #FF6960;
  --lg-red-light2: #FFCDC7;
  --lg-red-light3: #FFEAE5;

  /* ── Typography ── */
  --lg-font-default: 'Euclid Circular A', 'Helvetica Neue', Helvetica, Arial, sans-serif;
  --lg-font-code: 'Source Code Pro', Menlo, monospace;
  --lg-fw-regular: 400;
  --lg-fw-medium: 500;
  --lg-fw-semibold: 600;
  --lg-body1-size: 13px;
  --lg-body1-lh: 20px;
  --lg-body2-size: 16px;
  --lg-body2-lh: 28px;

  /* ── Spacing ── */
  --lg-space-100: 4px;
  --lg-space-150: 6px;
  --lg-space-200: 8px;
  --lg-space-300: 12px;
  --lg-space-400: 16px;
  --lg-space-500: 20px;
  --lg-space-600: 24px;
  --lg-space-800: 32px;
  --lg-space-1000: 40px;

  /* ── Radii ── */
  --lg-radius-50: 2px;
  --lg-radius-100: 4px;
  --lg-radius-150: 6px;
  --lg-radius-200: 8px;
  --lg-radius-300: 12px;
  --lg-radius-600: 24px;

  /* ── Shadows (light) ── */
  --lg-shadow-1: 0px 2px 4px 1px rgba(0, 30, 43, 0.15);
  --lg-shadow-2: 0px 18px 18px -15px rgba(0, 30, 43, 0.20);
  --lg-shadow-3: 0px 8px 20px -8px rgba(0, 30, 43, 0.60);

  /* ── Interaction rings ── */
  --lg-focus-ring: 0 0 0 2px #FFFFFF, 0 0 0 4px #016BF8;
  --lg-focus-ring-input: 0 0 0 3px #016BF8;
  --lg-hover-ring-gray: 0 0 0 3px #E8EDEB;
  --lg-hover-ring-green: 0 0 0 3px #C0FAE6;
  --lg-hover-ring-red: 0 0 0 3px #FFCDC7;

  /* ── Transitions ── */
  --lg-transition-default: 150ms;
  --lg-transition-slower: 300ms;

  /* ── Semantic (light mode) ── */
  --lg-bg-primary: var(--lg-white);
  --lg-bg-secondary: var(--lg-gray-light3);
  --lg-text-primary: var(--lg-black);
  --lg-text-secondary: var(--lg-gray-dark1);
  --lg-text-disabled: var(--lg-gray-base);
  --lg-text-link: var(--lg-blue-base);
  --lg-border-primary: var(--lg-gray-base);
  --lg-border-secondary: var(--lg-gray-light2);
}

/* Dark mode override */
[data-theme="dark"], .lg-dark {
  --lg-bg-primary: var(--lg-black);
  --lg-bg-secondary: var(--lg-gray-dark4);
  --lg-text-primary: var(--lg-white);
  --lg-text-secondary: var(--lg-gray-light1);
  --lg-text-disabled: var(--lg-gray-dark1);
  --lg-text-link: var(--lg-blue-light1);
  --lg-border-primary: var(--lg-gray-dark1);
  --lg-border-secondary: var(--lg-gray-dark2);
  --lg-shadow-1: unset;
  --lg-shadow-2: 0 18px 18px -15px rgba(0, 0, 0, 0.45);
  --lg-shadow-3: 0 8px 20px -8px rgba(0, 0, 0, 0.60);
  --lg-focus-ring: 0 0 0 2px #001E2B, 0 0 0 4px #0498EC;
  --lg-focus-ring-input: 0 0 0 3px #0498EC;
  --lg-hover-ring-gray: 0 0 0 3px #3D4F58;
}
`;

export default function App() {
  return (
    <>
      <style>{lgTokens}</style>
      <div style={{
        fontFamily: "var(--lg-font-default)",
        color: "var(--lg-text-primary)",
        backgroundColor: "var(--lg-bg-primary)",
        fontSize: "var(--lg-body2-size)",
        lineHeight: "var(--lg-body2-lh)",
        minHeight: "100vh"
      }}>
        {/* Build with var(--lg-*) tokens exclusively */}
      </div>
    </>
  );
}
```

**Rule**: Never hardcode hex values. Always reference `var(--lg-*)` custom properties.

---

## Core Component Patterns

The following patterns implement LeafyGreen components using only inline styles and CSS variables.
Each is a functional React component that can be dropped into the template above.

For the full variant/size/state specs, query `graph/components.json`:
```bash
python scripts/query_graph.py component button
python scripts/query_graph.py component textInput
python scripts/query_graph.py components --category form
```

### Button

Supports 5 variants × 4 sizes. Hover and focus use event handlers.
Spec source: `graph/components.json → categories.form.button`

```jsx
const LgButton = ({ variant = 'default', size = 'default', disabled, children, onClick }) => {
  const sizes = {
    xsmall: { height: 22, fontSize: 12, padding: '0 6px' },
    small: { height: 28, fontSize: 12, padding: '0 10px' },
    default: { height: 36, fontSize: 13, padding: '0 12px' },
    large: { height: 48, fontSize: 16, padding: '0 20px' },
  };
  const variants = {
    default: { bg: 'var(--lg-white)', color: 'var(--lg-black)', border: '1px solid var(--lg-gray-base)', hoverBg: 'var(--lg-gray-light2)', ring: 'var(--lg-hover-ring-gray)' },
    primary: { bg: 'var(--lg-green-dark2)', color: 'var(--lg-white)', border: 'none', hoverBg: 'var(--lg-green-dark3)', ring: 'var(--lg-hover-ring-green)' },
    primaryOutline: { bg: 'transparent', color: 'var(--lg-green-dark2)', border: '1px solid var(--lg-green-dark2)', hoverBg: 'var(--lg-green-light3)', ring: 'var(--lg-hover-ring-green)' },
    danger: { bg: 'var(--lg-red-base)', color: 'var(--lg-white)', border: 'none', hoverBg: 'var(--lg-red-dark2)', ring: 'var(--lg-hover-ring-red)' },
    dangerOutline: { bg: 'transparent', color: 'var(--lg-red-base)', border: '1px solid var(--lg-red-base)', hoverBg: 'var(--lg-red-light3)', ring: 'var(--lg-hover-ring-red)' },
  };
  const s = sizes[size]; const v = variants[variant];
  return <button disabled={disabled} onClick={onClick} style={{
    height: s.height, fontSize: s.fontSize, padding: s.padding,
    fontFamily: 'var(--lg-font-default)', fontWeight: 'var(--lg-fw-medium)',
    borderRadius: 'var(--lg-radius-150)',
    background: disabled ? 'var(--lg-gray-light2)' : v.bg,
    color: disabled ? 'var(--lg-gray-base)' : v.color,
    border: disabled ? '1px solid var(--lg-gray-light2)' : v.border,
    cursor: disabled ? 'not-allowed' : 'pointer',
    display: 'inline-flex', alignItems: 'center', gap: 6,
    transition: 'all var(--lg-transition-default) ease-in-out',
    outline: 'none', lineHeight: 1, whiteSpace: 'nowrap',
  }}
    onMouseEnter={e => { if (!disabled) { e.currentTarget.style.background = v.hoverBg; e.currentTarget.style.boxShadow = v.ring; }}}
    onMouseLeave={e => { if (!disabled) { e.currentTarget.style.background = v.bg; e.currentTarget.style.boxShadow = 'none'; }}}
    onFocus={e => { e.currentTarget.style.boxShadow = 'var(--lg-focus-ring)'; }}
    onBlur={e => { e.currentTarget.style.boxShadow = 'none'; }}
  >{children}</button>;
};
```

### TextInput

Spec source: `graph/components.json → categories.form.textInput`

```jsx
const LgTextInput = ({ label, description, errorMessage, value, onChange, placeholder, disabled, optional }) => {
  const [focused, setFocused] = useState(false);
  const hasError = !!errorMessage;
  return <div style={{ marginBottom: 'var(--lg-space-400)' }}>
    {label && <label style={{ display: 'block', fontSize: 14, fontWeight: 'var(--lg-fw-semibold)', color: 'var(--lg-text-primary)', marginBottom: 4, fontFamily: 'var(--lg-font-default)' }}>
      {label}{optional && <span style={{ color: 'var(--lg-text-secondary)', fontWeight: 400 }}> (Optional)</span>}
    </label>}
    {description && <p style={{ fontSize: 'var(--lg-body1-size)', color: 'var(--lg-text-secondary)', marginBottom: 4, marginTop: 0 }}>{description}</p>}
    <input type="text" value={value} onChange={onChange} placeholder={placeholder} disabled={disabled}
      onFocus={() => setFocused(true)} onBlur={() => setFocused(false)}
      style={{
        width: '100%', height: 36, borderRadius: 'var(--lg-radius-150)',
        border: `1px solid ${hasError ? 'var(--lg-red-base)' : focused ? 'var(--lg-blue-base)' : 'var(--lg-gray-base)'}`,
        padding: '0 12px', fontSize: 'var(--lg-body1-size)', fontFamily: 'var(--lg-font-default)',
        color: 'var(--lg-text-primary)', background: disabled ? 'var(--lg-gray-light2)' : 'var(--lg-bg-primary)',
        boxShadow: focused ? 'var(--lg-focus-ring-input)' : 'none', outline: 'none',
        transition: 'all var(--lg-transition-default) ease-in-out', boxSizing: 'border-box',
      }} />
    {hasError && <p style={{ fontSize: 'var(--lg-body1-size)', color: 'var(--lg-red-base)', marginTop: 4, marginBottom: 0 }}>{errorMessage}</p>}
  </div>;
};
```

### Card, Badge, Banner, Table, Tabs, Toggle, Select, Modal, Tooltip, SegmentedControl, Checkbox, ProgressBar, SideNav

These follow the same pattern as Button and TextInput. Query the component spec from
`graph/components.json`, then implement using:
- `var(--lg-*)` for all visual values
- Event handlers for hover/focus states
- Variant/size maps derived from the JSON spec

For the full implementation code for all of these, derive the pattern from the component's
`variantSpecs`, `sizeSpecs`, `styles`, and `states` in the graph data.

**Key patterns shared by all components:**
- `fontFamily: 'var(--lg-font-default)'` on every text element
- `transition: 'all var(--lg-transition-default) ease-in-out'` on all interactive elements
- Focus: `boxShadow: 'var(--lg-focus-ring)'` (buttons) or `'var(--lg-focus-ring-input)'` (inputs)
- Disabled: gray.light2 background, gray.base text, not-allowed cursor
- Card: radius.600, shadow.1, padding spacing.600, shadow.2 on hover
- Table header: gray.light3 bg, 12px uppercase semiBold, 0.4px letter-spacing
- Badge: variant light3 bg + dark2 text, radius.100, 12px uppercase semiBold
- Banner: variant light3 bg + 3px left border in dark2/base color
- Tabs: green.dark2 3px bottom border on active, gray.light2 container bottom border
- Toggle: green.dark1 on-state, gray.light1 off-state, white thumb with shadow
- Modal: rgba(0,30,43,0.6) overlay, radius.600, shadow.3, spacing.800 padding
