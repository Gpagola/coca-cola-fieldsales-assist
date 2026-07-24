import { useState, useEffect, useRef } from "react"
import ReactMarkdown from "react-markdown"
import { EvaluationModal } from "./EvaluationCard"
import RadarChart from "./RadarChart"
import RetentionGauge from "./RetentionGauge"
import SentimentLine from "./SentimentLine"
import "./ChatPanel.css"

const API = import.meta.env.VITE_API_URL || "/api"

const ACCEPTED = ".pdf,.jpg,.jpeg,.png,.webp"


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
  const [recState, setRecState]           = useState("idle") // "idle" | "recording" | "transcribing"
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
  const voicesRef        = useRef([])
  const utteranceRef     = useRef(null)
  const speechUnlockedRef = useRef(false)
  const sonarAudioRef    = useRef(null)
  const sonarTimeoutRef  = useRef(null)

  function getSonarAudio() {
    if (!sonarAudioRef.current) {
      const audio = new Audio(`${import.meta.env.BASE_URL}sounds/drmseq-appulse-165912.mp3`)
      audio.loop = true
      audio.volume = 0.5
      sonarAudioRef.current = audio
    }
    return sonarAudioRef.current
  }

  // iOS Safari solo deja sonar speechSynthesis.speak() y <audio>.play() si se
  // llaman dentro de (o muy cerca de) un gesto real del usuario. Los llamados
  // reales llegan varios segundos despues (grabar -> transcribir -> enviar ->
  // stream de la respuesta), asi que "desbloqueamos" ambos con un toque real
  // apenas el vendedor entra en modo audio o toca grabar.
  function unlockSpeech() {
    const audio = getSonarAudio()
    audio.play().then(() => { audio.pause(); audio.currentTime = 0 }).catch(() => {})
    if (speechUnlockedRef.current || !("speechSynthesis" in window)) return
    speechUnlockedRef.current = true
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(" "))
  }

  // Sonido de espera en loop mientras se procesa el mensaje de voz y hasta
  // que arranca la lectura en voz alta de la respuesta (cubre el silencio de
  // "pensando" + streaming + el pequeno delta antes de que hable el TTS).
  function startSonar() {
    if (!enableVoice || !voiceMode) return
    const audio = getSonarAudio()
    audio.currentTime = 0
    audio.play().catch(() => {})
    sonarTimeoutRef.current = setTimeout(stopSonar, 20000) // resguardo por si algo no llama a stopSonar
  }

  function stopSonar() {
    if (sonarAudioRef.current) { sonarAudioRef.current.pause(); sonarAudioRef.current.currentTime = 0 }
    if (sonarTimeoutRef.current) { clearTimeout(sonarTimeoutRef.current); sonarTimeoutRef.current = null }
  }

  async function streamChat(message, sessionId, controller, onToken, onStatus, onPedido, onSuggestions, onCierre, onRiskProfile) {
    const res = await fetch(`${API}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId }),
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
      const { session_id } = await r.json()
      setSessionId(session_id)
      setMessages([{
        role: "assistant",
        content: initialClienteCodigo
          ? `¡Hola! Soy tu asistente de venta en terreno. Ya tengo cargada la cuenta ${initialClienteCodigo}, dame un segundo para traer su ficha.`
          : "¡Hola! Soy tu asistente de venta en terreno de Coca-Cola. Decime el código de cliente (CLI-XXXX) de la cuenta con la que vas a trabajar."
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
          speakText(accumulated)
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
    if (!enableVoice || !("speechSynthesis" in window)) return
    const loadVoices = () => { voicesRef.current = window.speechSynthesis.getVoices() }
    loadVoices()
    window.speechSynthesis.onvoiceschanged = loadVoices
    return () => {
      window.speechSynthesis.onvoiceschanged = null
      window.speechSynthesis.cancel()
      stopSonar()
    }
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

  function speakText(text) {
    if (!enableVoice || !voiceMode || !("speechSynthesis" in window) || !text.trim()) {
      stopSonar()
      return
    }
    window.speechSynthesis.cancel()
    const utterance = new SpeechSynthesisUtterance(stripForSpeech(text))
    utterance.lang = "es-AR"
    const esVoice = voicesRef.current.find(v => v.lang?.startsWith("es"))
    if (esVoice) utterance.voice = esVoice
    utterance.onstart = stopSonar // el sonar suena hasta el instante en que arranca a hablar
    utterance.onerror = stopSonar
    utteranceRef.current = utterance // algunos navegadores cortan el audio si el objeto queda sin referencias y el GC lo recolecta a mitad de la lectura
    window.speechSynthesis.speak(utterance)
  }

  function toggleVoiceMode() {
    window.speechSynthesis?.cancel()
    stopSonar()
    if (!voiceMode) unlockSpeech()
    if (voiceMode && recState !== "idle") {
      mediaRecorderRef.current?.stop()
      mediaStreamRef.current?.getTracks().forEach(t => t.stop())
      setRecState("idle")
    }
    setVoiceMode(v => !v)
  }

  async function startRecording() {
    unlockSpeech()
    window.speechSynthesis?.cancel() // si el asistente estaba hablando, cortar de inmediato y pasar a escuchar
    stopSonar()
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      mediaStreamRef.current = stream
      const mimeType = ["audio/webm", "audio/mp4", "audio/ogg"].find(t => MediaRecorder.isTypeSupported(t)) || ""
      const rec = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      audioChunksRef.current = []
      rec.ondataavailable = (e) => { if (e.data.size > 0) audioChunksRef.current.push(e.data) }
      rec.onstop = () => {
        mediaStreamRef.current?.getTracks().forEach(t => t.stop())
        mediaStreamRef.current = null
        const blob = new Blob(audioChunksRef.current, { type: mimeType || "audio/webm" })
        transcribeAndSend(blob, mimeType)
      }
      mediaRecorderRef.current = rec
      rec.start()
      setRecState("recording")
    } catch (e) {
      setMessages(prev => [...prev, { role: "assistant", content: "⚠️ No se pudo acceder al micrófono. Revisá los permisos del navegador." }])
    }
  }

  function stopRecording() {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop()
      setRecState("transcribing")
    }
  }

  async function transcribeAndSend(blob, mimeType) {
    try {
      const ext = mimeType.includes("mp4") ? "mp4" : mimeType.includes("ogg") ? "ogg" : "webm"
      const formData = new FormData()
      formData.append("audio", blob, `recording.${ext}`)
      const res = await fetch(`${API}/transcribe`, { method: "POST", body: formData })
      const data = await res.json()
      if (data.error) throw new Error(data.error)
      if (data.text?.trim()) sendMessage(data.text.trim())
    } catch (e) {
      setMessages(prev => [...prev, { role: "assistant", content: `⚠️ Error al transcribir el audio: ${e.message}` }])
    } finally {
      setRecState("idle")
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
    if (voiceMode) startSonar()

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

      await streamChat(finalMessage, sessionId, controller, (token) => {
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
      }, setAgentStatus, setPedido, (s) => setSuggestions(s), undefined, (profile) => {
        setRiskProfile(profile)
        if (profile.resolucion != null) setRetention(profile.resolucion)
        if (profile.sentimiento != null) setSentimentPts(prev => [...prev, profile.sentimiento])
      })
      speakText(accumulated)
    } catch (e) {
      stopSonar()
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
    stopSonar()
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
        {suggestions.length > 0 && !loading && (
          <div className="suggestions">
            {suggestions.map((s, i) => (
              <button
                key={i}
                className="suggestion-btn"
                onClick={() => {
                  setInput(s)
                  setSuggestions([])
                  textareaRef.current?.focus()
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
                onClick={toggleVoiceMode}
                disabled={!sessionId}
                title={voiceMode ? "Volver a modo texto" : "Cambiar a modo audio"}
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
            {voiceMode ? (
              <div className="voice-record-row">
                <button
                  className={`voice-record-btn ${recState}`}
                  onClick={recState === "recording" ? stopRecording : recState === "idle" ? startRecording : undefined}
                  disabled={recState === "transcribing" || !sessionId}
                  title={recState === "recording" ? "Detener grabación y enviar" : "Tocar para hablar"}
                >
                  {recState === "recording" ? (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>
                  ) : (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10v1a7 7 0 0 1-14 0v-1"/><line x1="12" y1="18" x2="12" y2="22"/>
                    </svg>
                  )}
                </button>
                <span className="voice-status-label">
                  {recState === "recording" ? "Escuchando..." : recState === "transcribing" ? "Transcribiendo..." : "Tocá para hablar"}
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
