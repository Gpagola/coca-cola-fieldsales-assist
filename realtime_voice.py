# ── Modo "Voz en vivo" (OpenAI Realtime API) — modo experimental de voz ───────
#
# Este modulo NO reemplaza el pipeline de voz actual (VAD + /api/transcribe + /api/chat +
# /api/speak, que sigue intacto). Es un camino alternativo, mobile-only, que habla
# speech-to-speech directo con el modelo de OpenAI por WebRTC (el audio nunca pasa por
# este backend). Este archivo solo se encarga de:
#
#   1) armar la configuracion de sesion (system prompt + tono + tools) para pedirle a
#      OpenAI un token efimero de conexion (ver backend.py: POST /api/realtime/session)
#   2) ejecutar del lado del servidor las tools que el modelo invoque durante la
#      conversacion (ver backend.py: POST /api/realtime/tool-call), reusando las mismas
#      funciones de chatbot.py que usa el agente de texto — nunca se duplica logica de
#      negocio.
#
# Las tools que ESCRIBEN pedidos (crear_pedido, cambiar_condicion_pago_pedido,
# cancelar_pedido) no se exponen nunca directamente al modelo de voz: se reemplazan por
# versiones "preparar_*" (solo cotizan, no escriben) mas un gate de confirmacion
# explicito en codigo (confirmar_accion) — ver la seccion "Gate de confirmacion" abajo.

import os
import threading
import time
import uuid

import mysql.connector
from langchain_core.utils.function_calling import convert_to_openai_tool

import chatbot


# ── Configuracion (env vars, todas con default sensato) ──────────────────────

def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "")


REALTIME_ENABLED        = _env_bool("REALTIME_ENABLED", "1")
REALTIME_MODEL          = os.getenv("REALTIME_MODEL", "gpt-realtime-2.1")  # bajar a "-mini" es solo cambiar esto
REALTIME_VOICE          = os.getenv("REALTIME_VOICE", "coral")
REALTIME_TURN_DETECTION = os.getenv("REALTIME_TURN_DETECTION", "semantic_vad")  # server_vad | semantic_vad
REALTIME_TOKEN_TTL_S    = int(os.getenv("REALTIME_TOKEN_TTL_S", "120"))
REALTIME_MAX_SESSION_MS = int(os.getenv("REALTIME_MAX_SESSION_MS", str(10 * 60 * 1000)))  # tope de costo, no el limite de la API (60 min)
REALTIME_MAX_TOOL_OUTPUT_CHARS = int(os.getenv("REALTIME_MAX_TOOL_OUTPUT_CHARS", "2500"))
REALTIME_PENDING_TTL_S  = int(os.getenv("REALTIME_PENDING_TTL_S", "300"))
REALTIME_UI_CONFIRM     = _env_bool("REALTIME_UI_CONFIRM", "0")  # segunda capa opcional: exigir tambien un tap en pantalla
_MAX_PENDING            = 200


# ── Constantes de prompt compartidas con el modo voz clasico (para que las dos ────────
#    voces suenen a la misma persona) — movidas aca, backend.py las importa.

VOZ_INSTRUCCIONES_RIOPLATENSE = (
    "Hablá en español rioplatense de Argentina, con acento porteño "
    "natural (voseo, entonacion argentina). Tono formal y profesional, "
    "como un asesor comercial serio y confiable; claro y bien articulado, "
    "cordial pero sin excesos de calidez ni informalidad. Ritmo agil."
)

VOICE_BREVITY_INSTRUCTIONS = (
    "Responde en 1 o 2 oraciones, breve y directo, en tono conversacional. "
    "Prohibido usar listas, vinetas, numeraciones, tablas o markdown. "
    "Da solo lo esencial (el dato o el siguiente paso); si hay mucho que "
    "detallar, resumi y ofrece ampliar si el vendedor lo pide."
)

