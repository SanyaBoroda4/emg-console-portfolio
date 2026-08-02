import { useEffect, useRef, useState } from 'react'
import { addMaterial, searchMaterials } from '../api'

/** Stone-name typeahead (slab scans chapter). Searches the console's
 * materials catalog (fed by delivery slips + n8n feeds); an unknown name
 * can be added on the spot and is remembered for next time. */
export default function MaterialPicker({
  onPick,
  busy = false,
}: {
  onPick: (name: string) => void
  busy?: boolean
}) {
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<{ name: string }[]>([])
  const [open, setOpen] = useState(false)
  const seq = useRef(0)

  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) {
      setHits([])
      return
    }
    const mine = ++seq.current
    const timer = window.setTimeout(() => {
      void searchMaterials(q)
        .then((res) => {
          if (seq.current === mine) {
            setHits(res.materials)
            setOpen(true)
          }
        })
        .catch(() => {})
    }, 120)
    return () => window.clearTimeout(timer)
  }, [query])

  function pick(name: string) {
    setQuery('')
    setHits([])
    setOpen(false)
    onPick(name)
  }

  async function addAndPick() {
    const name = query.trim()
    if (name.length < 2) return
    try {
      await addMaterial(name)
    } catch {
      // catalog add is best-effort; the assignment still proceeds
    }
    pick(name)
  }

  const exactMatch = hits.some(
    (h) => h.name.toLowerCase() === query.trim().toLowerCase(),
  )

  return (
    <div className="relative">
      <input
        type="text"
        value={query}
        disabled={busy}
        placeholder="Start typing a material…"
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => hits.length > 0 && setOpen(true)}
        className="min-h-[48px] w-full rounded-xl border-2 border-gray-200 bg-white px-3.5 text-[16px] text-gray-900 placeholder:text-gray-400 focus:border-blue-600 focus:outline-none disabled:opacity-50"
      />
      {open && query.trim().length >= 2 && (
        <ul className="absolute z-[60] mt-1 w-full overflow-hidden rounded-xl border border-gray-200 bg-white shadow-lg">
          {hits.map((h) => (
            <li key={h.name}>
              <button
                type="button"
                onClick={() => pick(h.name)}
                className="block w-full px-3.5 py-2.5 text-left text-[15px] font-medium text-gray-900 hover:bg-blue-50"
              >
                {h.name}
              </button>
            </li>
          ))}
          {!exactMatch && (
            <li className="border-t border-gray-100">
              <button
                type="button"
                onClick={() => void addAndPick()}
                className="block w-full px-3.5 py-2.5 text-left text-[15px] font-medium text-blue-700 hover:bg-blue-50"
              >
                + Add “{query.trim()}” as a new material
              </button>
            </li>
          )}
        </ul>
      )}
    </div>
  )
}
