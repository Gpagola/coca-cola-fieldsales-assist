# ── Importaciones ──────────────────────────────────────────────────────────────

import argparse
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
    names = ["ontologia-procedimientos", "ontologia-faq"]
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


# ── Tools (herramientas del agente) ───────────────────────────────────────────

def _render_pedido_detalle(cur, numero: str) -> str | None:
    """Carga y formatea el detalle completo de un pedido. None si no existe."""
    cur.execute("""
        SELECT p.numero_pedido, p.fecha_pedido, p.estado, p.total,
               p.metodo_pago, p.direccion_envio, p.tracking,
               p.fecha_envio, p.fecha_entrega_estimada, p.fecha_entrega_real, p.notas,
               c.id, c.nombre, c.email, c.nivel_fidelidad, c.ciudad,
               c.total_compras, c.num_pedidos
        FROM pedidos p
        JOIN clientes c ON c.id = p.cliente_id
        WHERE p.numero_pedido = %s
    """, (numero,))
    row = cur.fetchone()
    if not row:
        return None

    cur.execute("""
        SELECT pr.nombre, pr.categoria, d.cantidad, d.precio_unitario
        FROM detalle_pedido d
        JOIN productos pr ON pr.id = d.producto_id
        WHERE d.numero_pedido = %s
    """, (numero,))
    items = cur.fetchall()

    (num, fecha_pedido, estado, total, metodo_pago, direccion,
     tracking, fecha_envio, fecha_entrega_est, fecha_entrega_real, notas,
     cli_id, cli_nombre, cli_email, nivel_fid, ciudad, total_compras, num_pedidos) = row

    items_str = "\n".join(
        f"    * {nom} ({cat}) x{cant} — {prec:.2f} EUR/u"
        for nom, cat, cant, prec in items
    ) or "    (sin items)"

    return (
        f"Pedido encontrado:\n"
        f"- Numero: {num}\n"
        f"- Fecha del pedido: {fecha_pedido.strftime('%d/%m/%Y')}\n"
        f"- Estado: {estado}\n"
        f"- Total: {float(total):.2f} EUR\n"
        f"- Metodo de pago: {metodo_pago or 'No disponible'}\n"
        f"- Direccion de envio: {direccion or 'No disponible'}\n"
        f"- Tracking: {tracking or 'Sin tracking aun'}\n"
        f"- Fecha envio: {fecha_envio.strftime('%d/%m/%Y') if fecha_envio else 'No enviado'}\n"
        f"- Entrega estimada: {fecha_entrega_est.strftime('%d/%m/%Y') if fecha_entrega_est else 'No disponible'}\n"
        f"- Entrega real: {fecha_entrega_real.strftime('%d/%m/%Y') if fecha_entrega_real else 'No entregado'}\n"
        f"- Notas: {notas or 'Sin notas'}\n"
        f"\nCliente:\n"
        f"- Nombre: {cli_nombre}\n"
        f"- Email: {cli_email or 'No disponible'}\n"
        f"- Nivel de fidelidad: {nivel_fid}\n"
        f"- Ciudad: {ciudad or 'No disponible'}\n"
        f"- Total acumulado de compras: {float(total_compras):.2f} EUR\n"
        f"- Numero de pedidos historicos: {num_pedidos}\n"
        f"\nItems del pedido:\n{items_str}"
    )


