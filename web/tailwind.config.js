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
          400: '#9da2b5',  // bumped for WCAG AA (was #7a8098, 3.35:1 — failed)
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
          cancelled:  '#9da2b5',  // bumped for WCAG AA (aligned with surface-400)
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
