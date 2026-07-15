import { useState, useEffect } from "react"
import "./PortalVendedor.css"

const API = import.meta.env.VITE_API_URL || "/api"

export default function PedidosEnCursoPanel() {
  const [pedidos, setPedidos] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetch(`${API}/cartera/pedidos-en-curso`)
      .then(r => r.json())
      .then(data => { if (!cancelled) setPedidos(Array.isArray(data) ? data : []) })
      .catch(e => { if (!cancelled) setError(e.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  return (
    <div className="portal-panel">
      {loading && <div className="portal-empty">Cargando pedidos en curso...</div>}
      {error && <div className="portal-empty portal-error">Error: {error}</div>}
      {!loading && !error && pedidos.length === 0 && (
        <div className="portal-empty">No hay pedidos en curso.</div>
      )}

      {!loading && !error && pedidos.length > 0 && (
        <div className="portal-table-wrap">
          <table className="portal-table">
            <thead>
              <tr>
                <th>Pedido</th>
                <th>Cuenta</th>
                <th>Canal</th>
                <th>Fecha</th>
                <th>Estado</th>
                <th>Condición pago</th>
                <th>Total</th>
                <th>Vendedor</th>
              </tr>
            </thead>
            <tbody>
              {pedidos.map(p => (
                <tr key={p.numero_pedido}>
                  <td className="portal-numero">{p.numero_pedido}</td>
                  <td>{p.nombre_comercial} <span className="portal-muted">({p.codigo_cliente})</span></td>
                  <td><span className={`portal-badge canal-${p.canal_venta}`}>{p.canal_venta}</span></td>
                  <td>{p.fecha_pedido}</td>
                  <td><span className={`portal-badge pedido-estado-${p.estado}`}>{p.estado}</span></td>
                  <td>{p.condicion_pago}</td>
                  <td>{p.total?.toFixed ? p.total.toFixed(2) : p.total}</td>
                  <td>{p.vendedor || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
