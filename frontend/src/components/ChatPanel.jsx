import { useState, useEffect, useRef } from "react"
import ReactMarkdown from "react-markdown"
import { EvaluationModal } from "./EvaluationCard"
import RadarChart from "./RadarChart"
import RetentionGauge from "./RetentionGauge"
import SentimentLine from "./SentimentLine"
import "./ChatPanel.css"

const API = import.meta.env.VITE_API_URL || "/api"

const ACCEPTED = ".pdf,.jpg,.jpeg,.png,.webp"


export default function ChatPanel({ onLoadingChange, onNewCase, showEval = false, initialClienteCodigo = null }) {
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
  const bottomRef    = useRef(null)
  const textareaRef  = useRef(null)
  const abortRef     = useRef(null)
  const fileInputRef = useRef(null)
  const genRef       = useRef(0)
  const messagesRef  = useRef([])
  const pedidoRef    = useRef(null)

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

  async function sendMessage() {
    const text = input.trim()
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
    } catch (e) {
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
              placeholder={ended ? "Conversación finalizada" : "Escribe un mensaje..."}
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
                onClick={sendMessage}
                disabled={(!input.trim() && !attachedFile) || !sessionId}
                title="Enviar tu mensaje al asistente SA (también puedes pulsar Enter). El asistente responderá aplicando las ontologías activas: el system prompt define su personalidad, los procedimientos guían la gestión y las FAQ aportan información sobre políticas."
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
                </svg>
              </button>
            ))}
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
