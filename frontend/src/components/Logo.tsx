// EMG monogram, drawn inline — crisp at any size, no asset files.
// Swap this for a real logo file whenever one exists.
export default function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 40 40" className={className ?? 'h-8 w-8'} role="img" aria-label="EMG logo">
      <defs>
        <linearGradient id="emg-gradient" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#3b82f6" />
          <stop offset="1" stopColor="#1e3a8a" />
        </linearGradient>
      </defs>
      <rect x="1" y="1" width="38" height="38" rx="9" fill="url(#emg-gradient)" />
      <text
        x="20"
        y="21"
        textAnchor="middle"
        dominantBaseline="central"
        fill="#ffffff"
        fontFamily="Arial, Helvetica, sans-serif"
        fontWeight="800"
        fontSize="13"
        letterSpacing="0.5"
      >
        EMG
      </text>
    </svg>
  )
}
