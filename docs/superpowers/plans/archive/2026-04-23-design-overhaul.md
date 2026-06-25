# Cardigan v4 Design Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the Cardigan web dashboard from default Tailwind dark mode into a distinctive, PBS Wisconsin-branded interface with warm professionalism, proper typography, and cohesive color system.

**Architecture:** Foundation-first approach — establish design tokens (color palette, typography, spacing) as CSS custom properties, then cascade changes through shared components, then update individual pages. Each task produces a visible, testable change.

**Tech Stack:** React 18, TypeScript, Tailwind CSS 3, Google Fonts (Atkinson Hyperlegible + Bricolage Grotesque), CSS custom properties via Tailwind `extend`.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `web/tailwind.config.js` | Modify | Design tokens: PBS-derived color palette, font families, spacing scale |
| `web/src/index.css` | Modify | @font-face imports, CSS custom properties, global resets, high contrast overrides |
| `web/index.html` | Modify | Google Fonts preconnect links |
| `web/src/components/Layout.tsx` | Modify | Nav redesign: grouped items, PBS blue active state, brand wordmark |
| `web/src/components/StatusBar.tsx` | Modify | Improved contrast, clearer expand pattern |
| `web/src/components/ui/Modal.tsx` | Create | Shared modal component extracted from 4 inline implementations |
| `web/src/components/ui/Button.tsx` | Create | Primary/secondary/ghost/danger button variants |
| `web/src/utils/statusColors.ts` | Modify | Replace Tailwind defaults with PBS-derived palette |
| `web/src/pages/Home.tsx` | Modify | Dashboard rethink: workstation overview, surface differentiation |
| `web/src/pages/Queue.tsx` | Modify | Apply new button system, fix `bg-gray-850`, filter tab contrast |
| `web/src/pages/JobDetail.tsx` | Modify | Visual hierarchy, action button cleanup, surface differentiation |
| `web/src/pages/Projects.tsx` | Modify | Apply new surfaces and button system |
| `web/src/pages/Settings.tsx` | Modify | Replace emoji tab icons with SVG, tier colors from PBS palette, range slider styling |
| `web/src/pages/ReadyForWork.tsx` | Modify | Apply new surfaces and button system |
| `web/src/pages/System.tsx` | Modify | Apply new surfaces, connection card cleanup |
| `web/src/pages/Help.tsx` | Modify | Apply new typography scale to prose |

---

## Task 1: Design Tokens — Color Palette

**Files:**
- Modify: `web/tailwind.config.js`
- Modify: `web/src/index.css`

This task establishes the PBS Wisconsin-derived color system as the foundation for everything else. All neutrals get a slight blue tint toward PBS blue `#1d4f91`. Interactive colors use PBS blue derivatives. Status colors remain distinct but are warmed to feel cohesive.

- [ ] **Step 1: Define the PBS-derived color palette in Tailwind config**

