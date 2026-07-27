"""
Backend Flask — Asistente de Fuerza de Venta en Terreno (Distribuidora Pampa)
Expone el agente LangGraph como API REST para el frontend React.
"""

import io
import os
import re
import uuid
import base64
import json
import unicodedata
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from dotenv import load_dotenv

import pypdf
import mysql.connector
from openai import OpenAI
from langchain_core.messages import HumanMessage

from chatbot import build_agent, get_conn, preload_ontologies, invalidate_ontology_cache, get_active_perfil_id

# Ontologias que pertenecen a un perfil (vs globales como autopilot-*)
PROFILE_ONTOLOGIES = ("system-prompt", "ontologia-procedimientos", "ontologia-descuentos", "ontologia-faq")
from autopilot import (
    generar_caso_aleatorio, evaluar_conversacion, get_all_pedidos,
    MOTIVOS, PERSONALIDADES, _generar_mensaje_cliente
)

load_dotenv()

app = Flask(__name__)
CORS(app)  # permite peticiones desde React (localhost:5173)

# ── Estado global del agente ──────────────────────────────────────────────────

from langgraph.checkpoint.memory import MemorySaver as _MemorySaver

_checkpointer = None
_agent = None

def get_agent():
    global _checkpointer, _agent
    if _agent is None:
        _checkpointer = _MemorySaver()
        _agent = build_agent(_checkpointer)
        preload_ontologies()
    return _agent


# ── Analisis de riesgo / satisfaccion ────────────────────────────────────────

def _analyze_risk_profile(conversation_text: str) -> dict | None:
    """Analiza la conversacion y devuelve scores de tipo de consulta, resolucion y sentimiento."""
    try:
        client = OpenAI()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    "Analiza esta conversacion de un vendedor de fuerza de venta en terreno con "
                    "su asistente y devuelve:\n"
                    "1. Tipo de consulta por dimension (0-100, 0=no detectado, 100=muy presente):\n"
                    "   - pedido: toma o seguimiento de un pedido\n"
                    "   - logistica: entrega, demora, distribucion directa/indirecta\n"
                    "   - posventa: faltante, defecto, error de facturacion\n"
                    "   - catalogo: consulta de producto, SKU, formato, precio de lista\n"
                    "   - pago: condicion de pago, credito, contado\n"
                    "   - escalamiento: excepcion de politica, pedido de descuento fuera de rango\n"
                    "2. resolucion: probabilidad de resolver satisfactoriamente la consulta (0-100)\n"
                    "3. sentimiento: estado de animo actual del vendedor (-100 muy negativo a +100 muy positivo)\n\n"
                    "Responde SOLO JSON: {\"pedido\":N,\"logistica\":N,\"posventa\":N,\"catalogo\":N,\"pago\":N,\"escalamiento\":N,\"resolucion\":N,\"sentimiento\":N}"
                )},
                {"role": "user", "content": conversation_text[-2000:]},
            ],
            max_tokens=120,
            temperature=0.1,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r'^```[a-z]*\n?', '', raw).rstrip('`').strip()
        result = json.loads(raw)
        for key in ["pedido", "logistica", "posventa", "catalogo", "pago", "escalamiento"]:
            result[key] = max(0, min(100, int(result.get(key, 0))))
        result["resolucion"]  = max(0, min(100, int(result.get("resolucion", 50))))
        result["sentimiento"] = max(-100, min(100, int(result.get("sentimiento", 0))))
        return result
    except Exception as e:
        print(f"[risk_profile] error: {e}")
        return None


# ── Generador de sugerencias rapidas (respuestas del cliente) ────────────────

def _generar_sugerencias_rapidas(user_msg: str, assistant_msg: str) -> list:
    """Genera sugerencias rapidas a partir del ultimo intercambio, sin acceder a la BD."""
    try:
        lines = []
        if user_msg:
            lines.append(f"Cliente: {user_msg[:300]}")
        lines.append(f"Asistente: {assistant_msg[:400]}")

        client = OpenAI()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    "Eres una ayuda en una app de asistente de venta en terreno para Distribuidora Pampa. "
                    "El usuario es el vendedor. Dado el ultimo mensaje del asistente, "
                    "genera 3-4 frases cortas (max 6 palabras) que el vendedor podria "
                    "escribir como respuesta o siguiente pregunta. "
                    "Ejemplos: 'Es CLI-0012', 'Cual es el descuento?', "
                    "'Quiero registrar el pedido', 'El cliente pide credito', 'Gracias'. "
                    "NUNCA generes frases del asistente. Solo respuestas del vendedor. "
                    "Responde SOLO con JSON array de strings."
                )},
                {"role": "user", "content": f"Ultimo mensaje del asistente:\n{lines[-1] if lines else ''}"},
            ],
            max_tokens=60,
            temperature=0.4,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
        result = json.loads(raw)
        if isinstance(result, list):
            return [str(s).strip() for s in result[:4] if s]
        if isinstance(result, dict):
            for v in result.values():
                if isinstance(v, list):
                    return [str(s).strip() for s in v[:4] if s]
    except Exception as e:
        print(f"[Sugerencias] error: {e}")
    return []


# ── Parser de resultado de consultar_cuenta_cliente ──────────────────────────

def _parse_pedido_result(text: str) -> dict | None:
    """Parsea el texto devuelto por consultar_cuenta_cliente y retorna un dict
    estructurado para la barra de contexto del chat (mismo nombre de funcion que
    antes por compatibilidad con el resto del streaming, aunque ahora describe
    una cuenta B2B en vez de un pedido de retail)."""
    if "Cuenta encontrada" not in text:
        return None
    def field(pattern):
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(1).strip() if m else None
    return {
        "numero":          field(r"Codigo[:\s]+([^\n]+)"),
        "cliente":         field(r"Nombre comercial[:\s]+([^\n]+)"),
        "estado":          field(r"Canal[:\s]+([^\n]+)"),
        "nivel_fidelidad": field(r"Tamano de canal[:\s]+([^\n]+)"),
        "metodo_pago":     field(r"Condicion de pago habitual[:\s]+([^\n]+)"),
        "tracking":        field(r"Tipo de distribucion[:\s]+([^\n]+)"),
        "ciudad":          field(r"Ciudad / zona[:\s]+([^\n]+)"),
    }


# ── Endpoints de chat ─────────────────────────────────────────────────────────

@app.route("/api/session/new", methods=["POST"])
def new_session():
    """Genera un nuevo session_id unico."""
    session_id = str(uuid.uuid4())
    return jsonify({"session_id": session_id})