@tool
def buscar_pedido(consulta: str) -> str:
    """Busca pedidos por numero, nombre de cliente o direccion de envio.

    Formatos aceptados:
    - Numero exacto (PED-XXXX): devuelve el detalle completo del pedido.
    - Nombre de cliente (parcial o completo, ej. 'Maria Garcia'): busca pedidos de ese cliente.
    - Fragmento de direccion (ej. 'Calle Mayor 23', 'Madrid'): busca pedidos con direcciones coincidentes.

    Comportamiento:
    - Si hay **un unico** resultado: devuelve el detalle completo (como si fuera busqueda por numero).
    - Si hay **varios** resultados: devuelve una lista resumida. Pide al cliente que confirme a cual se refiere
      y vuelve a llamar a esta tool con el numero exacto (PED-XXXX) para obtener el detalle.
    - Si no hay resultados: devuelve un mensaje pidiendo mas informacion."""
    q = consulta.strip()
    if not q:
        return "La consulta esta vacia. Pide al cliente el numero de pedido, su nombre, o la direccion de envio."

    conn = get_conn()
    try:
        cur = conn.cursor()

        # Caso 1: numero de pedido exacto
        if q.upper().startswith("PED-"):
            numero = q.upper()
            detalle = _render_pedido_detalle(cur, numero)
            cur.close()
            return detalle or f"No se encontro ningun pedido con el numero '{numero}'."

        # Caso 2: busqueda por nombre de cliente o direccion de envio
        like = f"%{q}%"
        cur.execute("""
            SELECT p.numero_pedido, p.fecha_pedido, p.estado, p.total,
                   p.direccion_envio, c.nombre, c.nivel_fidelidad
            FROM pedidos p
            JOIN clientes c ON c.id = p.cliente_id
            WHERE c.nombre LIKE %s OR p.direccion_envio LIKE %s
            ORDER BY p.fecha_pedido DESC
            LIMIT 10
        """, (like, like))
        rows = cur.fetchall()

        if not rows:
            cur.close()
            return (
                f"No se encontraron pedidos que coincidan con '{q}'. "
                f"Pide al cliente el numero de pedido (formato PED-XXXX) o mas datos para localizarlo."
            )

        # Un solo resultado: detalle completo
        if len(rows) == 1:
            numero = rows[0][0]
            detalle = _render_pedido_detalle(cur, numero)
            cur.close()
            return detalle or f"Error al cargar el pedido {numero}."

        # Varios resultados: lista para desambiguar
        out = [
            f"Se encontraron {len(rows)} pedidos que coinciden con '{q}'. "
            f"Pide al cliente que confirme a cual se refiere y vuelve a llamar a buscar_pedido con el numero exacto:"
        ]
        for numero, fecha, estado, total, direccion, cliente, nivel in rows:
            out.append(
                f"- {numero} | {fecha.strftime('%d/%m/%Y')} | {estado} | "
                f"{float(total):.2f} EUR | {cliente} ({nivel}) | {direccion or 'sin direccion'}"
            )
        cur.close()
        return "\n".join(out)
    finally:
        conn.close()