Replace the existing `colors` extend block with a complete palette. The neutral scale is computed by tinting Tailwind grays toward PBS blue hue (224deg in OKLCH). The `pbs` scale provides interactive/accent colors derived from the actual PBS blue.

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // PBS Wisconsin brand colors (source truth)
        'pbs-blue': '#1d4f91',
        'pbs-red': '#c8102e',

        // PBS-blue-tinted neutral scale (hue ~245, chroma ~0.01)
        // These replace Tailwind grays throughout the app
        surface: {
          50:  '#f0f1f5',
          100: '#dfe1e9',
          200: '#c0c4d1',
          300: '#9da2b5',
          400: '#7a8098',
          500: '#5c6380',
          600: '#464d68',
          700: '#343a52',
          800: '#242939',  // primary card surface
          850: '#1d2233',  // elevated surface
          900: '#161a2a',  // page background
          950: '#0f1220',  // deepest background (status bar)
        },

        // PBS blue interactive scale
        pbs: {
          50:  '#eef3fb',
          100: '#d4e0f5',
          200: '#a9c1eb',
          300: '#7da2e0',
          400: '#5283d6',
          500: '#1d4f91',  // brand blue
          600: '#1a4783',
          700: '#163d71',
          800: '#12335e',
          900: '#0e294c',
        },

        // Status colors — warmed to feel cohesive with blue-tinted neutrals
        status: {
          pending:    '#e5a83b',  // warm amber
          processing: '#5283d6',  // PBS blue-400
          completed:  '#34a86c',  // warm green
          failed:     '#d64545',  // warm red
          paused:     '#d68a45',  // warm orange
          cancelled:  '#7a8098',  // neutral-400
        },
      },
      fontFamily: {
        display: ['"Bricolage Grotesque"', 'system-ui', 'sans-serif'],
        body: ['"Atkinson Hyperlegible"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
}
```

- [ ] **Step 2: Add Google Fonts preconnect and stylesheet to index.html**

Open `web/index.html` and add inside `<head>`, before any other stylesheets:

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:ital,wght@0,400;0,700;1,400;1,700&family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,500;12..96,600;12..96,700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
```

- [ ] **Step 3: Update CSS custom properties and font stack in index.css**

Replace the `:root` and `body` blocks in `web/src/index.css`:

```css
:root {
  font-family: 'Atkinson Hyperlegible', system-ui, sans-serif;
  line-height: 1.6;
  font-weight: 400;
  color-scheme: dark;
  --text-scale: 1;

  /* Type scale — 1.25 ratio (Major Third) */
  --text-xs: 0.75rem;
  --text-sm: 0.875rem;
  --text-base: 1rem;
  --text-lg: 1.125rem;
  --text-xl: 1.25rem;
  --text-2xl: 1.563rem;
  --text-3xl: 1.953rem;
  --text-4xl: 2.441rem;
}
```

- [ ] **Step 4: Update high contrast overrides to use new surface colors**

Replace the `.high-contrast` section in `web/src/index.css`:

```css
/* High contrast mode enhancements */
.high-contrast .bg-surface-900 {
  background-color: #000000 !important;
}

.high-contrast .bg-surface-800,
.high-contrast .bg-surface-850 {
  background-color: #111118 !important;
}

.high-contrast .bg-surface-700 {
  background-color: #1a1a24 !important;
}

.high-contrast .text-surface-400 {
  color: #cccccc !important;
}

.high-contrast .text-surface-300 {
  color: #e5e5e5 !important;
}

.high-contrast .border-surface-700 {
  border-color: #444455 !important;
}

.high-contrast .border-surface-600 {
  border-color: #555566 !important;
}
```

- [ ] **Step 5: Verify fonts load and colors render**

Run: `cd web && npm run dev`

Open in browser. Verify:
- Body text renders in Atkinson Hyperlegible (check DevTools > Computed > font-family)
- The background color on `body` should be noticeably different from pure gray — slightly blue-shifted
- No Tailwind build errors in terminal

- [ ] **Step 6: Commit**

```bash
git add web/tailwind.config.js web/src/index.css web/index.html
git commit -m "feat(web): establish PBS-derived design token system

Add PBS Wisconsin blue-tinted neutral palette, brand color scales,
Atkinson Hyperlegible + Bricolage Grotesque fonts, and Major Third
type scale as the foundation for the design overhaul."
```

---

## Task 2: Global Surface Migration — Replace gray-* with surface-*

**Files:**
- Modify: `web/src/components/Layout.tsx`
- Modify: `web/src/components/StatusBar.tsx`
- Modify: `web/src/pages/Home.tsx`
- Modify: `web/src/pages/Queue.tsx`
- Modify: `web/src/pages/JobDetail.tsx`
- Modify: `web/src/pages/Projects.tsx`
- Modify: `web/src/pages/Settings.tsx`
- Modify: `web/src/pages/ReadyForWork.tsx`
- Modify: `web/src/pages/System.tsx`
- Modify: `web/src/pages/Help.tsx`

This is a mechanical find-and-replace task. Every `gray-*` Tailwind class becomes its `surface-*` equivalent. This immediately shifts the entire app from pure gray to PBS-blue-tinted.

- [ ] **Step 1: Replace all gray color classes with surface equivalents**

Perform these replacements across ALL files listed above. The mapping:

| Old | New |
|-----|-----|
| `bg-gray-950` | `bg-surface-950` |
| `bg-gray-900` | `bg-surface-900` |
| `bg-gray-850` | `bg-surface-850` |
| `bg-gray-800` | `bg-surface-800` |
| `bg-gray-750` | `bg-surface-800` (there is no 750, map to 800) |
| `bg-gray-700` | `bg-surface-700` |
| `text-gray-600` | `text-surface-500` |
| `text-gray-500` | `text-surface-400` |
| `text-gray-400` | `text-surface-400` |
| `text-gray-300` | `text-surface-300` |
| `text-gray-200` | `text-surface-200` |
| `border-gray-800` | `border-surface-800` |
| `border-gray-700` | `border-surface-700` |
| `border-gray-600` | `border-surface-600` |
| `divide-gray-700` | `divide-surface-700` |
| `hover:bg-gray-800` | `hover:bg-surface-800` |
| `hover:bg-gray-700` | `hover:bg-surface-700` |
| `hover:bg-gray-600` | `hover:bg-surface-600` |
| `hover:text-gray-200` | `hover:text-surface-200` |
| `text-white` | `text-white` (keep — white is fine for high-emphasis text) |

Also replace all `text-blue-400`/`bg-blue-600`/`hover:bg-blue-500` interactive colors:

| Old | New |
|-----|-----|
| `text-blue-400` | `text-pbs-400` |
| `text-blue-300` | `text-pbs-300` |
| `hover:text-blue-300` | `hover:text-pbs-300` |
| `bg-blue-600` | `bg-pbs-500` |
| `hover:bg-blue-500` | `hover:bg-pbs-400` |
| `bg-blue-900/20` | `bg-pbs-900/20` |
| `border-blue-500/30` | `border-pbs-500/30` |
| `border-blue-500` | `border-pbs-500` |
| `focus:border-blue-500` | `focus:border-pbs-400` |
| `focus:ring-blue-500` | `focus:ring-pbs-400` |

**Important**: Do NOT replace status-specific colors yet (green-400 for completed, red-400 for failed, etc.) — those will be handled in Task 3.

- [ ] **Step 2: Update index.css high contrast and focus-visible to match**

In `web/src/index.css`, replace the focus-visible blue:

```css
*:focus-visible {
  outline: 2px solid #5283d6; /* pbs-400 */
  outline-offset: 2px;
}
```

- [ ] **Step 3: Verify the app renders correctly**

Run: `cd web && npm run dev`

Open every page in the browser. Verify:
- Background has a subtle blue tint (not pure gray)
- All text remains readable — check contrast ratios on surface-400 text vs surface-900 bg
- Interactive elements (links, buttons) are now PBS blue instead of Tailwind blue
- No visual regressions — everything should look similar but warmer

- [ ] **Step 4: Commit**

```bash
git add -A web/src/
git commit -m "feat(web): migrate all surfaces from gray to PBS-tinted palette

Replace Tailwind gray scale with surface-* tokens and Tailwind blue
with pbs-* brand scale across all components and pages."
```

---

## Task 3: Status Colors — Cohesive Palette

**Files:**
- Modify: `web/src/utils/statusColors.ts`

Replace the Tailwind default status colors with the warmed `status.*` tokens defined in Task 1.

- [ ] **Step 1: Update statusColors.ts to use new palette**

```ts
/**
 * Status color utilities for consistent job status styling across components.
 */

export type JobStatus =
  | 'pending'
  | 'in_progress'
  | 'completed'
  | 'failed'
  | 'investigating'
  | 'paused'
  | 'cancelled'

export function getStatusTextColor(status: string): string {
  switch (status) {
    case 'pending':
      return 'text-status-pending'
    case 'in_progress':
      return 'text-status-processing'
    case 'completed':
      return 'text-status-completed'
    case 'failed':
      return 'text-status-failed'
    case 'investigating':
      return 'text-status-paused'
    case 'paused':
      return 'text-status-paused'
    case 'cancelled':
      return 'text-status-cancelled'
    default:
      return 'text-surface-400'
  }
}

export function getStatusBadgeColor(status: string): string {
  switch (status) {
    case 'pending':
      return 'bg-status-pending/15 text-status-pending border-status-pending/30'
    case 'in_progress':
      return 'bg-status-processing/15 text-status-processing border-status-processing/30'
    case 'completed':
      return 'bg-status-completed/15 text-status-completed border-status-completed/30'
    case 'failed':
      return 'bg-status-failed/15 text-status-failed border-status-failed/30'
    case 'investigating':
      return 'bg-pbs-500/15 text-pbs-400 border-pbs-500/30'
    case 'paused':
      return 'bg-status-paused/15 text-status-paused border-status-paused/30'
    case 'cancelled':
      return 'bg-status-cancelled/15 text-status-cancelled border-status-cancelled/30'
    default:
      return 'bg-surface-500/15 text-surface-400 border-surface-500/30'
  }
}
```

- [ ] **Step 2: Update remaining hardcoded status colors in pages**

Search all page files for hardcoded status color references like `text-yellow-400`, `text-green-400`, `text-red-400` that are used in status contexts (stat cards, phase icons, queue displays). Replace with `text-status-pending`, `text-status-completed`, `text-status-failed`, etc.

Key locations:
- `Home.tsx`: StatCard color props (lines ~96-112)
- `StatusBar.tsx`: queue count colors (lines ~87-93, ~127-140)
- `JobDetail.tsx`: phaseStatusIcon function (lines ~360-372), progress bar (`bg-blue-500` → `bg-pbs-400`)
- `System.tsx`: connection status dot colors, queue status display
- `Settings.tsx`: system component status dots

- [ ] **Step 3: Verify status colors across all pages**

Check Dashboard stat cards, Queue filter tabs and status badges, JobDetail phase icons, System connection status, and StatusBar. All should use the warmed status palette and feel cohesive.

- [ ] **Step 4: Commit**

```bash
git add web/src/utils/statusColors.ts web/src/pages/ web/src/components/StatusBar.tsx
git commit -m "feat(web): unify status colors with PBS-warmed palette

Replace default Tailwind status colors with cohesive warmed variants
that harmonize with the PBS-blue-tinted surface system."
```

---

## Task 4: Button Component

**Files:**
- Create: `web/src/components/ui/Button.tsx`

Extract a shared button component with 4 variants: primary (PBS blue), secondary (outlined), ghost (text-only), and danger (red). This replaces the 8+ ad-hoc button styles across the app.

- [ ] **Step 1: Create the Button component**

```tsx
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
type ButtonSize = 'sm' | 'md' | 'lg'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  children: ReactNode
}

const variantStyles: Record<ButtonVariant, string> = {
  primary:   'bg-pbs-500 hover:bg-pbs-400 text-white',
  secondary: 'bg-transparent border border-surface-600 text-surface-200 hover:bg-surface-800 hover:border-surface-500',
  ghost:     'bg-transparent text-surface-300 hover:text-white hover:bg-surface-800',
  danger:    'bg-status-failed/80 hover:bg-status-failed text-white',
}

const sizeStyles: Record<ButtonSize, string> = {
  sm: 'px-2.5 py-1 text-xs',
  md: 'px-4 py-2 text-sm',
  lg: 'px-5 py-2.5 text-base',
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'primary', size = 'md', className = '', children, disabled, ...props }, ref) => {
    return (
      <button
        ref={ref}
        disabled={disabled}
        className={`
          inline-flex items-center justify-center gap-1.5
          rounded-md font-medium transition-colors
          disabled:opacity-50 disabled:pointer-events-none
          ${variantStyles[variant]}
          ${sizeStyles[size]}
          ${className}
        `.trim().replace(/\s+/g, ' ')}
        {...props}
      >
        {children}
      </button>
    )
  }
)

Button.displayName = 'Button'
export default Button
export type { ButtonVariant, ButtonSize, ButtonProps }
```

- [ ] **Step 2: Verify button renders in isolation**

Temporarily import Button into Home.tsx and render all 4 variants to confirm styling. Remove after verification.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/ui/Button.tsx
git commit -m "feat(web): add shared Button component with PBS-branded variants

Four variants (primary/secondary/ghost/danger) and three sizes
replace 8+ ad-hoc button styles across the app."
```

---

## Task 5: Modal Component

**Files:**
- Create: `web/src/components/ui/Modal.tsx`

Extract the shared modal pattern from the 4 inline implementations (Layout keyboard shortcuts, JobDetail output viewer, JobDetail retry dialog, Projects artifact viewer).

- [ ] **Step 1: Create the Modal component**

```tsx
import { useEffect, useRef, type ReactNode } from 'react'
import { useFocusTrap } from '../../hooks/useFocusTrap'

interface ModalProps {
  isOpen: boolean
  onClose: () => void
  title: string
  children: ReactNode
  /** Max width class — defaults to 'max-w-lg' */
  maxWidth?: string
  /** ID for aria-labelledby */
  titleId?: string
}

export default function Modal({
  isOpen,
  onClose,
  title,
  children,
  maxWidth = 'max-w-lg',
  titleId,
}: ModalProps) {
  const modalRef = useFocusTrap(isOpen)
  const generatedId = useRef(`modal-title-${Math.random().toString(36).slice(2, 8)}`).current
  const labelId = titleId || generatedId

  useEffect(() => {
    if (!isOpen) return
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleEscape)
    return () => document.removeEventListener('keydown', handleEscape)
  }, [isOpen, onClose])

  if (!isOpen) return null

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        ref={modalRef}
        className={`bg-surface-900 rounded-lg border border-surface-700 w-full ${maxWidth} max-h-[90vh] flex flex-col`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelId}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-surface-700">
          <h3 id={labelId} className="text-lg font-display font-semibold text-white">
            {title}
          </h3>
          <button
            onClick={onClose}
            className="text-surface-400 hover:text-white text-2xl leading-none p-1"
            aria-label="Close"
          >
            &times;
          </button>
        </div>
        {/* Content */}
        <div className="flex-1 overflow-auto p-6">
          {children}
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add web/src/components/ui/Modal.tsx
git commit -m "feat(web): add shared Modal component

Extracts common modal pattern with focus trapping, escape-to-close,
and backdrop click from 4 inline implementations."
```

---

## Task 6: Navigation Redesign

**Files:**
- Modify: `web/src/components/Layout.tsx`

Redesign the nav to: group work items vs utility items, add PBS blue active indicator, give the Cardigan wordmark personality with the display font, and use the new Modal component for keyboard shortcuts.

- [ ] **Step 1: Update Layout.tsx navigation**

Key changes:
1. Use `font-display` on the "Cardigan" wordmark
2. Group nav links: work items (Dashboard, Ready for Work, Queue, Projects) left, utility items (Settings, System, Help) right with a separator
3. Replace `bg-gray-700` active state with a `border-b-2 border-pbs-400 text-white` underline indicator
4. Replace the inline keyboard shortcuts modal with the `<Modal>` component
5. Use new surface colors throughout

The navLinkClass function becomes:

```tsx
const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  `px-3 py-2 text-sm font-medium transition-colors relative ${
    isActive
      ? 'text-white after:absolute after:bottom-0 after:left-1 after:right-1 after:h-0.5 after:bg-pbs-400 after:rounded-full'
      : 'text-surface-300 hover:text-white'
  }`
```

The nav structure becomes:

```tsx
<nav className="bg-surface-850 border-b border-surface-700" aria-label="Main navigation">
  <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
    <div className="flex items-center justify-between h-14">
      {/* Brand */}
      <div className="flex items-center space-x-3">
        <img src="/pbs-logo.svg" alt="PBS Wisconsin" className="h-5" />
        <span className="font-display text-lg font-semibold text-white tracking-tight">
          Cardigan
        </span>
        <span className="text-xs text-surface-500 font-mono">v4</span>
      </div>

      {/* Nav groups */}
      <div className="flex items-center">
        {/* Work items */}
        <div className="flex items-center space-x-1">
          <NavLink to="/" className={navLinkClass} end>Dashboard</NavLink>
          <NavLink to="/ready" className={navLinkClass}>Ready for Work</NavLink>
          <NavLink to="/queue" className={navLinkClass}>Queue</NavLink>
          <NavLink to="/projects" className={navLinkClass}>Projects</NavLink>
        </div>

        {/* Separator */}
        <div className="w-px h-5 bg-surface-700 mx-3" />

        {/* Utility items */}
        <div className="flex items-center space-x-1">
          <NavLink to="/settings" className={navLinkClass}>Settings</NavLink>
          <NavLink to="/system" className={navLinkClass}>System</NavLink>
          <NavLink to="/help" className={navLinkClass}>Help</NavLink>
          {/* Keyboard shortcut button */}
          <button
            ref={triggerRef}
            onClick={() => setShowHelp(true)}
            className="px-2 py-2 text-surface-400 hover:text-white transition-colors"
            aria-label="Keyboard shortcuts (?)"
            title="Keyboard shortcuts (?)"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  </div>
</nav>
```

Replace the inline keyboard shortcuts modal with:

```tsx
<Modal isOpen={showHelp} onClose={closeHelp} title="Keyboard Shortcuts" maxWidth="max-w-md">
  <div className="space-y-3">
    {getKeyboardShortcuts().map((shortcut, index) => (
      <div key={index} className="flex items-center justify-between">
        <span className="text-surface-300">{shortcut.description}</span>
        <kbd className="px-2 py-1 bg-surface-800 border border-surface-600 rounded text-sm font-mono text-surface-300">
          {shortcut.keys}
        </kbd>
      </div>
    ))}
  </div>
  <div className="mt-6 pt-4 border-t border-surface-700">
    <p className="text-xs text-surface-500">
      Press <kbd className="px-1 py-0.5 bg-surface-800 border border-surface-600 rounded text-xs font-mono">?</kbd> to open this help anytime
    </p>
  </div>
</Modal>
```

- [ ] **Step 2: Download and save the PBS logo locally**

Save the PBS Wisconsin logo as a local SVG asset instead of loading from S3:

```bash
curl -o web/public/pbs-logo.svg "https://wisconsinpublictv.s3.us-east-2.amazonaws.com/wp-content/uploads/2023/08/pbs-wisconsin-wblue-rgb-2-412x62.png"
```

Note: if the S3 asset is a PNG, convert it to SVG or keep as PNG and reference as `/pbs-logo.png` in the `<img>` tag. Either way, it's now a local asset.

- [ ] **Step 3: Verify navigation**

Check:
- Cardigan wordmark renders in Bricolage Grotesque
- Active nav item has blue underline indicator
- Work items and utility items are visually separated
- Keyboard shortcuts modal uses the new Modal component
- Focus management still works correctly

- [ ] **Step 4: Commit**

```bash
git add web/src/components/Layout.tsx web/public/pbs-logo.*
git commit -m "feat(web): redesign navigation with PBS brand identity

Group work vs utility nav items, PBS blue active indicator,
Bricolage Grotesque wordmark, local logo asset, shared Modal."
```

---

## Task 7: Dashboard Rethink

**Files:**
- Modify: `web/src/pages/Home.tsx`

Replace the hero-metrics-template stat cards with a workstation overview that answers "What needs my attention?" Differentiate surfaces: the status summary gets a distinct treatment, recent jobs get more useful information.

- [ ] **Step 1: Redesign the dashboard layout**

Replace the identical StatCard grid with a single compact status bar and richer recent jobs:

```tsx
{/* Queue Summary — single compact row, not 4 cards */}
<div className="flex items-center gap-6 px-4 py-3 bg-surface-850 rounded-lg border border-surface-700">
  <span className="text-sm font-medium text-surface-300">Queue</span>
  <div className="flex items-center gap-4 text-sm">
    <span>
      <span className="font-mono font-medium text-status-pending">{stats?.pending ?? 0}</span>
      <span className="text-surface-400 ml-1">pending</span>
    </span>
    <span>
      <span className="font-mono font-medium text-status-processing">{stats?.in_progress ?? 0}</span>
      <span className="text-surface-400 ml-1">processing</span>
    </span>
    <span>
      <span className="font-mono font-medium text-status-completed">{stats?.completed ?? 0}</span>
      <span className="text-surface-400 ml-1">done</span>
    </span>
    {(stats?.failed ?? 0) > 0 && (
      <span>
        <span className="font-mono font-medium text-status-failed">{stats?.failed}</span>
        <span className="text-surface-400 ml-1">failed</span>
      </span>
    )}
  </div>
</div>
```

For the Recent Jobs list, add status indicators that are visual (not just text):

```tsx
{/* Status dot before project name */}
<div className={`w-2 h-2 rounded-full flex-shrink-0 ${
  job.status === 'completed' ? 'bg-status-completed' :
  job.status === 'in_progress' ? 'bg-status-processing animate-pulse' :
  job.status === 'failed' ? 'bg-status-failed' :
  job.status === 'pending' ? 'bg-status-pending' :
  'bg-surface-500'
}`} />
```

Improve the empty state:

```tsx
<div className="px-6 py-12 text-center">
  <p className="text-surface-300 font-medium">No jobs in the queue</p>
  <p className="text-surface-400 text-sm mt-1">
    Upload transcripts from the <Link to="/ready" className="text-pbs-400 hover:text-pbs-300">Ready for Work</Link> page to get started.
  </p>
</div>
```

- [ ] **Step 2: Add display font to page heading**

```tsx
<h1 className="text-2xl font-display font-bold text-white">Dashboard</h1>
```

- [ ] **Step 3: Verify dashboard**

Check that the queue summary is a single compact row (not 4 identical cards), recent jobs have status dots, and the empty state is helpful.

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/Home.tsx
git commit -m "feat(web): redesign dashboard as workstation overview

Replace hero-metrics stat cards with compact queue summary row.
Add status indicator dots, helpful empty state with navigation."
```

---

## Task 8: Apply Button Component Across Pages

**Files:**
- Modify: `web/src/pages/Queue.tsx`
- Modify: `web/src/pages/JobDetail.tsx`
- Modify: `web/src/pages/ReadyForWork.tsx`
- Modify: `web/src/pages/Settings.tsx`
- Modify: `web/src/pages/Projects.tsx`

Replace ad-hoc button styles with the shared `<Button>` component. Apply display font to all page h1 headings.

- [ ] **Step 1: Import Button and replace in Queue.tsx**

Key replacements:
- "+ Upload" → `<Button variant="primary" size="sm">`
- "Clear Failed/Cancelled" → `<Button variant="ghost" size="sm">` (demoted from prominent red)
- Filter tab buttons keep their custom styling (they're a tab pattern, not standalone buttons)
- "Prioritize" → `<Button variant="secondary" size="sm">`
- "Cancel" → `<Button variant="danger" size="sm">`
- Pagination buttons → `<Button variant="secondary">`

- [ ] **Step 2: Import Button and replace in JobDetail.tsx**

Key replacements:
- "Pause" → `<Button variant="secondary" size="sm">`
- "Resume" → `<Button variant="primary" size="sm">`
- "Retry & Escalate" → `<Button variant="primary" size="sm">`
- "Cancel" → `<Button variant="danger" size="sm">`
- "Open Chat" → `<Button variant="secondary" size="sm">`
- "Screengrabs" → `<Button variant="ghost" size="sm">`
- All output file view/download/retry button groups keep inline styles (they're a compound button pattern)

Also replace the retry modal with the `<Modal>` component, and use `<Button>` for its actions.

- [ ] **Step 3: Import Button and replace in ReadyForWork.tsx**

- "Check for New Files" → `<Button variant="primary">`
- "Queue" → `<Button variant="primary" size="sm">`
- "Queue Selected" → `<Button variant="primary" size="sm">`
- "Ignore" → `<Button variant="ghost" size="sm">`

- [ ] **Step 4: Import Button and replace in Settings.tsx**

- "Save Changes" → `<Button variant="primary" size="md">`
- "Reset" → `<Button variant="ghost" size="md">`

- [ ] **Step 5: Add display font to all page h1 headings**

In every page file, add `font-display` to the h1:

```tsx
<h1 className="text-2xl font-display font-bold text-white">Page Title</h1>
```

Pages: Queue, JobDetail (project name), Projects, Settings, System, Help, ReadyForWork.

- [ ] **Step 6: Verify button consistency across pages**

Navigate through every page. Verify:
- Primary actions are PBS blue
- Secondary actions are outlined
- Destructive actions are red
- Ghost actions are text-only
- No orphaned ad-hoc button styles remain
- All h1 headings render in Bricolage Grotesque

- [ ] **Step 7: Commit**

```bash
git add web/src/pages/ web/src/components/
git commit -m "feat(web): apply Button component and display font across all pages

Replace 8+ ad-hoc button styles with 4-variant Button component.
Add Bricolage Grotesque display font to all page headings."
```

---

## Task 9: Settings Page — Replace Emoji Icons, Style Range Sliders

**Files:**
- Modify: `web/src/pages/Settings.tsx`
- Modify: `web/src/index.css`

Replace emoji tab icons with small inline SVGs for consistency across platforms. Add CSS for range slider styling in dark mode. Update tier color system to use PBS-derived colors.

- [ ] **Step 1: Replace emoji tab icons with inline SVG**

Replace the TABS array icons from emoji to SVG markup rendered in a span. Use simple, clean SVG paths (from Heroicons or similar):

```tsx
const TABS: { id: TabId; label: string; icon: ReactNode }[] = [
  { id: 'agents', label: 'Agents', icon: <TabIcon d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0112 15a9.065 9.065 0 00-6.23.693L5 14.5m14.8.8l1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0112 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5" /> },
  { id: 'routing', label: 'Routing', icon: <TabIcon d="M7.5 21L3 16.5m0 0L7.5 12M3 16.5h13.5m0-13.5L21 7.5m0 0L16.5 12M21 7.5H7.5" /> },
  { id: 'worker', label: 'Worker', icon: <TabIcon d="M4.5 12a7.5 7.5 0 0015 0m-15 0a7.5 7.5 0 1115 0m-15 0H3m16.5 0H21m-1.5 0H12m-8.457 3.077l1.41-.513m14.095-5.13l1.41-.513M5.106 17.785l1.15-.964m11.49-9.642l1.149-.964M7.501 19.795l.75-1.3m7.5-12.99l.75-1.3m-6.063 16.658l.26-1.477m2.605-14.772l.26-1.477m0 17.726l-.26-1.477M10.698 4.614l-.26-1.477M16.5 19.794l-.75-1.299M7.5 4.205L12 12m6.894 5.785l-1.149-.964M6.256 7.178l-1.15-.964m15.352 8.864l-1.41-.513M4.954 9.435l-1.41-.514M12.002 12l-3.75 6.495" /> },
  { id: 'ingest', label: 'Ingest', icon: <TabIcon d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" /> },
  { id: 'system', label: 'System', icon: <TabIcon d="M9 17.25v1.007a3 3 0 01-.879 2.122L7.5 21h9l-.621-.621A3 3 0 0115 18.257V17.25m6-12V15a2.25 2.25 0 01-2.25 2.25H5.25A2.25 2.25 0 013 15V5.25m18 0A2.25 2.25 0 0018.75 3H5.25A2.25 2.25 0 003 5.25m18 0V12a2.25 2.25 0 01-2.25 2.25H5.25A2.25 2.25 0 013 12V5.25" /> },
  { id: 'accessibility', label: 'Accessibility', icon: <TabIcon d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z" /> },
]

function TabIcon({ d }: { d: string }) {
  return (
    <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" d={d} />
    </svg>
  )
}
```

- [ ] **Step 2: Add range slider CSS for dark mode**

Add to `web/src/index.css`:

```css
/* Custom range slider styling */
input[type="range"] {
  -webkit-appearance: none;
  appearance: none;
  height: 6px;
  border-radius: 3px;
  background: #343a52; /* surface-700 */
  outline: none;
}

input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #5283d6; /* pbs-400 */
  cursor: pointer;
  border: 2px solid #242939; /* surface-800 */
  transition: background 0.15s;
}

input[type="range"]::-webkit-slider-thumb:hover {
  background: #7da2e0; /* pbs-300 */
}

input[type="range"]::-moz-range-thumb {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #5283d6;
  cursor: pointer;
  border: 2px solid #242939;
}
```

- [ ] **Step 3: Update tier colors to PBS-derived scale**

Replace the arbitrary `TIER_COLORS` and `TIER_STYLES` with:

```tsx
const TIER_STYLES: Record<number, { bg: string; border: string; text: string }> = {
  0: { bg: 'bg-status-completed/10', border: 'border-status-completed/30', text: 'text-status-completed' },
  1: { bg: 'bg-pbs-500/10', border: 'border-pbs-500/30', text: 'text-pbs-400' },
  2: { bg: 'bg-pbs-300/10', border: 'border-pbs-300/30', text: 'text-pbs-300' },
}
```

- [ ] **Step 4: Verify Settings page**

Check all 6 tabs. Verify:
- SVG icons render consistently (no emoji variation)
- Range sliders have PBS blue thumbs
- Tier colors use the PBS-derived palette

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/Settings.tsx web/src/index.css
git commit -m "feat(web): polish Settings with SVG icons, styled sliders, PBS tier colors

Replace emoji tab icons with platform-consistent SVGs. Add dark mode
range slider CSS with PBS blue thumb. Unify tier colors with brand palette."
```

---

## Task 10: Typography Scale — Prose and Help Page

**Files:**
- Modify: `web/src/pages/Help.tsx`
- Modify: `web/src/index.css`

Apply the type scale and display font to the Help page prose rendering. Set up Tailwind typography plugin overrides for dark mode with the new palette.

- [ ] **Step 1: Update prose styles in Help.tsx**

Replace the current prose class string with one using the new palette:

```tsx
<div className="prose prose-invert max-w-none
  prose-headings:font-display prose-headings:scroll-mt-20
  prose-h1:text-[var(--text-3xl)] prose-h1:font-bold prose-h1:text-white prose-h1:border-b prose-h1:border-surface-700 prose-h1:pb-3 prose-h1:mb-6
  prose-h2:text-[var(--text-2xl)] prose-h2:font-semibold prose-h2:text-white prose-h2:mt-10 prose-h2:mb-4
  prose-h3:text-[var(--text-xl)] prose-h3:font-medium prose-h3:text-surface-200
  prose-p:text-surface-300 prose-p:leading-relaxed prose-p:max-w-[75ch]
  prose-a:text-pbs-400 prose-a:no-underline hover:prose-a:underline
  prose-strong:text-white
  prose-code:text-pbs-300 prose-code:bg-surface-800 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:text-sm prose-code:font-mono
  prose-table:border-collapse
  prose-th:bg-surface-850 prose-th:text-surface-200 prose-th:text-left prose-th:px-3 prose-th:py-2 prose-th:border prose-th:border-surface-700 prose-th:text-sm
  prose-td:px-3 prose-td:py-2 prose-td:border prose-td:border-surface-700 prose-td:text-sm prose-td:text-surface-300
  prose-li:text-surface-300
  prose-hr:border-surface-700
">
```

- [ ] **Step 2: Update TOC sidebar styling**

```tsx
<h2 className="text-sm font-display font-semibold text-surface-400 uppercase tracking-wider mb-3">
  Contents
</h2>
```

Active state:
```tsx
activeSection === entry.id
  ? 'text-pbs-400 bg-surface-800'
  : 'text-surface-400 hover:text-surface-200 hover:bg-surface-800/50'
```

- [ ] **Step 3: Verify Help page**

Check that headings render in Bricolage Grotesque, body text in Atkinson Hyperlegible, code blocks in JetBrains Mono. Verify the type scale creates clear hierarchy. Check that body text doesn't exceed ~75ch.

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/Help.tsx web/src/index.css
git commit -m "feat(web): apply type scale and display font to Help page prose

Bricolage Grotesque headings, Atkinson Hyperlegible body, JetBrains Mono
code. 75ch line length cap. PBS-derived link and surface colors."
```

---

## Task 11: Final Polish Pass — CopyEditorHandoff and Remaining Pages

**Files:**
- Modify: `web/src/pages/JobDetail.tsx` (CopyEditorHandoff component)
- Modify: `web/src/pages/System.tsx`
- Modify: `web/src/pages/Projects.tsx`
- Modify: `web/src/components/StatusBar.tsx`

Final pass to catch remaining one-off styles, improve StatusBar contrast, and make the CopyEditorHandoff component match the new design system.

- [ ] **Step 1: Update CopyEditorHandoff to use PBS palette instead of emerald gradient**

Replace the one-off emerald gradient:

```tsx
<div className="bg-pbs-900/20 rounded-lg border border-pbs-500/30 p-6">
```

Replace the emerald icon circle:
```tsx
<div className="w-12 h-12 bg-pbs-500/20 rounded-full flex items-center justify-center">
  <svg className="w-6 h-6 text-pbs-400" ...>
```

Replace emerald text colors with `text-pbs-300`, `text-pbs-400`, button with `<Button variant="primary" size="sm">`.

- [ ] **Step 2: Improve StatusBar contrast**

Increase text contrast: `text-surface-300` → `text-surface-200` for key information. Make the expand button label clearer:

```tsx
<button
  onClick={() => setExpanded(!expanded)}
  className="flex items-center gap-1 text-surface-400 hover:text-surface-200 px-2 py-1 rounded hover:bg-surface-800 transition-colors text-xs"
  aria-expanded={expanded}
  aria-label={expanded ? 'Hide system details' : 'Show system details'}
>
  <span>Details</span>
  <span className={`transform transition-transform text-[10px] ${expanded ? 'rotate-180' : ''}`}>
    ▼
  </span>
</button>
```

- [ ] **Step 3: Update System.tsx connection card and agent roster**

Replace the agent roster tier colors to match the new TIER_STYLES from Task 9. Replace all remaining green-500/red-500 connection status dots with `bg-status-completed`/`bg-status-failed`.

- [ ] **Step 4: Update Projects.tsx — apply display font and surface colors to detail panel**

Add `font-display` to section headers. Make sure the output viewer modal uses `<Modal>` component.

- [ ] **Step 5: Full visual regression check**

Open every page. Check:
- [ ] Dashboard: compact queue row, status dots on jobs, helpful empty state
- [ ] Ready for Work: PBS blue buttons, surface-tinted backgrounds
- [ ] Queue: button hierarchy, filter tab contrast, status badges
- [ ] Job Detail: visual hierarchy, action buttons aren't a rainbow, CopyEditorHandoff matches
- [ ] Projects: detail panel looks cohesive
- [ ] Settings: SVG tab icons, styled sliders, PBS tier colors
- [ ] System: connection card, agent roster colors
- [ ] Help: type scale, display font headings, 75ch body width
- [ ] Status bar: improved contrast, clearer expand label
- [ ] Keyboard shortcuts modal: renders via shared Modal component

- [ ] **Step 6: Commit**

```bash
git add web/src/
git commit -m "feat(web): final design polish — CopyEditorHandoff, StatusBar, remaining pages

Unify CopyEditorHandoff with PBS palette, improve StatusBar contrast,
update System/Projects for consistent surfaces and tier colors."
```

---

## Summary

| Task | What It Does | Files Changed |
|------|-------------|---------------|
| 1 | Design tokens: colors, fonts, type scale | tailwind.config.js, index.css, index.html |
| 2 | Global gray→surface migration | All components and pages |
| 3 | Cohesive status color palette | statusColors.ts, pages with hardcoded status colors |
| 4 | Button component | New: Button.tsx |
| 5 | Modal component | New: Modal.tsx |
| 6 | Navigation redesign | Layout.tsx |
| 7 | Dashboard rethink | Home.tsx |
| 8 | Button + display font across all pages | All pages |
| 9 | Settings polish | Settings.tsx, index.css |
| 10 | Typography / Help page | Help.tsx |
| 11 | Final polish pass | JobDetail, System, Projects, StatusBar |

Total: **11 tasks, ~15 files touched, 2 new component files.**
