# ── Importaciones ──────────────────────────────────────────────────────────────

import argparse
import json
import os
from datetime import date, datetime, timedelta

import mysql.connector
from dotenv import load_dotenv

# [LANGCHAIN] Modelo de lenguaje
from langchain_openai import ChatOpenAI

# [LANGCHAIN] Decorador que convierte una funcion Python en una tool que el agente puede invocar
from langchain_core.tools import tool

# [LANGCHAIN] SystemMessage para inyectar el system prompt en cada llamada
from langchain_core.messages import SystemMessage

# [LANGGRAPH] Componentes para construir el grafo ReAct manualmente
from langgraph.graph import StateGraph, MessagesState, START, END

# [LANGGRAPH] ToolNode ejecuta las tools que el LLM decide invocar
from langgraph.prebuilt import ToolNode

# [LANGGRAPH] Checkpointer en memoria
from langgraph.checkpoint.memory import MemorySaver


# ── Carga de variables de entorno ────────────────────────────────────────────
load_dotenv()

DB_CONFIG = {
    "host":     os.getenv("DB_HOST"),
    "port":     int(os.getenv("DB_PORT", 3306)),
    "database": os.getenv("DB_NAME"),
    "user":     os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}


# ── Conexion MySQL ────────────────────────────────────────────────────────────

def get_conn():
    """Crea y devuelve una nueva conexion MySQL."""
    return mysql.connector.connect(**DB_CONFIG)


# ── Perfil activo ─────────────────────────────────────────────────────────────

def get_active_perfil_id() -> int | None:
    """Devuelve el id del perfil marcado como activo en la tabla perfiles."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM perfiles WHERE activo = TRUE ORDER BY id LIMIT 1")
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    return row[0] if row else None


# ── Ontology Cache ────────────────────────────────────────────────────────────
_ontology_cache: dict = {}

def preload_ontologies():
    """Carga ontologias del perfil activo en memoria al arrancar."""
    perfil_id = get_active_perfil_id()
    if perfil_id is None:
        print("[preload_ontologies] No hay perfil activo. Saltando precarga.")
        return
    names = ["ontologia-procedimientos", "ontologia-descuentos", "ontologia-faq"]
    conn = get_conn()
    try:
        cur = conn.cursor()
        for name in names:
            cur.execute("""
                SELECT contenido FROM ontologias
                WHERE nombre = %s AND activo = TRUE AND perfil_id = %s
                ORDER BY version DESC LIMIT 1
            """, (name, perfil_id))
            row = cur.fetchone()
            if row:
                _ontology_cache[name] = row[0]
        cur.close()
    finally:
        conn.close()

def invalidate_ontology_cache(nombre: str = None):
    """Invalida el cache tras actualizar una ontologia o tras cambio de perfil."""
    if nombre:
        _ontology_cache.pop(nombre, None)
    else:
        _ontology_cache.clear()


# ── Carga del System Prompt desde la BD ───────────────────────────────────────

def cargar_system_prompt() -> str:
    """Carga la version activa del system prompt del perfil activo."""
    perfil_id = get_active_perfil_id()
    if perfil_id is None:
        raise RuntimeError("No hay perfil activo en la tabla perfiles. Ejecuta setup_db.py.")
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT contenido FROM ontologias
            WHERE nombre = 'system-prompt' AND activo = TRUE AND perfil_id = %s
            ORDER BY version DESC
            LIMIT 1
        """, (perfil_id,))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if not row:
        raise RuntimeError(
            f"No se encontro 'system-prompt' activo para perfil_id={perfil_id}. Ejecuta setup_db.py."
        )
    return row[0]


# ── Helpers de lectura ────────────────────────────────────────────────────────

def _fetch_empresa(cur, codigo_cliente: str):
    """Carga una empresa cliente por codigo. Devuelve dict o None."""
    cur.execute("""
        SELECT id, codigo_cliente, nombre_comercial, razon_social, canal, tipo_distribucion,
               tamano_canal, ciudad, zona, condicion_pago_habitual, vendedor_asignado, fecha_alta, activo
        FROM empresas_clientes
        WHERE codigo_cliente = %s
    """, (codigo_cliente,))
    row = cur.fetchone()
    if not row:
        return None
    keys = ["id", "codigo_cliente", "nombre_comercial", "razon_social", "canal", "tipo_distribucion",
            "tamano_canal", "ciudad", "zona", "condicion_pago_habitual", "vendedor_asignado",
            "fecha_alta", "activo"]
    return dict(zip(keys, row))


def _fetch_pedido(cur, numero_pedido: str):
    """Carga un pedido y su empresa cliente. Devuelve dict o None."""
    cur.execute("""
        SELECT p.numero_pedido, p.empresa_cliente_id, p.fecha_pedido, p.estado, p.canal_venta,
               p.condicion_pago, p.vendedor, p.descuento_aplicado_pct, p.subtotal, p.total, p.notas,
               e.codigo_cliente, e.nombre_comercial, e.canal, e.tamano_canal
        FROM pedidos p
        JOIN empresas_clientes e ON e.id = p.empresa_cliente_id
        WHERE p.numero_pedido = %s
    """, (numero_pedido,))
    row = cur.fetchone()
    if not row:
        return None
    keys = ["numero", "empresa_cliente_id", "fecha_pedido", "estado", "canal_venta", "condicion_pago",
            "vendedor", "descuento_aplicado_pct", "subtotal", "total", "notas",
            "codigo_cliente", "nombre_comercial", "canal", "tamano_canal"]
    return dict(zip(keys, row))