@app.route("/api/chat", methods=["POST"])
def chat():
    """
    Envia un mensaje al agente y devuelve la respuesta en streaming (SSE).
    Body: { "message": "...", "session_id": "..." }
    """
    data = request.get_json()
    message    = data.get("message", "").strip()
    session_id = data.get("session_id", "")
    voice_mode = bool(data.get("voice_mode"))

    if not message or not session_id:
        return jsonify({"error": "message y session_id son requeridos"}), 400

    # En modo voz la respuesta la ESCUCHA el vendedor: tiene que ser corta y
    # directa, sin listas ni markdown. Se lo indicamos al agente en este turno.
    if voice_mode:
        message = (
            message
            + "\n\n[MODO VOZ — la respuesta se va a ESCUCHAR en audio, no leer. "
            "Responde en 1 o 2 oraciones, breve y directo, en tono conversacional. "
            "Prohibido usar listas, vinetas, numeraciones, tablas o markdown. "
            "Da solo lo esencial (el dato o el siguiente paso); si hay mucho que "
            "detallar, resumi y ofrece ampliar si el vendedor lo pide.]"
        )

    agent  = get_agent()
    config = {"configurable": {"thread_id": session_id}}

    TOOL_STATUS = {
        "consultar_cuenta_cliente":      "Consultando cuenta...",
        "consultar_historico_pedidos":   "Consultando historico de pedidos...",
        "buscar_producto":               "Consultando catalogo...",
        "consultar_politica_descuento":  "Consultando politica de descuento...",
        "ontologia_procedimientos":      "Consultando procedimientos...",
        "ontologia_descuentos":          "Consultando ontologia de descuentos...",
        "ontologia_faq":                 "Buscando en FAQ...",
        "analizar_documento":            "Analizando documento adjunto...",
        "consultar_gestiones_posventa":  "Consultando gestiones de posventa...",
        "crear_pedido":                  "Registrando pedido...",
        "cambiar_condicion_pago_pedido": "Actualizando condicion de pago...",
        "cancelar_pedido":               "Cancelando pedido...",
        "agregar_nota_pedido":           "Anotando en el pedido...",
        "abrir_gestion_posventa":        "Abriendo gestion de posventa...",
    }

    def generate():
        try:
            # Risk profile al recibir el mensaje del cliente (antes de que responda el agente)
            try:
                prev_state = get_agent().get_state(config)
                prev_msgs = prev_state.values.get("messages", [])
                conv_lines = [
                    f"{'Cliente' if m.type == 'human' else 'Asistente'}: {m.content[:300]}"
                    for m in prev_msgs
                    if hasattr(m, "content") and isinstance(m.content, str) and m.content.strip()
                ]
                conv_lines.append(f"Cliente: {message[:300]}")
                if len(conv_lines) > 2:
                    risk = _analyze_risk_profile("\n".join(conv_lines))
                    if risk:
                        yield f"data: {json.dumps({'risk_profile': risk})}\n\n"
            except Exception as ex:
                print(f"[risk_profile chat] {ex}")

            current_node = None
            agent_response = ""

            for chunk, metadata in agent.stream(
                {"messages": [HumanMessage(content=message)]},
                config=config,
                stream_mode="messages",
            ):
                node = metadata.get("langgraph_node")

                if node != current_node:
                    current_node = node
                    if node == "agent":
                        yield f"data: {json.dumps({'status': 'Pensando...'})}\n\n"

                if node == "agent" and hasattr(chunk, "tool_calls") and chunk.tool_calls:
                    for tc in chunk.tool_calls:
                        name = tc.get("name", "")
                        if name in TOOL_STATUS:
                            yield f"data: {json.dumps({'status': TOOL_STATUS[name]})}\n\n"

                if node == "tools":
                    content = chunk.content if hasattr(chunk, "content") else ""
                    if isinstance(content, str) and "Pedido encontrado" in content:
                        pedido_data = _parse_pedido_result(content)
                        if pedido_data:
                            yield f"data: {json.dumps({'pedido': pedido_data})}\n\n"

                if node == "agent" and isinstance(chunk.content, str) and chunk.content:
                    agent_response += chunk.content
                    yield f"data: {json.dumps({'token': chunk.content})}\n\n"

            # Generar sugerencias al final con la respuesta completa — prompt minimo
            if agent_response.strip():
                try:
                    oai = OpenAI()
                    r = oai.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[
                            {"role": "system", "content": (
                                "Atencion al cliente retail. El usuario es el cliente. "
                                "Dado el mensaje del asistente, genera 3-4 frases cortas (max 6 palabras) "
                                "que el cliente podria responder o preguntar. Solo JSON array, sin markdown."
                            )},
                            {"role": "user", "content": agent_response[-600:]},
                        ],
                        max_tokens=60,
                        temperature=0.4,
                    )
                    raw = r.choices[0].message.content.strip()
                    raw = re.sub(r'^```[a-z]*\n?', '', raw).rstrip('`').strip()
                    print(f"[suggestions] {raw}")
                    sugerencias = json.loads(raw)
                    if isinstance(sugerencias, list) and sugerencias:
                        yield f"data: {json.dumps({'suggestions': sugerencias})}\n\n"
                except Exception as ex:
                    print(f"[suggestions error] {ex}")

                # Detectar cierre de conversacion
                CIERRE_KEYWORDS = ["buen dia", "buenas noches", "hasta luego", "que tenga",
                                   "un placer ayudarte", "no dude en contactar", "fue un placer",
                                   "que tengas un buen dia", "gracias por contactar"]
                if any(k in agent_response.lower() for k in CIERRE_KEYWORDS):
                    yield f"data: {json.dumps({'cierre': True})}\n\n"

            yield "data: [DONE]\n\n"
        except Exception as e:
            print(f"ERROR en /api/chat: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Endpoint de analisis de documentos ───────────────────────────────────────

@app.route("/api/suggestions", methods=["POST"])
def suggestions():
    """Genera sugerencias basadas en el ultimo intercambio (sin consultar la BD)."""
    data = request.get_json()
    user_msg      = data.get("user_msg", "").strip()
    assistant_msg = data.get("assistant_msg", "").strip()
    if not assistant_msg:
        return jsonify([])
    return jsonify(_generar_sugerencias_rapidas(user_msg, assistant_msg))


@app.route("/api/upload", methods=["POST"])
def upload_document():
    """
    Recibe un PDF o imagen, extrae su contenido y lo analiza con GPT-4o Vision.
    Devuelve el texto interpretado listo para pasarle al agente.
    """
    if "file" not in request.files:
        return jsonify({"error": "No se recibio ningun archivo"}), 400

    file     = request.files["file"]
    filename = file.filename.lower()
    client   = OpenAI()

    try:
        if filename.endswith(".pdf"):
            raw = file.read()
            reader = pypdf.PdfReader(io.BytesIO(raw))
            texto  = "\n".join(
                page.extract_text() or "" for page in reader.pages
            ).strip()

            if len(texto) > 100:
                analisis = _interpretar_con_vision(client, texto_plano=texto)
            else:
                b64 = base64.b64encode(raw).decode()
                analisis = _interpretar_con_vision(client, b64_pdf=b64)

            ext = "pdf"
            file_type = "pdf"

        elif filename.endswith((".jpg", ".jpeg", ".png", ".webp")):
            raw = file.read()
            b64 = base64.b64encode(raw).decode()
            ext = filename.rsplit(".", 1)[-1].replace("jpg", "jpeg")
            analisis = _interpretar_con_vision(client, b64_imagen=b64, mime=f"image/{ext}")
            file_type = "image"

        else:
            return jsonify({"error": "Formato no soportado. Usa PDF, JPG o PNG."}), 400

        # Persistir el archivo para que el frontend pueda mostrarlo y abrirlo
        safe_id = uuid.uuid4().hex
        stored_name = f"{safe_id}.{ext}"
        with open(os.path.join(ATTACHMENTS_DIR, stored_name), "wb") as f:
            f.write(raw)

        return jsonify({
            "contenido": analisis,
            "file_url":  f"/api/attachments/{stored_name}",
            "file_name": file.filename,
            "file_type": file_type,
        })

    except Exception as e:
        print(f"ERROR en /api/upload: {e}")
        return jsonify({"error": str(e)}), 500


# Frases que Whisper "alucina" cuando el audio esta en silencio o sin habla
# clara (provienen de sus datos de entrenamiento: subtitulos de YouTube). Si la
# transcripcion se reduce a una de estas, la descartamos para no mandar un
# mensaje fantasma al agente.
_WHISPER_ALUCINACIONES = {
    "subtitulos realizados por la comunidad de amara.org",
    "subtitulado por la comunidad de amara.org",
    "subtitulos por la comunidad de amara.org",
    "mas informacion en www.alimmenta.com",
    "gracias por ver el video",
    "gracias por ver el video.",
    "gracias.",
    "gracias",
    "amara.org",
    "subscribe to our channel",
    "thanks for watching",
    "thank you.",
    "you",
}


def _es_transcripcion_valida(texto):
    """False si la transcripcion esta vacia o es una alucinacion tipica de silencio."""
    limpio = texto.strip()
    if not limpio:
        return False
    # Normalizar: minusculas + sin tildes para comparar contra la lista.
    normal = unicodedata.normalize("NFKD", limpio.lower())
    normal = "".join(c for c in normal if not unicodedata.combining(c)).strip()
    if "amara.org" in normal:
        return False
    return normal not in _WHISPER_ALUCINACIONES


@app.route("/api/transcribe", methods=["POST"])
def transcribe_audio():
    """
    Recibe una grabacion de audio (modo de voz del chat movil) y la transcribe
    a texto con Whisper para pasarla al agente como si el vendedor la hubiera escrito.
    """
    if "audio" not in request.files:
        return jsonify({"error": "No se recibio ningun audio"}), 400

    audio_file = request.files["audio"]
    client = OpenAI()

    try:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=(audio_file.filename or "recording.webm", audio_file.read(), audio_file.mimetype),
            language="es",
        )
        texto = transcript.text if _es_transcripcion_valida(transcript.text) else ""
        return jsonify({"text": texto})
    except Exception as e:
        print(f"ERROR en /api/transcribe: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/speak", methods=["POST"])