REALTIME_PROTOCOL_INSTRUCTIONS = (
    "No tenes tools para registrar, cambiar o cancelar pedidos de forma directa. En su lugar:\n"
    "1. Usa siempre primero preparar_pedido / preparar_cambio_condicion_pago / preparar_cancelacion_pedido "
    "segun corresponda. Estas tools NO escriben nada todavia, solo cotizan y devuelven un resumen mas un "
    "confirmation_id (el confirmation_id queda en TU contexto, no hace falta que el vendedor lo escuche ni lo repita).\n"
    "2. En esa MISMA respuesta, decile al vendedor el resumen COMPLETO en voz alta (cuenta, items, condicion "
    "de pago, descuento, total, o lo que corresponda segun la accion) y preguntale si lo confirma. No dejes "
    "el resumen para una respuesta futura ni digas solamente 'ya te lo leo' — leelo ahora.\n"
    "3. En cuanto el vendedor responda con CUALQUIER afirmacion clara a continuacion — 'si', 'dale', 'confirmalo', "
    "'listo', 'adelante', 'registralo', 'hacelo', 'correcto', etc., sea cual sea la frase exacta — llama a "
    "confirmar_accion INMEDIATAMENTE en esa misma respuesta, usando el confirmation_id que ya tenes de tu propio "
    "paso 1. NO vuelvas a leer o repetir el resumen antes de confirmar: si ya se lo leiste una vez y el vendedor "
    "afirmo, confirma directamente. Solo un 'no', una duda concreta o un pedido de cambio evitan la confirmacion.\n"
    "4. Nunca inventes un confirmation_id ni lo digas en voz alta — siempre usa el que te devolvio tu propia "
    "llamada a preparar_*.\n"
    "5. Si el vendedor se arrepiente o pide cambiar algo, usa descartar_accion y volvé a preparar con los "
    "datos correctos.\n"
    "agregar_nota_pedido y abrir_gestion_posventa no necesitan este protocolo, se pueden llamar directamente."
)


# ── Conversion de las tools de lectura/escritura-segura a JSON-Schema de Realtime ─────

DIRECT_TOOLS = {
    t.name: t for t in [
        chatbot.consultar_cuenta_cliente,
        chatbot.consultar_historico_pedidos,
        chatbot.buscar_producto,
        chatbot.consultar_politica_descuento,
        chatbot.ontologia_procedimientos,
        chatbot.ontologia_descuentos,
        chatbot.ontologia_faq,
        chatbot.consultar_gestiones_posventa,
        chatbot.agregar_nota_pedido,
        chatbot.abrir_gestion_posventa,
    ]
}

# Overrides puntuales sobre lo que devuelve convert_to_openai_tool: enums derivados de las
# constantes reales de chatbot.py (nunca hardcodeados, para no desincronizarse si cambian).
SCHEMA_OVERRIDES = {
    "abrir_gestion_posventa": {
        "tipo": {"enum": sorted(chatbot._TIPOS_GESTION_VALIDOS)},
    },
}


def _apply_overrides(function_dict: dict) -> dict:
    name = function_dict["name"]
    params = function_dict.get("parameters", {})
    params.setdefault("properties", {})
    params.setdefault("required", [])
    params["additionalProperties"] = False
    for prop_name, override in SCHEMA_OVERRIDES.get(name, {}).items():
        if prop_name in params["properties"]:
            params["properties"][prop_name].update(override)
    function_dict["parameters"] = params
    return function_dict


def _flatten(function_dict: dict) -> dict:
    return {"type": "function", **function_dict}


_CONDICION_PAGO_ENUM = sorted(chatbot._CONDICIONES_PAGO_VALIDAS)