def _append_nota(notas_actuales: str | None, nueva: str) -> str:
    """Concatena una nota nueva con timestamp al campo notas."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"[{stamp}] {nueva}"
    if notas_actuales and notas_actuales.strip():
        return f"{notas_actuales.strip()}\n{entry}"
    return entry


def _siguiente_numero_pedido(cur) -> str:
    """Genera el proximo numero de pedido correlativo (PED-XXXX)."""
    cur.execute("""
        SELECT numero_pedido FROM pedidos
        WHERE numero_pedido REGEXP '^PED-[0-9]+$'
        ORDER BY CAST(SUBSTRING(numero_pedido, 5) AS UNSIGNED) DESC
        LIMIT 1
    """)
    row = cur.fetchone()
    siguiente = int(row[0].split("-")[1]) + 1 if row else 1
    return f"PED-{siguiente:04d}"


def _match_politica_descuento(cur, canal: str, condicion_pago: str, volumen_litros: float, tamano_canal: str | None):
    """Selecciona de forma deterministica la politica de descuento aplicable.
    Devuelve (descuento_pct, condiciones_adicionales) o (None, None) si no hay match."""
    cur.execute("""
        SELECT descuento_pct, condiciones_adicionales
        FROM politicas_descuento
        WHERE condicion_pago = %s
          AND (canal = %s OR canal = 'todos')
          AND (tamano_canal IS NULL OR tamano_canal = %s)
          AND %s BETWEEN volumen_min_litros AND COALESCE(volumen_max_litros, 999999999)
          AND activo = 1
        ORDER BY (tamano_canal IS NOT NULL) DESC, (canal <> 'todos') DESC, prioridad DESC
        LIMIT 1
    """, (condicion_pago, canal, tamano_canal or "", volumen_litros))
    row = cur.fetchone()
    if not row:
        return None, None
    return float(row[0]), row[1]


# ── Tools de lectura ───────────────────────────────────────────────────────────

def _ficha_cuenta_texto(empresa: dict) -> str:
    """Formatea la ficha de una cuenta (sin encabezado) — reusada por consultar_cuenta_cliente
    y por el contexto que se guarda al abrir una gestion de posventa."""
    return (
        f"- Codigo: {empresa['codigo_cliente']}\n"
        f"- Nombre comercial: {empresa['nombre_comercial']}\n"
        f"- Razon social: {empresa['razon_social'] or 'No disponible'}\n"
        f"- Canal: {empresa['canal']}\n"
        f"- Tipo de distribucion: {empresa['tipo_distribucion']}\n"
        f"- Tamano de canal: {empresa['tamano_canal']}\n"
        f"- Ciudad / zona: {empresa['ciudad'] or 'No disponible'} / {empresa['zona'] or 'No disponible'}\n"
        f"- Condicion de pago habitual: {empresa['condicion_pago_habitual']}\n"
        f"- Vendedor asignado: {empresa['vendedor_asignado'] or 'No disponible'}\n"
        f"- Cliente desde: {empresa['fecha_alta'].strftime('%d/%m/%Y')}"
    )


def _historico_pedidos_texto(cur, empresa: dict, limite: int = 5) -> str:
    """Formatea el historico reciente de pedidos de una cuenta (sin encabezado) — reusada por
    consultar_historico_pedidos y por el contexto que se guarda al abrir una gestion de posventa."""
    cur.execute("""
        SELECT numero_pedido, fecha_pedido, estado, condicion_pago, canal_venta,
               descuento_aplicado_pct, total
        FROM pedidos
        WHERE empresa_cliente_id = %s
        ORDER BY fecha_pedido DESC
        LIMIT %s
    """, (empresa["id"], limite))
    pedidos = cur.fetchall()

    cur.execute("""
        SELECT COUNT(DISTINCT p.numero_pedido), COALESCE(SUM(dp.cantidad * pr.litros), 0), COALESCE(SUM(p.total), 0)
        FROM pedidos p
        JOIN detalle_pedido dp ON dp.numero_pedido = p.numero_pedido
        JOIN productos pr ON pr.id = dp.producto_id
        WHERE p.empresa_cliente_id = %s AND p.fecha_pedido >= CURDATE() - INTERVAL 90 DAY
    """, (empresa["id"],))
    num_pedidos_90, volumen_90, facturado_90 = cur.fetchone()

    if not pedidos:
        return "Sin pedidos registrados aun."

    out = [f"Ultimos {len(pedidos)} pedidos:"]
    for numero, fecha, estado, cond_pago, canal_venta, desc_pct, total in pedidos:
        out.append(
            f"- {numero} | {fecha.strftime('%d/%m/%Y')} | {estado} | {cond_pago} | "
            f"canal: {canal_venta} | descuento: {float(desc_pct):.1f}% | total: {float(total):.2f}"
        )
    out.append(
        f"Resumen ultimos 90 dias: {num_pedidos_90} pedidos, "
        f"{float(volumen_90):.1f} litros, {float(facturado_90):.2f} facturado."
    )
    return "\n".join(out)


@tool
def consultar_cuenta_cliente(codigo_cliente: str = "", nombre_comercial: str = "") -> str:
    """Busca una cuenta (empresa cliente). Pasa el codigo (formato CLI-XXXX) si lo tenes, el
    nombre comercial si lo tenes, o AMBOS si el vendedor menciono los dos (por ejemplo, si dio
    un codigo que resulto no existir pero tambien dijo el nombre del negocio, o viceversa — pasa
    los dos datos juntos para que la busqueda los combine).

    Si hay un match exacto de codigo, devuelve directamente la ficha de la cuenta: canal, tipo
    de distribucion (directo/indirecto), tamano de canal, ciudad/zona, condicion de pago
    habitual y vendedor asignado. NO incluye historico de pedidos — para eso usa
    `consultar_historico_pedidos`.

    Si no hay match exacto, busca por aproximacion (codigo parcial, nombre comercial o razon
    social) y devuelve una lista de cuentas candidatas para que le muestres al vendedor y
    confirme cual es — nunca elijas una por tu cuenta ni inventes un codigo.

    Usar siempre al inicio de la conversacion para identificar la cuenta con la que se va a
    trabajar."""
    codigo = codigo_cliente.strip().upper()
    nombre = nombre_comercial.strip()

    if not codigo and not nombre:
        return "No se dio codigo ni nombre de cuenta. Pide al vendedor el codigo (formato CLI-XXXX) o el nombre comercial del negocio."

    conn = get_conn()
    try:
        cur = conn.cursor()

        empresa = _fetch_empresa(cur, codigo) if codigo else None

        if not empresa:
            condiciones = []
            params = []
            if codigo:
                condiciones.append("codigo_cliente LIKE %s")
                params.append(f"%{codigo}%")
            if nombre:
                condiciones.append("nombre_comercial LIKE %s")
                params.append(f"%{nombre}%")
                condiciones.append("razon_social LIKE %s")
                params.append(f"%{nombre}%")

            cur.execute(f"""
                SELECT codigo_cliente, nombre_comercial, canal, tamano_canal, ciudad
                FROM empresas_clientes
                WHERE {" OR ".join(condiciones)}
                ORDER BY nombre_comercial
                LIMIT 10
            """, params)
            rows = cur.fetchall()
            cur.close()

            dato_buscado = " y ".join(filter(None, [f"codigo '{codigo}'" if codigo else "", f"nombre '{nombre}'" if nombre else ""]))

            if not rows:
                return f"No se encontro ninguna cuenta que coincida con {dato_buscado}. Verifica el dato con el vendedor."
            if len(rows) == 1:
                empresa = _volver_a_buscar(rows[0][0])
            else:
                out = [f"Se encontraron {len(rows)} cuentas que coinciden con {dato_buscado}. Mostrale esta lista al vendedor y pedile que confirme el codigo exacto:"]
                for cod, nom, canal, tamano, ciudad in rows:
                    out.append(f"- {cod} | {nom} | canal: {canal} | tamano: {tamano} | {ciudad or 'sin ciudad'}")
                return "\n".join(out)
        else:
            cur.close()

        if not empresa:
            return f"No se encontro ninguna cuenta con codigo '{codigo}'."

        if not empresa["activo"]:
            return f"La cuenta {empresa['codigo_cliente']} ({empresa['nombre_comercial']}) figura como INACTIVA. Verifica con tu supervisor antes de continuar."

        return "Cuenta encontrada:\n" + _ficha_cuenta_texto(empresa)
    finally:
        conn.close()


def _volver_a_buscar(codigo_cliente: str):
    """Helper interno: reabre conexion para recargar una empresa tras desambiguar por nombre."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        empresa = _fetch_empresa(cur, codigo_cliente)
        cur.close()
        return empresa
    finally:
        conn.close()