def speak_text():
    """
    Convierte una oracion de la respuesta del asistente en audio con la voz
    de OpenAI (mucho mas natural que la voz nativa del navegador) para el
    modo de voz del chat movil. Body: { "text": "..." }
    """
    data = request.get_json()
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text es requerido"}), 400

    client = OpenAI()

    try:
        response = client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice="nova",
            input=text,
            speed=1.2,
            instructions="Hablá en español rioplatense de Argentina, con acento porteño "
                         "natural (voseo, entonacion argentina). Tono formal y profesional, "
                         "como un asesor comercial serio y confiable; claro y bien articulado, "
                         "cordial pero sin excesos de calidez ni informalidad. Ritmo agil.",
        )
        return Response(response.read(), mimetype="audio/mpeg")
    except Exception as e:
        print(f"ERROR en /api/speak: {e}")
        return jsonify({"error": str(e)}), 500


def _interpretar_con_vision(client, texto_plano=None, b64_imagen=None, b64_pdf=None, mime="image/jpeg"):
    """Llama a GPT-4o para interpretar el documento y clasificarlo."""
    instruccion = """Eres un asistente de fuerza de venta en terreno de Distribuidora Pampa.
Analiza el documento adjunto e identifica:
1. TIPO DE DOCUMENTO: es una foto de gondola/exhibicion, una orden de compra escaneada, un comprobante de pago, u otro?
2. DATOS CLAVE segun el tipo:
   - Si es orden de compra/comprobante: cuenta o cliente, fecha, productos, cantidades, total
   - Si es foto de gondola/exhibicion: estado aparente (surtido, vacio, con competencia), SKUs visibles si se distinguen
   - Otro: resumen del contenido relevante para la consulta
3. RECOMENDACION: que deberia hacer el asistente con esta informacion

Responde en espanol, de forma estructurada y concisa."""

    if texto_plano:
        messages = [{"role": "user", "content": f"{instruccion}\n\nContenido del documento:\n{texto_plano}"}]
    elif b64_imagen:
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": instruccion},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64_imagen}"}}
            ]
        }]
    else:
        messages = [{"role": "user", "content": f"{instruccion}\n\n(PDF escaneado — analiza segun el contexto disponible)"}]

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        max_tokens=1000,
    )
    return response.choices[0].message.content


# ── Endpoints de Perfiles ─────────────────────────────────────────────────────

LOGOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logos")
os.makedirs(LOGOS_DIR, exist_ok=True)

ATTACHMENTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "attachments")
os.makedirs(ATTACHMENTS_DIR, exist_ok=True)


@app.route("/api/attachments/<path:filename>", methods=["GET"])
def get_attachment(filename):
    """Sirve los archivos adjuntos subidos por el usuario en el chat."""
    from flask import send_from_directory
    return send_from_directory(ATTACHMENTS_DIR, filename)


def _reset_agent():
    """Fuerza la reconstruccion del agente con el system-prompt del perfil activo."""
    global _agent, _checkpointer
    _agent = None
    _checkpointer = None
    invalidate_ontology_cache()


@app.route("/api/perfiles", methods=["GET"])
def listar_perfiles():
    """Lista todos los perfiles. El frontend usa el flag activo para resaltar el actual."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, nombre, empresa, logo_url, activo
            FROM perfiles
            ORDER BY id
        """)
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    return jsonify([
        {"id": r[0], "nombre": r[1], "empresa": r[2], "logo_url": r[3], "activo": bool(r[4])}
        for r in rows
    ])


