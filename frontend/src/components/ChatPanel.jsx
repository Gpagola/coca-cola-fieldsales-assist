import { useState, useEffect, useRef } from "react"
import ReactMarkdown from "react-markdown"
import { EvaluationModal } from "./EvaluationCard"
import RadarChart from "./RadarChart"
import RetentionGauge from "./RetentionGauge"
import SentimentLine from "./SentimentLine"
import useRealtimeVoice from "./useRealtimeVoice"
import "./ChatPanel.css"

const API = import.meta.env.VITE_API_URL || "/api"

const ACCEPTED = ".pdf,.jpg,.jpeg,.png,.webp"

// Deteccion de voz (VAD) del modo audio manos-libres.
const VAD_THRESHOLD     = 0.02   // nivel RMS por encima del cual se considera que hay voz
const VAD_SILENCE_MS    = 1100   // silencio sostenido que marca el fin de lo que dijiste -> envia
const VAD_MIN_SPEECH_MS = 350    // duracion minima de voz para no enviar ruidos/golpes


export default function ChatPanel({ onLoadingChange, onNewCase, showEval = false, initialClienteCodigo = null, enableVoice = false }) {
  const [sessionId, setSessionId]   = useState(null)
  const [messages, setMessages]     = useState([])
  const [input, setInput]           = useState("")
  const [loading, setLoading]       = useState(false)
  const [pedido, setPedido]             = useState(null)
  const [attachedFile, setAttachedFile] = useState(null)
  const [isStreaming, setIsStreaming]   = useState(false)
  const [suggestions, setSuggestions]   = useState([])
  const [agentStatus, setAgentStatus]   = useState("")
  const [evaluating, setEvaluating]     = useState(false)
  const [evaluation, setEvaluation]     = useState(null)
  const [ended, setEnded]               = useState(false)
  const [showOptPrompt, setShowOptPrompt] = useState(false)
  const [riskProfile, setRiskProfile]     = useState(null)
  const [retention, setRetention]         = useState(null)
  const [sentimentPts, setSentimentPts]   = useState([])
  const [voiceMode, setVoiceMode]         = useState(false)
  const [voicePhase, setVoicePhase]       = useState("off") // off|listening|sending|responding|muted
  // Modo "Voz en vivo" (OpenAI Realtime API) — experimental, convive con voiceMode de arriba.
  const [rtEnabled, setRtEnabled]         = useState(false)
  const [rtCfg, setRtCfg]                 = useState(null)
  const [rtActive, setRtActive]           = useState(false)
  const [rtDraft, setRtDraft]             = useState(null)
  const rtActiveRef = useRef(false)
  const bottomRef    = useRef(null)
  const textareaRef  = useRef(null)
  const abortRef     = useRef(null)
  const fileInputRef = useRef(null)
  const genRef       = useRef(0)
  const messagesRef  = useRef([])
  const pedidoRef    = useRef(null)
  const mediaRecorderRef = useRef(null)
  const audioChunksRef   = useRef([])
  const mediaStreamRef   = useRef(null)
  const speechQueueRef   = useRef(Promise.resolve())
  const ttsAudioRef      = useRef(null)
  const currentAudioCleanupRef = useRef(null)
  // Modo voz manos-libres (VAD)
  const audioCtxRef      = useRef(null)
  const analyserRef      = useRef(null)
  const vadRafRef        = useRef(0)
  const listeningRef     = useRef(false)   // el VAD esta capturando activamente
  const mutedRef         = useRef(false)   // el usuario puso pausa/mute
  const voiceModeRef     = useRef(false)   // espejo de voiceMode para callbacks async
  const speakingRef      = useRef(false)   // hay voz en curso (grabando)
  const silenceStartRef  = useRef(0)
  const speechStartRef   = useRef(0)
  const recMimeRef       = useRef("")

  // Wav silencioso minimo, usado solo para "desbloquear" el autoplay de
  // <audio> en iOS Safari (requiere un play() real dentro de un gesto del
  // usuario). El audio real de la voz llega despues, de forma asincronica
  // (grabar -> transcribir -> enviar -> stream de la respuesta).
  const SILENT_AUDIO = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA="

  // Un UNICO elemento <audio> reutilizado para toda la voz. iOS Safari solo
  // permite reproducir sin gesto del usuario un elemento que ya fue
  // "desbloqueado" antes con un play() dentro de un gesto; crear un
  // new Audio() por cada oracion hacia que iOS bloqueara intermitentemente
  // (algunas respuestas sonaban y otras no). Reusando el mismo elemento
  // desbloqueado y solo cambiando su .src, la reproduccion es confiable.
  function getTtsAudio() {
    if (!ttsAudioRef.current) ttsAudioRef.current = new Audio()
    return ttsAudioRef.current
  }

  function unlockAudio() {
    const audio = getTtsAudio()
    audio.src = SILENT_AUDIO
    audio.play().then(() => audio.pause()).catch(() => {})
  }

  // Corta lo que se este reproduciendo y vacia los turnos de habla pendientes.
  function stopSpeech() {
    const audio = ttsAudioRef.current
    if (audio) { audio.onended = null; audio.onerror = null; audio.pause() }
    currentAudioCleanupRef.current?.() // resolver la reproduccion pendiente (para no colgar awaits)
    speechQueueRef.current = Promise.resolve()
  }

  // ── Modo "Voz en vivo" (OpenAI Realtime API) — toggle experimental ──────────
  // Convive con el modo de voz clasico (VAD) de arriba, mutuamente excluyente con el.
  const rt = useRealtimeVoice({
    api: API,
    sessionId,
    cfg: rtCfg,
    onUserTranscript: (text) => setMessages(prev => [...prev, { role: "user", content: text }]),
    onAssistantTranscript: (text) => { setRtDraft(null); setMessages(prev => [...prev, { role: "assistant", content: text }]) },
    onStatus: setAgentStatus,
    onPedido: setPedido,
    onDraft: (d) => setRtDraft(d),
    onError: (msg) => setMessages(prev => [...prev, { role: "assistant", content: `⚠️ ${msg}` }]),
  })

  async function handleRealtimeToggle() {
    if (rtActive) {
      rtActiveRef.current = false
      setRtActive(false)
      setRtDraft(null)
      await rt.stop()
    } else {
      if (voiceMode) exitVoiceMode() // exclusion mutua: solo un modo de voz activo a la vez
      rtActiveRef.current = true
      setRtActive(true)
      await rt.start()
    }
  }

  async function streamChat(message, sessionId, controller, onToken, onStatus, onPedido, onSuggestions, onCierre, onRiskProfile) {
    const res = await fetch(`${API}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId, voice_mode: enableVoice && voiceModeRef.current }),
      signal: controller.signal,
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ""

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split("\n")
      buffer = lines.pop()
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue
        const data = line.slice(6)
        if (data === "[DONE]") return
        try {
          const parsed = JSON.parse(data)
          if (parsed.error) throw new Error(parsed.error)
          if (parsed.status) onStatus?.(parsed.status)
          if (parsed.pedido) onPedido?.(parsed.pedido)
          if (parsed.suggestions) onSuggestions?.(parsed.suggestions)
          if (parsed.risk_profile) onRiskProfile?.(parsed.risk_profile)
          if (parsed.cierre) onCierre?.()
          if (parsed.token) { onStatus?.(""); onToken(parsed.token) }
        } catch (e) {
          if (e.message !== "SyntaxError") throw e
        }
      }
    }
  }

  async function evaluateChat(currentMessages, currentPedido) {
    setEvaluating(true)
    setEvaluation(null)
    try {
      const r = await fetch(`${API}/chat/evaluate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: currentMessages, pedido: currentPedido }),
      })
      const data = await r.json()
      if (data.error) throw new Error(data.error)
      setEvaluation(data)
    } catch (e) {
      console.error("[evaluate]", e)
    } finally {
      setEvaluating(false)
    }
  }

  function handleFin() {
    abortRef.current?.abort()
    setEnded(true)
    setShowOptPrompt(true)
  }

  function handleOptimize() {
    setShowOptPrompt(false)
    evaluateChat(messagesRef.current, pedidoRef.current)
  }

  function handleSkipOptimize() {
    setShowOptPrompt(false)
    onNewCase?.()
  }

  function handleModalClose() {
    onNewCase?.()                     // resetea ChatPanel via key change en App
  }

  useEffect(() => {
    async function init() {
      const r = await fetch(`${API}/session/new`, { method: "POST" })
      const { session_id, realtime } = await r.json()
      setSessionId(session_id)
      if (realtime) { setRtEnabled(!!realtime.enabled); setRtCfg(realtime) }
      setMessages([{
        role: "assistant",
        content: initialClienteCodigo
          ? `¡Hola! Soy tu asistente de venta en terreno. Ya tengo cargada la cuenta ${initialClienteCodigo}, dame un segundo para traer su ficha.`
          : "¡Hola! Soy tu asistente de venta en terreno de Coca-Cola. Decime el código de cliente (CLI-XXXX) o el nombre del comercio con el que vas a trabajar."
      }])

      if (initialClienteCodigo) {
        // Cuenta ya seleccionada desde el Portal Vendedor: la identificamos automáticamente.
        setLoading(true); onLoadingChange?.(true)
        const controller = new AbortController()
        abortRef.current = controller
        try {
          let accumulated = ""
          let started = false
          await streamChat(
            `Mi codigo de cliente es ${initialClienteCodigo}`, session_id, controller,
            (token) => {
              accumulated += token
              if (!started) {
                started = true
                setIsStreaming(true)
                setMessages(prev => [...prev, { role: "assistant", content: accumulated }])
              } else {
                setMessages(prev => {
                  const msgs = [...prev]
                  msgs[msgs.length - 1] = { role: "assistant", content: accumulated }
                  return msgs
                })
              }
            },
            setAgentStatus, setPedido, (s) => setSuggestions(s), undefined,
            (profile) => {
              setRiskProfile(profile)
              if (profile.resolucion != null) setRetention(profile.resolucion)
              if (profile.sentimiento != null) setSentimentPts(prev => [...prev, profile.sentimiento])
            }
          )
          speakChunk(accumulated)
        } catch (e) {
          if (e.name !== "AbortError")
            setMessages(prev => [...prev, { role: "assistant", content: `⚠️ Error: ${e.message}` }])
        } finally {
          setLoading(false); onLoadingChange?.(false)
          setIsStreaming(false)
          setAgentStatus("")
          abortRef.current = null
        }
      }
    }
    init()
  }, [])

  useEffect(() => {
    messagesRef.current = messages
  }, [messages])

  // Al desmontar el chat, soltar microfono / AudioContext / loop de VAD y cortar audio.
  useEffect(() => {
    return () => {
      voiceModeRef.current = false
      if (vadRafRef.current) cancelAnimationFrame(vadRafRef.current)
      try { mediaStreamRef.current?.getTracks().forEach(t => t.stop()) } catch (_) {}
      try { audioCtxRef.current?.close() } catch (_) {}
      const a = ttsAudioRef.current; if (a) a.pause()
      if (rtActiveRef.current) { rtActiveRef.current = false; rt.stop() }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    pedidoRef.current = pedido
  }, [pedido])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages, loading])

  useEffect(() => {
    if (!loading) textareaRef.current?.focus()
  }, [loading])

  useEffect(() => {
    if (!enableVoice) return
    return () => stopSpeech()
  }, [enableVoice])

  function stripForSpeech(text) {
    return text
      .replace(/\*\*(.*?)\*\*/g, "$1")
      .replace(/\*(.*?)\*/g, "$1")
      .replace(/^#+\s*/gm, "")
      .replace(/^[-*]\s+/gm, "")
      .replace(/`/g, "")
      .replace(/\p{Emoji_Presentation}/gu, "")
      .trim()
  }

  // Habla un fragmento de texto. La voz la genera el backend con OpenAI TTS
  // (mucho mas natural que la nativa del navegador). Los fragmentos se encolan
  // en speechQueueRef para reproducirse en orden, pero se piden (fetch) sin
  // esperar a que el anterior termine de sonar, asi el siguiente ya esta listo
  // cuando llega su turno.
  // onStart (opcional) se dispara EN EL INSTANTE en que la voz empieza a
  // reproducirse — lo usamos para recien ahi revelar el texto en pantalla,
  // asi el audio va un paso adelante del texto en modo voz.
  function speakChunk(text, onStart) {
    if (!enableVoice || !voiceModeRef.current) { onStart?.(); return }
    const clean = stripForSpeech(text)
    if (!clean.trim()) { onStart?.(); return }
    const fetchPromise = fetch(`${API}/speak`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: clean }),
    }).then(res => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return res.blob()
    })
    speechQueueRef.current = speechQueueRef.current
      .then(() => fetchPromise)
      .then(blob => playAudioBlob(blob, onStart))
      .catch(() => { onStart?.() }) // si falla el TTS, revelar el texto igual y no trabar la cola
  }

  function playAudioBlob(blob, onStart) {
    return new Promise((resolve) => {
      const url = URL.createObjectURL(blob)
      const audio = getTtsAudio() // reusar el elemento ya desbloqueado (ver getTtsAudio)
      const cleanup = () => {
        URL.revokeObjectURL(url); audio.onended = null; audio.onerror = null
        currentAudioCleanupRef.current = null; resolve()
      }
      currentAudioCleanupRef.current = cleanup
      audio.onended = cleanup
      audio.onerror = () => { onStart?.(); cleanup() }
      audio.src = url
      audio.play().then(() => onStart?.()).catch(() => { onStart?.(); cleanup() })
    })
  }

  // ── Modo audio manos-libres (VAD) ────────────────────────────────────────
  // Boton mic (texto) -> entra en modo audio; tecladito -> vuelve a texto.
  async function handleModeToggle() {
    if (voiceMode) exitVoiceMode()
    else await enterVoiceMode()
  }

  async function enterVoiceMode() {
    if (rtActiveRef.current) { // exclusion mutua: solo un modo de voz activo a la vez
      rtActiveRef.current = false
      setRtActive(false)
      setRtDraft(null)
      await rt.stop()
    }
    unlockAudio()
    voiceModeRef.current = true
    mutedRef.current = false
    setVoiceMode(true)
    try {
      // Mono + cancelacion de ruido: mejor reconocimiento y menos eco del asistente.
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      })
      mediaStreamRef.current = stream
      const Ctx = window.AudioContext || window.webkitAudioContext
      const ctx = new Ctx()
      if (ctx.state === "suspended") await ctx.resume()
      const source = ctx.createMediaStreamSource(stream)
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 2048
      source.connect(analyser)
      audioCtxRef.current = ctx
      analyserRef.current = analyser
      startListening()
      vadRafRef.current = requestAnimationFrame(vadTick)
    } catch (e) {
      setMessages(prev => [...prev, { role: "assistant", content: "⚠️ No se pudo acceder al micrófono. Revisá los permisos del navegador." }])
      exitVoiceMode()
    }
  }

  function exitVoiceMode() {
    voiceModeRef.current = false
    listeningRef.current = false
    cancelUtterance()
    stopSpeech()
    if (vadRafRef.current) { cancelAnimationFrame(vadRafRef.current); vadRafRef.current = 0 }
    mediaStreamRef.current?.getTracks().forEach(t => t.stop())
    mediaStreamRef.current = null
    if (audioCtxRef.current) { audioCtxRef.current.close().catch(() => {}); audioCtxRef.current = null }
    analyserRef.current = null
    setVoiceMode(false)
    setVoicePhase("off")
  }

  // Empieza (o retoma) a escuchar en busca de voz.
  function startListening() {
    speakingRef.current = false
    silenceStartRef.current = 0
    listeningRef.current = true
    setVoicePhase("listening")
  }

  // Loop que mide el volumen del microfono: detecta cuando arrancas a hablar y,
  // sobre todo, cuando te quedas en silencio -> ahi corta y envia solo.
  function vadTick() {
    const analyser = analyserRef.current
    if (analyser && listeningRef.current) {
      const buf = new Uint8Array(analyser.fftSize)
      analyser.getByteTimeDomainData(buf)
      let sum = 0
      for (let i = 0; i < buf.length; i++) { const v = (buf[i] - 128) / 128; sum += v * v }
      const rms = Math.sqrt(sum / buf.length)
      const now = performance.now()
      if (rms > VAD_THRESHOLD) {
        if (!speakingRef.current) { speakingRef.current = true; speechStartRef.current = now; beginUtterance() }
        silenceStartRef.current = 0
      } else if (speakingRef.current) {
        if (!silenceStartRef.current) silenceStartRef.current = now
        else if (now - silenceStartRef.current > VAD_SILENCE_MS) {
          const dur = now - speechStartRef.current
          speakingRef.current = false
          silenceStartRef.current = 0
          endUtterance(dur >= VAD_MIN_SPEECH_MS)
        }
      }
    }
    if (voiceModeRef.current) vadRafRef.current = requestAnimationFrame(vadTick)
  }

  function beginUtterance() {
    const stream = mediaStreamRef.current
    if (!stream) return
    const mimeType = ["audio/webm", "audio/mp4", "audio/ogg"].find(t => MediaRecorder.isTypeSupported(t)) || ""
    recMimeRef.current = mimeType
    const recOpts = { audioBitsPerSecond: 24000 }
    if (mimeType) recOpts.mimeType = mimeType
    const rec = new MediaRecorder(stream, recOpts)
    audioChunksRef.current = []
    rec.ondataavailable = (e) => { if (e.data.size > 0) audioChunksRef.current.push(e.data) }
    mediaRecorderRef.current = rec
    rec.start()
  }

  // Cierra la toma actual; si send=true la transcribe y envia, si no la descarta.
  function endUtterance(send) {
    const rec = mediaRecorderRef.current
    mediaRecorderRef.current = null
    if (!rec || rec.state === "inactive") return
    const mimeType = recMimeRef.current
    if (send) { listeningRef.current = false; setVoicePhase("sending") }
    rec.onstop = () => {
      if (!send) return
      const blob = new Blob(audioChunksRef.current, { type: mimeType || "audio/webm" })
      transcribeAndSend(blob, mimeType)
    }
    try { rec.stop() } catch (_) {}
  }

  function cancelUtterance() {
    const rec = mediaRecorderRef.current
    mediaRecorderRef.current = null
    speakingRef.current = false
    silenceStartRef.current = 0
    if (rec && rec.state !== "inactive") { rec.onstop = null; try { rec.stop() } catch (_) {} }
  }

  async function transcribeAndSend(blob, mimeType) {
    try {
      const ext = mimeType.includes("mp4") ? "mp4" : mimeType.includes("ogg") ? "ogg" : "webm"
      const formData = new FormData()
      formData.append("audio", blob, `recording.${ext}`)
      const res = await fetch(`${API}/transcribe`, { method: "POST", body: formData })
      const data = await res.json()
      if (data.error) throw new Error(data.error)
      const said = data.text?.trim()
      if (said) {
        setVoicePhase("responding")
        await sendMessage(said)
        await speechQueueRef.current // esperar a que termine de sonar la respuesta
      }
    } catch (e) {
      setMessages(prev => [...prev, { role: "assistant", content: `⚠️ Error al transcribir el audio: ${e.message}` }])
    } finally {
      // Volver a escuchar solo, salvo que el usuario haya salido o puesto pausa.
      if (voiceModeRef.current && !mutedRef.current) startListening()
    }
  }

  // Boton del microfono dentro del modo audio = mutear / reanudar.
  function toggleMute() {
    if (mutedRef.current) {
      mutedRef.current = false
      startListening()
    } else {
      mutedRef.current = true
      listeningRef.current = false
      cancelUtterance()
      stopSpeech() // corta la voz del asistente si esta hablando
      setVoicePhase("muted")
    }
  }

  async function sendMessage(overrideText) {
    const text = (overrideText ?? input).trim()
    if ((!text && !attachedFile) || !sessionId) return

    // Abort any in-progress stream and claim this generation
    abortRef.current?.abort()
    const myGen = ++genRef.current

    const fileToSend = attachedFile
    setInput("")
    setAttachedFile(null)
    setSuggestions([])
    setEvaluation(null)
    if (fileInputRef.current) fileInputRef.current.value = ""

    // Placeholder local (blob URL) para que la miniatura aparezca al instante
    const localPreviewUrl = fileToSend ? URL.createObjectURL(fileToSend.file) : null
    const localAttachment = fileToSend
      ? {
          url:  localPreviewUrl,
          name: fileToSend.name,
          type: fileToSend.file.type?.startsWith("image/") ? "image" : "pdf",
        }
      : null

    setMessages(prev => [...prev, { role: "user", content: text, attachment: localAttachment }])
    setLoading(true); onLoadingChange?.(true)
    if (voiceModeRef.current) stopSpeech() // limpiar cualquier resto de la cola de un mensaje anterior

    const controller = new AbortController()
    abortRef.current = controller
    const userMsgIdx = messagesRef.current.length  // índice del mensaje que acabamos de añadir

    try {
      let finalMessage = text

      // Si hay archivo adjunto, subirlo primero
      if (fileToSend) {
        const formData = new FormData()
        formData.append("file", fileToSend.file)
        const uploadRes = await fetch(`${API}/upload`, {
          method: "POST",
          body: formData,
          signal: controller.signal,
        })
        const uploadData = await uploadRes.json()
        if (uploadData.error) throw new Error(uploadData.error)

        // Sustituir la URL local (blob) por la URL persistente del servidor
        if (uploadData.file_url) {
          const serverUrl = uploadData.file_url.startsWith("/api/")
            ? `${API}${uploadData.file_url.slice(4)}`
            : uploadData.file_url
          setMessages(prev => prev.map((m, i) => i === userMsgIdx && m.attachment
            ? { ...m, attachment: { ...m.attachment, url: serverUrl, type: uploadData.file_type || m.attachment.type } }
            : m
          ))
          if (localPreviewUrl) URL.revokeObjectURL(localPreviewUrl)
        }

        const docContext = `[Documento adjunto analizado: ${fileToSend.name}]\n\n${uploadData.contenido}`
        finalMessage = text ? `${docContext}\n\n${text}` : docContext
      }

      let accumulated = ""
      let started = false
      const voiceTurn = enableVoice && voiceModeRef.current

      const revealUpTo = (len) => {
        const shown = accumulated.slice(0, len)
        if (!started) {
          started = true
          setIsStreaming(true)
          setMessages(prev => [...prev, { role: "assistant", content: shown }])
        } else {
          setMessages(prev => {
            const msgs = [...prev]
            msgs[msgs.length - 1] = { role: "assistant", content: shown }
            return msgs
          })
        }
      }

      await streamChat(finalMessage, sessionId, controller, (token) => {
        accumulated += token
        // En modo texto el texto aparece a medida que llega. En modo voz NO se
        // muestra todavia: se revela recien cuando arranca el audio (abajo),
        // asi la voz siempre va un paso adelante de la pantalla.
        if (!voiceTurn) revealUpTo(accumulated.length)
      }, setAgentStatus, setPedido, (s) => setSuggestions(s), undefined, (profile) => {
        setRiskProfile(profile)
        if (profile.resolucion != null) setRetention(profile.resolucion)
        if (profile.sentimiento != null) setSentimentPts(prev => [...prev, profile.sentimiento])
      })

      if (voiceTurn) {
        // Respuesta completa (en modo voz es breve) en una sola pieza de audio;
        // el texto se revela en el instante en que empieza a sonar la voz.
        const reveal = () => revealUpTo(accumulated.length)
        if (stripForSpeech(accumulated).trim()) speakChunk(accumulated, reveal)
        else reveal()
      }
    } catch (e) {
      if (voiceModeRef.current) stopSpeech()
      if (e.name !== "AbortError")
        setMessages(prev => [...prev, { role: "assistant", content: `⚠️ Error: ${e.message}` }])
    } finally {
      // Only reset loading state if no newer message has taken over
      if (genRef.current === myGen) {
        setLoading(false); onLoadingChange?.(false)
        setIsStreaming(false)
        setAgentStatus("")
        abortRef.current = null
      }
    }
  }

  function handleStop() {
    abortRef.current?.abort()
    if (voiceModeRef.current) stopSpeech()
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  function handleInput(e) {
    setInput(e.target.value)
    const el = textareaRef.current
    el.style.height = "auto"
    el.style.height = Math.min(el.scrollHeight, 200) + "px"
  }

  function handleFileChange(e) {
    const file = e.target.files?.[0]
    if (file) {
      const isImage = file.type?.startsWith("image/")
      setAttachedFile({
        file,
        name: file.name,
        previewUrl: URL.createObjectURL(file),
        type: isImage ? "image" : "pdf",
      })
    }
  }

  function removeAttachment() {
    if (attachedFile?.previewUrl) URL.revokeObjectURL(attachedFile.previewUrl)
    setAttachedFile(null)
    if (fileInputRef.current) fileInputRef.current.value = ""
  }

  function formatAssistantMsg(text) {
    // 1. Texto entre comillas (españolas o rectas) largas → blockquote
    let result = text.replace(/[""\u00ab]([^""\u00bb]{30,})[""\u00bb]/g, (_, quoted) => {
      return '\n\n> 💬 *' + quoted.trim() + '*\n\n'
    })
    return result
  }

  return (
    <div className="chat-panel-wrap">
      {/* Barra de contexto — siempre visible, ancho completo */}
      <div className="session-bar">
        {pedido ? (<>
          <span className="session-item">
            <span className="session-label">Cuenta</span>
            <span className="session-value">{pedido.numero}</span>
          </span>
          {pedido.cliente && (<>
            <span className="session-sep">·</span>
            <span className="session-item">
              <span className="session-label">Nombre comercial</span>
              <span className="session-value">{pedido.cliente}</span>
            </span>
          </>)}
          {pedido.estado && (<>
            <span className="session-sep">·</span>
            <span className="session-item">
              <span className="session-label">Canal</span>
              <span className={`session-badge estado-${pedido.estado?.toLowerCase().replace(/\s+/g, "-")}`}>
                {pedido.estado}
              </span>
            </span>
          </>)}
          {pedido.metodo_pago && (<>
            <span className="session-sep">·</span>
            <span className="session-item">
              <span className="session-label">Condición de pago</span>
              <span className="session-value">{pedido.metodo_pago}</span>
            </span>
          </>)}
          {pedido.tracking && (<>
            <span className="session-sep">·</span>
            <span className="session-item">
              <span className="session-label">Distribución</span>
              <span className="session-value">{pedido.tracking}</span>
            </span>
          </>)}
          {pedido.nivel_fidelidad && (<>
            <span className="session-sep">·</span>
            <span className="session-item">
              <span className="session-label">Tamaño canal</span>
              <span className={`session-badge fidelidad-${pedido.nivel_fidelidad?.toLowerCase()}`}>
                {pedido.nivel_fidelidad}
              </span>
            </span>
          </>)}
          {pedido.ciudad && (<>
            <span className="session-sep">·</span>
            <span className="session-item">
              <span className="session-label">Ciudad / zona</span>
              <span className="session-value">{pedido.ciudad}</span>
            </span>
          </>)}
        </>) : (
          <span className="session-empty">Sin cuenta cargada</span>
        )}
      </div>

      <div className="chat-panel">
      <div className="chat-main">
      <div className="messages">
        {messages.map((msg, i) => (
          <div key={i} className={`message-row ${msg.role}`}>
            {msg.role === "assistant" && <div className="avatar">SA</div>}
            <div className="bubble">
              {msg.attachment && (
                msg.attachment.type === "image" ? (
                  <a
                    href={msg.attachment.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="attachment-thumb"
                    title={`Abrir ${msg.attachment.name}`}
                  >
                    <img src={msg.attachment.url} alt={msg.attachment.name} />
                  </a>
                ) : (
                  <a
                    href={msg.attachment.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="attachment-card"
                    title={`Abrir ${msg.attachment.name}`}
                  >
                    <span className="attachment-card-icon">
                      <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm-1 1.5L18.5 9H13V3.5z"/>
                      </svg>
                    </span>
                    <span className="attachment-card-meta">
                      <span className="attachment-card-name">{msg.attachment.name}</span>
                      <span className="attachment-card-type">PDF</span>
                    </span>
                  </a>
                )
              )}
              {msg.role === "assistant"
                ? <ReactMarkdown>{formatAssistantMsg(msg.content)}</ReactMarkdown>
                : (msg.content && <span>{msg.content}</span>)
              }
            </div>
          </div>
        ))}
        {loading && !isStreaming && (
          <div className="agent-status-row">
            <span className="pulse-dot" />
            <span className="status-label">
              {agentStatus || "Pensando"}
              <span className="ellipsis-anim" />
            </span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* ── Modal de evaluación (solo en modo ontologista) ── */}
      {showEval && (evaluating || evaluation) && (
        <EvaluationModal
          evaluation={evaluation}
          evaluating={evaluating}
          onClose={handleModalClose}
        />
      )}

      <div className="input-area">
        {rtActive && rtDraft && (
          <div className="rt-draft-chip">
            <span className="rt-draft-icon">📋</span>
            <span className="rt-draft-text">{rtDraft.resumen}</span>
          </div>
        )}
        {suggestions.length > 0 && !loading && (
          <div className="suggestions">
            {suggestions.map((s, i) => (
              <button
                key={i}
                className="suggestion-btn"
                onClick={() => {
                  setSuggestions([])
                  sendMessage(s)
                }}
              >
                {s}
              </button>
            ))}
          </div>
        )}
        {attachedFile && (
          <div className="file-preview">
            {attachedFile.type === "image" ? (
              <img src={attachedFile.previewUrl} alt={attachedFile.name} className="file-preview-thumb" />
            ) : (
              <span className="file-preview-icon">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm-1 1.5L18.5 9H13V3.5z"/>
                </svg>
              </span>
            )}
            <span className="file-name">{attachedFile.name}</span>
            <button className="file-remove" onClick={removeAttachment} title="Quitar el archivo adjunto antes de enviarlo. El asistente SA puede analizar documentos PDF e imágenes (facturas, fotos de productos, comprobantes) para ayudarte mejor.">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          </div>
        )}
        <div className="input-row">
          <div className="input-box">
            {enableVoice && (
              <button
                className="mode-toggle-btn"
                onClick={handleModeToggle}
                disabled={!sessionId || rtActive}
                title={voiceMode ? "Volver a modo texto" : "Cambiar a modo audio (manos libres)"}
              >
                {voiceMode ? (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="2" y="5" width="20" height="14" rx="2"/><path d="M6 9h.01M10 9h.01M14 9h.01M18 9h.01M6 13h.01M18 13h.01M8 13h8"/>
                  </svg>
                ) : (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10v1a7 7 0 0 1-14 0v-1"/><line x1="12" y1="18" x2="12" y2="22"/>
                  </svg>
                )}
              </button>
            )}
            {enableVoice && rtEnabled && !voiceMode && (
              <button
                className={`realtime-toggle-btn ${rtActive ? "active" : ""}`}
                onClick={handleRealtimeToggle}
                disabled={!sessionId}
                title={rtActive ? "Cortar la voz en vivo y volver a modo texto" : "Probar voz en vivo (beta) — conversación continua, podés interrumpir hablando"}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M4 12h3l3-7 4 14 3-7h3"/>
                </svg>
                {!rtActive && <span className="rt-beta-dot" title="Experimental" />}
              </button>
            )}
            {rtActive ? (
              <div className="voice-record-row realtime-row">
                <button
                  className={`voice-record-btn live ${rt.muted ? "muted" : rt.phase}`}
                  onClick={rt.toggleMute}
                  disabled={!sessionId}
                  title={rt.muted ? "Reanudar el micrófono" : "Silenciar el micrófono"}
                >
                  {rt.muted ? (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="2" y1="2" x2="22" y2="22"/><path d="M18.9 13.2A7 7 0 0 0 19 12v-1"/><path d="M5 11v1a7 7 0 0 0 10.7 6"/><path d="M9 5a3 3 0 0 1 6 0v5"/><path d="M9 9v2a3 3 0 0 0 4.6 2.5"/><line x1="12" y1="18" x2="12" y2="22"/>
                    </svg>
                  ) : (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10v1a7 7 0 0 1-14 0v-1"/><line x1="12" y1="18" x2="12" y2="22"/>
                    </svg>
                  )}
                </button>
                <span className="voice-status-label">
                  {rt.muted ? "Micrófono en pausa"
                   : rt.phase === "connecting" ? "Conectando…"
                   : rt.phase === "listening"  ? "Te escucho — hablá cuando quieras"
                   : rt.phase === "thinking"   ? "Pensando…"
                   : rt.phase === "speaking"   ? "Hablando… (podés interrumpirme)"
                   : rt.phase === "error"      ? "Se cortó la conexión — tocá el ícono para reconectar"
                   : ""}
                </span>
                {rt.needsAudioTap && (
                  <button className="rt-tap-audio-btn" onClick={rt.enableAudio}>Tocá para escuchar</button>
                )}
              </div>
            ) : voiceMode ? (
              <div className="voice-record-row">
                <button
                  className={`voice-record-btn ${voicePhase}`}
                  onClick={toggleMute}
                  disabled={!sessionId}
                  title={voicePhase === "muted" ? "Reanudar la escucha" : "Silenciar / pausar"}
                >
                  {voicePhase === "muted" ? (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="2" y1="2" x2="22" y2="22"/><path d="M18.9 13.2A7 7 0 0 0 19 12v-1"/><path d="M5 11v1a7 7 0 0 0 10.7 6"/><path d="M9 5a3 3 0 0 1 6 0v5"/><path d="M9 9v2a3 3 0 0 0 4.6 2.5"/><line x1="12" y1="18" x2="12" y2="22"/>
                    </svg>
                  ) : (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10v1a7 7 0 0 1-14 0v-1"/><line x1="12" y1="18" x2="12" y2="22"/>
                    </svg>
                  )}
                </button>
                <span className="voice-status-label">
                  {voicePhase === "listening"  ? "Te escucho… hablá"
                   : voicePhase === "sending"    ? "Enviando…"
                   : voicePhase === "responding" ? "Respondiendo… (tocá para silenciar)"
                   : voicePhase === "muted"      ? "En pausa — tocá el micrófono para hablar"
                   : ""}
                </span>
              </div>
            ) : (
              <>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={ACCEPTED}
                  style={{ display: "none" }}
                  onChange={handleFileChange}
                />
                <button
                  className="attach-btn"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={!sessionId}
                  title="Adjuntar un archivo PDF o imagen. El asistente SA puede leer documentos del cliente (facturas, fotos de producto, comprobantes) para resolver mejor la consulta según las ontologías activas."
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
                  </svg>
                </button>
                <textarea
                  ref={textareaRef}
                  className="chat-input"
                  placeholder={ended ? "Conversación finalizada" : "Mensaje..."}
                  value={input}
                  onChange={handleInput}
                  onKeyDown={handleKeyDown}
                  rows={1}
                  disabled={!sessionId || ended}
                />
                {!ended && (loading ? (
                  <button className="stop-btn" onClick={handleStop} title="Detener la respuesta del asistente SA en curso. Útil si detectas que la ontología está generando una respuesta incorrecta o demasiado larga.">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                      <rect x="3" y="3" width="18" height="18" rx="2"/>
                    </svg>
                  </button>
                ) : (
                  <button
                    className="send-btn"
                    onClick={() => sendMessage()}
                    disabled={(!input.trim() && !attachedFile) || !sessionId}
                    title="Enviar tu mensaje al asistente SA (también puedes pulsar Enter). El asistente responderá aplicando las ontologías activas: el system prompt define su personalidad, los procedimientos guían la gestión y las FAQ aportan información sobre políticas."
                  >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
                    </svg>
                  </button>
                ))}
              </>
            )}
          </div>
          {showEval && (
            <button
              className="eval-btn"
              title="Finalizar la conversación y lanzar la evaluación automática. Un agente evaluador analizará toda la conversación en tres niveles — system prompt, procedimientos y FAQ — puntuando cada ontología del 1 al 10 y proponiendo mejoras concretas que puedes aplicar con un clic."
              disabled={messages.length < 2 || loading || evaluating || ended}
              onClick={handleFin}
            >
              Fin
            </button>
          )}
        </div>
        <p className="disclaimer">Desarrollado por Braintrust CS firma miembro de Andersen Consulting</p>
        {enableVoice && rtEnabled && (
          <audio ref={rt.audioElRef} autoPlay playsInline style={{ display: "none" }} />
        )}
      </div>

      </div>{/* close chat-main */}

      {/* ── Radar de riesgo (solo en modo test) ── */}
      {showEval && (
        <div className="chat-radar-sidebar">
          <RadarChart data={riskProfile} />
          <RetentionGauge value={retention} />
          <SentimentLine points={sentimentPts} />
        </div>
      )}

      {/* ── Prompt optimizador ── */}
      {showOptPrompt && (
        <div className="ap-opt-overlay">
          <div className="ap-opt-dialog">
            <div className="ap-opt-icon">&#9881;</div>
            <h3>Conversación finalizada</h3>
            <p>¿Deseas ejecutar el agente optimizador para analizar la conversación y sugerir mejoras a la ontología?</p>
            <div className="ap-opt-actions">
              <button className="ap-opt-yes" onClick={handleOptimize}>Sí, optimizar</button>
              <button className="ap-opt-no" onClick={handleSkipOptimize}>No, nuevo caso</button>
            </div>
          </div>
        </div>
      )}
    </div>{/* close chat-panel */}
    </div>
  )
}