@tool
def consultar_historico_pedidos(codigo_cliente: str, limite: int = 10) -> str:
    """Consulta el historico de pedidos de una cuenta (por codigo CLI-XXXX): numero, fecha,
    estado, condicion de pago, canal de venta, descuento aplicado y total de cada pedido
    reciente, mas un resumen agregado de volumen y facturacion de los ultimos 90 dias.
    Usar para argumentar el pitch de venta con datos reales de la cuenta."""
    codigo = codigo_cliente.strip().upper()
    limite = max(1, min(int(limite), 30))

    conn = get_conn()
    try:
        cur = conn.cursor()
        empresa = _fetch_empresa(cur, codigo)
        if not empresa:
            cur.close()
            return f"No se encontro ninguna cuenta con codigo '{codigo}'. Verifica el dato con el vendedor."

        historico = _historico_pedidos_texto(cur, empresa, limite)
        cur.close()
    finally:
        conn.close()

    return f"Historico de {empresa['nombre_comercial']} ({empresa['codigo_cliente']}):\n{historico}"


@tool
def buscar_producto(nombre_o_categoria: str) -> str:
    """Busca productos del catalogo Coca-Cola por nombre o categoria (gaseosas, aguas,
    saborizadas, jugos, isotonicas, energizantes). Devuelve SKU, nombre, categoria,
    presentacion, formato, litros y precio de lista. Usar cuando el vendedor pregunte
    por disponibilidad, formato o precio de un producto."""
    q = f"%{nombre_o_categoria.strip()}%"
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT codigo_sku, nombre, categoria, presentacion_tipo, formato, litros, precio_lista, descripcion
            FROM productos
            WHERE activo = 1 AND (
                nombre LIKE %s OR categoria LIKE %s OR CONCAT(nombre, ' ', formato) LIKE %s
            )
            ORDER BY nombre
            LIMIT 8
        """, (q, q, q))
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    if not rows:
        return f"No se encontraron productos con '{nombre_o_categoria}'."

    out = [f"Productos encontrados ({len(rows)}):"]
    for sku, nom, cat, pres, formato, litros, precio, desc in rows:
        out.append(
            f"\n- {nom} ({cat}) — SKU {sku}\n"
            f"  Presentacion: {pres} | Formato: {formato} ({float(litros):.3f} L)\n"
            f"  Precio de lista: {float(precio):.2f}\n"
            f"  {desc or ''}"
        )
    return "\n".join(out)


@tool
def consultar_politica_descuento(canal: str, condicion_pago: str, volumen_litros: float, tamano_canal: str = "") -> str:
    """Consulta de forma DETERMINISTA la politica de descuento aplicable segun canal,
    condicion de pago (contado/credito), volumen del pedido en litros y opcionalmente
    tamano de canal (pequeno/mediano/grande). USAR SIEMPRE antes de informar un descuento
    al vendedor — nunca calcules ni inventes el porcentaje vos mismo. Si no hay ninguna
    politica aplicable, devuelve 0% y un mensaje explicito de que debe escalarse a un
    supervisor comercial."""
    canal_norm = canal.strip().lower().replace(" ", "_")
    condicion_norm = condicion_pago.strip().lower()
    tamano_norm = (tamano_canal or "").strip().lower() or None

    if condicion_norm not in ("contado", "credito"):
        return f"Condicion de pago '{condicion_pago}' no valida. Usa 'contado' o 'credito'."

    try:
        vol = float(volumen_litros)
    except (TypeError, ValueError):
        return "El volumen en litros no es un numero valido."
    if vol < 0:
        return "El volumen en litros no puede ser negativo."

    conn = get_conn()
    try:
        cur = conn.cursor()
        pct, condiciones = _match_politica_descuento(cur, canal_norm, condicion_norm, vol, tamano_norm)
        cur.close()
    finally:
        conn.close()

    if pct is None:
        return (
            f"No hay una politica de descuento especifica para canal='{canal_norm}', "
            f"condicion_pago='{condicion_norm}', volumen={vol:.1f}L. "
            f"No ofrezcas un descuento por tu cuenta: indica al vendedor que debe escalar "
            f"esta condicion a su supervisor comercial."
        )

    detalle = f" Condiciones: {condiciones}." if condiciones else ""
    return (
        f"Descuento aplicable: {pct:.1f}% "
        f"(canal={canal_norm}, condicion_pago={condicion_norm}, volumen={vol:.1f}L"
        f"{', tamano_canal=' + tamano_norm if tamano_norm else ''}).{detalle}"
    )


@tool
def ontologia_procedimientos() -> str:
    """Consulta la ontologia de procedimientos por canal (tradicional, moderno, on premise,
    off premise, mayoristas, e-commerce, institucional/Horeca, vending, directo/indirecto).
    Usar siempre antes de guiar al vendedor en la negociacion segun el canal de la cuenta."""
    cache_key = "ontologia-procedimientos"
    if cache_key in _ontology_cache:
        return _ontology_cache[cache_key]

    perfil_id = get_active_perfil_id()
    if perfil_id is None:
        return "No hay perfil activo configurado."

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT contenido FROM ontologias
            WHERE nombre = 'ontologia-procedimientos' AND activo = TRUE AND perfil_id = %s
            ORDER BY version DESC LIMIT 1
        """, (perfil_id,))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    if not row:
        return "No se encontro la ontologia de procedimientos."
    return row[0]


