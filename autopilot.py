"""
Autopilot — Simulador de cliente + Evaluador de conversaciones (RETAIL)
Corre conversaciones automaticas contra el agente de atencion al cliente para hacer pruebas.
El cliente simulado habla DIRECTAMENTE con el asistente (no hay ejecutivo intermediario).
"""

import json
import random
from openai import OpenAI
from chatbot import get_conn

client = OpenAI()

# ── Datos de referencia ───────────────────────────────────────────────────────

MOTIVOS = [
    # Estado de pedido
    "Quiero saber en que estado esta mi pedido, lleva varios dias sin noticias",
    "Necesito el tracking de mi pedido, no me ha llegado ningun email",
    # Retraso de envio
    "Mi pedido deberia haber llegado ayer y sigo sin recibirlo",
    "Pague envio express pero lleva mas de una semana sin moverse",
    # Devoluciones / cambios
    "Quiero devolver un producto, no me ha gustado",
    "El producto que recibi no es mi talla, quiero cambiarlo",
    "Necesito cambiar un articulo por otro modelo",
    # Producto defectuoso / incorrecto
    "El producto me llego roto, necesito una solucion",
    "Me enviaron un articulo diferente al que pedi",
    "Falta una pieza del pedido que hice",
    # Informacion de producto
    "Quiero saber si hay stock de un producto en concreto",
    "Necesito informacion sobre las caracteristicas de un articulo",
    # Facturacion / pago
    "Hay un cobro que no reconozco en mi pedido",
    "Necesito la factura de mi ultima compra",
    # Queja / mala experiencia
    "Estoy muy descontento con el servicio, quiero poner una queja",
    "La atencion que recibi en la tienda fue pesima",
    # Cancelacion de pedido
    "Quiero cancelar un pedido que acabo de hacer",
]

PERSONALIDADES = [
    "amable y paciente, colabora con el asistente",
    "impaciente y directo, quiere soluciones rapidas",
    "frustrado por la espera, algo molesto pero razonable",
    "muy exigente, espera un trato excelente",
    "confundido, no recuerda bien los detalles y necesita ayuda",
    "enfadado por una mala experiencia previa",
    "pragmatico, va al grano y valora la eficiencia",
]


# ── Carga de pedido aleatorio desde la BD ─────────────────────────────────────

def get_pedido_aleatorio() -> dict:
    """Devuelve un pedido al azar de la BD junto con datos del cliente."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.numero_pedido, p.estado, c.nombre, c.nivel_fidelidad
            FROM pedidos p
            JOIN clientes c ON c.id = p.cliente_id
            ORDER BY RAND()
            LIMIT 1
        """)
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    if not row:
        raise RuntimeError("No hay pedidos en la base de datos.")
    return {
        "numero_pedido":   row[0],
        "estado":          row[1],
        "cliente":         row[2],
        "nivel_fidelidad": row[3],
    }


def get_all_pedidos() -> list[dict]:
    """Devuelve todos los pedidos disponibles para el selector del autopilot."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.numero_pedido, p.estado, p.fecha_pedido, p.total,
                   c.nombre, c.nivel_fidelidad, c.ciudad
            FROM pedidos p
            JOIN clientes c ON c.id = p.cliente_id
            ORDER BY p.numero_pedido
        """)
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    return [
        {
            "numero_pedido":   r[0],
            "estado":          r[1],
            "fecha_pedido":    r[2].strftime("%d/%m/%Y") if r[2] else "",
            "total":           float(r[3]) if r[3] is not None else 0.0,
            "cliente":         r[4],
            "nivel_fidelidad": r[5],
            "ciudad":          r[6],
        }
        for r in rows
    ]


# ── Generador de caso aleatorio ───────────────────────────────────────────────

