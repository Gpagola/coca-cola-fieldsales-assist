"""
Autopilot — Simulador de vendedor + Evaluador de conversaciones (DISTRIBUIDORA PAMPA FIELD SALES)
Corre conversaciones automaticas contra el agente de venta en terreno para hacer pruebas.
El "cliente" simulado aqui representa al VENDEDOR que conversa DIRECTAMENTE con el asistente
(no hay ejecutivo intermediario) — es quien realmente usa el chat para preparar sus visitas.
"""

import json
import random
from openai import OpenAI
from chatbot import get_conn

client = OpenAI()

# ── Datos de referencia ───────────────────────────────────────────────────────

MOTIVOS = [
    # Preparacion de visita / pitch
    "Necesito el historico de pedidos de esta cuenta para preparar la visita",
    "Quiero confirmar el canal y el tamano de esta cuenta antes de negociar",
    # Descuentos / condiciones comerciales
    "Quiero saber el descuento aplicable para un pedido de gran volumen",
    "El cliente quiere renegociar el descuento por volumen",
    "El cliente pide un descuento mayor al que indica la politica",
    "Necesito preparar el pitch para renovar el contrato anual de esta cuenta",
    # Condiciones de pago
    "El cliente quiere cambiar la condicion de pago a credito",
    "El pedido quedo rechazado por backoffice por un tema de credito",
    # Catalogo
    "El cliente pregunta por un SKU nuevo que no se si tenemos",
    "El cliente de un bar pregunta por condiciones de comodato de heladera",
    # Toma de pedido
    "Quiero registrar el pedido que acaba de confirmar el cliente",
    "Necesito cancelar un pedido que tome por error",
    # Posventa
    "El cliente reporta un faltante de bultos en la ultima entrega",
    "Hay un error en la factura del ultimo pedido",
    "El cliente reporta demora en la entrega pactada",
]

PERSONALIDADES = [
    "amable y paciente, colabora con el asistente",
    "impaciente y directo, quiere numeros concretos rapido",
    "frustrado porque el cliente lo presiono, algo apurado pero razonable",
    "muy exigente, espera que el asistente resuelva todo de una vez",
    "confundido, no recuerda bien el codigo de cuenta y necesita ayuda",
    "molesto porque un pedido anterior tuvo problemas",
    "pragmatico, va al grano y valora la eficiencia",
]


# ── Carga de pedido aleatorio desde la BD ─────────────────────────────────────

def get_pedido_aleatorio() -> dict:
    """Devuelve un pedido al azar de la BD junto con datos de la cuenta asociada."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.numero_pedido, p.estado, e.nombre_comercial, e.canal, e.codigo_cliente
            FROM pedidos p
            JOIN empresas_clientes e ON e.id = p.empresa_cliente_id
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
        "nivel_fidelidad": row[3],  # reutilizado para transportar el canal de la cuenta
        "codigo_cliente":  row[4],
    }


def get_all_pedidos() -> list[dict]:
    """Devuelve todos los pedidos disponibles para el selector del autopilot."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.numero_pedido, p.estado, p.fecha_pedido, p.total,
                   e.nombre_comercial, e.canal, e.ciudad
            FROM pedidos p
            JOIN empresas_clientes e ON e.id = p.empresa_cliente_id
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
            "nivel_fidelidad": r[5],  # canal de la cuenta
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
                SELECT p.numero_pedido, p.estado, e.nombre_comercial, e.canal, e.codigo_cliente
                FROM pedidos p
                JOIN empresas_clientes e ON e.id = p.empresa_cliente_id
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
            "codigo_cliente":  row[4],
        }
    else:
        pedido = get_pedido_aleatorio()

    return {
        "numero_pedido":   pedido["numero_pedido"],
        "estado":          pedido["estado"],
        "cliente":         pedido["cliente"],
        "nivel_fidelidad": pedido["nivel_fidelidad"],
        "codigo_cliente":  pedido["codigo_cliente"],
        "motivo":          random.choice(MOTIVOS),
        "personalidad":    random.choice(PERSONALIDADES),
    }


# ── LLM "Cliente" (en este dominio: simula al VENDEDOR que usa el chat) ───────

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


SYSTEM_CLIENTE_FALLBACK = """Eres un vendedor de fuerza de venta en terreno de Distribuidora Pampa que esta usando el
asistente para preparar una visita o resolver algo de una cuenta. Hablas
DIRECTAMENTE con el asistente (no hay otro intermediario).

Datos de tu caso:
- Cuenta que estas visitando: {cliente} (codigo {codigo_cliente})
- Canal de la cuenta: {nivel_fidelidad}
- Numero de tu ultimo pedido relacionado: {numero_pedido} (estado: {estado})
- Motivo de tu consulta: {motivo}
- Tu personalidad: {personalidad}

Instrucciones:
- Responde SIEMPRE en 1-3 frases cortas, como escribiria un vendedor real en un chat.
- Si es tu primer mensaje (primer_turno=True), plantea tu necesidad de forma natural
  y directa, como lo harias al abrir el chat antes de una visita. Podes mencionar el
  codigo de cuenta si es relevante al motivo.
- Si el asistente te pide el codigo de cliente u otro dato, proporcionalo segun tu caso.
- Sos un vendedor REALISTA: si el asistente te da una respuesta clara (politica de
  descuento, historico, o registra el pedido), mostrate conforme y cierra la
  conversacion con [DECISION: RESUELTO].
- Si el asistente no te da un numero concreto, inventa un descuento, o no resuelve
  nada tras varios intentos, mostrate insatisfecho y cierra con [DECISION: NO_RESUELTO].
- No decidas antes del turno 3. Dale al asistente la oportunidad de ayudarte.
- Si el asistente se despide o cierra la conversacion, decide INMEDIATAMENTE con
  [DECISION: RESUELTO] o [DECISION: NO_RESUELTO] segun si te ayudo o no.
- NUNCA respondas como asistente. Solo sos el vendedor escribiendo en el chat."""