@tool
def buscar_cliente(email_o_telefono: str) -> str:
    """Busca un cliente por email o telefono.
    Devuelve su nombre, nivel de fidelidad, historial y los pedidos recientes.
    Usar cuando el cliente no tenga numero de pedido pero si email o telefono."""
    q = email_o_telefono.strip()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, nombre, email, telefono, nivel_fidelidad, ciudad,
                   total_compras, num_pedidos, fecha_registro
            FROM clientes
            WHERE email = %s OR telefono = %s
            LIMIT 1
        """, (q, q))
        row = cur.fetchone()
        pedidos = []
        if row:
            cur.execute("""
                SELECT numero_pedido, fecha_pedido, estado, total
                FROM pedidos
                WHERE cliente_id = %s
                ORDER BY fecha_pedido DESC
                LIMIT 5
            """, (row[0],))
            pedidos = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    if not row:
        return f"No se encontro ningun cliente con '{email_o_telefono}'."

    (_, nombre, email, telefono, nivel, ciudad, total_compras, num_pedidos, fecha_reg) = row
    pedidos_str = "\n".join(
        f"  * {num} — {fp.strftime('%d/%m/%Y')} — {estado} — {float(tot):.2f} EUR"
        for num, fp, estado, tot in pedidos
    ) or "  (sin pedidos recientes)"

    return (
        f"Cliente encontrado:\n"
        f"- Nombre: {nombre}\n"
        f"- Email: {email or 'No disponible'}\n"
        f"- Telefono: {telefono or 'No disponible'}\n"
        f"- Nivel de fidelidad: {nivel}\n"
        f"- Ciudad: {ciudad or 'No disponible'}\n"
        f"- Registrado desde: {fecha_reg.strftime('%d/%m/%Y')}\n"
        f"- Total acumulado de compras: {float(total_compras):.2f} EUR\n"
        f"- Numero de pedidos historicos: {num_pedidos}\n"
        f"\nPedidos recientes:\n{pedidos_str}"
    )


@tool
def buscar_producto(nombre_o_categoria: str) -> str:
    """Busca productos por nombre o categoria.
    Devuelve nombre, categoria, precio, stock y descripcion.
    Usar cuando el cliente pregunte por informacion de un producto o disponibilidad."""
    q = f"%{nombre_o_categoria.strip()}%"
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT nombre, categoria, precio, stock, descripcion
            FROM productos
            WHERE nombre LIKE %s OR categoria LIKE %s
            ORDER BY stock DESC
            LIMIT 5
        """, (q, q))
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    if not rows:
        return f"No se encontraron productos con '{nombre_o_categoria}'."

    out = [f"Productos encontrados ({len(rows)}):"]
    for nom, cat, prec, stock, desc in rows:
        out.append(
            f"\n- {nom} ({cat})\n"
            f"  Precio: {float(prec):.2f} EUR | Stock: {stock} unidades\n"
            f"  {desc or ''}"
        )
    return "\n".join(out)


@tool
def ontologia_procedimientos() -> str:
    """Consulta la ontologia de procedimientos de atencion al cliente.
    Contiene los pasos a seguir segun el tipo de consulta: estado de pedido,
    devoluciones, retrasos de envio, cancelaciones, informacion de producto,
    facturacion, quejas. Usar siempre antes de guiar al cliente en una gestion."""
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
def ontologia_faq() -> str:
    """Consulta las preguntas frecuentes (FAQ) de la tienda: envios, devoluciones,
    pagos, programa de fidelidad, tiendas fisicas. Usar cuando el cliente haga
    una pregunta general sobre politicas o funcionamiento de la tienda."""
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


# ── Helpers para tools de escritura ───────────────────────────────────────────

_METODOS_PAGO_VALIDOS = {"tarjeta", "paypal", "bizum", "transferencia", "contra_reembolso"}
_PLAZO_DEVOLUCION_DIAS = 30
_PLAZO_DEVOLUCION_VIP_DIAS = 45
_TIPOS_RECLAMO_VALIDOS = {"defecto", "no_recibido", "retraso", "cobro_indebido", "atencion", "otro"}


def _fetch_pedido(cur, numero_pedido: str):
    """Carga un pedido y su cliente. Devuelve dict o None."""
    cur.execute("""
        SELECT p.numero_pedido, p.cliente_id, p.fecha_pedido, p.estado, p.total,
               p.metodo_pago, p.direccion_envio, p.tracking,
               p.fecha_envio, p.fecha_entrega_estimada, p.fecha_entrega_real, p.notas,
               c.nombre, c.nivel_fidelidad
        FROM pedidos p
        JOIN clientes c ON c.id = p.cliente_id
        WHERE p.numero_pedido = %s
    """, (numero_pedido,))
    row = cur.fetchone()
    if not row:
        return None
    keys = ["numero", "cliente_id", "fecha_pedido", "estado", "total",
            "metodo_pago", "direccion_envio", "tracking",
            "fecha_envio", "fecha_entrega_estimada", "fecha_entrega_real", "notas",
            "cliente_nombre", "nivel_fidelidad"]
    return dict(zip(keys, row))


