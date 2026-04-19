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
      typography: ({ theme }: { theme: (path: string) => string }) => ({
        DEFAULT: {
          css: {
            '--tw-prose-body': theme('colors.slate.600'),
            '--tw-prose-headings': theme('colors.slate.900'),
            '--tw-prose-bold': theme('colors.slate.700'),
            '--tw-prose-bullets': theme('colors.slate.400'),
            '--tw-prose-counters': theme('colors.slate.500'),
            '--tw-prose-th-borders': theme('colors.slate.200'),
            '--tw-prose-td-borders': theme('colors.slate.100'),
            '--tw-prose-hr': theme('colors.slate.200'),
          },
        },
        invert: {
          css: {
            '--tw-prose-body': theme('colors.slate.300'),
            '--tw-prose-headings': theme('colors.slate.100'),
            '--tw-prose-bold': theme('colors.slate.200'),
            '--tw-prose-bullets': theme('colors.slate.500'),
            '--tw-prose-counters': theme('colors.slate.400'),
            '--tw-prose-th-borders': theme('colors.slate.700'),
            '--tw-prose-td-borders': theme('colors.slate.800'),
            '--tw-prose-hr': theme('colors.slate.800'),
          },
        },
        sm: {
          css: {
            fontSize: '0.875rem',
            lineHeight: '1.625',
            'h2': {
              fontSize: '0.75rem',
              fontWeight: '700',
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
              marginTop: '1rem',
              marginBottom: '0.25rem',
              '&:first-child': { marginTop: '0' },
            },
            'h3': {
              fontSize: '0.875rem',
              fontWeight: '600',
              marginTop: '0.75rem',
              marginBottom: '0.25rem',
            },
            'p': {
              marginTop: '0.25rem',
              marginBottom: '0.25rem',
            },
            'ul': {
              listStyleType: 'disc',
              marginTop: '0.25rem',
              marginBottom: '0.25rem',
              paddingLeft: '1.25rem',
            },
            'ol': {
              listStyleType: 'decimal',
              marginTop: '0.25rem',
              marginBottom: '0.25rem',
              paddingLeft: '1.25rem',
            },
            'li': {
              marginTop: '0.125rem',
              marginBottom: '0.125rem',
              paddingLeft: '0.25rem',
            },
            'li::marker': {
              color: 'var(--tw-prose-bullets)',
              fontWeight: '600',
              fontSize: '0.75rem',
            },
            'ol > li::marker': {
              color: 'var(--tw-prose-counters)',
              fontWeight: '700',
              fontSize: '0.6875rem',
            },
            'ul ul, ol ul': {
              listStyleType: 'circle',
              marginTop: '0.125rem',
              marginBottom: '0.125rem',
            },
            'strong': {
              fontWeight: '600',
              color: 'var(--tw-prose-bold)',
            },
            'hr': {
              marginTop: '0.75rem',
              marginBottom: '0.75rem',
              borderColor: 'var(--tw-prose-hr)',
            },
            'table': {
              fontSize: '0.75rem',
              lineHeight: '1.5',
              marginTop: '0.5rem',
              marginBottom: '0.5rem',
              width: '100%',
            },
            'thead': {
              borderBottomWidth: '1px',
              borderColor: 'var(--tw-prose-th-borders)',
            },
            'thead th': {
              fontWeight: '600',
              fontSize: '0.6875rem',
              textTransform: 'uppercase',
              letterSpacing: '0.025em',
              padding: '0.5rem 0.625rem',
              verticalAlign: 'bottom',
            },
            'tbody tr': {
              borderBottomWidth: '1px',
              borderColor: 'var(--tw-prose-td-borders)',
            },
            'tbody td': {
              padding: '0.4375rem 0.625rem',
              verticalAlign: 'top',
            },
          },
        },
      }),
    },
  },
  plugins: [typography],
}

export default config