@app.route("/api/perfiles", methods=["POST"])
def crear_perfil():
    """
    Crea un perfil nuevo y le copia las ontologias del perfil indicado en
    `copy_from_perfil_id` (por defecto, el perfil activo). Si no hay ninguno
    para copiar, las inserta vacias.
    Body: { nombre, empresa, logo_url?, copy_from_perfil_id? }
    """
    data = request.get_json() or {}
    nombre     = (data.get("nombre") or "").strip()
    empresa    = (data.get("empresa") or "").strip()
    logo_url   = (data.get("logo_url") or "").strip() or None
    copy_from  = data.get("copy_from_perfil_id")

    if not nombre or not empresa:
        return jsonify({"error": "nombre y empresa son requeridos"}), 400

    if copy_from is None:
        copy_from = get_active_perfil_id()

    conn = get_conn()
    try:
        cur = conn.cursor()

        # Crear el perfil
        try:
            cur.execute("""
                INSERT INTO perfiles (nombre, empresa, logo_url, activo)
                VALUES (%s, %s, %s, 0)
            """, (nombre, empresa, logo_url))
        except mysql.connector.IntegrityError:
            return jsonify({"error": f"Ya existe un perfil con el nombre '{nombre}'"}), 409

        new_id = cur.lastrowid

        # Copiar ontologias de perfil desde el perfil origen
        for ont_nombre in PROFILE_ONTOLOGIES:
            contenido = ""
            if copy_from:
                cur.execute("""
                    SELECT contenido FROM ontologias
                    WHERE nombre = %s AND activo = TRUE AND perfil_id = %s
                    ORDER BY id DESC LIMIT 1
                """, (ont_nombre, copy_from))
                row = cur.fetchone()
                if row:
                    contenido = row[0]
            cur.execute("""
                INSERT INTO ontologias (nombre, version, contenido, activo, perfil_id)
                VALUES (%s, '1.0', %s, TRUE, %s)
            """, (ont_nombre, contenido, new_id))

        conn.commit()
        cur.close()
    finally:
        conn.close()

    return jsonify({"ok": True, "id": new_id}), 201


@app.route("/api/perfiles/<int:perfil_id>", methods=["PUT"])
def actualizar_perfil(perfil_id):
    """Actualiza metadatos del perfil. Body: { nombre?, empresa?, logo_url? }"""
    data = request.get_json() or {}
    fields = []
    args = []
    for key in ("nombre", "empresa", "logo_url"):
        if key in data:
            val = data[key]
            if isinstance(val, str):
                val = val.strip() or None
            fields.append(f"{key} = %s")
            args.append(val)
    if not fields:
        return jsonify({"error": "Nada para actualizar"}), 400

    args.append(perfil_id)
    conn = get_conn()
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"UPDATE perfiles SET {', '.join(fields)} WHERE id = %s", tuple(args))
        except mysql.connector.IntegrityError:
            return jsonify({"error": "Ya existe un perfil con ese nombre"}), 409
        conn.commit()
        affected = cur.rowcount
        cur.close()
    finally:
        conn.close()

    if affected == 0:
        return jsonify({"error": "Perfil no encontrado"}), 404
    return jsonify({"ok": True})


@app.route("/api/perfiles/<int:perfil_id>", methods=["DELETE"])
def borrar_perfil(perfil_id):
    """
    Elimina el perfil y, en cascada, sus ontologias (FK ON DELETE CASCADE).
    Restricciones:
    - No se puede borrar el perfil activo.
    - Debe quedar al menos un perfil en el sistema.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT activo FROM perfiles WHERE id = %s", (perfil_id,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return jsonify({"error": "Perfil no encontrado"}), 404
        if row[0]:
            cur.close()
            return jsonify({"error": "No se puede borrar el perfil activo. Activa otro primero."}), 400

        cur.execute("SELECT COUNT(*) FROM perfiles")
        if cur.fetchone()[0] <= 1:
            cur.close()
            return jsonify({"error": "Debe existir al menos un perfil"}), 400

        cur.execute("DELETE FROM perfiles WHERE id = %s", (perfil_id,))
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return jsonify({"ok": True})


@app.route("/api/perfiles/<int:perfil_id>/activate", methods=["POST"])
def activar_perfil(perfil_id):
    """Marca el perfil como activo (los demas quedan inactivos) y resetea el agente."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM perfiles WHERE id = %s", (perfil_id,))
        if not cur.fetchone():
            cur.close()
            return jsonify({"error": "Perfil no encontrado"}), 404

        cur.execute("UPDATE perfiles SET activo = 0")
        cur.execute("UPDATE perfiles SET activo = 1 WHERE id = %s", (perfil_id,))
        conn.commit()
        cur.close()
    finally:
        conn.close()

    _reset_agent()
    return jsonify({"ok": True, "id": perfil_id})


@app.route("/api/perfiles/upload-logo", methods=["POST"])
def upload_logo():
    """Recibe un archivo de imagen y lo guarda en disco. Devuelve la URL publica."""
    if "file" not in request.files:
        return jsonify({"error": "No se recibio ningun archivo"}), 400

    file = request.files["file"]
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ("jpg", "jpeg", "png", "webp", "svg"):
        return jsonify({"error": "Formato no soportado (jpg, png, webp, svg)"}), 400

    safe_name = f"logo_{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(LOGOS_DIR, safe_name))
    return jsonify({"logo_url": f"/api/logos/{safe_name}"})


@app.route("/api/logos/<path:filename>", methods=["GET"])
def serve_logo(filename):
    """Sirve los logos guardados en LOGOS_DIR."""
    from flask import send_from_directory
    return send_from_directory(LOGOS_DIR, filename)


# ── Endpoints de Cartera (Portal Vendedor) ───────────────────────────────────

@app.route("/api/cartera/clientes", methods=["GET"])
def cartera_clientes():
    """Lista las cuentas (empresas cliente) de la cartera. Soporta ?q= para filtrar
    por codigo o nombre comercial."""
    q = (request.args.get("q") or "").strip()
    conn = get_conn()
    try:
        cur = conn.cursor()
        if q:
            like = f"%{q}%"
            cur.execute("""
                SELECT codigo_cliente, nombre_comercial, canal, tipo_distribucion, tamano_canal,
                       ciudad, zona, condicion_pago_habitual, vendedor_asignado, activo
                FROM empresas_clientes
                WHERE activo = 1 AND (codigo_cliente LIKE %s OR nombre_comercial LIKE %s)
                ORDER BY nombre_comercial
            """, (like, like))
        else:
            cur.execute("""
                SELECT codigo_cliente, nombre_comercial, canal, tipo_distribucion, tamano_canal,
                       ciudad, zona, condicion_pago_habitual, vendedor_asignado, activo
                FROM empresas_clientes
                WHERE activo = 1
                ORDER BY nombre_comercial
            """)
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    return jsonify([
        {
            "codigo_cliente": r[0], "nombre_comercial": r[1], "canal": r[2],
            "tipo_distribucion": r[3], "tamano_canal": r[4], "ciudad": r[5], "zona": r[6],
            "condicion_pago_habitual": r[7], "vendedor_asignado": r[8], "activo": bool(r[9]),
        }
        for r in rows
    ])


