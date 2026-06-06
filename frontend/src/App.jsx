import React, { useState, useRef, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'

const ROLES = [
  { label: '管理员', role_mask: 2147483647, dept_mask: 0 },
  { label: '研发部', role_mask: 1, dept_mask: 1 },
  { label: '品质部', role_mask: 2, dept_mask: 2 },
  { label: '法规部', role_mask: 4, dept_mask: 4 },
  { label: '销售部', role_mask: 8, dept_mask: 8 },
  { label: '访客', role_mask: 0, dept_mask: 0 },
]

function generateSessionId() {
  return 'sess_' + Math.random().toString(36).substring(2, 10)
}

export default function App() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [role, setRole] = useState(ROLES[0])
  const [sessionId] = useState(generateSessionId)
  const [imageFile, setImageFile] = useState(null)
  const [imagePreview, setImagePreview] = useState(null)
  const [useWebSocket, setUseWebSocket] = useState(false)
  const [wsConnected, setWsConnected] = useState(false)
  const chatEndRef = useRef(null)
  const wsRef = useRef(null)
  const fileInputRef = useRef(null)

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const connectWebSocket = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${proto}//${window.location.host}/api/ws/chat/${sessionId}?user_id=web_user&role_mask=${role.role_mask}&dept_mask=${role.dept_mask}`
    const ws = new WebSocket(url)
    ws.onopen = () => setWsConnected(true)
    ws.onclose = () => { setWsConnected(false); wsRef.current = null }
    ws.onerror = () => { setWsConnected(false); wsRef.current = null }
    ws.onmessage = (evt) => {
      const data = JSON.parse(evt.data)
      if (data.error) {
        setMessages(prev => [...prev, { role: 'error', content: data.error }])
      } else {
        setMessages(prev => [...prev, { role: 'assistant', content: data.answer, evidence: data.history }])
      }
      setLoading(false)
    }
    wsRef.current = ws
  }, [sessionId, role])

  useEffect(() => {
    if (useWebSocket) connectWebSocket()
    return () => { wsRef.current?.close() }
  }, [useWebSocket, connectWebSocket])

  const handleImageSelect = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setImageFile(file)
    const reader = new FileReader()
    reader.onload = (ev) => setImagePreview(ev.target.result)
    reader.readAsDataURL(file)
  }

  const clearImage = () => {
    setImageFile(null)
    setImagePreview(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const sendMessage = async () => {
    const text = input.trim()
    if (!text && !imageFile) return
    if (loading) return

    const userMsg = { role: 'user', content: text }
    if (imagePreview) userMsg.image = imagePreview
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setLoading(true)

    if (useWebSocket && wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ message: text }))
      clearImage()
      return
    }

    try {
      let response
      if (imageFile) {
        const formData = new FormData()
        formData.append('query', text)
        formData.append('image', imageFile)
        formData.append('session_id', sessionId)
        response = await fetch('/api/query/upload', {
          method: 'POST',
          headers: {
            'X-User-ID': 'web_user',
            'X-Role-Mask': String(role.role_mask),
            'X-Dept-Mask': String(role.dept_mask),
          },
          body: formData,
        })
      } else {
        response = await fetch('/api/chat', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-User-ID': 'web_user',
            'X-Role-Mask': String(role.role_mask),
            'X-Dept-Mask': String(role.dept_mask),
          },
          body: JSON.stringify({ message: text, session_id: sessionId }),
        })
      }

      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: response.statusText }))
        throw new Error(err.detail || `HTTP ${response.status}`)
      }

      const data = await response.json()
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: data.answer,
        business_type: data.business_type,
        intent: data.intent,
        evidence: data.evidence_doc_ids,
        latency: data.latency_ms,
        cache_hit: data.cache_hit,
      }])
    } catch (err) {
      setMessages(prev => [...prev, { role: 'error', content: err.message }])
    } finally {
      setLoading(false)
      clearImage()
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  return (
    <div className="app">
      <header className="header">
        <div className="header-left">
          <h1>💄 化妆品 RAG 智能问答</h1>
          <span className="subtitle">成分查询 · 法规咨询 · 配方研发</span>
        </div>
        <div className="header-right">
          <select
            value={role.label}
            onChange={(e) => setRole(ROLES.find(r => r.label === e.target.value))}
            className="role-select"
          >
            {ROLES.map(r => <option key={r.label} value={r.label}>{r.label}</option>)}
          </select>
          <label className="ws-toggle">
            <input
              type="checkbox"
              checked={useWebSocket}
              onChange={(e) => setUseWebSocket(e.target.checked)}
            />
            WS {wsConnected && <span className="ws-dot" />}
          </label>
        </div>
      </header>

      <main className="chat-area">
        {messages.length === 0 && (
          <div className="empty-state">
            <div className="empty-icon">🔬</div>
            <p>输入问题开始对话</p>
            <div className="examples">
              {['烟酰胺的安全浓度是多少？', '化妆品中铅含量的限量标准？', '如何设计保湿配方的防腐体系？'].map(q => (
                <button key={q} className="example-btn" onClick={() => { setInput(q); }}>{q}</button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`message message-${msg.role}`}>
            <div className="message-avatar">
              {msg.role === 'user' ? '👤' : msg.role === 'assistant' ? '🤖' : '⚠️'}
            </div>
            <div className="message-body">
              {msg.image && <img src={msg.image} alt="uploaded" className="message-image" />}
              {msg.role === 'assistant' ? (
                <div className="message-content"><ReactMarkdown>{msg.content}</ReactMarkdown></div>
              ) : (
                <div className="message-content">{msg.content}</div>
              )}
              {msg.role === 'assistant' && msg.evidence?.length > 0 && (
                <div className="evidence-bar">
                  <span className="evidence-label">📄 引用文档:</span>
                  {msg.evidence.map((doc, j) => <span key={j} className="evidence-tag">{doc}</span>)}
                </div>
              )}
              {msg.role === 'assistant' && msg.business_type && (
                <div className="meta-bar">
                  {msg.business_type && <span className="meta-tag">{msg.business_type}</span>}
                  {msg.intent && <span className="meta-tag">{msg.intent}</span>}
                  {msg.latency && <span className="meta-tag">{msg.latency.toFixed(0)}ms</span>}
                  {msg.cache_hit && <span className="meta-tag cache">缓存命中</span>}
                </div>
              )}
            </div>
          </div>
        ))}

        {loading && (
          <div className="message message-assistant">
            <div className="message-avatar">🤖</div>
            <div className="message-body"><div className="typing-indicator"><span /><span /><span /></div></div>
          </div>
        )}
        <div ref={chatEndRef} />
      </main>

      <footer className="input-area">
        {imagePreview && (
          <div className="image-preview">
            <img src={imagePreview} alt="preview" />
            <button onClick={clearImage} className="image-remove">✕</button>
          </div>
        )}
        <div className="input-row">
          <button onClick={() => fileInputRef.current?.click()} className="btn-icon" title="上传图片">📎</button>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            onChange={handleImageSelect}
            style={{ display: 'none' }}
          />
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入问题... (Enter 发送, Shift+Enter 换行)"
            rows={1}
            className="chat-input"
          />
          <button onClick={sendMessage} disabled={loading || (!input.trim() && !imageFile)} className="btn-send">
            {loading ? '⏳' : '发送'}
          </button>
        </div>
      </footer>
    </div>
  )
}