def _generar_mensaje_cliente(historial: list, caso: dict, primer_turno: bool = False) -> str:
    """Genera la respuesta del vendedor simulado dado el historial.

    Si primer_turno=True, genera el mensaje inicial con el que el vendedor abre
    el chat planteando su consulta.
    """
    system = _load_global_ontologia("autopilot-cliente", SYSTEM_CLIENTE_FALLBACK).format(
        cliente=caso.get("cliente", "Cuenta"),
        codigo_cliente=caso.get("codigo_cliente", ""),
        nivel_fidelidad=caso.get("nivel_fidelidad", "estandar"),
        numero_pedido=caso.get("numero_pedido", ""),
        estado=caso.get("estado", ""),
        motivo=caso.get("motivo", ""),
        personalidad=caso.get("personalidad", ""),
    )

    if primer_turno:
        user_prompt = (
            "Acabas de abrir el chat con el asistente. Escribe tu PRIMER mensaje "
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

SYSTEM_EVALUADOR_FALLBACK = """Eres un experto en calidad de procesos comerciales para fuerza de venta de
Distribuidora Pampa. Evaluaras una conversacion entre un asistente de IA de venta en
terreno y un vendedor.

Debes evaluar en 4 dimensiones y dar recomendaciones concretas de mejora para
cada nivel de la ontologia:

1. **system-prompt**: instrucciones generales del agente (claridad, foco en datos,
   disciplina de confirmacion antes de tomar pedidos)
2. **ontologia-procedimientos**: reglas por canal (tradicional, moderno, on premise,
   off premise, mayoristas, ecommerce, institucional/horeca, vending, directo/indirecto)
3. **ontologia-descuentos**: uso correcto y disciplinado de la tool de consulta de
   politica de descuento (nunca inventar un %)
4. **ontologia-faq**: preguntas frecuentes de un vendedor en terreno

Para cada dimension:
- Score del 1 al 10
- Lista de problemas detectados (puede estar vacia)
- Recomendacion concreta de texto a agregar/modificar en esa ontologia

RESTRICCION CRITICA — todas las recomendaciones deben basarse EXCLUSIVAMENTE en:
- Mejorar la claridad y disciplina de uso de las tools (especialmente la de
  politica de descuento)
- Uso mas efectivo de los datos disponibles (cuenta, canal, historico de pedidos)
- Mejorar los procedimientos y reglas de negociacion por canal
- Completar o precisar informacion de FAQ sobre politicas existentes

NUNCA recomiendes ni insinues:
- Otorgar descuentos, bonificaciones o condiciones fuera de politica
- Cambios en la politica de precios o condiciones comerciales
- Compensaciones economicas
- Cualquier accion que implique coste o aprobacion comercial sin escalar

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
      "recomendacion": "En la seccion de canal moderno, agregar: ..."
    },
    "ontologia_descuentos": {
      "score": 7,
      "problemas": [],
      "recomendacion": null
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
- Cuenta: {caso.get('cliente', 'N/D')} | Canal: {caso.get('nivel_fidelidad', 'N/D')}
- Motivo de la consulta: {caso.get('motivo', 'N/D')}
- Personalidad: {caso.get('personalidad', 'N/D')}
- Decision final: {decision}

TRANSCRIPCION:
{transcript_text}

Evalua esta conversacion de un vendedor con el asistente de venta en terreno."""

    def _llamar_evaluador(model, use_max_completion_tokens=False):
        kwargs = dict(
            model=model,
            messages=[
                {"role": "system", "content": _load_global_ontologia("autopilot-evaluador", SYSTEM_EVALUADOR_FALLBACK)},
                {"role": "user", "content": prompt},
            ],
        )
        if use_max_completion_tokens:
            # Modelo de razonamiento: max_completion_tokens incluye tokens de razonamiento
            # ocultos ademas del JSON visible, por eso el presupuesto es mucho mayor que
            # para un modelo no-razonador (evita respuestas truncadas/JSON invalido).
            kwargs["max_completion_tokens"] = 4000
        else:
            kwargs["max_tokens"] = 1200
            kwargs["temperature"] = 0.2
        return client.chat.completions.create(**kwargs)

    def _parsear(raw: str):
        """Intenta parsear el JSON devuelto. Devuelve el dict o None si no es valido."""
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            import re
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group())
                except json.JSONDecodeError:
                    return None
            return None

    # Intentar con gpt-5.4, fallback a gpt-4o. Solo se acepta un intento si su JSON
    # realmente parsea — un modelo con salida truncada/invalida no debe bloquear el
    # fallback al siguiente (bug previo: se aceptaba el primer `raw` no vacio aunque
    # el JSON estuviera roto, y nunca se probaba gpt-4o).
    last_raw = ""
    for model, use_mct in [("gpt-5.4", True), ("gpt-4o", False)]:
        try:
            response = _llamar_evaluador(model, use_mct)
            raw = (response.choices[0].message.content or "").strip()
            print(f"[evaluador] model={model} raw_len={len(raw)} raw_preview={raw[:120]!r}")
            if not raw:
                continue
            last_raw = raw
            parsed = _parsear(raw)
            if parsed is not None:
                return parsed
        except Exception as e:
            print(f"[evaluador] model={model} error: {e}")

    if not last_raw:
        return {"error": "No se pudo obtener evaluacion", "raw": ""}
    return {"error": "No se pudo parsear la evaluacion", "raw": last_raw}
