import { useState } from "react"
import CarteraClientesPanel from "./CarteraClientesPanel"
import PedidosEnCursoPanel from "./PedidosEnCursoPanel"
import GestionesPosventaPanel from "./GestionesPosventaPanel"
import "./PortalVendedor.css"

export default function PortalVendedor({ onIniciarConversacion }) {
  const [subTab, setSubTab] = useState("clientes") // "clientes" | "pedidos" | "gestiones"

  return (
    <div className="portal-vendedor">
      <div className="portal-nav">
        <button
          className={`portal-nav-item ${subTab === "clientes" ? "active" : ""}`}
          onClick={() => setSubTab("clientes")}
        >
          Mi cartera de clientes
        </button>
        <button
          className={`portal-nav-item ${subTab === "pedidos" ? "active" : ""}`}
          onClick={() => setSubTab("pedidos")}
        >
          Pedidos en curso
        </button>
        <button
          className={`portal-nav-item ${subTab === "gestiones" ? "active" : ""}`}
          onClick={() => setSubTab("gestiones")}
        >
          Gestiones de posventa
        </button>
        <div className="portal-nav-spacer" />
        <button
          className="portal-nav-new"
          onClick={() => onIniciarConversacion?.(null)}
          title="Iniciar una conversación nueva sin una cuenta preseleccionada"
        >
          Nueva conversación
        </button>
      </div>

      {subTab === "clientes" && <CarteraClientesPanel onIniciarConversacion={onIniciarConversacion} />}
      {subTab === "pedidos" && <PedidosEnCursoPanel />}
      {subTab === "gestiones" && <GestionesPosventaPanel />}
    </div>
  )
}
