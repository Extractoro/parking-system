// Inline stroke icons (lucide-style). Decorative: aria-hidden; meaning is always in the text next to them.

type P = { size?: number; color?: string; className?: string }

function base({ size = 18, color = 'currentColor', className }: P) {
  return {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: color,
    strokeWidth: 2,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
    className,
  }
}

/** Brand mark C6: a map pin over a parking bay. */
export function LogoMark({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" fill="none" stroke="#fff" strokeWidth={4.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M24 29s8.5-7.2 8.5-13.8a8.5 8.5 0 0 0-17 0c0 6.6 8.5 13.8 8.5 13.8z" />
      <circle cx="24" cy="15.2" r="3.4" fill="#fff" stroke="none" />
      <path d="M10 33.5v8.5" />
      <path d="M38 33.5v8.5" />
      <path d="M10 42h28" opacity={0.6} />
    </svg>
  )
}

export const AlertIcon = (p: P) => (
  <svg {...base(p)}>
    <circle cx="12" cy="12" r="10" />
    <path d="M12 8v4" />
    <path d="M12 16h.01" />
  </svg>
)

export const CheckCircle = (p: P) => (
  <svg {...base(p)}>
    <circle cx="12" cy="12" r="10" />
    <path d="m8 12 3 3 5-6" />
  </svg>
)

export const Check = (p: P) => (
  <svg {...base({ ...p })} strokeWidth={2.2}>
    <path d="M20 6 9 17l-5-5" />
  </svg>
)

export const Wifi = (p: P) => (
  <svg {...base(p)}>
    <path d="M12 20h.01" />
    <path d="M8.5 16.4a5 5 0 0 1 7 0" />
    <path d="M5 12.9a10 10 0 0 1 14 0" />
    <path d="M2 8.8a15 15 0 0 1 20 0" />
  </svg>
)

export const Plus = (p: P) => (
  <svg {...base(p)} strokeWidth={2.2}>
    <path d="M12 5v14" />
    <path d="M5 12h14" />
  </svg>
)

export const Calendar = (p: P) => (
  <svg {...base(p)}>
    <rect x="3" y="4" width="18" height="18" rx="2" />
    <path d="M16 2v4" />
    <path d="M8 2v4" />
    <path d="M3 10h18" />
  </svg>
)

export const Spinner = (p: P) => (
  <svg {...base(p)} strokeWidth={2.4} className="spin">
    <path d="M21 12a9 9 0 1 1-6.2-8.6" />
  </svg>
)