def generar_caso_aleatorio(numero_pedido: str = None) -> dict:
    """Genera un caso de test aleatorio. Si no se da pedido, elige uno de la BD."""
    if numero_pedido:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT p.numero_pedido, p.estado, c.nombre, c.nivel_fidelidad
                FROM pedidos p
                JOIN clientes c ON c.id = p.cliente_id
                WHERE p.numero_pedido = %s
            """, (numero_pedido.upper().strip(),))
            row = cur.fetchone()
            cur.close()
        finally:
            conn.close()
        if not row:
            raise ValueError(f"No se encontro el pedido '{numero_pedido}'")
        pedido = {
            "numero_pedido":   row[0],
            "estado":          row[1],
            "cliente":         row[2],
            "nivel_fidelidad": row[3],
        }
    else:
        pedido = get_pedido_aleatorio()

    return {
        "numero_pedido":   pedido["numero_pedido"],
        "estado":          pedido["estado"],
        "cliente":         pedido["cliente"],
        "nivel_fidelidad": pedido["nivel_fidelidad"],
        "motivo":          random.choice(MOTIVOS),
        "personalidad":    random.choice(PERSONALIDADES),
    }


# ── LLM Cliente (simula al cliente final que contacta al servicio) ────────────

def _load_global_ontologia(nombre: str, fallback: str) -> str:
    """Carga una ontologia global (perfil_id IS NULL) desde la BD.
    Si no existe, devuelve el fallback hardcoded — asi el modulo sigue
    funcionando aunque setup_db.py no haya seedeado las globales."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT contenido FROM ontologias
            WHERE nombre = %s AND activo = TRUE AND perfil_id IS NULL
            ORDER BY version DESC LIMIT 1
        """, (nombre,))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    return row[0] if row else fallback


SYSTEM_CLIENTE_FALLBACK = """Eres un cliente de una tienda (retail) que contacta con el servicio de atencion al cliente a traves del chat. Estas hablando DIRECTAMENTE con el asistente de la tienda (no hay ejecutivo intermediario).

Datos de tu caso:
- Tu nombre: {cliente}
- Tu nivel de fidelidad: {nivel_fidelidad}
- Tu pedido: {numero_pedido} (estado actual: {estado})
- Motivo de tu consulta: {motivo}
- Tu personalidad: {personalidad}

Instrucciones:
- Responde SIEMPRE en 1-3 frases cortas, como escribiria un cliente real en un chat.
- Si es tu primer mensaje (primer_turno=True), plantea tu problema de forma natural y directa, como lo harias al abrir un chat de soporte. Puedes mencionar el numero de pedido si es relevante al motivo.
- Si el asistente te pide el numero de pedido, tu email o datos, proporcionalos segun tu caso.
- Eres un cliente REALISTA: si el asistente te da una respuesta clara, resuelve tu duda o te propone una solucion concreta, muestrate satisfecho y cierra la conversacion con [DECISION: RESUELTO].
- Si el asistente no entiende tu problema, da respuestas genericas, te ignora o no resuelve nada tras varios intentos, muestrate insatisfecho y cierra con [DECISION: NO_RESUELTO].
- No decidas antes del turno 3. Dale al asistente la oportunidad de ayudarte.
- Si el asistente se despide o cierra la conversacion, decide INMEDIATAMENTE con [DECISION: RESUELTO] o [DECISION: NO_RESUELTO] segun si te ayudo o no.
- NUNCA respondas como asistente, agente ni ejecutivo. Solo eres el cliente escribiendo en el chat."""

def _generar_mensaje_cliente(historial: list, caso: dict, primer_turno: bool = False) -> str:
    """Genera la respuesta del cliente simulado dado el historial.

    Si primer_turno=True, genera el mensaje inicial con el que el cliente abre
    el chat planteando su consulta.
    """
    system = _load_global_ontologia("autopilot-cliente", SYSTEM_CLIENTE_FALLBACK).format(
        cliente=caso.get("cliente", "Cliente"),
        nivel_fidelidad=caso.get("nivel_fidelidad", "estandar"),
        numero_pedido=caso.get("numero_pedido", ""),
        estado=caso.get("estado", ""),
        motivo=caso.get("motivo", ""),
        personalidad=caso.get("personalidad", ""),
    )

    if primer_turno:
        user_prompt = (
            "Acabas de abrir el chat de atencion al cliente. Escribe tu PRIMER mensaje "
            "planteando tu consulta de forma natural y directa. Maximo 2 frases."
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]
        max_tok = 80
    else:
        messages = [{"role": "system", "content": system}] + historial[-6:]
        max_tok = 80

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        max_tokens=max_tok,
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()


# ── Evaluador ─────────────────────────────────────────────────────────────────

SYSTEM_EVALUADOR_FALLBACK = """Eres un experto en calidad de atencion al cliente para comercio retail.
Evaluaras una conversacion entre un asistente de IA de atencion al cliente y un cliente final.

