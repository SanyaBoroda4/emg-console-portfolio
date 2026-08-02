import { Link } from 'react-router-dom'

/** Renders message text with URLs as real links. Console card URLs
 * (…/deliveries/item/… or /payments/item/…) become in-app links labeled
 * "open that card →" so dedup messages let the manager jump straight to
 * the original card. External URLs open in a new tab. */
export default function LinkifiedText({ text }: { text: string }) {
  const parts = text.split(/(https?:\/\/[^\s]+)/g)
  return (
    <>
      {parts.map((part, i) => {
        if (!/^https?:\/\//.test(part)) return <span key={i}>{part}</span>
        // Trim trailing punctuation the sentence added after the URL.
        const match = /^(.*?)([.,;)!?]*)$/.exec(part)
        const url = match?.[1] ?? part
        const trailing = match?.[2] ?? ''
        const cardPath = /(\/(?:deliveries|payments)\/item\/[0-9a-f-]+)/i.exec(url)?.[1]
        return (
          <span key={i}>
            {cardPath ? (
              <Link
                to={cardPath}
                className="font-semibold text-blue-700 underline dark:text-blue-400"
              >
                open that card →
              </Link>
            ) : (
              <a
                href={url}
                target="_blank"
                rel="noreferrer"
                className="text-blue-700 underline dark:text-blue-400"
              >
                {url}
              </a>
            )}
            {trailing}
          </span>
        )
      })}
    </>
  )
}
