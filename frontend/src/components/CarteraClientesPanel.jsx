import { useState, useEffect, useCallback } from "react"
import "./PortalVendedor.css"

const API = import.meta.env.VITE_API_URL || "/api"

export default function CarteraClientesPanel({ onIniciarConversacion }) {
  const [clientes, setClientes] = useState([])
  const [q, setQ]               = useState("")
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState(null)

  const cargar = useCallback(async (query) => {
    setLoading(true)
    setError(null)
    try {
      const url = query ? `${API}/cartera/clientes?q=${encodeURIComponent(query)}` : `${API}/cartera/clientes`
      const r = await fetch(url)
      const data = await r.json()
      setClientes(Array.isArray(data) ? data : [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { cargar("") }, [cargar])

  function handleSubmit(e) {
    e.preventDefault()
    cargar(q)
  }

  return (
    <div className="portal-panel">
      <form className="portal-search" onSubmit={handleSubmit}>
        <input
          type="text"
          placeholder="Buscar por código o nombre comercial..."
          value={q}
          onChange={e => setQ(e.target.value)}
        />
        <button type="submit">Buscar</button>
      </form>

      {loading && <div className="portal-empty">Cargando cartera...</div>}
      {error && <div className="portal-empty portal-error">Error: {error}</div>}
      {!loading && !error && clientes.length === 0 && (
        <div className="portal-empty">No se encontraron cuentas.</div>
      )}

      {!loading && !error && clientes.length > 0 && (
        <div className="portal-table-wrap">
          <table className="portal-table">
            <thead>
              <tr>
                <th>Código</th>
                <th>Nombre comercial</th>
                <th>Canal</th>
                <th>Tamaño</th>
                <th>Distribución</th>
                <th>Ciudad / Zona</th>
                <th>Condición pago</th>
                <th>Vendedor</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {clientes.map(c => (
                <tr key={c.codigo_cliente}>
                  <td className="portal-numero">{c.codigo_cliente}</td>
                  <td>{c.nombre_comercial}</td>
                  <td><span className={`portal-badge canal-${c.canal}`}>{c.canal}</span></td>
                  <td><span className={`portal-badge tamano-${c.tamano_canal}`}>{c.tamano_canal}</span></td>
                  <td>{c.tipo_distribucion}</td>
                  <td>{c.ciudad}{c.zona ? ` / ${c.zona}` : ""}</td>
                  <td>{c.condicion_pago_habitual}</td>
                  <td>{c.vendedor_asignado || "—"}</td>
                  <td>
                    <button
                      className="portal-action-btn"
                      onClick={() => onIniciarConversacion?.(c.codigo_cliente)}
                      title={`Iniciar una conversación nueva con la cuenta ${c.codigo_cliente} ya identificada`}
                    >
                      Iniciar conversación
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
