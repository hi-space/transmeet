import type { Config } from 'tailwindcss'
import typography from '@tailwindcss/typography'

const config: Config = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: ['var(--font-geist-sans)', 'system-ui', 'sans-serif'],
        mono: ['var(--font-geist-mono)', 'monospace'],
      },
      typography: {
        sm: {
          css: {
            '--tw-prose-body': 'var(--tw-prose-body)',
            '--tw-prose-headings': 'var(--tw-prose-headings)',
            'h2': {
              fontSize: '0.7rem',
              fontWeight: '700',
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
              paddingTop: '0.75rem',
              marginTop: '0',
              marginBottom: '0.375rem',
            },
            'h3': {
              fontSize: '0.8125rem',
              fontWeight: '600',
              marginTop: '0.625rem',
              marginBottom: '0.25rem',
            },
            'ul': {
              marginTop: '0.25rem',
              marginBottom: '0.25rem',
              paddingLeft: '1.25rem',
            },
            'ol': {
              marginTop: '0.25rem',
              marginBottom: '0.25rem',
              paddingLeft: '1.25rem',
            },
            'li': {
              marginTop: '0.125rem',
              marginBottom: '0.125rem',
            },
            'p': {
              marginTop: '0.25rem',
              marginBottom: '0.25rem',
            },
            'table': {
              fontSize: '0.75rem',
              marginTop: '0.5rem',
              marginBottom: '0.5rem',
            },
            'thead th': {
              fontSize: '0.675rem',
              fontWeight: '600',
              textTransform: 'uppercase',
              letterSpacing: '0.025em',
              paddingTop: '0.375rem',
              paddingBottom: '0.375rem',
              paddingLeft: '0.5rem',
              paddingRight: '0.5rem',
            },
            'tbody td': {
              paddingTop: '0.3125rem',
              paddingBottom: '0.3125rem',
              paddingLeft: '0.5rem',
              paddingRight: '0.5rem',
            },
            'strong': {
              fontWeight: '600',
            },
            'hr': {
              marginTop: '0.75rem',
              marginBottom: '0.75rem',
            },
          },
        },
      },
    },
  },
  plugins: [typography],
}

export default config