@tool
def ontologia_descuentos() -> str:
    """Consulta la ontologia que explica la logica de las politicas de descuento (canal,
    volumen, condicion de pago, tamano de canal). Util para entender el criterio general,
    pero el numero exacto siempre debe salir de `consultar_politica_descuento`."""
    cache_key = "ontologia-descuentos"
    if cache_key in _ontology_cache:
        return _ontology_cache[cache_key]

    perfil_id = get_active_perfil_id()
    if perfil_id is None:
        return "No hay perfil activo configurado."

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT contenido FROM ontologias
            WHERE nombre = 'ontologia-descuentos' AND activo = TRUE AND perfil_id = %s
            ORDER BY version DESC LIMIT 1
        """, (perfil_id,))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    if not row:
        return "No se encontro la ontologia de descuentos."
    return row[0]


@tool
def ontologia_faq() -> str:
    """Consulta las preguntas frecuentes de un vendedor en terreno: objeciones de precio,
    condiciones de pago, logistica directa/indirecta, SKUs no catalogados, aprobacion de
    pedidos. Usar cuando el vendedor haga una pregunta general de proceso."""
    cache_key = "ontologia-faq"
    if cache_key in _ontology_cache:
        return _ontology_cache[cache_key]

    perfil_id = get_active_perfil_id()
    if perfil_id is None:
        return "No hay perfil activo configurado."

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT contenido FROM ontologias
            WHERE nombre = 'ontologia-faq' AND activo = TRUE AND perfil_id = %s
            ORDER BY version DESC LIMIT 1
        """, (perfil_id,))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    if not row:
        return "No se encontro la ontologia FAQ."
    return row[0]


@tool
def analizar_documento(contenido: str) -> str:
    """Analiza el contenido extraido de un documento (PDF o imagen) subido por el vendedor
    (ej. foto de exhibidor, orden de compra escaneada). Usar cuando el vendedor adjunte
    un archivo al chat."""
    return contenido


@tool
def consultar_gestiones_posventa(numero: str) -> str:
    """Consulta gestiones de posventa existentes. Acepta dos formatos:
    - Un numero de gestion (GES-XXXX): devuelve el detalle completo.
    - Un numero de pedido (PED-XXXX): devuelve la lista de gestiones vinculadas a ese pedido.

    Usar para verificar si un pedido ya tiene una gestion abierta antes de abrir una nueva."""
    q = numero.upper().strip()
    conn = get_conn()
    try:
        cur = conn.cursor()

        if q.startswith("GES-"):
            cur.execute("""
                SELECT g.numero_gestion, g.numero_pedido, g.tipo, g.descripcion, g.estado,
                       g.prioridad, g.canal_reporte, g.vendedor, g.fecha_apertura, g.fecha_cierre,
                       g.resolucion, e.nombre_comercial, e.codigo_cliente
                FROM gestiones_posventa g
                JOIN empresas_clientes e ON e.id = g.empresa_cliente_id
                WHERE g.numero_gestion = %s
            """, (q,))
            row = cur.fetchone()
            cur.close()
            if not row:
                return f"No se encontro la gestion '{q}'."
            (num, num_ped, tipo, desc, estado, prio, canal, vend, f_ap, f_cie, resol, nombre_e, codigo_e) = row
            return (
                f"Gestion de posventa encontrada:\n"
                f"- Numero: {num}\n"
                f"- Pedido asociado: {num_ped or '(sin pedido asociado)'}\n"
                f"- Cuenta: {nombre_e} ({codigo_e})\n"
                f"- Tipo: {tipo}\n"
                f"- Estado: {estado}\n"
                f"- Prioridad: {prio}\n"
                f"- Vendedor: {vend or 'No disponible'}\n"
                f"- Abierta el: {f_ap.strftime('%d/%m/%Y %H:%M')}\n"
                f"- Cerrada el: {f_cie.strftime('%d/%m/%Y %H:%M') if f_cie else 'Aun abierta'}\n"
                f"- Descripcion: {desc}\n"
                f"- Resolucion: {resol or 'Pendiente'}"
            )

        if q.startswith("PED-"):
            cur.execute("""
                SELECT numero_gestion, tipo, estado, prioridad, fecha_apertura, fecha_cierre
                FROM gestiones_posventa
                WHERE numero_pedido = %s
                ORDER BY fecha_apertura DESC
            """, (q,))
            rows = cur.fetchall()
            cur.close()
            if not rows:
                return f"El pedido {q} no tiene gestiones de posventa registradas."
            out = [f"Gestiones vinculadas al pedido {q} ({len(rows)}):"]
            for num_g, tipo, estado, prio, f_ap, f_cie in rows:
                cierre = f_cie.strftime('%d/%m/%Y') if f_cie else "abierta"
                out.append(
                    f"- {num_g} | {tipo} | estado: {estado} | prioridad: {prio} | "
                    f"abierta: {f_ap.strftime('%d/%m/%Y')} | cerrada: {cierre}"
                )
            return "\n".join(out)

        cur.close()
        return f"Formato no reconocido: '{numero}'. Usa GES-XXXX (gestion) o PED-XXXX (pedido)."
    finally:
        conn.close()


# ── Helpers y constantes para tools de escritura ──────────────────────────────

_CONDICIONES_PAGO_VALIDAS = {"contado", "credito"}
_TIPOS_GESTION_VALIDOS = {
    "producto_defectuoso", "faltante_entrega", "error_facturacion",
    "reclamo_precio", "logistica_retraso", "solicitud_credito", "otro",
}
_ESTADOS_PEDIDO_ACTIVOS_PARA_CANCELAR = {"solicitado", "en_revision"}


# ── Tools de escritura ─────────────────────────────────────────────────────────