# Tools sinteticas del gate de confirmacion (no existen como @tool de chatbot.py — son
# el unico "vocabulario de escritura" que el modelo de voz en vivo tiene disponible).
_MANUAL_TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "preparar_pedido",
        "description": (
            "Cotiza y prepara un pedido nuevo SIN registrarlo todavia. Devuelve un resumen "
            "(cuenta, items, condicion de pago, descuento, total) y un confirmation_id. Leele "
            "el resumen COMPLETO al vendedor en voz alta y esperá su confirmacion verbal "
            "explicita antes de llamar a confirmar_accion con ese mismo confirmation_id. No se "
            "puede confirmar en el mismo turno en que se preparo."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "codigo_cliente": {"type": "string", "description": "Codigo de la cuenta, formato CLI-XXXX."},
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "producto": {"type": "string", "description": "Nombre o SKU del producto."},
                            "cantidad": {"type": "integer", "minimum": 1},
                        },
                        "required": ["producto", "cantidad"],
                        "additionalProperties": False,
                    },
                },
                "condicion_pago": {"type": "string", "enum": _CONDICION_PAGO_ENUM},
                "notas": {"type": "string", "description": "Notas opcionales sobre el pedido."},
            },
            "required": ["codigo_cliente", "items", "condicion_pago"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "preparar_cambio_condicion_pago",
        "description": (
            "Recalcula el descuento de un pedido existente bajo una nueva condicion de pago, "
            "SIN aplicar el cambio todavia. Devuelve un resumen y un confirmation_id. Leele el "
            "resumen al vendedor y esperá confirmacion verbal antes de llamar a confirmar_accion."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "numero_pedido": {"type": "string", "description": "Numero de pedido, formato PED-XXXX."},
                "nueva_condicion_pago": {"type": "string", "enum": _CONDICION_PAGO_ENUM},
            },
            "required": ["numero_pedido", "nueva_condicion_pago"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "preparar_cancelacion_pedido",
        "description": (
            "Valida que un pedido se pueda cancelar y prepara la cancelacion SIN aplicarla "
            "todavia. Devuelve un resumen y un confirmation_id. Leele el resumen al vendedor y "
            "esperá confirmacion verbal antes de llamar a confirmar_accion."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "numero_pedido": {"type": "string"},
                "motivo": {"type": "string", "description": "Motivo de la cancelacion segun lo indique el vendedor."},
            },
            "required": ["numero_pedido"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "confirmar_accion",
        "description": (
            "Ejecuta de forma definitiva una accion previamente preparada con preparar_pedido, "
            "preparar_cambio_condicion_pago o preparar_cancelacion_pedido, usando el "
            "confirmation_id que devolvieron. SOLO llamar despues de que el vendedor haya "
            "confirmado VERBALMENTE Y EXPLICITAMENTE el resumen completo que le leiste. Nunca "
            "inventes un confirmation_id."
        ),
        "parameters": {
            "type": "object",
            "properties": {"confirmation_id": {"type": "string"}},
            "required": ["confirmation_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "descartar_accion",
        "description": (
            "Descarta un borrador preparado previamente (por ejemplo si el vendedor se "
            "arrepintio o pidio cambiar algo). No aplica ningun cambio en el sistema."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "confirmation_id": {"type": "string"},
                "motivo": {"type": "string"},
            },
            "required": ["confirmation_id"],
            "additionalProperties": False,
        },
    },
]


def build_realtime_tool_schemas() -> list:
    """Devuelve la lista completa de tools (JSON-Schema, formato Realtime) para el modo de
    voz en vivo: 10 tools de chatbot.py convertidas (lectura + 2 escrituras seguras) + 5
    tools sinteticas del gate de confirmacion. crear_pedido/cambiar_condicion_pago_pedido/
    cancelar_pedido deliberadamente NO aparecen aca — ver el modulo docstring."""
    convertidas = [
        _flatten(_apply_overrides(convert_to_openai_tool(tool_obj)["function"]))
        for tool_obj in DIRECT_TOOLS.values()
    ]
    return convertidas + _MANUAL_TOOL_SCHEMAS


def _truncar_para_voz(texto):
    """Los outputs de ontologia_* pueden ser bloques de texto largos — recortarlos evita
    reventar la ventana de contexto de Realtime (32k) y que el modelo intente leer un
    documento entero en voz alta."""
    if not isinstance(texto, str) or len(texto) <= REALTIME_MAX_TOOL_OUTPUT_CHARS:
        return texto
    return texto[:REALTIME_MAX_TOOL_OUTPUT_CHARS] + (
        "\n[...texto recortado para modo voz. Si el vendedor necesita el detalle completo, "
        "decile que lo revise en la app o consulta el punto especifico.]"
    )


# ── Cache del system prompt (evita pegarle a MySQL en cada mint de token) ─────────────

_sp_cache_lock = threading.Lock()
_sp_cache = None


def _system_prompt_cached() -> str:
    global _sp_cache
    with _sp_cache_lock:
        if _sp_cache is None:
            _sp_cache = chatbot.cargar_system_prompt()
        return _sp_cache


def invalidar_prompt_cache() -> None:
    """Llamar desde backend._reset_agent() para que editar una ontologia tambien recargue
    el prompt del modo de voz en vivo, igual que ya pasa con el agente de texto."""
    global _sp_cache
    with _sp_cache_lock:
        _sp_cache = None


def build_realtime_instructions(contexto_previo: str = "") -> str:
    partes = [
        _system_prompt_cached(),
        "\n\n--- TONO DE VOZ ---\n" + VOZ_INSTRUCCIONES_RIOPLATENSE,
        "\n\n--- BREVEDAD (modo voz en vivo) ---\n" + VOICE_BREVITY_INSTRUCTIONS,
        "\n\n--- PROTOCOLO DE CONFIRMACION PARA PEDIDOS ---\n" + REALTIME_PROTOCOL_INSTRUCTIONS,
    ]
    if contexto_previo:
        partes.append("\n\n--- CONTEXTO PREVIO DE ESTA VISITA ---\n" + contexto_previo[:1200])
    return "".join(partes)


def build_session_config(instructions: str) -> dict:
    """Body para POST https://api.openai.com/v1/realtime/client_secrets. La forma exacta del
    objeto "session" es la superficie mas fragil de toda la integracion (GA vs beta pueden
    anidar audio/turn_detection distinto) — si OpenAI rechaza esto con 400, loggear el body
    crudo del error (ver backend.py) y ajustar SOLO esta funcion."""
    if REALTIME_TURN_DETECTION == "semantic_vad":
        turn_detection = {
            "type": "semantic_vad", "eagerness": "auto",
            "create_response": True, "interrupt_response": True,
        }
    else:
        turn_detection = {
            "type": "server_vad", "threshold": 0.5,
            "silence_duration_ms": 700, "prefix_padding_ms": 300,
            "create_response": True, "interrupt_response": True,
        }

    return {
        "expires_after": {"anchor": "created_at", "seconds": REALTIME_TOKEN_TTL_S},
        "session": {
            "type": "realtime",
            "model": REALTIME_MODEL,
            "instructions": instructions,
            "audio": {
                "input": {
                    "transcription": {"model": "gpt-4o-mini-transcribe", "language": "es"},
                    "turn_detection": turn_detection,
                },
                "output": {"voice": REALTIME_VOICE, "speed": 1.1},
            },
            "tools": build_realtime_tool_schemas(),
            "tool_choice": "auto",
        },
    }


# ── Gate de confirmacion ───────────────────────────────────────────────────────────────
#
# crear_pedido/cambiar_condicion_pago_pedido/cancelar_pedido nunca se exponen directamente
# al modelo de voz en vivo (ver DIRECT_TOOLS arriba, no las incluye). En su lugar, el
# modelo solo puede "preparar" (cotizar sin escribir) y despues "confirmar" un borrador ya
# preparado. La regla anti-confirmacion-en-el-mismo-turno es necesaria porque
# gpt-realtime-2+ soporta tool calls paralelas/asincronas por defecto — sin este chequeo
# el modelo podria preparar y confirmar en la misma respuesta.

_PENDING = {}
_PENDING_LOCK = threading.Lock()

_ACCIONES_REALES = {
    "crear_pedido": chatbot.crear_pedido,
    "cambiar_condicion_pago_pedido": chatbot.cambiar_condicion_pago_pedido,
    "cancelar_pedido": chatbot.cancelar_pedido,
}

PREPARAR_DISPATCH = {
    "preparar_pedido": (chatbot.previsualizar_pedido, "crear_pedido"),
    "preparar_cambio_condicion_pago": (chatbot.previsualizar_cambio_condicion, "cambiar_condicion_pago_pedido"),
    "preparar_cancelacion_pedido": (chatbot.previsualizar_cancelacion, "cancelar_pedido"),
}


def _purgar_vencidos_locked():
    """Debe llamarse ya dentro de _PENDING_LOCK."""
    ahora = time.time()
    vencidos = [
        cid for cid, e in _PENDING.items()
        if e["estado"] == "pendiente" and ahora - e["created_at"] > REALTIME_PENDING_TTL_S
    ]
    for cid in vencidos:
        del _PENDING[cid]
    if len(_PENDING) > _MAX_PENDING:
        exceso = len(_PENDING) - _MAX_PENDING
        for cid in sorted(_PENDING, key=lambda c: _PENDING[c]["created_at"])[:exceso]:
            _PENDING.pop(cid, None)


def _crear_borrador(session_id: str, accion: str, commit_args: dict, resumen: str, user_turn_seq: int) -> dict:
    with _PENDING_LOCK:
        _purgar_vencidos_locked()
        # Un borrador nuevo para la misma (sesion, accion) reemplaza al anterior: evita
        # confirmar un borrador viejo si el vendedor cambio algo a mitad de camino.
        for entry in _PENDING.values():
            if entry["session_id"] == session_id and entry["accion"] == accion and entry["estado"] == "pendiente":
                entry["estado"] = "superseded"
        confirmation_id = uuid.uuid4().hex[:12]
        entry = {
            "confirmation_id": confirmation_id,
            "session_id": session_id,
            "accion": accion,
            "commit_args": commit_args,
            "resumen": resumen,
            "created_at": time.time(),
            "created_at_turn": user_turn_seq,
            "estado": "pendiente",
        }
        _PENDING[confirmation_id] = entry
        return entry


def _confirmar(confirmation_id: str, session_id: str, user_turn_seq: int, ui_confirmed: bool = False):
    # OJO: a proposito NO se llama a _purgar_vencidos_locked() aca — si purgara aca, un
    # borrador vencido desaparecería del dict ANTES de llegar al chequeo explícito de TTL
    # de abajo, y el vendedor vería el mensaje generico de "no encuentro ese borrador" en
    # vez del mensaje especifico de "vencio" (menos accionable). El purgado global de
    # entradas viejas ocurre en _crear_borrador, que es donde realmente hace falta acotar
    # el crecimiento del dict.
    with _PENDING_LOCK:
        entry = _PENDING.get(confirmation_id)

        if not entry or entry["session_id"] != session_id:
            return "No encuentro ese borrador. Puede haber vencido o ser de otra conversacion — volvé a prepararlo.", {}
        if entry["estado"] == "confirmado":
            return "Esa accion ya fue confirmada y ejecutada antes. No hace falta repetirla.", {}
        if entry["estado"] == "descartado":
            return "Ese borrador fue descartado. Si el vendedor todavia lo quiere, preparalo de nuevo.", {}
        if entry["estado"] == "superseded":
            return "Ese borrador quedo reemplazado por uno mas reciente. Confirma el ultimo borrador que se preparo.", {}
        if entry["estado"] == "error":
            return "La confirmacion anterior de este borrador fallo del lado del sistema. Preparalo de nuevo desde cero.", {}
        if time.time() - entry["created_at"] > REALTIME_PENDING_TTL_S:
            del _PENDING[confirmation_id]
            return "Ese borrador vencio (paso demasiado tiempo). Volvé a prepararlo con los mismos datos.", {}
        if user_turn_seq <= entry["created_at_turn"]:
            return (
                "No se puede confirmar en el mismo turno en que se preparo la accion. Leele el "
                "resumen completo al vendedor, esperá su confirmacion verbal explicita, y recien "
                "en tu proxima respuesta llama a confirmar_accion."
            ), {}
        if REALTIME_UI_CONFIRM and not ui_confirmed:
            return "Falta la confirmacion en pantalla del vendedor. Pedile que toque el boton de confirmar en su celular.", {}

        # Marcar confirmado ANTES de ejecutar: bajo tool calls paralelas/duplicadas, la
        # segunda llamada cae en el chequeo de arriba ("ya fue confirmada") en vez de
        # volver a escribir en la base.
        entry["estado"] = "confirmado"
        tool = _ACCIONES_REALES[entry["accion"]]
        try:
            output = tool.invoke(entry["commit_args"])
        except Exception as e:
            entry["estado"] = "error"
            print(f"[realtime confirmar_accion] {entry['accion']} fallo: {e}")
            return (
                "Se intento confirmar pero la ejecucion fallo del lado del sistema. Decile al "
                "vendedor que hubo un problema y que hay que prepararlo de nuevo."
            ), {}
        return output, {}


def _descartar(confirmation_id: str, session_id: str, motivo: str = ""):
    with _PENDING_LOCK:
        entry = _PENDING.get(confirmation_id)
        if not entry or entry["session_id"] != session_id:
            return "No encuentro ese borrador (puede haber vencido).", {}
        if entry["estado"] == "pendiente":
            entry["estado"] = "descartado"
        return "Borrador descartado, no se registro ningun cambio.", {}


def limpiar_borradores_de_sesion(session_id: str) -> None:
    """Llamada opcional desde POST /api/realtime/end al cerrar una sesion."""
    with _PENDING_LOCK:
        for cid, entry in list(_PENDING.items()):
            if entry["session_id"] == session_id and entry["estado"] == "pendiente":
                entry["estado"] = "descartado"


# ── Dispatch principal — llamado por backend.py: POST /api/realtime/tool-call ─────────

def ejecutar_tool(name: str, arguments: dict, session_id: str, user_turn_seq: int, ui_confirmed: bool = False):
    """Ejecuta una tool-call del modelo de voz en vivo. Devuelve (output_str, meta_dict).
    Nunca levanta: cualquier excepcion se traduce a un mensaje en español para que el
    modelo pueda seguir la conversacion en vez de quedar con un call_id sin resolver."""
    try:
        if name in PREPARAR_DISPATCH:
            preview_fn, accion = PREPARAR_DISPATCH[name]
            preview = preview_fn(**arguments)
            if not preview.get("ok"):
                return preview.get("error", "No se pudo preparar la accion."), {}
            draft = _crear_borrador(session_id, accion, preview["commit_args"], preview["resumen"], user_turn_seq)
            output = (
                f"{preview['resumen']} confirmation_id={draft['confirmation_id']}. Leele este resumen "
                "completo al vendedor y esperá que lo confirme en voz antes de llamar a confirmar_accion."
            )
            return _truncar_para_voz(output), {
                "draft": {"confirmation_id": draft["confirmation_id"], "resumen": preview["resumen"]}
            }

        if name == "confirmar_accion":
            return _confirmar(
                arguments.get("confirmation_id", ""), session_id, user_turn_seq,
                ui_confirmed=bool(arguments.get("ui_confirmed", ui_confirmed)),
            )

        if name == "descartar_accion":
            return _descartar(arguments.get("confirmation_id", ""), session_id, arguments.get("motivo", ""))

        if name in DIRECT_TOOLS:
            output = DIRECT_TOOLS[name].invoke(arguments)
            return _truncar_para_voz(output), {}

        return f"La herramienta '{name}' no esta disponible en el modo de voz en vivo.", {}

    except mysql.connector.Error as e:
        print(f"[realtime tool db error] {name}: {e}")
        return (
            "No pude consultar el sistema en este momento (error de base de datos). "
            "Decile al vendedor que reintente en unos segundos."
        ), {}
    except Exception as e:
        print(f"[realtime tool error] {name}: {e}")
        return f"Hubo un error inesperado ejecutando '{name}'. Decile al vendedor que reintente.", {}
