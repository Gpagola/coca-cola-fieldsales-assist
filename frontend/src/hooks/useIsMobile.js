import { useState, useEffect } from "react"

/** Detecta si el viewport actual esta por debajo de un ancho (celular/tablet chica).
 * Usa matchMedia + evento "change" (mas eficiente que escuchar "resize"). */
export default function useIsMobile(breakpoint = 820) {
  const query = `(max-width: ${breakpoint}px)`
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== "undefined" ? window.matchMedia(query).matches : false
  )

  useEffect(() => {
    const mql = window.matchMedia(query)
    const handler = (e) => setIsMobile(e.matches)
    mql.addEventListener("change", handler)
    return () => mql.removeEventListener("change", handler)
  }, [query])

  return isMobile
}
