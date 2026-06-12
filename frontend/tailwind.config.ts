import type { Config } from 'tailwindcss'

const colorScale = (light: string, base: string, dark: string) => ({
  50: light,
  100: light,
  200: light,
  300: base,
  400: base,
  500: base,
  600: dark,
  700: dark,
  800: dark,
  900: dark,
})

export default {
  content: ['./index.html', './src/**/*.{vue,ts,tsx}'],
  theme: {
    // Replace the default palette so raw classes like `bg-blue-500` resolve to
    // nothing and surface as visible regressions (the ESLint rule blocks them
    // earlier in development).
    colors: {
      transparent: 'transparent',
      current: 'currentColor',
      white: '#ffffff',
      black: '#000000',
      success: {
        50: '#ecfdf5',
        100: '#d1fae5',
        200: '#a7f3d0',
        300: '#6ee7b7',
        400: '#34d399',
        500: '#10b981',
        600: '#059669',
        700: '#047857',
        800: '#065f46',
        900: '#064e3b',
      },
      warning: {
        50: '#fffbeb',
        100: '#fef3c7',
        200: '#fde68a',
        300: '#fcd34d',
        400: '#fbbf24',
        500: '#f59e0b',
        600: '#d97706',
        700: '#b45309',
        800: '#92400e',
        900: '#78350f',
      },
      danger: {
        50: '#fef2f2',
        100: '#fee2e2',
        200: '#fecaca',
        300: '#fca5a5',
        400: '#f87171',
        500: '#ef4444',
        600: '#dc2626',
        700: '#b91c1c',
        800: '#991b1b',
        900: '#7f1d1d',
      },
      info: {
        50: '#eff6ff',
        100: '#dbeafe',
        200: '#bfdbfe',
        300: '#93c5fd',
        400: '#60a5fa',
        500: '#3b82f6',
        600: '#2563eb',
        700: '#1d4ed8',
        800: '#1e40af',
        900: '#1e3a8a',
      },
      neutral: {
        50: '#fafafa',
        100: '#f5f5f5',
        200: '#e5e5e5',
        300: '#d4d4d4',
        400: '#a3a3a3',
        500: '#737373',
        600: '#525252',
        700: '#404040',
        800: '#262626',
        900: '#171717',
      },
      accent: {
        50: '#f5f3ff',
        100: '#ede9fe',
        200: '#ddd6fe',
        300: '#c4b5fd',
        400: '#a78bfa',
        500: '#8b5cf6',
        600: '#7c3aed',
        700: '#6d28d9',
        800: '#5b21b6',
        900: '#4c1d95',
      },
    },
    fontFamily: {
      sans: ['Inter', 'ui-sans-serif', 'system-ui'],
      mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular'],
    },
    // Exactly four font sizes: display / h2 / body / caption.
    fontSize: {
      caption: ['12px', { lineHeight: '16px' }],
      body: ['14px', { lineHeight: '20px' }],
      h2: ['18px', { lineHeight: '24px' }],
      display: ['24px', { lineHeight: '32px' }],
    },
    // 8 px spacing grid; 4 px half-step kept for sub-pixel alignments.
    spacing: {
      0: '0px',
      1: '4px',
      2: '8px',
      3: '12px',
      4: '16px',
      6: '24px',
      8: '32px',
      12: '48px',
    },
    borderRadius: {
      none: '0px',
      sm: '4px',
      md: '8px',
      lg: '12px',
      full: '9999px',
    },
    extend: {
      boxShadow: {
        card: '0 1px 2px rgba(0,0,0,0.05), 0 1px 3px rgba(0,0,0,0.08)',
      },
    },
  },
  plugins: [],
  // Suppress eslint unused warning; helper retained for incremental extension.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _unused: colorScale,
} satisfies Config