def _cotizar_pedido(cur, codigo: str, items: list, condicion_norm: str):
    """Valida y cotiza un pedido SIN escribir en la base (solo SELECTs). Devuelve
    (cotizacion, None) si todo esta ok, o (None, mensaje_de_error) si algo fallo.
    Extraida de crear_pedido para poder reusarla desde previsualizar_pedido (modo
    voz en vivo) sin duplicar la logica de calculo de descuento."""
    empresa = _fetch_empresa(cur, codigo)
    if not empresa:
        return None, f"No se encontro ninguna cuenta con codigo '{codigo}'. Verifica el dato antes de tomar el pedido."
    if not empresa["activo"]:
        return None, f"La cuenta {codigo} figura como INACTIVA. No se puede tomar un pedido nuevo."

    lineas = []
    volumen_total = 0.0
    for item in items:
        nombre_prod = str(item.get("producto", "")).strip()
        try:
            cantidad = int(item.get("cantidad", 0))
        except (TypeError, ValueError):
            cantidad = 0
        if not nombre_prod or cantidad <= 0:
            return None, f"Item invalido: {item}. Cada item necesita 'producto' y 'cantidad' (entero positivo)."

        cur.execute("""
            SELECT id, nombre, litros, precio_lista FROM productos
            WHERE activo = 1 AND (
                nombre LIKE %s OR codigo_sku LIKE %s OR CONCAT(nombre, ' ', formato) LIKE %s
            )
            ORDER BY nombre LIMIT 1
        """, (f"%{nombre_prod}%", f"%{nombre_prod}%", f"%{nombre_prod}%"))
        prod_row = cur.fetchone()
        if not prod_row:
            return None, f"No se encontro el producto '{nombre_prod}' en el catalogo. Verifica el nombre o SKU."

        prod_id, prod_nombre, litros_unit, precio_lista = prod_row
        volumen_total += float(litros_unit) * cantidad
        lineas.append((prod_id, prod_nombre, cantidad, float(precio_lista)))

    pct, _condiciones = _match_politica_descuento(
        cur, empresa["canal"], condicion_norm, round(volumen_total, 3), empresa["tamano_canal"]
    )
    pct = pct if pct is not None else 0.0

    subtotal = 0.0
    detalle_rows = []
    for prod_id, prod_nombre, cantidad, precio_unit in lineas:
        precio_neto = round(precio_unit * (1 - pct / 100), 2)
        subtotal_linea = round(precio_neto * cantidad, 2)
        subtotal += subtotal_linea
        detalle_rows.append((prod_id, prod_nombre, cantidad, precio_unit, pct, precio_neto, subtotal_linea))
    subtotal = round(subtotal, 2)

    return {
        "empresa": empresa,
        "lineas": lineas,
        "detalle_rows": detalle_rows,
        "volumen_litros": volumen_total,
        "descuento_pct": pct,
        "subtotal": subtotal,
        "total": subtotal,
    }, None