def _append_nota(notas_actuales: str | None, nueva: str) -> str:
    """Concatena una nota nueva con timestamp al campo notas."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"[{stamp}] {nueva}"
    if notas_actuales and notas_actuales.strip():
        return f"{notas_actuales.strip()}\n{entry}"
    return entry


# ── Tools de escritura (mutaciones sobre pedidos) ─────────────────────────────

@tool
def cancelar_pedido(numero_pedido: str, motivo: str) -> str:
    """Cancela un pedido. Solo es posible si el estado es 'pendiente' o 'procesando'.
    Si el pedido ya esta enviado, devuelve un mensaje explicando las alternativas.
    Usar cuando el cliente confirme que quiere cancelar su pedido."""
    numero = numero_pedido.upper().strip()
    conn = get_conn()
    try:
        cur = conn.cursor()
        pedido = _fetch_pedido(cur, numero)
        if not pedido:
            cur.close()
            return f"No se encontro el pedido '{numero}'. Verifica el numero."

        estado = (pedido["estado"] or "").lower()
        if estado in ("cancelado", "devuelto"):
            cur.close()
            return f"El pedido {numero} ya esta en estado '{estado}'. No se puede cancelar de nuevo."
        if estado not in ("pendiente", "procesando"):
            cur.close()
            return (
                f"No se puede cancelar el pedido {numero} porque su estado actual es '{estado}'. "
                f"Alternativas: si aun no lo has recibido, puedes rechazar la entrega al mensajero; "
                f"si ya lo recibiste, puedes iniciar una devolucion."
            )

        nueva_nota = _append_nota(pedido["notas"], f"CANCELACION solicitada por el cliente. Motivo: {motivo}")
        cur.execute("""
            UPDATE pedidos
            SET estado = 'cancelado', notas = %s
            WHERE numero_pedido = %s
        """, (nueva_nota, numero))
        conn.commit()
        cur.close()
    finally:
        conn.close()

    return (
        f"Pedido {numero} cancelado correctamente. "
        f"El reembolso de {float(pedido['total']):.2f} EUR se procesara en 3-5 dias habiles "
        f"al metodo de pago original ({pedido['metodo_pago'] or 'metodo original'})."
    )


@tool
def actualizar_direccion_envio(numero_pedido: str, nueva_direccion: str) -> str:
    """Actualiza la direccion de envio de un pedido. Solo es posible si el pedido aun no ha salido
    (fecha_envio vacia). Usar cuando el cliente indique un error en la direccion o un cambio de ultima hora."""
    numero = numero_pedido.upper().strip()
    direccion = nueva_direccion.strip()
    if len(direccion) < 10:
        return "La direccion proporcionada es demasiado corta. Pide al cliente la direccion completa (calle, numero, ciudad, CP)."

    conn = get_conn()
    try:
        cur = conn.cursor()
        pedido = _fetch_pedido(cur, numero)
        if not pedido:
            cur.close()
            return f"No se encontro el pedido '{numero}'. Verifica el numero."

        if pedido["fecha_envio"] is not None:
            cur.close()
            return (
                f"No se puede cambiar la direccion del pedido {numero} porque ya fue enviado "
                f"el {pedido['fecha_envio'].strftime('%d/%m/%Y')}. "
                f"El cliente puede contactar con la empresa de transporte usando el tracking "
                f"{pedido['tracking'] or '(pendiente de asignar)'} para redirigir la entrega."
            )

        estado = (pedido["estado"] or "").lower()
        if estado in ("cancelado", "devuelto"):
            cur.close()
            return f"No se puede actualizar la direccion: el pedido {numero} esta '{estado}'."

        direccion_anterior = pedido["direccion_envio"] or "(sin direccion previa)"
        nueva_nota = _append_nota(
            pedido["notas"],
            f"DIRECCION actualizada. Anterior: {direccion_anterior}. Nueva: {direccion}"
        )
        cur.execute("""
            UPDATE pedidos
            SET direccion_envio = %s, notas = %s
            WHERE numero_pedido = %s
        """, (direccion, nueva_nota, numero))
        conn.commit()
        cur.close()
    finally:
        conn.close()

    return f"Direccion de envio del pedido {numero} actualizada a: {direccion}."


@tool
def cambiar_metodo_pago(numero_pedido: str, nuevo_metodo: str) -> str:
    """Cambia el metodo de pago de un pedido. Solo es posible si el estado es 'pendiente'.
    Metodos validos: tarjeta, paypal, bizum, transferencia, contra_reembolso.
    Usar cuando el cliente quiera cambiar la forma de pago antes de que se procese."""
    numero = numero_pedido.upper().strip()
    metodo = nuevo_metodo.strip().lower().replace(" ", "_")

    if metodo not in _METODOS_PAGO_VALIDOS:
        validos = ", ".join(sorted(_METODOS_PAGO_VALIDOS))
        return f"Metodo de pago '{nuevo_metodo}' no valido. Metodos aceptados: {validos}."

    conn = get_conn()
    try:
        cur = conn.cursor()
        pedido = _fetch_pedido(cur, numero)
        if not pedido:
            cur.close()
            return f"No se encontro el pedido '{numero}'. Verifica el numero."

        estado = (pedido["estado"] or "").lower()
        if estado != "pendiente":
            cur.close()
            return (
                f"No se puede cambiar el metodo de pago del pedido {numero} porque su estado es '{estado}'. "
                f"Solo es posible modificar el pago de pedidos en estado 'pendiente'."
            )

        if (pedido["metodo_pago"] or "").lower() == metodo:
            cur.close()
            return f"El pedido {numero} ya tiene '{metodo}' como metodo de pago. No se requiere cambio."

        nueva_nota = _append_nota(
            pedido["notas"],
            f"METODO_PAGO actualizado. Anterior: {pedido['metodo_pago'] or 'ninguno'}. Nuevo: {metodo}"
        )
        cur.execute("""
            UPDATE pedidos
            SET metodo_pago = %s, notas = %s
            WHERE numero_pedido = %s
        """, (metodo, nueva_nota, numero))
        conn.commit()
        cur.close()
    finally:
        conn.close()

    return f"Metodo de pago del pedido {numero} cambiado a '{metodo}' correctamente."


@tool
def iniciar_devolucion(numero_pedido: str, motivo: str) -> str:
    """Inicia una devolucion de un pedido entregado. Solo es posible si el estado es 'entregado'
    y esta dentro del plazo de devolucion (30 dias, 45 para VIP).
    Cambia el estado a 'devuelto' y registra el motivo. Usar cuando el cliente confirme que quiere devolver."""
    numero = numero_pedido.upper().strip()
    conn = get_conn()
    try:
        cur = conn.cursor()
        pedido = _fetch_pedido(cur, numero)
        if not pedido:
            cur.close()
            return f"No se encontro el pedido '{numero}'. Verifica el numero."

        estado = (pedido["estado"] or "").lower()
        if estado == "devuelto":
            cur.close()
            return f"El pedido {numero} ya fue devuelto previamente."
        if estado != "entregado":
            cur.close()
            return (
                f"No se puede iniciar una devolucion del pedido {numero} porque su estado es '{estado}'. "
                f"Solo se pueden devolver pedidos con estado 'entregado'."
            )

        fecha_entrega = pedido["fecha_entrega_real"]
        if fecha_entrega is None:
            cur.close()
            return f"El pedido {numero} figura como entregado pero no tiene fecha de entrega registrada. Abre una incidencia antes de proceder."

        nivel = (pedido["nivel_fidelidad"] or "").lower()
        limite_dias = _PLAZO_DEVOLUCION_VIP_DIAS if nivel == "vip" else _PLAZO_DEVOLUCION_DIAS
        dias_desde_entrega = (date.today() - fecha_entrega).days

        if dias_desde_entrega > limite_dias:
            cur.close()
            return (
                f"No se puede procesar la devolucion del pedido {numero}: han pasado {dias_desde_entrega} dias "
                f"desde la entrega y el plazo para este cliente es de {limite_dias} dias. "
                f"Sugiere al cliente acudir a una tienda fisica o escalar a supervisor si lo considera."
            )

        nueva_nota = _append_nota(
            pedido["notas"],
            f"DEVOLUCION iniciada. Motivo: {motivo}. Dias desde entrega: {dias_desde_entrega}."
        )
        cur.execute("""
            UPDATE pedidos
            SET estado = 'devuelto', notas = %s
            WHERE numero_pedido = %s
        """, (nueva_nota, numero))
        conn.commit()
        cur.close()
    finally:
        conn.close()

    return (
        f"Devolucion del pedido {numero} iniciada correctamente. "
        f"Recibiras por email una etiqueta de devolucion prepagada. "
        f"Una vez recibamos el producto, el reembolso de {float(pedido['total']):.2f} EUR "
        f"se procesara en 5-7 dias habiles."
    )


@tool
def marcar_incidencia_entrega(numero_pedido: str, descripcion: str) -> str:
    """Registra una incidencia de entrega cuando el cliente indica que no recibio un pedido marcado como entregado.
    No cambia el estado del pedido; solo anota la incidencia para que el equipo de logistica investigue.
    Usar cuando el cliente diga 'no lo recibi' y el estado sea 'entregado'."""
    numero = numero_pedido.upper().strip()
    conn = get_conn()
    try:
        cur = conn.cursor()
        pedido = _fetch_pedido(cur, numero)
        if not pedido:
            cur.close()
            return f"No se encontro el pedido '{numero}'. Verifica el numero."

        estado = (pedido["estado"] or "").lower()
        if estado != "entregado":
            cur.close()
            return (
                f"El pedido {numero} no figura como 'entregado' (estado actual: '{estado}'). "
                f"Si hay un problema con el envio, usa el procedimiento de retraso en su lugar."
            )

        nueva_nota = _append_nota(
            pedido["notas"],
            f"INCIDENCIA_ENTREGA: cliente indica no recibido. Detalle: {descripcion}"
        )
        cur.execute("""
            UPDATE pedidos
            SET notas = %s
            WHERE numero_pedido = %s
        """, (nueva_nota, numero))
        conn.commit()
        cur.close()
    finally:
        conn.close()

    return (
        f"Incidencia registrada en el pedido {numero}. El equipo de logistica contactara con la empresa "
        f"de transporte (tracking {pedido['tracking'] or 'no disponible'}) y te daremos respuesta en 48h habiles. "
        f"Recibiras un email con el numero de caso."
    )


@tool
def agregar_nota_pedido(numero_pedido: str, nota: str) -> str:
    """Anade una nota interna al pedido con timestamp. Util para registrar acuerdos puntuales,
    feedback de quejas, preferencias del cliente, o cualquier informacion que deba quedar trazada.
    No altera el estado del pedido. Usar despues de una queja, una peticion especial, o cualquier
    acuerdo que no tenga su propia tool especifica."""
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
            UPDATE pedidos
            SET notas = %s
            WHERE numero_pedido = %s
        """, (nueva_nota, numero))
        conn.commit()
        cur.close()
    finally:
        conn.close()

    return f"Nota registrada en el pedido {numero}."


