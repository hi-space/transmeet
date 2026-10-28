import type { SVGProps } from 'react'

const paths = {
  waveform: 'M3 10v4M7.5 5v14M12 8v8M16.5 3v18M21 9v6',
  menu: 'M4 6h16M4 12h16M4 18h16',
  panelLeft: 'M9 3v18M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z',
  panelRight: 'M15 3v18M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z',
  plus: 'M12 5v14M5 12h14',
  close: 'm6 6 12 12M6 18 18 6',
  search: 'm21 21-4.5-4.5M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z',
  chevronDown: 'm6 9 6 6 6-6',
  chevronRight: 'm9 5 7 7-7 7',
  arrowRight: 'M4 12h16m-6-6 6 6-6 6',
  mic: 'M9 5a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0V5ZM5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8',
  stop: 'M7 5h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z',
  volume: 'm11 5-5 4H3v6h3l5 4V5Zm5 3a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14',
  notes: 'M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5Z',
  summary: 'M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6Zm0 0v6h6M8 13h8m-8 4h6',
  page: 'M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2ZM3 9h18M9 9v12',
  languages: 'M3 5h12M8 3v2m-3 3 7 7M13 5c-1 5-4 8-9 11m9 5 4-10 4 10m-6.8-3h5.6',
  settings:
    'm9.5 3-.6 2.4-2 1.2-2.4-.7L2 10.1l1.8 1.7v2.4L2 15.9l2.5 4.2 2.4-.7 2 1.2.6 2.4h5l.6-2.4 2-1.2 2.4.7 2.5-4.2-1.8-1.7v-2.4l1.8-1.7-2.5-4.2-2.4.7-2-1.2-.6-2.4h-5Z',
  sun: 'M12 3v1m0 16v1M3 12h1m16 0h1M5.6 5.6l.7.7m11.4 11.4.7.7M5.6 18.4l.7-.7M17.7 6.3l.7-.7M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',
  moon: 'M20.8 13A9 9 0 0 1 11 3.2 9 9 0 1 0 20.8 13Z',
  logout: 'M9 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4m6-12 5 4-5 4M9 12h11',
  send: 'm4 4 17 8-17 8 3-8-3-8Zm3 8h14',
  check: 'm5 12 4 4L19 6',
  copy: 'M9 9h11v11H9V9Zm6-4V3H3v12h2',
  refresh: 'M20 7v5h-5M4 17v-5h5m-4-5a8 8 0 0 1 13-2l2 2M4 17l2 2a8 8 0 0 0 13-2',
  download: 'M12 3v12m-5-5 5 5 5-5M4 16v4h16v-4',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  trash: 'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7',
  clock: 'M12 8v4l3 2m6-2a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
  message: 'M21 11a8 8 0 0 1-8 8H7l-4 3V5a2 2 0 0 1 2-2h8a8 8 0 0 1 8 8Z',
  calendar:
    'M8 2v4m8-4v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z',
  loader: 'M21 12a9 9 0 1 1-6.22-8.56',
  sparkles: 'm12 3 2.7 6.3L21 12l-6.3 2.7L12 21l-2.7-6.3L3 12l6.3-2.7L12 3Z',
} as const

export type IconName = keyof typeof paths

export default function Icon({
  name,
  className = 'h-4 w-4',
  ...props
}: SVGProps<SVGSVGElement> & { name: IconName }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={`shrink-0 ${className}`}
      {...props}
    >
      <path d={paths[name]} />
      {name === 'settings' && <circle cx="12" cy="13" r="3" />}
    </svg>
  )
}