Debes evaluar en 3 dimensiones y dar recomendaciones concretas de mejora para cada nivel de la ontologia:

1. **system-prompt**: instrucciones generales del agente (tono, estructura, empatia, personalizacion)
2. **ontologia-procedimientos**: pasos y procesos de atencion segun el tipo de consulta (estado de pedido, devoluciones, retrasos, cancelaciones, producto defectuoso, quejas)
3. **ontologia-faq**: preguntas frecuentes sobre politicas y funcionamiento de la tienda (envios, devoluciones, pagos, fidelidad)

Para cada dimension:
- Score del 1 al 10
- Lista de problemas detectados (puede estar vacia)
- Recomendacion concreta de texto a agregar/modificar en esa ontologia

RESTRICCION CRITICA — todas las recomendaciones deben basarse EXCLUSIVAMENTE en:
- Mejorar la claridad, empatia y personalizacion del discurso
- Uso mas efectivo de los datos disponibles (pedido, cliente, nivel de fidelidad, historial)
- Mejorar los procedimientos y pasos a seguir en cada tipo de gestion
- Completar o precisar informacion de FAQ sobre politicas existentes

NUNCA recomiendes ni insinues:
- Descuentos, bonificaciones, promociones o regalos
- Cambios en la politica de precios, envios o devoluciones
- Compensaciones economicas o cupones
- Cualquier accion que implique coste o aprobacion comercial

Ademas:
- Score global ponderado
- Resultado: "resuelto", "no_resuelto" o "indeciso"
- Analisis narrativo breve (3-4 oraciones)

Responde SOLO con JSON valido, sin markdown, con esta estructura exacta:
{
  "score_global": 7.5,
  "resultado": "resuelto",
  "analisis": "Texto narrativo...",
  "niveles": {
    "system_prompt": {
      "score": 8,
      "problemas": ["problema 1"],
      "recomendacion": "Agregar al system prompt: ..."
    },
    "ontologia_procedimientos": {
      "score": 6,
      "problemas": ["problema 1"],
      "recomendacion": "En la seccion de devoluciones, agregar: ..."
    },
    "ontologia_faq": {
      "score": 9,
      "problemas": [],
      "recomendacion": null
    }
  }
}"""

def evaluar_conversacion(transcripcion: list[dict], caso: dict, decision: str) -> dict:
    """Evalua la conversacion y retorna un dict con scores y recomendaciones."""
    transcript_text = "\n".join(
        f"[{t['role'].upper()}]: {t['content']}"
        for t in transcripcion
    )

    prompt = f"""CASO:
- Pedido: {caso.get('numero_pedido', 'N/D')} | Estado: {caso.get('estado', 'N/D')}
- Cliente: {caso.get('cliente', 'N/D')} | Nivel de fidelidad: {caso.get('nivel_fidelidad', 'N/D')}
- Motivo de la consulta: {caso.get('motivo', 'N/D')}
- Personalidad: {caso.get('personalidad', 'N/D')}
- Decision final: {decision}

TRANSCRIPCION:
{transcript_text}

Evalua esta conversacion de atencion al cliente."""

    def _llamar_evaluador(model, use_max_completion_tokens=False):
        kwargs = dict(
            model=model,
            messages=[
                {"role": "system", "content": _load_global_ontologia("autopilot-evaluador", SYSTEM_EVALUADOR_FALLBACK)},
                {"role": "user", "content": prompt},
            ],
        )
        if use_max_completion_tokens:
            kwargs["max_completion_tokens"] = 1200
        else:
            kwargs["max_tokens"] = 1200
            kwargs["temperature"] = 0.2
        return client.chat.completions.create(**kwargs)

    # Intentar con gpt-5.4, fallback a gpt-4o si falla o devuelve vacio
    raw = ""
    for model, use_mct in [("gpt-5.4", True), ("gpt-4o", False)]:
        try:
            response = _llamar_evaluador(model, use_mct)
            raw = (response.choices[0].message.content or "").strip()
            print(f"[evaluador] model={model} raw_len={len(raw)} raw_preview={raw[:120]!r}")
            if raw:
                break
        except Exception as e:
            print(f"[evaluador] model={model} error: {e}")

    if not raw:
        return {"error": "No se pudo obtener evaluacion", "raw": ""}

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        import re
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            return json.loads(m.group())
        return {"error": "No se pudo parsear la evaluacion", "raw": raw}