@tool
def abrir_reclamo(numero_pedido: str, tipo: str, descripcion: str) -> str:
    """Abre un reclamo formal vinculado a un pedido. Genera un numero de reclamo REC-XXXX
    y lo registra en el sistema con estado 'abierto'. La prioridad se asigna automaticamente
    segun el nivel de fidelidad del cliente y el tipo de reclamo.

    Tipos validos: defecto, no_recibido, retraso, cobro_indebido, atencion, otro.

    Usar cuando:
    - El cliente reporta un producto defectuoso o danado.
    - El cliente insiste en que no ha recibido un pedido marcado como entregado y ya existe
      una incidencia de entrega sin resolver (escalar a reclamo formal).
    - Hay un cobro duplicado o indebido que requiere investigacion del equipo de pagos.
    - El cliente pide escalar una queja o pide hablar con un supervisor.
    - Cualquier situacion que requiera trazabilidad formal y respuesta del equipo."""
    numero = numero_pedido.upper().strip()
    tipo_norm = tipo.strip().lower().replace(" ", "_")
    desc = descripcion.strip()

    if tipo_norm not in _TIPOS_RECLAMO_VALIDOS:
        validos = ", ".join(sorted(_TIPOS_RECLAMO_VALIDOS))
        return f"Tipo de reclamo '{tipo}' no valido. Tipos aceptados: {validos}."

    if len(desc) < 10:
        return "La descripcion del reclamo es demasiado corta. Pide al cliente mas detalle sobre lo ocurrido."

    conn = get_conn()
    try:
        cur = conn.cursor()
        pedido = _fetch_pedido(cur, numero)
        if not pedido:
            cur.close()
            return f"No se encontro el pedido '{numero}'. Verifica el numero antes de abrir el reclamo."

        nivel = (pedido["nivel_fidelidad"] or "").lower()
        # Prioridad: VIP siempre alta. Tipos sensibles (defecto, no_recibido, cobro_indebido) al menos media.
        if nivel == "vip":
            prioridad = "alta"
        elif tipo_norm in ("defecto", "no_recibido", "cobro_indebido"):
            prioridad = "alta" if nivel == "premium" else "media"
        else:
            prioridad = "media" if nivel == "premium" else "baja"

        cur.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM reclamos")
        next_id = cur.fetchone()[0]
        numero_reclamo = f"REC-{next_id:04d}"

        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("""
            INSERT INTO reclamos (numero_reclamo, numero_pedido, cliente_id, tipo, descripcion,
                                  estado, prioridad, canal, fecha_apertura)
            VALUES (%s, %s, %s, %s, %s, 'abierto', %s, 'chat', %s)
        """, (numero_reclamo, numero, pedido["cliente_id"], tipo_norm, desc, prioridad, ahora))

        # Dejar trazado tambien en el pedido
        nueva_nota = _append_nota(
            pedido["notas"],
            f"RECLAMO {numero_reclamo} abierto. Tipo: {tipo_norm}. Prioridad: {prioridad}."
        )
        cur.execute("UPDATE pedidos SET notas = %s WHERE numero_pedido = %s", (nueva_nota, numero))
        conn.commit()
        cur.close()
    finally:
        conn.close()

    sla = "24h" if prioridad == "alta" else "48h" if prioridad == "media" else "72h"
    return (
        f"Reclamo {numero_reclamo} abierto correctamente.\n"
        f"- Pedido asociado: {numero}\n"
        f"- Tipo: {tipo_norm}\n"
        f"- Prioridad: {prioridad}\n"
        f"- Estado: abierto\n"
        f"Nuestro equipo contactara al cliente en un plazo maximo de {sla}. "
        f"El numero de referencia es {numero_reclamo} — compartelo con el cliente."
    )