def _insertar_pedido(cur, cot: dict, condicion_norm: str, notas: str) -> str:
    """Escribe en la base (INSERT pedidos + detalle_pedido) a partir de una cotizacion ya
    calculada por _cotizar_pedido. Devuelve el numero_pedido generado."""
    empresa = cot["empresa"]
    numero_pedido = _siguiente_numero_pedido(cur)
    notas_iniciales = f"Pedido tomado en terreno. {notas.strip()}" if notas.strip() else "Pedido tomado en terreno."
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cur.execute("""
        INSERT INTO pedidos (numero_pedido, empresa_cliente_id, fecha_pedido, estado, canal_venta,
                              condicion_pago, vendedor, descuento_aplicado_pct, subtotal, total, notas,
                              fecha_actualizacion_estado)
        VALUES (%s, %s, CURDATE(), 'solicitado', %s, %s, %s, %s, %s, %s, %s, %s)
    """, (numero_pedido, empresa["id"], empresa["canal"], condicion_norm,
          empresa["vendedor_asignado"], cot["descuento_pct"], cot["subtotal"], cot["total"],
          notas_iniciales, ahora))

    for prod_id, _nombre, cantidad, precio_unit, pct, precio_neto, subtotal_linea in cot["detalle_rows"]:
        cur.execute("""
            INSERT INTO detalle_pedido (numero_pedido, producto_id, cantidad, precio_unitario,
                                         descuento_pct, precio_neto_unitario, subtotal_linea)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (numero_pedido, prod_id, cantidad, precio_unit, pct, precio_neto, subtotal_linea))

    return numero_pedido


@tool
def crear_pedido(codigo_cliente: str, items_json: str, condicion_pago: str, notas: str = "") -> str:
    """Registra un pedido nuevo para una cuenta. `items_json` es un string JSON con una lista
    de items, ej: '[{"producto": "Coca-Cola Original 1.5L", "cantidad": 24}]' (el producto se
    busca por nombre o SKU). El descuento se calcula automaticamente consultando la politica
    aplicable (canal de la cuenta + condicion de pago + volumen total). El pedido SIEMPRE
    queda registrado en estado 'solicitado', pendiente de revision de backoffice — nunca se
    aprueba automaticamente. Confirma con el vendedor el detalle y el descuento ANTES de
    invocar esta tool."""
    codigo = codigo_cliente.strip().upper()
    condicion_norm = condicion_pago.strip().lower()

    if condicion_norm not in _CONDICIONES_PAGO_VALIDAS:
        return f"Condicion de pago '{condicion_pago}' no valida. Usa 'contado' o 'credito'."

    try:
        items = json.loads(items_json)
        if not isinstance(items, list) or not items:
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        return "El formato de items_json no es valido. Debe ser una lista JSON de objetos {producto, cantidad}."

    conn = get_conn()
    try:
        cur = conn.cursor()
        cot, error = _cotizar_pedido(cur, codigo, items, condicion_norm)
        if error:
            cur.close()
            return error
        numero_pedido = _insertar_pedido(cur, cot, condicion_norm, notas)
        conn.commit()
        cur.close()
    finally:
        conn.close()

    empresa = cot["empresa"]
    items_str = "\n".join(f"  * {nom} x{cant}" for _, nom, cant, _ in cot["lineas"])
    return (
        f"Pedido {numero_pedido} registrado correctamente para {empresa['nombre_comercial']} ({codigo}).\n"
        f"- Estado: solicitado (pendiente de revision de backoffice)\n"
        f"- Condicion de pago: {condicion_norm}\n"
        f"- Descuento aplicado: {cot['descuento_pct']:.1f}%\n"
        f"- Total: {cot['total']:.2f}\n"
        f"Items:\n{items_str}"
    )


def _recotizar_condicion_pago(cur, pedido: dict, condicion_norm: str):
    """SELECT-only: recalcula el descuento y los importes de un pedido existente bajo una
    nueva condicion de pago. Devuelve (pct, subtotal, detalle_calc) donde detalle_calc es
    una lista de (detalle_pedido_id, precio_neto, subtotal_linea) lista para UPDATE. Extraida
    de cambiar_condicion_pago_pedido para reusarla desde previsualizar_cambio_condicion."""
    cur.execute("""
        SELECT dp.id, dp.producto_id, dp.cantidad, dp.precio_unitario, pr.litros
        FROM detalle_pedido dp
        JOIN productos pr ON pr.id = dp.producto_id
        WHERE dp.numero_pedido = %s
    """, (pedido["numero"],))
    detalle = cur.fetchall()
    volumen_total = sum(float(litros) * cantidad for _, _, cantidad, _, litros in detalle)

    pct, _condiciones = _match_politica_descuento(
        cur, pedido["canal"], condicion_norm, round(volumen_total, 3), pedido["tamano_canal"]
    )
    pct = pct if pct is not None else 0.0

    subtotal = 0.0
    detalle_calc = []
    for det_id, prod_id, cantidad, precio_unit, _litros in detalle:
        precio_neto = round(float(precio_unit) * (1 - pct / 100), 2)
        subtotal_linea = round(precio_neto * cantidad, 2)
        subtotal += subtotal_linea
        detalle_calc.append((det_id, precio_neto, subtotal_linea))
    subtotal = round(subtotal, 2)

    return pct, subtotal, detalle_calc


@tool
def cambiar_condicion_pago_pedido(numero_pedido: str, nueva_condicion_pago: str) -> str:
    """Cambia la condicion de pago de un pedido y recalcula el descuento aplicable. Solo es
    posible si el pedido esta en estado 'solicitado' (antes de que backoffice lo procese).
    Usar cuando el vendedor indique que el cliente cambio de opinion sobre la forma de pago."""
    numero = numero_pedido.upper().strip()
    condicion_norm = nueva_condicion_pago.strip().lower()

    if condicion_norm not in _CONDICIONES_PAGO_VALIDAS:
        return f"Condicion de pago '{nueva_condicion_pago}' no valida. Usa 'contado' o 'credito'."

    conn = get_conn()
    try:
        cur = conn.cursor()
        pedido = _fetch_pedido(cur, numero)
        if not pedido:
            cur.close()
            return f"No se encontro el pedido '{numero}'. Verifica el numero."

        if pedido["estado"] != "solicitado":
            cur.close()
            return (
                f"No se puede cambiar la condicion de pago del pedido {numero} porque su estado es "
                f"'{pedido['estado']}'. Solo es posible mientras esta 'solicitado'."
            )

        if pedido["condicion_pago"] == condicion_norm:
            cur.close()
            return f"El pedido {numero} ya tiene condicion de pago '{condicion_norm}'. No se requiere cambio."

        pct, subtotal, detalle_calc = _recotizar_condicion_pago(cur, pedido, condicion_norm)
        for det_id, precio_neto, subtotal_linea in detalle_calc:
            cur.execute("""
                UPDATE detalle_pedido
                SET descuento_pct = %s, precio_neto_unitario = %s, subtotal_linea = %s
                WHERE id = %s
            """, (pct, precio_neto, subtotal_linea, det_id))

        nueva_nota = _append_nota(
            pedido["notas"],
            f"CONDICION_PAGO actualizada. Anterior: {pedido['condicion_pago']}. Nueva: {condicion_norm}. "
            f"Descuento recalculado: {pct:.1f}%."
        )
        cur.execute("""
            UPDATE pedidos
            SET condicion_pago = %s, descuento_aplicado_pct = %s, subtotal = %s, total = %s,
                notas = %s, fecha_actualizacion_estado = %s
            WHERE numero_pedido = %s
        """, (condicion_norm, pct, subtotal, subtotal, nueva_nota, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), numero))
        conn.commit()
        cur.close()
    finally:
        conn.close()

    return (
        f"Condicion de pago del pedido {numero} cambiada a '{condicion_norm}'. "
        f"Descuento recalculado a {pct:.1f}%. Nuevo total: {subtotal:.2f}."
    )


@tool
def cancelar_pedido(numero_pedido: str, motivo: str) -> str:
    """Cancela un pedido. Solo es posible si el estado es 'solicitado' o 'en_revision'
    (antes de que backoffice lo apruebe). Si el pedido ya avanzo mas alla de ese punto,
    indica que debe abrirse una gestion de posventa en su lugar. Usar cuando el vendedor
    confirme que el cliente ya no quiere el pedido."""
    numero = numero_pedido.upper().strip()
    conn = get_conn()
    try:
        cur = conn.cursor()
        pedido = _fetch_pedido(cur, numero)
        if not pedido:
            cur.close()
            return f"No se encontro el pedido '{numero}'. Verifica el numero."

        if pedido["estado"] == "cancelado":
            cur.close()
            return f"El pedido {numero} ya esta cancelado."
        if pedido["estado"] not in _ESTADOS_PEDIDO_ACTIVOS_PARA_CANCELAR:
            cur.close()
            return (
                f"No se puede cancelar el pedido {numero} porque su estado actual es '{pedido['estado']}'. "
                f"Si hay un problema con este pedido, abre una gestion de posventa en su lugar "
                f"(`abrir_gestion_posventa`)."
            )

        nueva_nota = _append_nota(pedido["notas"], f"CANCELACION solicitada por el vendedor. Motivo: {motivo}")
        cur.execute("""
            UPDATE pedidos
            SET estado = 'cancelado', notas = %s, fecha_actualizacion_estado = %s
            WHERE numero_pedido = %s
        """, (nueva_nota, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), numero))
        conn.commit()
        cur.close()
    finally:
        conn.close()

    return f"Pedido {numero} cancelado correctamente."


@tool
def agregar_nota_pedido(numero_pedido: str, nota: str) -> str:
    """Anade una nota interna al pedido con timestamp. Util para registrar acuerdos puntuales
    o cualquier informacion que deba quedar trazada. No altera el estado del pedido."""
    numero = numero_pedido.upper().strip()
    texto = nota.strip()
    if len(texto) < 3:
        return "La nota es demasiado corta. Proporciona mas detalle."

    conn = get_conn()
    try:
        cur = conn.cursor()
        pedido = _fetch_pedido(cur, numero)
        if not pedido:
            cur.close()
            return f"No se encontro el pedido '{numero}'. Verifica el numero."

        nueva_nota = _append_nota(pedido["notas"], f"NOTA: {texto}")
        cur.execute("""
            UPDATE pedidos SET notas = %s WHERE numero_pedido = %s
        """, (nueva_nota, numero))
        conn.commit()
        cur.close()
    finally:
        conn.close()

    return f"Nota registrada en el pedido {numero}."


@tool
def abrir_gestion_posventa(codigo_cliente: str, numero_pedido: str, tipo: str, descripcion: str) -> str:
    """Abre una gestion de posventa formal vinculada a una cuenta (y opcionalmente a un pedido).
    Genera un numero de gestion GES-XXXX con estado 'abierto'. La prioridad se asigna
    automaticamente segun el tamano de canal de la cuenta y el tipo de gestion. La gestion
    guarda automaticamente una ficha completa de la cuenta y su historico reciente de pedidos
    junto con la descripcion — backoffice vera todo ese contexto sin pedirlo de nuevo, asi que
    la `descripcion` debe enfocarse en explicar con claridad y en detalle CUAL ES LA SOLICITUD
    o el problema puntual del vendedor (que paso, que pide, que resultado espera), no en repetir
    datos de la cuenta.

    Tipos validos: producto_defectuoso, faltante_entrega, error_facturacion, reclamo_precio,
    logistica_retraso, solicitud_credito, otro.

    Usar cuando el vendedor reporte un problema de un pedido YA TOMADO (faltante, defecto,
    facturacion, demora, credito) — no para tomar un pedido nuevo. Si numero_pedido no aplica,
    pasa un string vacio."""
    codigo = codigo_cliente.strip().upper()
    numero_ped = numero_pedido.strip().upper() or None
    tipo_norm = tipo.strip().lower().replace(" ", "_")
    desc = descripcion.strip()

    if tipo_norm not in _TIPOS_GESTION_VALIDOS:
        validos = ", ".join(sorted(_TIPOS_GESTION_VALIDOS))
        return f"Tipo de gestion '{tipo}' no valido. Tipos aceptados: {validos}."

    if len(desc) < 10:
        return "La descripcion es demasiado corta. Pide al vendedor mas detalle sobre lo ocurrido."

    conn = get_conn()
    try:
        cur = conn.cursor()
        empresa = _fetch_empresa(cur, codigo)
        if not empresa:
            cur.close()
            return f"No se encontro ninguna cuenta con codigo '{codigo}'. Verifica el dato antes de abrir la gestion."

        if numero_ped:
            pedido = _fetch_pedido(cur, numero_ped)
            if not pedido:
                cur.close()
                return f"No se encontro el pedido '{numero_ped}'. Verifica el numero o deja el campo vacio si no aplica."

        tamano = (empresa["tamano_canal"] or "").lower()
        tipos_sensibles = ("producto_defectuoso", "faltante_entrega", "error_facturacion")
        if tamano == "grande":
            prioridad = "alta"
        elif tipo_norm in tipos_sensibles:
            prioridad = "alta" if tamano == "mediano" else "media"
        else:
            prioridad = "media" if tamano == "mediano" else "baja"

        contexto_cuenta = (
            f"FICHA DE CUENTA (al momento de abrir la gestion):\n{_ficha_cuenta_texto(empresa)}\n\n"
            f"HISTORICO DE PEDIDOS:\n{_historico_pedidos_texto(cur, empresa, limite=5)}"
        )

        cur.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM gestiones_posventa")
        next_id = cur.fetchone()[0]
        numero_gestion = f"GES-{next_id:04d}"

        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("""
            INSERT INTO gestiones_posventa (numero_gestion, numero_pedido, empresa_cliente_id, tipo,
                                             descripcion, contexto_cuenta, estado, prioridad,
                                             canal_reporte, vendedor, fecha_apertura)
            VALUES (%s, %s, %s, %s, %s, %s, 'abierto', %s, 'chat', %s, %s)
        """, (numero_gestion, numero_ped, empresa["id"], tipo_norm, desc, contexto_cuenta, prioridad,
              empresa["vendedor_asignado"], ahora))

        if numero_ped:
            nueva_nota = _append_nota(
                pedido["notas"],
                f"GESTION {numero_gestion} abierta. Tipo: {tipo_norm}. Prioridad: {prioridad}."
            )
            cur.execute("UPDATE pedidos SET notas = %s WHERE numero_pedido = %s", (nueva_nota, numero_ped))
        conn.commit()
        cur.close()
    finally:
        conn.close()

    sla = "24h" if prioridad == "alta" else "48h" if prioridad == "media" else "72h"
    return (
        f"Gestion {numero_gestion} abierta correctamente.\n"
        f"- Cuenta: {empresa['nombre_comercial']} ({codigo})\n"
        f"- Pedido asociado: {numero_ped or '(sin pedido asociado)'}\n"
        f"- Tipo: {tipo_norm}\n"
        f"- Prioridad: {prioridad}\n"
        f"- Estado: abierto\n"
        f"Backoffice respondera en un plazo maximo de {sla}. "
        f"El numero de referencia es {numero_gestion}."
    )


# ── Previsualizacion para el modo de voz en vivo (Realtime, gate de confirmacion) ─────────
#
# Estas funciones NO estan decoradas con @tool: son invisibles para el agente de texto/LangGraph.
# Las usa realtime_voice.py para cotizar/validar sin escribir en la base, de forma que el modelo
# de voz en vivo nunca pueda escribir un pedido/cambio/cancelacion directamente — solo prepara
# un borrador que el backend confirma explicitamente (ver realtime_voice.confirmar_accion).

def previsualizar_pedido(codigo_cliente: str, items: list, condicion_pago: str, notas: str = "") -> dict:
    """Cotiza un pedido nuevo SIN escribir en la base. `items` es una lista de dicts
    {"producto": str, "cantidad": int}. Devuelve {"ok": True, "resumen", "commit_args", "datos"}
    o {"ok": False, "error"}. `commit_args` son los kwargs exactos con los que luego se invoca
    la tool real `crear_pedido` al confirmar — el modelo no puede alterar el precio entre medio."""
    codigo = codigo_cliente.strip().upper()
    condicion_norm = condicion_pago.strip().lower()

    if condicion_norm not in _CONDICIONES_PAGO_VALIDAS:
        return {"ok": False, "error": f"Condicion de pago '{condicion_pago}' no valida. Usa 'contado' o 'credito'."}
    if not isinstance(items, list) or not items:
        return {"ok": False, "error": "Debe indicarse al menos un item con producto y cantidad."}

    conn = get_conn()
    try:
        cur = conn.cursor()
        cot, error = _cotizar_pedido(cur, codigo, items, condicion_norm)
        cur.close()
    finally:
        conn.close()

    if error:
        return {"ok": False, "error": error}

    empresa = cot["empresa"]
    items_str = ", ".join(f"{cant} x {nom}" for _, nom, cant, _ in cot["lineas"])
    resumen = (
        f"Pedido para {empresa['nombre_comercial']} ({codigo}): {items_str}. "
        f"Condicion {condicion_norm}, descuento {cot['descuento_pct']:.1f}%. Total {cot['total']:.2f}."
    )
    return {
        "ok": True,
        "resumen": resumen,
        "commit_args": {
            "codigo_cliente": codigo,
            "items_json": json.dumps(items, ensure_ascii=False),
            "condicion_pago": condicion_norm,
            "notas": notas,
        },
        "datos": {"descuento_pct": cot["descuento_pct"], "total": cot["total"], "volumen_litros": cot["volumen_litros"]},
    }


def previsualizar_cambio_condicion(numero_pedido: str, nueva_condicion_pago: str) -> dict:
    """Recotiza un cambio de condicion de pago SIN escribir en la base. Misma forma de
    retorno que previsualizar_pedido."""
    numero = numero_pedido.upper().strip()
    condicion_norm = nueva_condicion_pago.strip().lower()

    if condicion_norm not in _CONDICIONES_PAGO_VALIDAS:
        return {"ok": False, "error": f"Condicion de pago '{nueva_condicion_pago}' no valida. Usa 'contado' o 'credito'."}

    conn = get_conn()
    try:
        cur = conn.cursor()
        pedido = _fetch_pedido(cur, numero)
        if not pedido:
            cur.close()
            return {"ok": False, "error": f"No se encontro el pedido '{numero}'. Verifica el numero."}
        if pedido["estado"] != "solicitado":
            cur.close()
            return {"ok": False, "error": (
                f"No se puede cambiar la condicion de pago del pedido {numero} porque su estado es "
                f"'{pedido['estado']}'. Solo es posible mientras esta 'solicitado'."
            )}
        if pedido["condicion_pago"] == condicion_norm:
            cur.close()
            return {"ok": False, "error": f"El pedido {numero} ya tiene condicion de pago '{condicion_norm}'. No se requiere cambio."}

        pct, subtotal, _detalle_calc = _recotizar_condicion_pago(cur, pedido, condicion_norm)
        cur.close()
    finally:
        conn.close()

    resumen = (
        f"Pedido {numero} ({pedido['nombre_comercial']}): condicion de pago '{pedido['condicion_pago']}' "
        f"pasa a '{condicion_norm}'. Descuento recalculado {pct:.1f}%. Nuevo total {subtotal:.2f}."
    )
    return {
        "ok": True,
        "resumen": resumen,
        "commit_args": {"numero_pedido": numero, "nueva_condicion_pago": condicion_norm},
        "datos": {"descuento_pct": pct, "total": subtotal},
    }


def previsualizar_cancelacion(numero_pedido: str, motivo: str = "") -> dict:
    """Valida que un pedido se pueda cancelar SIN escribir en la base. Misma forma de
    retorno que previsualizar_pedido."""
    numero = numero_pedido.upper().strip()
    conn = get_conn()
    try:
        cur = conn.cursor()
        pedido = _fetch_pedido(cur, numero)
        cur.close()
    finally:
        conn.close()

    if not pedido:
        return {"ok": False, "error": f"No se encontro el pedido '{numero}'. Verifica el numero."}
    if pedido["estado"] == "cancelado":
        return {"ok": False, "error": f"El pedido {numero} ya esta cancelado."}
    if pedido["estado"] not in _ESTADOS_PEDIDO_ACTIVOS_PARA_CANCELAR:
        return {"ok": False, "error": (
            f"No se puede cancelar el pedido {numero} porque su estado actual es '{pedido['estado']}'. "
            f"Si hay un problema con este pedido, hay que abrir una gestion de posventa en su lugar."
        )}

    motivo_norm = motivo.strip() or "Cancelado por el vendedor (confirmado por voz en vivo)."
    resumen = f"Cancelar el pedido {numero} de {pedido['nombre_comercial']} (total {float(pedido['total']):.2f}). Motivo: {motivo_norm}."
    return {
        "ok": True,
        "resumen": resumen,
        "commit_args": {"numero_pedido": numero, "motivo": motivo_norm},
        "datos": {"total": float(pedido["total"])},
    }


# ── Construccion del agente ───────────────────────────────────────────────────

def build_agent(checkpointer):
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7)
    tools = [
        # Lectura
        consultar_cuenta_cliente, consultar_historico_pedidos, buscar_producto,
        consultar_politica_descuento, ontologia_procedimientos, ontologia_descuentos,
        ontologia_faq, analizar_documento, consultar_gestiones_posventa,
        # Escritura
        crear_pedido, cambiar_condicion_pago_pedido, cancelar_pedido,
        agregar_nota_pedido, abrir_gestion_posventa,
    ]

    system_prompt = cargar_system_prompt()
    llm_with_tools = llm.bind_tools(tools)

    def agent_node(state: MessagesState):
        messages = [SystemMessage(content=system_prompt)] + state["messages"]
        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    def should_continue(state: MessagesState):
        last = state["messages"][-1]
        return "tools" if last.tool_calls else END

    builder = StateGraph(MessagesState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", should_continue)
    builder.add_edge("tools", "agent")

    return builder.compile(checkpointer=checkpointer)


# ── Funcion principal ─────────────────────────────────────────────────────────

def run_agent(session_id: str):
    checkpointer = MemorySaver()
    agent = build_agent(checkpointer)

    config = {"configurable": {"thread_id": session_id}}

    print(f"\n Asistente de fuerza de venta — sesion: '{session_id}'")
    print("Escribe 'salir' para terminar.\n")

    from langchain_core.messages import HumanMessage
    result = agent.invoke(
        {"messages": [HumanMessage(content="Hola")]},
        config=config
    )
    print(f"Asistente: {result['messages'][-1].content}\n")

    while True:
        user_input = input("Vendedor: ").strip()
        if not user_input:
            continue
        if user_input.lower() in ("salir", "exit", "quit"):
            print("Sesion cerrada.")
            break

        result = agent.invoke(
            {"messages": [HumanMessage(content=user_input)]},
            config=config
        )
        print(f"\nAsistente: {result['messages'][-1].content}\n")


# ── Punto de entrada ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Asistente de fuerza de venta en terreno — Coca-Cola")
    parser.add_argument("--session", type=str, default="default", help="ID de sesion")
    args = parser.parse_args()

    run_agent(args.session)
