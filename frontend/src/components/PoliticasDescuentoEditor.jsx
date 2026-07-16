import { useState, useEffect, useCallback } from "react"
import "./PoliticasDescuentoEditor.css"

const API = import.meta.env.VITE_API_URL || "/api"

const CANALES = ["todos", "tradicional", "moderno", "on_premise", "off_premise", "mayorista", "ecommerce", "institucional_horeca", "vending"]
const TAMANOS = ["", "pequeno", "mediano", "grande"]
const CONDICIONES = ["contado", "credito"]

let _tempIdSeq = -1
function nuevaFilaVacia() {
  return {
    id: _tempIdSeq--, // id temporal negativo, nunca choca con IDs reales de la BD
    canal: "todos",
    tamano_canal: "",
    condicion_pago: "contado",
    volumen_min_litros: 0,
    volumen_max_litros: "",
    descuento_pct: 0,
    condiciones_adicionales: "",
    prioridad: 0,
    activo: true,
    _isNew: true,
    _dirty: true,
  }
}

export default function PoliticasDescuentoEditor() {
  const [rows, setRows]       = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)
  const [savingId, setSavingId] = useState(null)

  const cargar = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await fetch(`${API}/politicas-descuento`)
      const data = await r.json()
      setRows(Array.isArray(data) ? data.map(d => ({ ...d, _isNew: false, _dirty: false })) : [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { cargar() }, [cargar])

  function updateRow(id, field, value) {
    setRows(prev => prev.map(row => row.id === id ? { ...row, [field]: value, _dirty: true } : row))
  }

  function agregarFila() {
    setRows(prev => [nuevaFilaVacia(), ...prev])
  }

  function descartarFilaNueva(id) {
    setRows(prev => prev.filter(row => row.id !== id))
  }

  async function guardarFila(row) {
    setSavingId(row.id)
    const body = {
      canal: row.canal,
      tamano_canal: row.tamano_canal || null,
      condicion_pago: row.condicion_pago,
      volumen_min_litros: Number(row.volumen_min_litros) || 0,
      volumen_max_litros: row.volumen_max_litros === "" ? null : Number(row.volumen_max_litros),
      descuento_pct: Number(row.descuento_pct) || 0,
      condiciones_adicionales: row.condiciones_adicionales || "",
      prioridad: Number(row.prioridad) || 0,
      activo: !!row.activo,
    }
    try {
      if (row._isNew) {
        const r = await fetch(`${API}/politicas-descuento`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        })
        const data = await r.json()
        if (data.error) throw new Error(data.error)
        setRows(prev => prev.map(x => x.id === row.id ? { ...x, id: data.id, _isNew: false, _dirty: false } : x))
      } else {
        const r = await fetch(`${API}/politicas-descuento/${row.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        })
        const data = await r.json()
        if (data.error) throw new Error(data.error)
        setRows(prev => prev.map(x => x.id === row.id ? { ...x, _dirty: false } : x))
      }
    } catch (e) {
      alert(`Error al guardar: ${e.message}`)
    } finally {
      setSavingId(null)
    }
  }

  async function eliminarFila(row) {
    if (row._isNew) { descartarFilaNueva(row.id); return }
    if (!window.confirm(`¿Eliminar la política de descuento #${row.id} (${row.canal} / ${row.condicion_pago})?`)) return
    setSavingId(row.id)
    try {
      const r = await fetch(`${API}/politicas-descuento/${row.id}`, { method: "DELETE" })
      const data = await r.json()
      if (data.error) throw new Error(data.error)
      setRows(prev => prev.filter(x => x.id !== row.id))
    } catch (e) {
      alert(`Error al eliminar: ${e.message}`)
    } finally {
      setSavingId(null)
    }
  }

  return (
    <div className="pol-editor">
      <div className="pol-editor-header">
        <p className="pol-editor-hint">
          Estas son las reglas reales que usa <code>consultar_politica_descuento</code> para calcular el
          descuento — el asistente nunca inventa un porcentaje, siempre consulta esta tabla.
        </p>
        <button className="pol-add-btn" onClick={agregarFila}>+ Agregar política</button>
      </div>

      {loading && <div className="pol-empty">Cargando políticas...</div>}
      {error && <div className="pol-empty pol-error">Error: {error}</div>}

      {!loading && !error && (
        <div className="pol-table-wrap">
          <table className="pol-table">
            <thead>
              <tr>
                <th>Canal</th>
                <th>Tamaño</th>
                <th>Pago</th>
                <th>Vol. mín (L)</th>
                <th>Vol. máx (L)</th>
                <th>Descuento %</th>
                <th>Condiciones</th>
                <th>Prioridad</th>
                <th>Activo</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map(row => (
                <tr key={row.id} className={row._dirty ? "pol-row-dirty" : ""}>
                  <td>
                    <select value={row.canal} onChange={e => updateRow(row.id, "canal", e.target.value)}>
                      {CANALES.map(c => <option key={c} value={c}>{c}</option>)}
                    </select>
                  </td>
                  <td>
                    <select value={row.tamano_canal || ""} onChange={e => updateRow(row.id, "tamano_canal", e.target.value)}>
                      {TAMANOS.map(t => <option key={t} value={t}>{t || "(todos)"}</option>)}
                    </select>
                  </td>
                  <td>
                    <select value={row.condicion_pago} onChange={e => updateRow(row.id, "condicion_pago", e.target.value)}>
                      {CONDICIONES.map(c => <option key={c} value={c}>{c}</option>)}
                    </select>
                  </td>
                  <td><input type="number" min="0" value={row.volumen_min_litros} onChange={e => updateRow(row.id, "volumen_min_litros", e.target.value)} /></td>
                  <td><input type="number" min="0" placeholder="sin tope" value={row.volumen_max_litros ?? ""} onChange={e => updateRow(row.id, "volumen_max_litros", e.target.value)} /></td>
                  <td><input type="number" min="0" max="100" step="0.1" value={row.descuento_pct} onChange={e => updateRow(row.id, "descuento_pct", e.target.value)} /></td>
                  <td><input type="text" value={row.condiciones_adicionales || ""} onChange={e => updateRow(row.id, "condiciones_adicionales", e.target.value)} /></td>
                  <td><input type="number" value={row.prioridad} onChange={e => updateRow(row.id, "prioridad", e.target.value)} /></td>
                  <td className="pol-activo-cell">
                    <input type="checkbox" checked={!!row.activo} onChange={e => updateRow(row.id, "activo", e.target.checked)} />
                  </td>
                  <td className="pol-actions-cell">
                    <button
                      className="pol-save-btn"
                      onClick={() => guardarFila(row)}
                      disabled={!row._dirty || savingId === row.id}
                    >
                      {row._isNew ? "Crear" : "Guardar"}
                    </button>
                    <button className="pol-delete-btn" onClick={() => eliminarFila(row)} disabled={savingId === row.id}>
                      Eliminar
                    </button>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr><td colSpan={10} className="pol-empty-row">No hay políticas de descuento cargadas.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
