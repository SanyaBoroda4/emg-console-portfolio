// Exactly three tabs (Stage 1.5 §3): All · Confirmed · Pending.
// needs_job is reachable only via its stats card.
const TABS: { label: string; value: string | null }[] = [
  { label: 'All', value: null },
  { label: 'Confirmed', value: 'confirmed' },
  { label: 'Pending', value: 'pending' },
]

interface Props {
  active: string | null
  onChange: (status: string | null) => void
}

export default function StatusFilter({ active, onChange }: Props) {
  return (
    <div role="group" aria-label="Filter by status" className="flex flex-wrap gap-2">
      {TABS.map((tab) => {
        const isActive = active === tab.value
        return (
          <button
            key={tab.label}
            type="button"
            onClick={() => onChange(tab.value)}
            aria-pressed={isActive}
            className={`min-h-11 rounded-full border px-4 text-sm font-medium hover:border-gray-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 ${
              isActive
                ? 'border-blue-600 bg-blue-600 text-white hover:border-blue-700'
                : 'border-gray-300 bg-white text-gray-700'
            }`}
          >
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}