@app.route("/api/cartera/pedidos-en-curso", methods=["GET"])
def cartera_pedidos_en_curso():
    """Lista los pedidos en estados no finales (solicitado, en_revision, aprobado),
    incluyendo el detalle de lineas (producto, cantidad, precio acordado) de cada uno."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.numero_pedido, e.nombre_comercial, e.codigo_cliente, p.canal_venta,
                   p.fecha_pedido, p.estado, p.condicion_pago, p.total, p.vendedor
            FROM pedidos p
            JOIN empresas_clientes e ON e.id = p.empresa_cliente_id
            WHERE p.estado IN ('solicitado', 'en_revision', 'aprobado')
            ORDER BY p.fecha_pedido DESC
        """)
        rows = cur.fetchall()

        detalle_por_pedido = {}
        if rows:
            numeros = [r[0] for r in rows]
            placeholders = ",".join(["%s"] * len(numeros))
            cur.execute(f"""
                SELECT dp.numero_pedido, pr.nombre, pr.codigo_sku, dp.cantidad,
                       dp.precio_unitario, dp.descuento_pct, dp.precio_neto_unitario, dp.subtotal_linea
                FROM detalle_pedido dp
                JOIN productos pr ON pr.id = dp.producto_id
                WHERE dp.numero_pedido IN ({placeholders})
            """, numeros)
            for numero, nombre, sku, cantidad, precio_unit, desc_pct, precio_neto, subtotal in cur.fetchall():
                detalle_por_pedido.setdefault(numero, []).append({
                    "producto": nombre, "codigo_sku": sku, "cantidad": cantidad,
                    "precio_unitario": float(precio_unit), "descuento_pct": float(desc_pct),
                    "precio_neto_unitario": float(precio_neto), "subtotal_linea": float(subtotal),
                })
        cur.close()
    finally:
        conn.close()

    return jsonify([
        {
            "numero_pedido": r[0], "nombre_comercial": r[1], "codigo_cliente": r[2],
            "canal_venta": r[3], "fecha_pedido": r[4].strftime("%d/%m/%Y") if r[4] else "",
            "estado": r[5], "condicion_pago": r[6], "total": float(r[7]) if r[7] is not None else 0.0,
            "vendedor": r[8], "detalle": detalle_por_pedido.get(r[0], []),
        }
        for r in rows
    ])


@app.route("/api/cartera/gestiones-posventa", methods=["GET"])
def cartera_gestiones_posventa():
    """Lista las gestiones de posventa. Soporta ?estado= para filtrar."""
    estado = (request.args.get("estado") or "").strip()
    conn = get_conn()
    try:
        cur = conn.cursor()
        if estado:
            cur.execute("""
                SELECT g.numero_gestion, e.nombre_comercial, e.codigo_cliente, g.numero_pedido,
                       g.tipo, g.estado, g.prioridad, g.fecha_apertura
                FROM gestiones_posventa g
                JOIN empresas_clientes e ON e.id = g.empresa_cliente_id
                WHERE g.estado = %s
                ORDER BY g.fecha_apertura DESC
            """, (estado,))
        else:
            cur.execute("""
                SELECT g.numero_gestion, e.nombre_comercial, e.codigo_cliente, g.numero_pedido,
                       g.tipo, g.estado, g.prioridad, g.fecha_apertura
                FROM gestiones_posventa g
                JOIN empresas_clientes e ON e.id = g.empresa_cliente_id
                ORDER BY g.fecha_apertura DESC
            """)
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    return jsonify([
        {
            "numero_gestion": r[0], "nombre_comercial": r[1], "codigo_cliente": r[2],
            "numero_pedido": r[3], "tipo": r[4], "estado": r[5], "prioridad": r[6],
            "fecha_apertura": r[7].strftime("%d/%m/%Y %H:%M") if r[7] else "",
        }
        for r in rows
    ])


@app.route("/api/cartera/gestiones-posventa/<numero_gestion>", methods=["GET"])
def cartera_gestion_posventa_detalle(numero_gestion):
    """Detalle completo de una gestion de posventa: descripcion, contexto de cuenta e
    historico capturados al momento de abrirla, estado, prioridad y resolucion."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT g.numero_gestion, e.nombre_comercial, e.codigo_cliente, g.numero_pedido,
                   g.tipo, g.descripcion, g.contexto_cuenta, g.estado, g.prioridad,
                   g.canal_reporte, g.vendedor, g.fecha_apertura, g.fecha_cierre, g.resolucion
            FROM gestiones_posventa g
            JOIN empresas_clientes e ON e.id = g.empresa_cliente_id
            WHERE g.numero_gestion = %s
        """, (numero_gestion.upper().strip(),))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    if not row:
        return jsonify({"error": "Gestion no encontrada"}), 404

    (num, nombre_e, codigo_e, num_ped, tipo, desc, contexto, estado, prio,
     canal_rep, vendedor, f_ap, f_cie, resol) = row
    return jsonify({
        "numero_gestion": num, "nombre_comercial": nombre_e, "codigo_cliente": codigo_e,
        "numero_pedido": num_ped, "tipo": tipo, "descripcion": desc,
        "contexto_cuenta": contexto, "estado": estado, "prioridad": prio,
        "canal_reporte": canal_rep, "vendedor": vendedor,
        "fecha_apertura": f_ap.strftime("%d/%m/%Y %H:%M") if f_ap else "",
        "fecha_cierre": f_cie.strftime("%d/%m/%Y %H:%M") if f_cie else None,
        "resolucion": resol,
    })


# ── Endpoints de Politicas de Descuento (editor en AdminPanel) ──────────────

_POLITICA_CAMPOS = (
    "canal", "tamano_canal", "condicion_pago", "volumen_min_litros",
    "volumen_max_litros", "descuento_pct", "condiciones_adicionales", "prioridad", "activo",
)


def _serializar_politica(row):
    (id_, canal, tamano, cond, vmin, vmax, pct, extra, prio, activo) = row
    return {
        "id": id_, "canal": canal, "tamano_canal": tamano, "condicion_pago": cond,
        "volumen_min_litros": float(vmin), "volumen_max_litros": float(vmax) if vmax is not None else None,
        "descuento_pct": float(pct), "condiciones_adicionales": extra, "prioridad": prio,
        "activo": bool(activo),
    }


