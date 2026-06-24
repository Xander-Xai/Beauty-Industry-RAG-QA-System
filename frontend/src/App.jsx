import React, { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
const TOKEN_STORAGE_KEY = 'rag.accessToken'
const REFRESH_STORAGE_KEY = 'rag.refreshToken'
const USER_STORAGE_KEY = 'rag.user'
const SESSION_STORAGE_KEY = 'rag.sessionId'

const DEFAULT_METADATA = {
  app: {
    title: import.meta.env.VITE_APP_TITLE || '化妆品行业知识问答助手',
    subtitle: '面向化妆品行业知识库的检索增强问答助手',
    version: '',
  },
  auth: {
    dev_mode: false,
    auth_required: false,
    jwt_enabled: false,
    login_enabled: false,
    anonymous_user_id: 'web-user',
  },
  rbac: {
    default_role: '',
    role_options: [
      { key: 'public', label: 'Public Visitor', role_mask: 0, dept_mask: 0 },
    ],
  },
}

function apiUrl(path) {
  return `${API_BASE_URL}${path}`
}

function generateSessionId() {
  return `sess_${Math.random().toString(36).substring(2, 10)}`
}

function getStoredJson(key) {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

/** Decode a JWT payload (base64). Returns null on failure. */
function decodeJwtPayload(token) {
  try {
    const parts = token.split('.')
    if (parts.length !== 3) return null
    return JSON.parse(atob(parts[1]))
  } catch {
    return null
  }
}

/** Check if a JWT is expired (within a 60s grace window). */
function isJwtExpired(token) {
  const payload = decodeJwtPayload(token)
  if (!payload || !payload.exp) return true
  return Date.now() / 1000 >= payload.exp - 60
}

export default function App() {
  const [metadata, setMetadata] = useState(DEFAULT_METADATA)
  const [messages, setMessages] = useState([])
  const [requestMode, setRequestMode] = useState('chat')
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [metadataError, setMetadataError] = useState('')
  const [accessToken, setAccessToken] = useState(() => localStorage.getItem(TOKEN_STORAGE_KEY) || '')
  const [currentUser, setCurrentUser] = useState(() => getStoredJson(USER_STORAGE_KEY))
  const [loginForm, setLoginForm] = useState({ username: '', password: '' })
  const [loginError, setLoginError] = useState('')
  const [loginLoading, setLoginLoading] = useState(false)
  const [sessionId] = useState(() => {
    const stored = localStorage.getItem(SESSION_STORAGE_KEY)
    if (stored) return stored
    const next = generateSessionId()
    localStorage.setItem(SESSION_STORAGE_KEY, next)
    return next
  })
  const [showAdmin, setShowAdmin] = useState(false)
  const [users, setUsers] = useState([])
  const [usersLoading, setUsersLoading] = useState(false)
  const [createForm, setCreateForm] = useState({
    user_id: '',
    username: '',
    password: '',
    display_name: '',
    roles: [],
    departments: [],
  })
  const [adminError, setAdminError] = useState('')
  const [showSessionPanel, setShowSessionPanel] = useState(false)
  const [sessionHistory, setSessionHistory] = useState(null)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [showStatsPanel, setShowStatsPanel] = useState(false)
  const [stats, setStats] = useState(null)
  const [statsLoading, setStatsLoading] = useState(false)
  const [statsError, setStatsError] = useState('')
  const chatEndRef = useRef(null)

  const roleOptions = metadata.rbac.role_options?.length
    ? metadata.rbac.role_options
    : DEFAULT_METADATA.rbac.role_options

  const defaultRole = useMemo(() => {
    return (
      roleOptions.find(role => role.key === metadata.rbac.default_role) ||
      roleOptions[0]
    )
  }, [metadata.rbac.default_role, roleOptions])

  const adminRoleMask = metadata.rbac.roles?.admin ?? null
  const roleNames = Object.keys(metadata.rbac.roles || {})
  const departmentNames = Object.keys(metadata.rbac.departments || {}).filter(name => name !== 'all')
  const roleLabels = useMemo(() => {
    const mapping = {}
    for (const option of roleOptions) {
      mapping[option.key] = option.label
    }
    return mapping
  }, [roleOptions])

  const [role, setRole] = useState(defaultRole)

  useEffect(() => {
    setRole(defaultRole)
  }, [defaultRole])

  useEffect(() => {
    let mounted = true

    async function loadMetadata() {
      try {
        const response = await fetch(apiUrl('/api/auth/metadata'))
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }
        const data = await response.json()
        if (mounted) {
          setMetadata(data)
          setMetadataError('')
        }
      } catch (err) {
        if (mounted) {
          setMetadataError(`Using local defaults: ${err.message}`)
        }
      }
    }

    loadMetadata()
    return () => {
      mounted = false
    }
  }, [])

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    document.title = metadata.app.title || DEFAULT_METADATA.app.title
  }, [metadata.app.title])

  // Periodic token expiry check — refresh automatically every 5 min
  useEffect(() => {
    if (!accessToken) return
    const interval = setInterval(async () => {
      if (isJwtExpired(accessToken)) {
        const refreshed = await refreshAccessToken()
        if (!refreshed) {
          handleLogout()
        }
      }
    }, 300_000)
    return () => clearInterval(interval)
  }, [accessToken])

  const authRequired = metadata.auth.auth_required ?? (metadata.auth.jwt_enabled && !metadata.auth.dev_mode)
  const loginEnabled = metadata.auth.login_enabled ?? metadata.auth.jwt_enabled
  const canSend = Boolean(input.trim()) && !loading && (!authRequired || accessToken)

  const buildAuthHeaders = (tokenOverride = '') => {
    const headers = {}
    const token = tokenOverride || accessToken
    if (token) {
      headers.Authorization = `Bearer ${token}`
      return headers
    }
    if (metadata.auth.dev_mode) {
      headers['X-User-ID'] = metadata.auth.anonymous_user_id || 'web-user'
      headers['X-Role-Mask'] = String(role.role_mask)
      headers['X-Dept-Mask'] = String(role.dept_mask)
    }
    return headers
  }

  const toggleSelection = (items, value) => {
    return items.includes(value)
      ? items.filter(item => item !== value)
      : [...items, value]
  }

  /** Try to refresh the JWT. Returns the new access token on success. */
  const refreshAccessToken = async () => {
    const storedRefresh = localStorage.getItem(REFRESH_STORAGE_KEY)
    if (!storedRefresh) return ''
    try {
      const resp = await fetch(apiUrl('/api/auth/refresh'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: storedRefresh }),
      })
      if (!resp.ok) return ''
      const data = await resp.json()
      setAccessToken(data.access_token)
      localStorage.setItem(TOKEN_STORAGE_KEY, data.access_token)
      if (data.refresh_token) {
        localStorage.setItem(REFRESH_STORAGE_KEY, data.refresh_token)
      }
      return data.access_token
    } catch {
      return ''
    }
  }

  const handleLogin = async (event) => {
    event.preventDefault()
    setLoginLoading(true)
    setLoginError('')

    try {
      const response = await fetch(apiUrl('/api/auth/login'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(loginForm),
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(data.detail || `HTTP ${response.status}`)
      }

      setAccessToken(data.access_token)
      setCurrentUser(data.user)
      localStorage.setItem(TOKEN_STORAGE_KEY, data.access_token)
      localStorage.setItem(REFRESH_STORAGE_KEY, data.refresh_token)
      localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(data.user))
      setLoginForm({ username: '', password: '' })
    } catch (err) {
      setLoginError(err.message)
    } finally {
      setLoginLoading(false)
    }
  }

  const handleLogout = () => {
    setAccessToken('')
    setCurrentUser(null)
    localStorage.removeItem(TOKEN_STORAGE_KEY)
    localStorage.removeItem(REFRESH_STORAGE_KEY)
    localStorage.removeItem(USER_STORAGE_KEY)
    setShowAdmin(false)
    setShowSessionPanel(false)
    setShowStatsPanel(false)
  }

  /** Check if the current user matches the configured admin role. */
  const isAdmin = Boolean(
    currentUser &&
    adminRoleMask !== null &&
    (currentUser.role_mask === adminRoleMask || (currentUser.role_mask & adminRoleMask) === adminRoleMask)
  )

  const fetchSessionHistory = async () => {
    setHistoryLoading(true)
    setHistoryError('')
    try {
      let resp = await fetch(apiUrl(`/api/dialog_history?session_id=${encodeURIComponent(sessionId)}`), {
        headers: buildAuthHeaders(),
      })
      if (resp.status === 401 && accessToken) {
        const refreshedToken = await refreshAccessToken()
        if (!refreshedToken) {
          setHistoryError('Session expired. Please sign in again.')
          return
        }
        resp = await fetch(apiUrl(`/api/dialog_history?session_id=${encodeURIComponent(sessionId)}`), {
          headers: buildAuthHeaders(refreshedToken),
        })
      }
      if (!resp.ok) {
        throw new Error((await resp.json().catch(() => ({ detail: resp.statusText }))).detail || `HTTP ${resp.status}`)
      }
      const data = await resp.json()
      setSessionHistory(data)
    } catch (err) {
      setHistoryError(err.message)
    } finally {
      setHistoryLoading(false)
    }
  }

  const fetchStats = async () => {
    setStatsLoading(true)
    setStatsError('')
    try {
      const resp = await fetch(apiUrl('/api/stats'))
      if (!resp.ok) {
        throw new Error((await resp.json().catch(() => ({ detail: resp.statusText }))).detail || `HTTP ${resp.status}`)
      }
      const data = await resp.json()
      setStats(data)
    } catch (err) {
      setStatsError(err.message)
    } finally {
      setStatsLoading(false)
    }
  }

  /** Fetch user list from admin endpoint. */
  const fetchUsers = async () => {
    setUsersLoading(true)
    setAdminError('')
    try {
      const resp = await fetch(apiUrl('/api/auth/users'), {
        headers: buildAuthHeaders(),
      })
      if (resp.status === 401) {
        const refreshedToken = await refreshAccessToken()
        if (!refreshedToken) { setAdminError('Session expired. Please sign in again.'); return }
        const retry = await fetch(apiUrl('/api/auth/users'), {
          headers: buildAuthHeaders(refreshedToken),
        })
        if (!retry.ok) throw new Error((await retry.json()).detail || `HTTP ${retry.status}`)
        const data = await retry.json()
        setUsers(data.users || [])
      } else if (!resp.ok) {
        throw new Error((await resp.json()).detail || `HTTP ${resp.status}`)
      } else {
        const data = await resp.json()
        setUsers(data.users || [])
      }
    } catch (err) {
      setAdminError(err.message)
    } finally {
      setUsersLoading(false)
    }
  }

  /** Create a new user via admin endpoint. */
  const createUser = async (event) => {
    event.preventDefault()
    setAdminError('')
    try {
      const resp = await fetch(apiUrl('/api/auth/users'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...buildAuthHeaders() },
        body: JSON.stringify(createForm),
      })
      if (resp.status === 401) {
        const refreshedToken = await refreshAccessToken()
        if (!refreshedToken) { setAdminError('Session expired. Please sign in again.'); return }
        const retry = await fetch(apiUrl('/api/auth/users'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...buildAuthHeaders(refreshedToken) },
          body: JSON.stringify(createForm),
        })
        if (!retry.ok) throw new Error((await retry.json()).detail || `HTTP ${retry.status}`)
      } else if (!resp.ok) {
        throw new Error((await resp.json()).detail || `HTTP ${resp.status}`)
      }
      setCreateForm({ user_id: '', username: '', password: '', display_name: '', roles: [], departments: [] })
      fetchUsers()
    } catch (err) {
      setAdminError(err.message)
    }
  }

  const updateUserDraft = (userId, field, value) => {
    setUsers(prev => prev.map(user => (
      user.user_id === userId ? { ...user, [field]: value } : user
    )))
  }

  const saveUserRoles = async (user) => {
    setAdminError('')
    try {
      const payload = {
        roles: user.roles || [],
        departments: user.departments || [],
      }
      const resp = await fetch(apiUrl(`/api/auth/users/${user.user_id}/roles`), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...buildAuthHeaders() },
        body: JSON.stringify(payload),
      })
      if (resp.status === 401) {
        const refreshedToken = await refreshAccessToken()
        if (!refreshedToken) { setAdminError('Session expired. Please sign in again.'); return }
        const retry = await fetch(apiUrl(`/api/auth/users/${user.user_id}/roles`), {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', ...buildAuthHeaders(refreshedToken) },
          body: JSON.stringify(payload),
        })
        if (!retry.ok) throw new Error((await retry.json()).detail || `HTTP ${retry.status}`)
        const data = await retry.json()
        updateUserDraft(user.user_id, 'roles', data.roles || [])
        updateUserDraft(user.user_id, 'departments', data.departments || [])
      } else if (!resp.ok) {
        throw new Error((await resp.json()).detail || `HTTP ${resp.status}`)
      } else {
        const data = await resp.json()
        updateUserDraft(user.user_id, 'roles', data.roles || [])
        updateUserDraft(user.user_id, 'departments', data.departments || [])
      }
    } catch (err) {
      setAdminError(err.message)
    }
  }

  const openEvidence = async (docId) => {
    try {
      let resp = await fetch(apiUrl(`/api/media/${docId}`), {
        headers: buildAuthHeaders(),
      })
      if (resp.status === 401 && accessToken) {
        const refreshedToken = await refreshAccessToken()
        if (!refreshedToken) throw new Error('Session expired. Please sign in again.')
        resp = await fetch(apiUrl(`/api/media/${docId}`), {
          headers: buildAuthHeaders(refreshedToken),
        })
      }
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }))
        throw new Error(err.detail?.detail || err.detail || `HTTP ${resp.status}`)
      }
      const data = await resp.json()
      window.open(data.url, '_blank', 'noopener,noreferrer')
    } catch (err) {
      setMessages(prev => [...prev, { role: 'error', content: err.message }])
    }
  }

  const sendMessage = async () => {
    const text = input.trim()
    if (!text || loading) return

    if (authRequired && !accessToken) {
      setMessages(prev => [
        ...prev,
        { role: 'error', content: 'Please sign in before sending a question.' },
      ])
      return
    }

    const userMsg = { role: 'user', content: text, request_mode: requestMode }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setLoading(true)

    const attemptSend = async (token) => {
      const headers = { 'Content-Type': 'application/json', ...buildAuthHeaders(token) }
      const endpoint = requestMode === 'query' ? '/api/query' : '/api/chat'
      const payload = requestMode === 'query'
        ? { query: text, session_id: sessionId }
        : { message: text, session_id: sessionId }

      return fetch(apiUrl(endpoint), {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
      })
    }

    try {
      let response = await attemptSend(accessToken)

      // 401 → try token refresh once, then retry
      if (response.status === 401 && accessToken) {
        const refreshedToken = await refreshAccessToken()
        if (refreshedToken) {
          response = await attemptSend(refreshedToken)
        }
      }

      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: response.statusText }))
        throw new Error(err.detail || `HTTP ${response.status}`)
      }

      const data = await response.json()
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: data.answer,
        request_mode: requestMode,
        business_type: data.business_type,
        intent: data.intent,
        evidence: data.evidence_doc_ids,
        latency: data.latency_ms,
        cache_hit: data.cache_hit,
      }])
      if (showSessionPanel) {
        fetchSessionHistory()
      }
    } catch (err) {
      setMessages(prev => [...prev, { role: 'error', content: err.message }])
    } finally {
      setLoading(false)
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
          <h1>{metadata.app.title}</h1>
          <span className="subtitle">{metadata.app.subtitle}</span>
        </div>
        <div className="header-right">
          {metadata.auth.dev_mode && (
            <select
              value={role.key || role.label}
              onChange={(e) => {
                const nextRole = roleOptions.find(r => (r.key || r.label) === e.target.value)
                if (nextRole) setRole(nextRole)
              }}
              className="role-select"
              aria-label="Development role"
            >
              {roleOptions.map(r => (
                <option key={r.key || r.label} value={r.key || r.label}>{r.label}</option>
              ))}
            </select>
          )}
          {accessToken && currentUser && (
            <div className="user-chip">
              <span>{currentUser.display_name || currentUser.username}</span>
              {isAdmin && (
                <button type="button" onClick={() => { setShowAdmin(!showAdmin); if (!showAdmin) fetchUsers() }}>
                  {showAdmin ? 'Close' : 'Admin'}
                </button>
              )}
              <button type="button" onClick={handleLogout}>Sign out</button>
            </div>
          )}
        </div>
      </header>

      {metadataError && <div className="notice">{metadataError}</div>}

      {authRequired && !loginEnabled && (
        <div className="notice notice-error">
          JWT login is required for this deployment, but the backend has not enabled token signing yet.
        </div>
      )}

      {authRequired && !accessToken && (
        <form className="login-panel" onSubmit={handleLogin}>
          <input
            value={loginForm.username}
            onChange={(e) => setLoginForm(prev => ({ ...prev, username: e.target.value }))}
            placeholder="Username"
            autoComplete="username"
          />
          <input
            value={loginForm.password}
            onChange={(e) => setLoginForm(prev => ({ ...prev, password: e.target.value }))}
            placeholder="Password"
            type="password"
            autoComplete="current-password"
          />
          <button type="submit" disabled={loginLoading || !loginForm.username || !loginForm.password}>
            {loginLoading ? 'Signing in...' : 'Sign in'}
          </button>
          {loginError && <span className="login-error">{loginError}</span>}
        </form>
      )}

      <div className="workspace-toolbar">
        <div className="toolbar-group">
          <button
            type="button"
            className={`toolbar-button ${requestMode === 'chat' ? 'active' : ''}`}
            onClick={() => setRequestMode('chat')}
          >
            Multi-turn Chat
          </button>
          <button
            type="button"
            className={`toolbar-button ${requestMode === 'query' ? 'active' : ''}`}
            onClick={() => setRequestMode('query')}
          >
            Single Query
          </button>
        </div>
        <div className="toolbar-group">
          <button
            type="button"
            className={`toolbar-button ${showSessionPanel ? 'active' : ''}`}
            onClick={() => {
              const next = !showSessionPanel
              setShowSessionPanel(next)
              if (next) fetchSessionHistory()
            }}
          >
            Session
          </button>
          <button
            type="button"
            className={`toolbar-button ${showStatsPanel ? 'active' : ''}`}
            onClick={() => {
              const next = !showStatsPanel
              setShowStatsPanel(next)
              if (next) fetchStats()
            }}
          >
            Stats
          </button>
        </div>
      </div>

      {showAdmin && isAdmin && (
        <div className="admin-panel">
          <div className="admin-header">
            <h3>User Management</h3>
            <button onClick={fetchUsers} disabled={usersLoading} className="admin-refresh-btn">
              {usersLoading ? 'Loading...' : 'Refresh'}
            </button>
          </div>
          {adminError && <div className="admin-error">{adminError}</div>}

          <div className="admin-section">
            <h4>Create User</h4>
            <form className="admin-create-form" onSubmit={createUser}>
              <input value={createForm.user_id} onChange={(e) => setCreateForm(p => ({...p, user_id: e.target.value}))} placeholder="User ID" />
              <input value={createForm.username} onChange={(e) => setCreateForm(p => ({...p, username: e.target.value}))} placeholder="Username" />
              <input value={createForm.password} onChange={(e) => setCreateForm(p => ({...p, password: e.target.value}))} placeholder="Password" type="password" />
              <input value={createForm.display_name} onChange={(e) => setCreateForm(p => ({...p, display_name: e.target.value}))} placeholder="Display Name" />
              <div className="admin-matrix">
                {roleNames.map(name => (
                  <label key={name}>
                    <input
                      type="checkbox"
                      checked={createForm.roles.includes(name)}
                      onChange={() => setCreateForm(prev => ({
                        ...prev,
                        roles: toggleSelection(prev.roles, name),
                      }))}
                    />
                    {roleLabels[name] || name}
                  </label>
                ))}
              </div>
              <div className="admin-matrix">
                {departmentNames.map(name => (
                  <label key={name}>
                    <input
                      type="checkbox"
                      checked={createForm.departments.includes(name)}
                      onChange={() => setCreateForm(prev => ({
                        ...prev,
                        departments: toggleSelection(prev.departments, name),
                      }))}
                    />
                    {name}
                  </label>
                ))}
              </div>
              <button type="submit" disabled={!createForm.user_id || !createForm.username || !createForm.password}>Create</button>
            </form>
          </div>

          <div className="admin-section">
            <h4>Users ({users.length})</h4>
            <table className="admin-table">
              <thead>
                <tr><th>ID</th><th>Username</th><th>Display Name</th><th>Roles</th><th>Departments</th><th>Active</th><th>Actions</th></tr>
              </thead>
              <tbody>
                {users.map(u => (
                  <tr key={u.user_id}>
                    <td>{u.user_id}</td>
                    <td>{u.username}</td>
                    <td>{u.display_name}</td>
                    <td>
                      <div className="admin-matrix">
                        {roleNames.map(name => (
                          <label key={`${u.user_id}-${name}`}>
                            <input
                              type="checkbox"
                              checked={(u.roles || []).includes(name)}
                              onChange={() => updateUserDraft(u.user_id, 'roles', toggleSelection(u.roles || [], name))}
                            />
                            {roleLabels[name] || name}
                          </label>
                        ))}
                      </div>
                    </td>
                    <td>
                      <div className="admin-matrix">
                        {departmentNames.map(name => (
                          <label key={`${u.user_id}-${name}`}>
                            <input
                              type="checkbox"
                              checked={(u.departments || []).includes(name)}
                              onChange={() => updateUserDraft(u.user_id, 'departments', toggleSelection(u.departments || [], name))}
                            />
                            {name}
                          </label>
                        ))}
                      </div>
                    </td>
                    <td>{u.is_active ? 'Yes' : 'No'}</td>
                    <td><button type="button" onClick={() => saveUserRoles(u)}>Save</button></td>
                  </tr>
                ))}
                {users.length === 0 && <tr><td colSpan={7} className="admin-empty">No users found</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {showSessionPanel && (
        <section className="utility-panel">
          <div className="utility-panel-header">
            <h3>Session State</h3>
            <button type="button" className="admin-refresh-btn" onClick={fetchSessionHistory} disabled={historyLoading}>
              {historyLoading ? 'Loading...' : 'Refresh'}
            </button>
          </div>
          {historyError && <div className="admin-error">{historyError}</div>}
          {sessionHistory && (
            <div className="utility-panel-content">
              <div className="meta-bar">
                <span className="meta-tag">{sessionHistory.session_id}</span>
                <span className="meta-tag">{(sessionHistory.rounds || []).length} rounds</span>
                <span className="meta-tag">{(sessionHistory.locked_doc_ids || []).length} locked docs</span>
              </div>
              {(sessionHistory.locked_doc_ids || []).length > 0 && (
                <div className="evidence-bar utility-evidence">
                  <span className="evidence-label">Locked evidence:</span>
                  {sessionHistory.locked_doc_ids.map(doc => (
                    <button
                      type="button"
                      key={doc}
                      className="evidence-tag evidence-link"
                      onClick={() => openEvidence(doc)}
                    >
                      {doc}
                    </button>
                  ))}
                </div>
              )}
              <div className="history-list">
                {(sessionHistory.rounds || []).length === 0 && (
                  <div className="history-empty">No rounds have been recorded for this session yet.</div>
                )}
                {(sessionHistory.rounds || []).map((round, index) => (
                  <div key={`${sessionHistory.session_id}-${index}`} className="history-item">
                    <div className="history-label">Round {index + 1}</div>
                    <div className="history-question">{round.user_input}</div>
                    <div className="history-answer">{round.response}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      {showStatsPanel && (
        <section className="utility-panel">
          <div className="utility-panel-header">
            <h3>System Stats</h3>
            <button type="button" className="admin-refresh-btn" onClick={fetchStats} disabled={statsLoading}>
              {statsLoading ? 'Loading...' : 'Refresh'}
            </button>
          </div>
          {statsError && <div className="admin-error">{statsError}</div>}
          {stats && (
            <div className="utility-panel-content">
              <div className="stats-grid">
                <div className="stats-card">
                  <span className="stats-label">Uptime</span>
                  <strong>{Math.round(stats.uptime_seconds || 0)}s</strong>
                </div>
                <div className="stats-card">
                  <span className="stats-label">Active Requests</span>
                  <strong>{stats.active_requests ?? 0}</strong>
                </div>
                <div className="stats-card">
                  <span className="stats-label">Rewrite Fallback</span>
                  <strong>{((stats.rewrite_fallback_rate || 0) * 100).toFixed(1)}%</strong>
                </div>
                <div className="stats-card">
                  <span className="stats-label">KV Pressure</span>
                  <strong>{(stats.kv_pressure || 0).toFixed(2)}</strong>
                </div>
                <div className="stats-card">
                  <span className="stats-label">L1 Cache Hit</span>
                  <strong>{((stats.cache_hit_rate?.L1 || 0) * 100).toFixed(1)}%</strong>
                </div>
                <div className="stats-card">
                  <span className="stats-label">L2 Cache Hit</span>
                  <strong>{((stats.cache_hit_rate?.L2 || 0) * 100).toFixed(1)}%</strong>
                </div>
              </div>
              <div className="history-list">
                {Object.entries(stats.latency_percentiles || {}).map(([stage, values]) => (
                  <div key={stage} className="history-item">
                    <div className="history-label">{stage}</div>
                    <div className="meta-bar">
                      <span className="meta-tag">p50 {Number(values.p50 || 0).toFixed(1)}ms</span>
                      <span className="meta-tag">p95 {Number(values.p95 || 0).toFixed(1)}ms</span>
                      <span className="meta-tag">p99 {Number(values.p99 || 0).toFixed(1)}ms</span>
                      <span className="meta-tag">count {values.count || 0}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      <main className="chat-area">
        {messages.length === 0 && (
          <div className="empty-state">
            <div className="empty-icon">?</div>
            <p>
              {requestMode === 'chat'
                ? 'Ask a multi-turn question from your configured knowledge base.'
                : 'Run a single-turn query against the current backend pipeline.'}
            </p>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`message message-${msg.role}`}>
            <div className="message-avatar">
              {msg.role === 'user' ? 'U' : msg.role === 'assistant' ? 'A' : '!'}
            </div>
            <div className="message-body">
              {msg.role === 'assistant' ? (
                <div className="message-content"><ReactMarkdown>{msg.content}</ReactMarkdown></div>
              ) : (
                <div className="message-content">{msg.content}</div>
              )}
              {msg.role === 'assistant' && msg.evidence?.length > 0 && (
                <div className="evidence-bar">
                  <span className="evidence-label">Evidence:</span>
                  {msg.evidence.map((doc, j) => (
                    <button
                      type="button"
                      key={j}
                      className="evidence-tag evidence-link"
                      onClick={() => openEvidence(doc)}
                      title="Open media file"
                    >
                      {doc}
                    </button>
                  ))}
                </div>
              )}
              {msg.role === 'assistant' && msg.business_type && (
                <div className="meta-bar">
                  {msg.request_mode && <span className="meta-tag">{msg.request_mode}</span>}
                  {msg.business_type && <span className="meta-tag">{msg.business_type}</span>}
                  {msg.intent && <span className="meta-tag">{msg.intent}</span>}
                  {msg.latency && <span className="meta-tag">{msg.latency.toFixed(0)}ms</span>}
                  {msg.cache_hit && <span className="meta-tag cache">Cache hit</span>}
                </div>
              )}
            </div>
          </div>
        ))}

        {loading && (
          <div className="message message-assistant">
            <div className="message-avatar">A</div>
            <div className="message-body"><div className="typing-indicator"><span /><span /><span /></div></div>
          </div>
        )}
        <div ref={chatEndRef} />
      </main>

      <footer className="input-area">
        <div className="input-row">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              authRequired && !accessToken
                ? 'Sign in to ask a question'
                : requestMode === 'chat'
                  ? 'Type a multi-turn question...'
                  : 'Type a single-turn query...'
            }
            rows={1}
            className="chat-input"
            disabled={authRequired && !accessToken}
          />
          <button onClick={sendMessage} disabled={!canSend} className="btn-send">
            {loading ? 'Sending' : 'Send'}
          </button>
        </div>
      </footer>
    </div>
  )
}