@tool
def consultar_reclamos(numero: str) -> str:
    """Consulta reclamos existentes. Acepta dos formatos:
    - Un numero de reclamo (REC-XXXX): devuelve el detalle completo de ese reclamo.
    - Un numero de pedido (PED-XXXX): devuelve la lista de todos los reclamos vinculados a ese pedido.

    Usar cuando el cliente pregunte por el estado de un reclamo previo o cuando quieras
    verificar si un pedido ya tiene reclamos abiertos antes de abrir uno nuevo."""
    q = numero.upper().strip()
    conn = get_conn()
    try:
        cur = conn.cursor()

        if q.startswith("REC-"):
            cur.execute("""
                SELECT r.numero_reclamo, r.numero_pedido, r.tipo, r.descripcion,
                       r.estado, r.prioridad, r.canal, r.fecha_apertura, r.fecha_cierre,
                       r.resolucion, c.nombre, c.nivel_fidelidad
                FROM reclamos r
                JOIN clientes c ON c.id = r.cliente_id
                WHERE r.numero_reclamo = %s
            """, (q,))
            row = cur.fetchone()
            cur.close()
            if not row:
                return f"No se encontro el reclamo '{q}'."
            (num, num_ped, tipo, desc, estado, prio, canal, f_ap, f_cie, resol, cli_nom, nivel) = row
            return (
                f"Reclamo encontrado:\n"
                f"- Numero: {num}\n"
                f"- Pedido asociado: {num_ped or '(sin pedido asociado)'}\n"
                f"- Cliente: {cli_nom} ({nivel})\n"
                f"- Tipo: {tipo}\n"
                f"- Estado: {estado}\n"
                f"- Prioridad: {prio}\n"
                f"- Canal: {canal}\n"
                f"- Abierto el: {f_ap.strftime('%d/%m/%Y %H:%M')}\n"
                f"- Cerrado el: {f_cie.strftime('%d/%m/%Y %H:%M') if f_cie else 'Aun abierto'}\n"
                f"- Descripcion: {desc}\n"
                f"- Resolucion: {resol or 'Pendiente'}"
            )

        if q.startswith("PED-"):
            cur.execute("""
                SELECT numero_reclamo, tipo, estado, prioridad, fecha_apertura, fecha_cierre
                FROM reclamos
                WHERE numero_pedido = %s
                ORDER BY fecha_apertura DESC
            """, (q,))
            rows = cur.fetchall()
            cur.close()
            if not rows:
                return f"El pedido {q} no tiene reclamos registrados."
            out = [f"Reclamos vinculados al pedido {q} ({len(rows)}):"]
            for num_r, tipo, estado, prio, f_ap, f_cie in rows:
                cierre = f_cie.strftime('%d/%m/%Y') if f_cie else "abierto"
                out.append(
                    f"- {num_r} | {tipo} | estado: {estado} | prioridad: {prio} | "
                    f"abierto: {f_ap.strftime('%d/%m/%Y')} | cerrado: {cierre}"
                )
            return "\n".join(out)

        cur.close()
        return f"Formato no reconocido: '{numero}'. Usa REC-XXXX (reclamo) o PED-XXXX (pedido)."
    finally:
        conn.close()