@app.route("/api/politicas-descuento", methods=["GET"])
def listar_politicas_descuento():
    """Lista todas las politicas de descuento (activas e inactivas)."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, canal, tamano_canal, condicion_pago, volumen_min_litros, volumen_max_litros,
                   descuento_pct, condiciones_adicionales, prioridad, activo
            FROM politicas_descuento
            ORDER BY canal, condicion_pago, volumen_min_litros
        """)
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    return jsonify([_serializar_politica(r) for r in rows])


@app.route("/api/politicas-descuento", methods=["POST"])
def crear_politica_descuento():
    """Crea una politica de descuento nueva. Body: canal, tamano_canal, condicion_pago,
    volumen_min_litros, volumen_max_litros, descuento_pct, condiciones_adicionales, prioridad, activo."""
    data = request.get_json() or {}
    canal = (data.get("canal") or "").strip().lower()
    condicion_pago = (data.get("condicion_pago") or "").strip().lower()
    if not canal or condicion_pago not in ("contado", "credito"):
        return jsonify({"error": "canal y condicion_pago ('contado'/'credito') son requeridos"}), 400

    tamano = (data.get("tamano_canal") or "").strip().lower() or None
    vmin = data.get("volumen_min_litros", 0) or 0
    vmax = data.get("volumen_max_litros")
    vmax = None if vmax in (None, "", 0) and vmax != 0 else vmax
    try:
        descuento_pct = float(data.get("descuento_pct", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "descuento_pct debe ser un numero"}), 400
    condiciones = (data.get("condiciones_adicionales") or "").strip() or None
    prioridad = int(data.get("prioridad", 0) or 0)
    activo = 1 if data.get("activo", True) else 0

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO politicas_descuento (canal, tamano_canal, condicion_pago, volumen_min_litros,
                                              volumen_max_litros, descuento_pct, condiciones_adicionales,
                                              prioridad, activo)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (canal, tamano, condicion_pago, vmin, vmax, descuento_pct, condiciones, prioridad, activo))
        new_id = cur.lastrowid
        conn.commit()
        cur.close()
    finally:
        conn.close()

    return jsonify({"ok": True, "id": new_id}), 201


@app.route("/api/politicas-descuento/<int:politica_id>", methods=["PUT"])
def actualizar_politica_descuento(politica_id):
    """Actualiza una politica de descuento existente. Solo actualiza los campos presentes en el body."""
    data = request.get_json() or {}
    fields = []
    args = []
    for key in _POLITICA_CAMPOS:
        if key not in data:
            continue
        val = data[key]
        if key == "canal":
            val = (val or "").strip().lower()
        elif key == "tamano_canal":
            val = (val or "").strip().lower() or None
        elif key == "condicion_pago":
            val = (val or "").strip().lower()
            if val not in ("contado", "credito"):
                return jsonify({"error": "condicion_pago debe ser 'contado' o 'credito'"}), 400
        elif key == "condiciones_adicionales":
            val = (val or "").strip() or None
        elif key == "activo":
            val = 1 if val else 0
        fields.append(f"{key} = %s")
        args.append(val)

    if not fields:
        return jsonify({"error": "Nada para actualizar"}), 400

    args.append(politica_id)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE politicas_descuento SET {', '.join(fields)} WHERE id = %s", tuple(args))
        affected = cur.rowcount
        conn.commit()
        cur.close()
    finally:
        conn.close()

    if affected == 0:
        return jsonify({"error": "Politica no encontrada"}), 404
    return jsonify({"ok": True})


