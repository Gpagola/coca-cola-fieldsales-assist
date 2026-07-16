import { useState, useEffect, useCallback } from "react"
import "./PortalVendedor.css"

const API = import.meta.env.VITE_API_URL || "/api"

const ESTADOS = ["", "abierto", "en_gestion", "resuelto", "cerrado"]

export default function GestionesPosventaPanel() {
  const [gestiones, setGestiones] = useState([])
  const [estado, setEstado]       = useState("")
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState(null)

  const [detalle, setDetalle]         = useState(null)
  const [detalleLoading, setDetalleLoading] = useState(false)
  const [detalleError, setDetalleError]     = useState(null)

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

  async function verDetalle(numeroGestion) {
    setDetalle({ numero_gestion: numeroGestion })
    setDetalleLoading(true)
    setDetalleError(null)
    try {
      const r = await fetch(`${API}/cartera/gestiones-posventa/${numeroGestion}`)
      const data = await r.json()
      if (data.error) throw new Error(data.error)
      setDetalle(data)
    } catch (e) {
      setDetalleError(e.message)
    } finally {
      setDetalleLoading(false)
    }
  }

  function cerrarDetalle() {
    setDetalle(null)
    setDetalleError(null)
  }

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
                <th></th>
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
                  <td>
                    <button className="portal-action-btn" onClick={() => verDetalle(g.numero_gestion)}>
                      Detalle
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {detalle && (
        <div className="portal-modal-overlay" onClick={cerrarDetalle}>
          <div className="portal-modal" onClick={e => e.stopPropagation()}>
            <div className="portal-modal-header">
              <span className="portal-modal-title">Gestión {detalle.numero_gestion}</span>
              <button className="portal-modal-close" onClick={cerrarDetalle} title="Cerrar">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                </svg>
              </button>
            </div>
            <div className="portal-modal-body">
              {detalleLoading && <div className="portal-empty">Cargando detalle...</div>}
              {detalleError && <div className="portal-empty portal-error">Error: {detalleError}</div>}
              {!detalleLoading && !detalleError && detalle.tipo && (<>
                <div className="portal-modal-meta">
                  <span>{detalle.nombre_comercial} <span className="portal-muted">({detalle.codigo_cliente})</span></span>
                  <span className={`portal-badge gestion-estado-${detalle.estado}`}>{detalle.estado}</span>
                  <span className={`portal-badge prioridad-${detalle.prioridad}`}>{detalle.prioridad}</span>
                  <span className="portal-muted">{detalle.tipo}</span>
                  {detalle.numero_pedido && <span className="portal-muted">Pedido: {detalle.numero_pedido}</span>}
                </div>

                <h4 className="portal-modal-section-title">Solicitud del vendedor</h4>
                <p className="portal-modal-text">{detalle.descripcion}</p>

                <h4 className="portal-modal-section-title">Ficha de cuenta e histórico (al momento de abrir la gestión)</h4>
                <pre className="portal-modal-pre">{detalle.contexto_cuenta || "Sin contexto capturado."}</pre>

                {detalle.resolucion && (<>
                  <h4 className="portal-modal-section-title">Resolución</h4>
                  <p className="portal-modal-text">{detalle.resolucion}</p>
                </>)}

                <div className="portal-modal-footer-meta">
                  Apertura: {detalle.fecha_apertura} {detalle.fecha_cierre ? `· Cierre: ${detalle.fecha_cierre}` : ""} · Vendedor: {detalle.vendedor || "—"}
                </div>
              </>)}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