@tool
def analizar_documento(contenido: str) -> str:
    """Analiza el contenido extraido de un documento (PDF o imagen) subido por el cliente.
    Determina el tipo de documento (factura, foto de producto, comprobante, otro) y extrae
    la informacion relevante. Usar cuando el cliente adjunte un archivo al chat."""
    return contenido


# ── Construccion del agente ───────────────────────────────────────────────────

def build_agent(checkpointer):
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7)
    tools = [
        # Lectura
        buscar_pedido, buscar_cliente, buscar_producto,
        ontologia_procedimientos, ontologia_faq, analizar_documento,
        consultar_reclamos,
        # Escritura (mutaciones sobre pedidos)
        cancelar_pedido, actualizar_direccion_envio, cambiar_metodo_pago,
        iniciar_devolucion, marcar_incidencia_entrega, agregar_nota_pedido,
        abrir_reclamo,
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

    print(f"\n Asistente de atencion al cliente — sesion: '{session_id}'")
    print("Escribe 'salir' para terminar.\n")

    from langchain_core.messages import HumanMessage
    result = agent.invoke(
        {"messages": [HumanMessage(content="Hola")]},
        config=config
    )
    print(f"Asistente: {result['messages'][-1].content}\n")

    while True:
        user_input = input("Cliente: ").strip()
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
    parser = argparse.ArgumentParser(description="Asistente de atencion al cliente para retail")
    parser.add_argument("--session", type=str, default="default", help="ID de sesion")
    args = parser.parse_args()

    run_agent(args.session)