@app.route("/api/politicas-descuento/<int:politica_id>", methods=["DELETE"])
def borrar_politica_descuento(politica_id):
    """Elimina una politica de descuento."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM politicas_descuento WHERE id = %s", (politica_id,))
        affected = cur.rowcount
        conn.commit()
        cur.close()
    finally:
        conn.close()

    if affected == 0:
        return jsonify({"error": "Politica no encontrada"}), 404
    return jsonify({"ok": True})


# ── Endpoints de administracion ───────────────────────────────────────────────

@app.route("/api/ontologias", methods=["GET"])
def listar_ontologias():
    """
    Lista las ontologias activas:
    - Las de perfil (system-prompt, ontologia-procedimientos, ontologia-faq)
      filtradas por el perfil_id activo.
    - Las globales (autopilot-*) con perfil_id IS NULL.
    """
    perfil_id = get_active_perfil_id()
    conn = get_conn()
    try:
        cur = conn.cursor()
        if perfil_id is not None:
            cur.execute("""
                SELECT o.nombre, o.version, o.contenido
                FROM ontologias o
                INNER JOIN (
                    SELECT nombre, MAX(id) AS max_id
                    FROM ontologias
                    WHERE activo = TRUE AND perfil_id = %s
                    GROUP BY nombre
                ) latest ON o.id = latest.max_id
                ORDER BY o.id
            """, (perfil_id,))
            perfil_rows = cur.fetchall()
        else:
            perfil_rows = []

        cur.execute("""
            SELECT o.nombre, o.version, o.contenido
            FROM ontologias o
            INNER JOIN (
                SELECT nombre, MAX(id) AS max_id
                FROM ontologias
                WHERE activo = TRUE AND perfil_id IS NULL
                GROUP BY nombre
            ) latest ON o.id = latest.max_id
            ORDER BY o.id
        """)
        global_rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    return jsonify([
        {"nombre": r[0], "version": r[1], "contenido": r[2]}
        for r in (*perfil_rows, *global_rows)
    ])


def _guardar_nueva_version(nombre: str, contenido: str) -> str:
    """
    Desactiva la version activa e inserta una nueva fila.
    Las ontologias de perfil se guardan ligadas al perfil activo;
    las globales (autopilot-*) se guardan con perfil_id NULL.
    Devuelve el numero de version nuevo.
    """
    es_perfil = nombre in PROFILE_ONTOLOGIES
    perfil_id = get_active_perfil_id() if es_perfil else None
    if es_perfil and perfil_id is None:
        raise RuntimeError(f"No hay perfil activo para guardar '{nombre}'.")

    scope_clause = "perfil_id = %s" if es_perfil else "perfil_id IS NULL"
    scope_args   = (perfil_id,) if es_perfil else ()

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT version FROM ontologias WHERE nombre = %s AND activo = TRUE AND {scope_clause} "
            f"ORDER BY id DESC LIMIT 1",
            (nombre, *scope_args)
        )
        row = cur.fetchone()
        version_actual = row[0] if row else "1.0"
        try:
            nueva_version = f"{float(version_actual) + 0.1:.1f}"
        except (ValueError, TypeError):
            nueva_version = "1.1"

        cur.execute(
            f"UPDATE ontologias SET activo = FALSE WHERE nombre = %s AND activo = TRUE AND {scope_clause}",
            (nombre, *scope_args)
        )
        cur.execute(
            "INSERT INTO ontologias (nombre, version, contenido, activo, perfil_id) "
            "VALUES (%s, %s, %s, TRUE, %s)",
            (nombre, nueva_version, contenido, perfil_id)
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return nueva_version


def _on_ontology_changed(nombre: str):
    """Invalida cache y, si cambia el system-prompt, fuerza recarga del agente."""
    global _agent, _checkpointer
    invalidate_ontology_cache(nombre)
    if nombre in ("system-prompt", None):
        _agent = None
        _checkpointer = None


@app.route("/api/ontologias/<nombre>", methods=["PUT"])
def actualizar_ontologia(nombre):
    """
    Guarda una nueva version de la ontologia (dejar la anterior inactiva).
    Body: { "contenido": "..." }
    """
    data      = request.get_json()
    contenido = data.get("contenido", "").strip()

    if not contenido:
        return jsonify({"error": "contenido es requerido"}), 400

    nueva_version = _guardar_nueva_version(nombre, contenido)
    _on_ontology_changed(nombre)
    return jsonify({"ok": True, "version": nueva_version})


@app.route("/api/chat/evaluate", methods=["POST", "OPTIONS"])
def chat_evaluate():
    """
    Evalua una conversacion manual usando el mismo evaluador del autopilot.
    Body: { "messages": [{role, content}], "pedido": {...} }
    """
    if request.method == "OPTIONS":
        return "", 200

    data     = request.get_json()
    messages = data.get("messages", [])
    pedido   = data.get("pedido") or {}

    # Convertir al formato que espera evaluar_conversacion
    transcripcion = [
        {
            "role": "cliente" if m["role"] == "user" else "asistente",
            "content": m["content"],
        }
        for m in messages if m.get("content", "").strip()
    ]

    caso = {
        "numero_pedido":  pedido.get("numero", "N/A"),
        "estado":         pedido.get("estado", "N/A"),
        "cliente":        pedido.get("cliente", "N/A"),
        "nivel_fidelidad": pedido.get("nivel_fidelidad", "N/A"),
        "motivo":         "modo manual",
        "personalidad":   "modo manual",
    }

    try:
        evaluacion = evaluar_conversacion(transcripcion, caso, "indeciso")
        return jsonify(evaluacion)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/autopilot/apply-recommendation", methods=["POST"])
def apply_recommendation():
    """
    Aplica quirurgicamente una recomendacion del evaluador a la ontologia correspondiente.
    Guarda una nueva version en la BD y invalida el cache.
    Body: { "nivel": "system_prompt|ontologia_procedimientos|ontologia_faq",
            "recomendacion": "..." }
    """
    data = request.get_json()
    nivel         = data.get("nivel", "").strip()
    recomendacion = data.get("recomendacion", "").strip()

    if not nivel or not recomendacion:
        return jsonify({"error": "nivel y recomendacion son requeridos"}), 400

    NIVEL_TO_NOMBRE = {
        "system_prompt":            "system-prompt",
        "ontologia_procedimientos": "ontologia-procedimientos",
        "ontologia_faq":            "ontologia-faq",
    }
    nombre = NIVEL_TO_NOMBRE.get(nivel)
    if not nombre:
        return jsonify({"error": f"nivel desconocido: {nivel}"}), 400

    perfil_id = get_active_perfil_id()
    if perfil_id is None:
        return jsonify({"error": "No hay perfil activo"}), 400

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT contenido, version FROM ontologias "
            "WHERE nombre = %s AND activo = TRUE AND perfil_id = %s "
            "ORDER BY id DESC LIMIT 1",
            (nombre, perfil_id)
        )
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    if not row:
        return jsonify({"error": f"No se encontro la ontologia '{nombre}' en el perfil activo"}), 404

    contenido_actual = row[0]

    openai_client = OpenAI()
    SYSTEM_APLICAR = (
        "Eres un experto en sistemas de atencion al cliente para retail. "
        "Tu tarea es aplicar una mejora puntual a un fragmento de ontologia de un agente de IA.\n\n"
        "Reglas de aplicacion:\n"
        "- Aplica UNICAMENTE el cambio recomendado, sin alterar el contenido no relacionado\n"
        "- Manten el formato, estructura y estilo del texto original\n"
        "- Si la recomendacion indica agregar texto, agregalo en el lugar mas apropiado\n"
        "- Si indica modificar algo especifico, modificalo con precision quirurgica\n"
        "- Devuelve SOLO el texto completo actualizado, sin explicaciones ni marcadores extra"
    )
    prompt = (
        f"ONTOLOGIA ACTUAL ({nombre}):\n"
        f"---\n{contenido_actual}\n---\n\n"
        f"RECOMENDACION A APLICAR:\n{recomendacion}\n\n"
        f"Devuelve el texto completo de la ontologia con el cambio aplicado."
    )

    contenido_nuevo = None
    last_error = None
    for model_name, use_mct in (("gpt-5.4", True), ("gpt-4o", False)):
        try:
            kwargs = dict(
                model=model_name,
                messages=[
                    {"role": "system", "content": SYSTEM_APLICAR},
                    {"role": "user",   "content": prompt},
                ],
            )
            if use_mct:
                kwargs["max_completion_tokens"] = 4000
            else:
                kwargs["max_tokens"]  = 4000
                kwargs["temperature"] = 0.2
            resp = openai_client.chat.completions.create(**kwargs)
            contenido_nuevo = (resp.choices[0].message.content or "").strip()
            print(f"[apply-recommendation] model={model_name} len={len(contenido_nuevo)}")
            if contenido_nuevo:
                break
        except Exception as e:
            last_error = e
            print(f"[apply-recommendation] model={model_name} error: {e}")

    if not contenido_nuevo:
        return jsonify({"error": f"Error al generar cambio: {last_error or 'respuesta vacia'}"}), 500

    nueva_version = _guardar_nueva_version(nombre, contenido_nuevo)
    _on_ontology_changed(nombre)

    return jsonify({
        "ok":      True,
        "nombre":  nombre,
        "version": nueva_version,
    })


# ── Endpoints de Autopilot ────────────────────────────────────────────────────

@app.route("/api/autopilot/opciones", methods=["GET"])
def autopilot_opciones():
    """Devuelve los pedidos disponibles y las listas de motivos/personalidades."""
    return jsonify({
        "pedidos":        get_all_pedidos(),
        "motivos":        MOTIVOS,
        "personalidades": PERSONALIDADES,
    })


@app.route("/api/autopilot/start", methods=["POST"])
def autopilot_start():
    """
    Genera (o valida) el caso de test y crea la sesion.
    Body (todos opcionales): { "numero_pedido": "...", "motivo": "...", "personalidad": "..." }
    Retorna el caso completo con session_id.
    """
    data = request.get_json() or {}
    numero_pedido = data.get("numero_pedido", "").strip() or None
    motivo        = data.get("motivo", "").strip() or None
    personalidad  = data.get("personalidad", "").strip() or None

    try:
        caso = generar_caso_aleatorio(numero_pedido)
        if motivo:
            caso["motivo"] = motivo
        if personalidad:
            caso["personalidad"] = personalidad
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    session_id = str(uuid.uuid4())
    caso["session_id"] = session_id
    return jsonify(caso)


@app.route("/api/autopilot/run/<session_id>", methods=["GET"])
def autopilot_run(session_id):
    """
    Corre la conversacion autopilot y emite eventos SSE en tiempo real.
    Ahora el cliente simulado habla DIRECTAMENTE con el asistente (no hay ejecutivo).
    """
    caso = {
        "numero_pedido":   request.args.get("numero_pedido", ""),
        "estado":          request.args.get("estado", ""),
        "cliente":         request.args.get("cliente", ""),
        "nivel_fidelidad": request.args.get("nivel_fidelidad", ""),
        "motivo":          request.args.get("motivo", ""),
        "personalidad":    request.args.get("personalidad", ""),
    }

    def generate():
        transcripcion = []
        decision = "indeciso"

        try:
            from langchain_core.messages import HumanMessage
            from langgraph.checkpoint.memory import MemorySaver
            from chatbot import build_agent

            checkpointer = MemorySaver()
            agent = build_agent(checkpointer)
            config = {"configurable": {"thread_id": session_id}}

            historial_cliente = []

            # El cliente inicia con su consulta. Construir el primer mensaje segun el motivo.
            primer_msg_cliente = _generar_mensaje_cliente(historial_cliente, caso, primer_turno=True)
            transcripcion.append({"role": "cliente", "content": primer_msg_cliente})
            yield f"data: {json.dumps({'type': 'turn', 'role': 'cliente', 'content': primer_msg_cliente})}\n\n"

            def _stream_agent(input_msg):
                """Corre un turno del agente y hace yield de eventos SSE."""
                response = ""
                for chunk, metadata in agent.stream(
                    {"messages": [HumanMessage(content=input_msg)]},
                    config=config,
                    stream_mode="messages",
                ):
                    node = metadata.get("langgraph_node")

                    if node == "agent" and hasattr(chunk, "tool_calls") and chunk.tool_calls:
                        for tc in chunk.tool_calls:
                            tool_name = tc.get("name", "")
                            yield f"data: {json.dumps({'type': 'tool', 'name': tool_name})}\n\n"

                    if node == "tools" and hasattr(chunk, "content") and isinstance(chunk.content, str):
                        if "Pedido encontrado" in chunk.content:
                            pedido_data = _parse_pedido_result(chunk.content)
                            if pedido_data:
                                yield f"data: {json.dumps({'pedido': pedido_data})}\n\n"

                    if node == "agent" and isinstance(chunk.content, str) and chunk.content:
                        response += chunk.content
                        yield f"data: {json.dumps({'type': 'agent_token', 'token': chunk.content})}\n\n"

                if response:
                    yield f"data: {json.dumps({'type': 'agent_end'})}\n\n"

            # Agente responde al primer mensaje del cliente
            for event in _stream_agent(primer_msg_cliente):
                yield event

            # Leer respuesta acumulada del estado
            state = agent.get_state(config)
            msgs = state.values.get("messages", [])
            last_ai = next((m for m in reversed(msgs) if hasattr(m, "content") and m.type == "ai" and isinstance(m.content, str) and m.content), None)
            agent_response = last_ai.content if last_ai else ""

            if agent_response:
                transcripcion.append({"role": "asistente", "content": agent_response})
                historial_cliente.append({"role": "assistant", "content": primer_msg_cliente})
                historial_cliente.append({"role": "user", "content": agent_response[:200]})

            CIERRE_KEYWORDS = ["que tenga un buen dia", "buenas noches", "hasta luego",
                               "no dude en contactar", "fue un placer", "gracias por contactar",
                               "te deseo lo mejor"]

            # Turnos de conversacion
            for turno in range(8):
                if agent_response and any(k in agent_response.lower() for k in CIERRE_KEYWORDS):
                    break

                msg_cliente = _generar_mensaje_cliente(historial_cliente, caso)

                if "[DECISION: RESUELTO]" in msg_cliente:
                    decision = "resuelto"
                    msg_cliente = msg_cliente.replace("[DECISION: RESUELTO]", "").strip()
                elif "[DECISION: NO_RESUELTO]" in msg_cliente:
                    decision = "no_resuelto"
                    msg_cliente = msg_cliente.replace("[DECISION: NO_RESUELTO]", "").strip()

                transcripcion.append({"role": "cliente", "content": msg_cliente})
                yield f"data: {json.dumps({'type': 'turn', 'role': 'cliente', 'content': msg_cliente})}\n\n"

                # Risk profile al recibir mensaje del cliente
                try:
                    conv_text = "\n".join(
                        f"{t['role'].capitalize()}: {t['content'][:300]}"
                        for t in transcripcion
                    )
                    risk = _analyze_risk_profile(conv_text)
                    if risk:
                        yield f"data: {json.dumps({'type': 'risk_profile', 'data': risk})}\n\n"
                except Exception as ex:
                    print(f"[risk_profile autopilot] {ex}")

                if decision in ("resuelto", "no_resuelto"):
                    break

                for event in _stream_agent(msg_cliente):
                    yield event

                state = agent.get_state(config)
                msgs = state.values.get("messages", [])
                last_ai = next((m for m in reversed(msgs) if hasattr(m, "content") and m.type == "ai" and isinstance(m.content, str) and m.content), None)
                agent_response = last_ai.content if last_ai else ""

                if agent_response:
                    transcripcion.append({"role": "asistente", "content": agent_response})
                    historial_cliente.append({"role": "assistant", "content": msg_cliente})
                    historial_cliente.append({"role": "user", "content": agent_response[:200]})

                    if any(k in agent_response.lower() for k in CIERRE_KEYWORDS):
                        decision = decision or "resuelto"

            yield f"data: {json.dumps({'type': 'done_conversation', 'transcripcion': transcripcion, 'decision': decision})}\n\n"

        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/autopilot/evaluate", methods=["POST", "OPTIONS"])
def autopilot_evaluate():
    """Evalua una conversacion de autopilot bajo demanda."""
    if request.method == "OPTIONS":
        return "", 200

    data          = request.get_json()
    transcripcion = data.get("transcripcion", [])
    caso          = data.get("caso", {})
    decision      = data.get("decision", "indeciso")

    try:
        evaluacion = evaluar_conversacion(transcripcion, caso, decision)
        evaluacion["decision"] = decision
        return jsonify(evaluacion)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e), "decision": decision}), 500


# ── Arranque ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=False, use_reloader=False, port=int(os.getenv("PORT", 5002)), threaded=True)
