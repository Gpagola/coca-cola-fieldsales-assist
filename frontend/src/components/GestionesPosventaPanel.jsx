import { useState, useEffect, useCallback } from "react"
import "./PortalVendedor.css"

const API = import.meta.env.VITE_API_URL || "/api"

const ESTADOS = ["", "abierto", "en_gestion", "resuelto", "cerrado"]

export default function GestionesPosventaPanel() {
  const [gestiones, setGestiones] = useState([])
  const [estado, setEstado]       = useState("")
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState(null)

  const cargar = useCallback(async (filtroEstado) => {
    setLoading(true)
    setError(null)
    try {
      const url = filtroEstado
        ? `${API}/cartera/gestiones-posventa?estado=${encodeURIComponent(filtroEstado)}`
        : `${API}/cartera/gestiones-posventa`
      const r = await fetch(url)
      const data = await r.json()
      setGestiones(Array.isArray(data) ? data : [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { cargar(estado) }, [cargar, estado])

  return (
    <div className="portal-panel">
      <div className="portal-search">
        <select value={estado} onChange={e => setEstado(e.target.value)}>
          {ESTADOS.map(s => (
            <option key={s} value={s}>{s === "" ? "Todos los estados" : s}</option>
          ))}
        </select>
      </div>

      {loading && <div className="portal-empty">Cargando gestiones de posventa...</div>}
      {error && <div className="portal-empty portal-error">Error: {error}</div>}
      {!loading && !error && gestiones.length === 0 && (
        <div className="portal-empty">No hay gestiones de posventa registradas.</div>
      )}

      {!loading && !error && gestiones.length > 0 && (
        <div className="portal-table-wrap">
          <table className="portal-table">
            <thead>
              <tr>
                <th>Gestión</th>
                <th>Cuenta</th>
                <th>Pedido</th>
                <th>Tipo</th>
                <th>Estado</th>
                <th>Prioridad</th>
                <th>Apertura</th>
              </tr>
            </thead>
            <tbody>
              {gestiones.map(g => (
                <tr key={g.numero_gestion}>
                  <td className="portal-numero">{g.numero_gestion}</td>
                  <td>{g.nombre_comercial} <span className="portal-muted">({g.codigo_cliente})</span></td>
                  <td>{g.numero_pedido || "—"}</td>
                  <td>{g.tipo}</td>
                  <td><span className={`portal-badge gestion-estado-${g.estado}`}>{g.estado}</span></td>
                  <td><span className={`portal-badge prioridad-${g.prioridad}`}>{g.prioridad}</span></td>
                  <td>{g.fecha_apertura}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
