import { useState, useRef, useEffect } from "react"

// Modo "Voz en vivo" (OpenAI Realtime API) — experimental, mobile-only, convive con el
// modo de voz clasico (VAD + /api/transcribe + /api/speak) sin reemplazarlo. El audio va
// directo browser<->OpenAI por WebRTC; este hook solo se ocupa de la señalizacion (pedir
// el token efimero, negociar la conexion) y del puente de tool-calls hacia el backend
// (ver backend.py: /api/realtime/session, /api/realtime/tool-call, /api/realtime/end).
//
// Convencion de refs-espejo: igual que ChatPanel.jsx (voiceModeRef), los valores que se
// necesitan dentro de callbacks async/eventos del data channel se leen desde refs, nunca
// directamente del estado de React — ya mordio un bug de stale-closure en este proyecto.

const SILENT_AUDIO = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA="

export default function useRealtimeVoice({
  api, sessionId, cfg,
  onUserTranscript, onAssistantTranscript, onStatus, onPedido, onDraft, onError,
}) {
  const [phase, setPhase] = useState("idle") // idle|connecting|listening|thinking|speaking|error
  const [needsAudioTap, setNeedsAudioTap] = useState(false)
  const [muted, setMuted] = useState(false)

  const audioElRef = useRef(null) // <audio> remoto (WebRTC), lo renderiza ChatPanel.jsx
  const pcRef = useRef(null)
  const dcRef = useRef(null)
  const micStreamRef = useRef(null)
  const activeRef = useRef(false)
  const mutedRef = useRef(false)
  const phaseRef = useRef("idle")
  const userTurnSeqRef = useRef(0)
  const pendingArgsRef = useRef(new Map())     // call_id -> deltas de argumentos acumulados
  const outstandingCallsRef = useRef(new Set()) // call_ids de un lote de tool-calls en curso
  const lastAssistantItemIdRef = useRef(null)
  const audioStartedAtRef = useRef(0)
  const maxSessionTimerRef = useRef(null)
  const connectTimeoutRef = useRef(null)
  const assistantTranscriptRef = useRef("")
  const responseActiveRef = useRef(false) // hay una response en curso del lado de OpenAI

  const sessionIdRef = useRef(sessionId)
  useEffect(() => { sessionIdRef.current = sessionId }, [sessionId])
  const cfgRef = useRef(cfg)
  useEffect(() => { cfgRef.current = cfg }, [cfg])

  // Los callbacks del padre se guardan en un ref actualizado en cada render (no hace
  // falta useEffect: es solo una asignacion de objeto, sin efectos colaterales).
  const callbacksRef = useRef({})
  callbacksRef.current = { onUserTranscript, onAssistantTranscript, onStatus, onPedido, onDraft, onError }

  function setPhaseBoth(p) {
    phaseRef.current = p
    setPhase(p)
  }

  function cleanupConnection() {
    if (maxSessionTimerRef.current) { clearTimeout(maxSessionTimerRef.current); maxSessionTimerRef.current = null }
    if (connectTimeoutRef.current) { clearTimeout(connectTimeoutRef.current); connectTimeoutRef.current = null }
    try { dcRef.current?.close() } catch (_) {}
    dcRef.current = null
    try { pcRef.current?.getSenders().forEach(s => s.track?.stop()) } catch (_) {}
    try { pcRef.current?.close() } catch (_) {}
    pcRef.current = null
    try { micStreamRef.current?.getTracks().forEach(t => t.stop()) } catch (_) {}
    micStreamRef.current = null
    const el = audioElRef.current
    if (el) { try { el.pause() } catch (_) {}; el.srcObject = null }
    pendingArgsRef.current.clear()
    outstandingCallsRef.current.clear()
    lastAssistantItemIdRef.current = null
    userTurnSeqRef.current = 0
    assistantTranscriptRef.current = ""
    responseActiveRef.current = false
    mutedRef.current = false
    setMuted(false)
  }

  function handleDisconnect() {
    if (!activeRef.current) return
    activeRef.current = false
    setPhaseBoth("error")
    callbacksRef.current.onError?.("Se cortó la conexión de voz en vivo.")
    cleanupConnection()
  }

  // Barge-in: la deteccion de que el vendedor empezo a hablar es automatica del lado de
  // OpenAI (turn_detection), pero cortar el audio ya en curso y avisarle al modelo que
  // no termino de decir lo que iba a decir NO lo es — hay que pedirlo explicitamente.
  function truncateAssistant() {
    const dc = dcRef.current
    if (!dc || dc.readyState !== "open") return
    // Solo cancelar si hay una response realmente en curso — si no, OpenAI devuelve un
    // evento de error ("no active response found") que no aporta nada util al usuario.
    if (responseActiveRef.current) {
      try { dc.send(JSON.stringify({ type: "response.cancel" })) } catch (_) {}
    }
    const itemId = lastAssistantItemIdRef.current
    if (itemId) {
      const audioMs = Math.max(0, Date.now() - (audioStartedAtRef.current || Date.now()))
      try {
        dc.send(JSON.stringify({
          type: "conversation.item.truncate",
          item_id: itemId, content_index: 0, audio_end_ms: audioMs,
        }))
      } catch (_) {}
    }
  }

  // Ejecuta contra el backend una tool-call que pidio el modelo, y le devuelve el
  // resultado por el data channel. Nunca deja un call_id sin resolver: si el fetch
  // falla, igual se manda un function_call_output con un mensaje de error en español.
  async function runToolCall({ name, call_id, argsJson }) {
    let parsedArgs = {}
    try { parsedArgs = argsJson ? JSON.parse(argsJson) : {} } catch (_) { parsedArgs = {} }

    let output
    try {
      const r = await fetch(`${api}/realtime/tool-call`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionIdRef.current, call_id, name, arguments: parsedArgs,
          user_turn_seq: userTurnSeqRef.current, ui_confirmed: false,
        }),
      })
      const data = await r.json()
      output = data.output ?? "Error inesperado ejecutando la herramienta."
      if (data.meta?.pedido) callbacksRef.current.onPedido?.(data.meta.pedido)
      if (data.meta?.draft) callbacksRef.current.onDraft?.(data.meta.draft)
    } catch (e) {
      output = "No pude conectarme al sistema. Decile al vendedor que hubo un problema de conexión y reintentá."
    }

    outstandingCallsRef.current.delete(call_id)
    const dc = dcRef.current
    if (dc && dc.readyState === "open") {
      try {
        dc.send(JSON.stringify({
          type: "conversation.item.create",
          item: { type: "function_call_output", call_id, output },
        }))
        // Agrupa llamadas paralelas: recien pide que el modelo retome cuando el lote
        // completo de tool-calls pendientes de esta respuesta ya se resolvio. Si por
        // alguna razon ya hay una response en curso (p.ej. el server_vad/semantic_vad
        // disparo una sola), no hace falta pedir otra.
        if (outstandingCallsRef.current.size === 0 && !responseActiveRef.current) {
          dc.send(JSON.stringify({ type: "response.create" }))
        }
      } catch (_) {}
    }
  }

  function handleServerEvent(msg) {
    switch (msg.type) {
      case "response.created": {
        responseActiveRef.current = true
        break
      }
      case "input_audio_buffer.speech_started": {
        userTurnSeqRef.current += 1
        if (phaseRef.current === "speaking") truncateAssistant()
        setPhaseBoth("listening")
        break
      }
      case "conversation.item.input_audio_transcription.completed": {
        const t = (msg.transcript || "").trim()
        if (t) callbacksRef.current.onUserTranscript?.(t)
        break
      }
      case "response.output_audio_transcript.delta":
      case "response.audio_transcript.delta": {
        assistantTranscriptRef.current += (msg.delta || "")
        break
      }
      case "response.output_audio_transcript.done":
      case "response.audio_transcript.done": {
        const full = (msg.transcript || assistantTranscriptRef.current || "").trim()
        assistantTranscriptRef.current = ""
        if (full) callbacksRef.current.onAssistantTranscript?.(full)
        setPhaseBoth("speaking")
        break
      }
      case "response.output_item.added": {
        const item = msg.item || {}
        if (item.id) lastAssistantItemIdRef.current = item.id
        audioStartedAtRef.current = Date.now()
        break
      }
      case "response.function_call_arguments.delta": {
        const cid = msg.call_id
        const prev = pendingArgsRef.current.get(cid) || ""
        pendingArgsRef.current.set(cid, prev + (msg.delta || ""))
        break
      }
      case "response.function_call_arguments.done": {
        const cid = msg.call_id
        const name = msg.name
        const argsJson = msg.arguments ?? pendingArgsRef.current.get(cid) ?? "{}"
        pendingArgsRef.current.delete(cid)
        outstandingCallsRef.current.add(cid)
        const label = cfgRef.current?.tool_status?.[name]
        if (label) callbacksRef.current.onStatus?.(label)
        setPhaseBoth("thinking")
        runToolCall({ name, call_id: cid, argsJson })
        break
      }
      case "response.done": {
        responseActiveRef.current = false
        if (msg.response?.status === "failed") {
          console.error("[realtime] response fallida:", msg.response?.status_details)
        }
        callbacksRef.current.onStatus?.("")
        if (outstandingCallsRef.current.size === 0 && phaseRef.current !== "speaking") {
          setPhaseBoth("listening")
        }
        break
      }
      case "error": {
        // Eventos de error "blandos" (cancelar una response que ya termino, truncar mas
        // audio del que en realidad sono) son esperables por las condiciones de carrera
        // normales de una conversacion hablada — se loggean pero NO se muestran como
        // burbuja de error al usuario, para no llenar la pantalla de ruido. Los errores
        // que sí importan (falla de conexion, sesion caida) se manejan en start()/
        // handleDisconnect(), que sí llaman a onError.
        console.error("[realtime] evento de error (no fatal):", msg.error)
        break
      }
      default:
        break
    }
  }

  async function start() {
    if (activeRef.current) return
    activeRef.current = true
    setNeedsAudioTap(false)
    setPhaseBoth("connecting")
    const t0 = Date.now()
    const mark = (label) => console.log(`[realtime] ${label}: +${Date.now() - t0}ms`)
    try {
      // 1. Desbloquear el <audio> remoto en iOS: un play() real dentro de este click.
      const audioEl = audioElRef.current
      if (audioEl) {
        try {
          audioEl.muted = true
          audioEl.src = SILENT_AUDIO
          await audioEl.play().catch(() => {})
          audioEl.pause()
          audioEl.removeAttribute("src")
          audioEl.muted = false
        } catch (_) {}
      }
      mark("audio desbloqueado")

      // 2. Microfono y 3. token efimero en PARALELO — son independientes entre si, y
      // esperarlos en serie (como antes) suma la latencia de ambos en vez de la del mas
      // lento. El backend nunca ve el audio, solo emite el token.
      const [stream, res] = await Promise.all([
        navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        }).then(s => { mark("microfono listo"); return s }),
        fetch(`${api}/realtime/session`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionIdRef.current }),
        }).then(r => { mark("token recibido"); return r }),
      ])
      if (!activeRef.current) { stream.getTracks().forEach(t => t.stop()); return }
      micStreamRef.current = stream

      const data = await res.json()
      if (!res.ok || data.error) throw new Error(data.detail || data.error || `HTTP ${res.status}`)
      const clientSecret = data.client_secret
      const model = data.model || cfgRef.current?.model

      // 4. WebRTC: mic hacia OpenAI, audio del modelo de vuelta, data channel de eventos.
      // ICE_SERVERS: sin STUN, RTCPeerConnection solo reune candidatos "host" (IP local).
      // Eso alcanza en una red hogar/oficina donde el NAT permite hairpin, pero en datos
      // moviles / NAT restrictivo (el caso mas probable en un evento) la conexion queda
      // "conectando" para siempre porque nunca hay un candidato usable. STUN publico
      // resuelve el caso comun; si el NAT es simetrico (frecuente en redes moviles/4G)
      // ni STUN alcanza y hace falta un TURN — no incluido aca por ahora.
      const pc = new RTCPeerConnection({
        iceServers: [
          { urls: "stun:stun.l.google.com:19302" },
          { urls: "stun:stun1.l.google.com:19302" },
        ],
      })
      pcRef.current = pc
      stream.getTracks().forEach(t => pc.addTrack(t, stream))
      pc.ontrack = (e) => {
        const el = audioElRef.current
        if (!el) return
        el.srcObject = e.streams[0]
        el.play().catch(() => setNeedsAudioTap(true))
      }
      const dc = pc.createDataChannel("oai-events")
      dcRef.current = dc
      dc.onmessage = (e) => {
        try { handleServerEvent(JSON.parse(e.data)) } catch (_) {}
      }
      dc.onopen = () => {
        if (!activeRef.current) return
        mark("data channel abierto (listo para hablar)")
        if (connectTimeoutRef.current) { clearTimeout(connectTimeoutRef.current); connectTimeoutRef.current = null }
        setPhaseBoth("listening")
      }
      pc.oniceconnectionstatechange = () => {
        console.log("[realtime] iceConnectionState:", pc.iceConnectionState)
        const st = pc.iceConnectionState
        if ((st === "failed" || st === "disconnected" || st === "closed") && activeRef.current) {
          handleDisconnect()
        }
      }
      pc.onconnectionstatechange = () => {
        console.log("[realtime] connectionState:", pc.connectionState)
        if (pc.connectionState === "failed" && activeRef.current) handleDisconnect()
      }

      const offer = await pc.createOffer()
      await pc.setLocalDescription(offer)
      mark("oferta SDP lista, enviando a OpenAI")

      const sdpRes = await fetch(`https://api.openai.com/v1/realtime/calls?model=${encodeURIComponent(model)}`, {
        method: "POST",
        headers: { "Authorization": `Bearer ${clientSecret}`, "Content-Type": "application/sdp" },
        body: offer.sdp,
      })
      if (!sdpRes.ok) throw new Error(`HTTP ${sdpRes.status} negociando la conexion de voz`)
      const answerSdp = await sdpRes.text()
      mark("respuesta SDP de OpenAI recibida")
      await pc.setRemoteDescription({ type: "answer", sdp: answerSdp })
      mark("respuesta SDP aplicada, esperando ICE/data channel")

      // Reloj de guardia: si el data channel no abre en 15s (ICE nunca conecta — la red
      // no deja pasar UDP, NAT simetrico, etc.), cortar en vez de dejar "Conectando..."
      // colgado para siempre.
      connectTimeoutRef.current = setTimeout(() => {
        if (activeRef.current && phaseRef.current === "connecting") {
          activeRef.current = false
          setPhaseBoth("error")
          callbacksRef.current.onError?.(
            "No se pudo establecer la conexión de voz en vivo (probablemente la red bloquea la conexión). Probá con otra red/WiFi."
          )
          cleanupConnection()
        }
      }, 15000)

      const maxMs = cfgRef.current?.max_session_ms || 10 * 60 * 1000
      maxSessionTimerRef.current = setTimeout(() => {
        callbacksRef.current.onError?.("Sesión de voz en vivo finalizada por tiempo.")
        stop()
      }, maxMs)
    } catch (e) {
      activeRef.current = false
      setPhaseBoth("error")
      callbacksRef.current.onError?.(e.message || String(e))
      cleanupConnection()
    }
  }

  async function stop() {
    const wasActive = activeRef.current
    activeRef.current = false
    cleanupConnection()
    setPhaseBoth("idle")
    setNeedsAudioTap(false)
    if (wasActive && sessionIdRef.current) {
      fetch(`${api}/realtime/end`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionIdRef.current }),
      }).catch(() => {})
    }
  }

  function enableAudio() {
    const el = audioElRef.current
    if (!el) return
    el.play().then(() => setNeedsAudioTap(false)).catch(() => {})
  }

  function toggleMute() {
    const stream = micStreamRef.current
    if (!stream) return
    mutedRef.current = !mutedRef.current
    stream.getAudioTracks().forEach(t => { t.enabled = !mutedRef.current })
    setMuted(mutedRef.current)
  }

  // Escotilla de prueba para dev: permite ejercitar el modelo real, el data channel, el
  // puente de tools y el gate de confirmacion mandando TEXTO en vez de audio (los
  // dispositivos de audio falso de Playwright/Chromium no producen habla real).
  function sendText(text) {
    const dc = dcRef.current
    if (!dc || dc.readyState !== "open") return
    userTurnSeqRef.current += 1
    dc.send(JSON.stringify({
      type: "conversation.item.create",
      item: { type: "message", role: "user", content: [{ type: "input_text", text }] },
    }))
    if (!responseActiveRef.current) {
      dc.send(JSON.stringify({ type: "response.create" }))
    }
  }

  useEffect(() => {
    if (import.meta.env.DEV) {
      window.__rt = { sendText, toggleMute, get phase() { return phaseRef.current } }
    }
    return () => { activeRef.current = false; cleanupConnection() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return { phase, needsAudioTap, muted, audioElRef, start, stop, enableAudio, toggleMute, sendText }
}
