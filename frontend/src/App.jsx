import { useEffect, useState } from 'react'

const STATUS_LABELS = { pending: '待处理', running: '复核中', done: '已出结论' }

export default function App() {
  const [username, setUsername] = useState('printer')
  const [password, setPassword] = useState('print123456')
  const [token, setToken] = useState(localStorage.getItem('print_token') || '')
  const [role, setRole] = useState(localStorage.getItem('print_role') || '')
  const [view, setView] = useState('jobs')
  const [rows, setRows] = useState([])
  const [sheet, setSheet] = useState('插页-02')
  const [cyan, setCyan] = useState('0.08')
  const [magenta, setMagenta] = useState('0.02')
  const [error, setError] = useState('')
  const [candidates, setCandidates] = useState([])
  const [packages, setPackages] = useState([])
  const [selected, setSelected] = useState([])
  const [openId, setOpenId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [freezeError, setFreezeError] = useState('')

  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '请求失败')
    return data
  }

  async function load() {
    setRows(await api('/api/jobs'))
  }

  async function loadFreeze() {
    const [cands, pkgs] = await Promise.all([
      api('/api/freeze/candidates'),
      api('/api/freeze/packages'),
    ])
    setCandidates(cands)
    setPackages(pkgs)
  }

  useEffect(() => {
    if (!token) return
    if (view === 'jobs') {
      load()
      const timer = setInterval(load, 1000)
      return () => clearInterval(timer)
    }
    loadFreeze()
    const timer = setInterval(loadFreeze, 1000)
    return () => clearInterval(timer)
  }, [token, view])

  async function enter() {
    const data = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    localStorage.setItem('print_token', data.access_token)
    localStorage.setItem('print_role', data.role)
    setToken(data.access_token)
    setRole(data.role)
  }

  async function send() {
    setError('')
    try {
      await api('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({
          sheet,
          cyan_mm: Number(cyan),
          magenta_mm: Number(magenta),
        }),
      })
    } catch (err) {
      setError(err.message)
    }
  }

  async function reviseRow(row) {
    setError('')
    const c = window.prompt(`「${row.sheet}」新青偏差(mm)`, String(row.cyan_mm))
    if (c === null) return
    const m = window.prompt(`「${row.sheet}」新品偏差(mm)`, String(row.magenta_mm))
    if (m === null) return
    try {
      await api(`/api/jobs/${row.id}/revise`, {
        method: 'POST',
        body: JSON.stringify({ cyan_mm: Number(c), magenta_mm: Number(m) }),
      })
    } catch (err) {
      setError(err.message)
    }
  }

  function toggle(id) {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    )
  }

  async function sign() {
    setFreezeError('')
    try {
      await api('/api/freeze', {
        method: 'POST',
        body: JSON.stringify({ job_ids: selected }),
      })
      setSelected([])
      await loadFreeze()
    } catch (err) {
      setFreezeError(err.message)
    }
  }

  async function togglePackage(id) {
    setFreezeError('')
    if (openId === id) {
      setOpenId(null)
      setDetail(null)
      return
    }
    try {
      setDetail(await api(`/api/freeze/packages/${id}`))
      setOpenId(id)
    } catch (err) {
      setFreezeError(err.message)
    }
  }

  function leave() {
    localStorage.clear()
    setToken('')
    setRole('')
  }

  if (!token) {
    return (
      <main>
        <h1>印刷套准复核台</h1>
        <p>提交后接口只入队。另一进程领走偏差并写结论，页面轮询到结论出现。</p>
        <input value={username} onChange={(e) => setUsername(e.target.value)} />
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        <button onClick={enter}>登录</button>
        <p>printer / print123456 可送复核并签发冻结包；checker / check123456 只能看</p>
      </main>
    )
  }

  return (
    <main>
      <h1>印刷套准复核台</h1>
      <p>
        <button disabled={view === 'jobs'} onClick={() => setView('jobs')}>总表</button>{' '}
        <button disabled={view === 'freeze'} onClick={() => setView('freeze')}>导出冻结包</button>{' '}
        <button onClick={leave}>退出</button>
      </p>
      {view === 'jobs' && (
        <>
          {role === 'writer' && (
            <p>
              <input value={sheet} onChange={(e) => setSheet(e.target.value)} />
              <input value={cyan} onChange={(e) => setCyan(e.target.value)} />
              <input value={magenta} onChange={(e) => setMagenta(e.target.value)} />
              <button onClick={send}>送复核</button>
            </p>
          )}
          {error && <p>{error}</p>}
          <table>
            <thead>
              <tr><th>印张</th><th>青</th><th>品</th><th>状态</th><th>结论</th><th>冻结包</th>{role === 'writer' && <th>现场改动</th>}</tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>{row.sheet}</td>
                  <td>{row.cyan_mm}</td>
                  <td>{row.magenta_mm}</td>
                  <td>{STATUS_LABELS[row.status] || row.status}</td>
                  <td>{row.verdict || '等待'}</td>
                  <td>{row.frozen_package_id ? `#${row.frozen_package_id}` : '—'}</td>
                  {role === 'writer' && (
                    <td><button onClick={() => reviseRow(row)}>改现场偏差重判</button></td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {view === 'freeze' && (
        <>
          <h2>可签发列表</h2>
          {freezeError && <p>{freezeError}</p>}
          {candidates.length === 0 ? (
            <p>暂无已出结论且未冻结的印张。</p>
          ) : (
            <table>
              <thead>
                <tr>
                  {role === 'writer' && <th>选择</th>}
                  <th>印张</th><th>青</th><th>品</th><th>结论</th><th>理由</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((row) => (
                  <tr key={row.id}>
                    {role === 'writer' && (
                      <td>
                        <input
                          type="checkbox"
                          checked={selected.includes(row.id)}
                          onChange={() => toggle(row.id)}
                        />
                      </td>
                    )}
                    <td>{row.sheet}</td>
                    <td>{row.cyan_mm}</td>
                    <td>{row.magenta_mm}</td>
                    <td>{row.verdict}</td>
                    <td>{row.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {role === 'writer' && (
            <p>
              <button disabled={selected.length === 0} onClick={sign}>
                签发选中（{selected.length}）
              </button>
            </p>
          )}
          <h2>已签发包</h2>
          {packages.length === 0 ? (
            <p>暂无冻结包。</p>
          ) : (
            <table>
              <thead>
                <tr><th>包号</th><th>签发人</th><th>签发时间</th><th>笔数</th><th></th></tr>
              </thead>
              <tbody>
                {packages.map((pkg) => (
                  <tr key={pkg.id}>
                    <td>#{pkg.id}</td>
                    <td>{pkg.created_by}</td>
                    <td>{new Date(pkg.created_at).toLocaleString()}</td>
                    <td>{pkg.item_count}</td>
                    <td>
                      <button onClick={() => togglePackage(pkg.id)}>
                        {openId === pkg.id ? '收起' : '查看明细'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {openId !== null && detail && (
            <>
              <h2>包 #{detail.id} 明细（签发当时快照，只读）</h2>
              <table>
                <thead>
                  <tr><th>印张</th><th>青</th><th>品</th><th>结论</th><th>理由</th></tr>
                </thead>
                <tbody>
                  {detail.items.map((item) => (
                    <tr key={item.id}>
                      <td>{item.sheet}</td>
                      <td>{item.cyan_mm}</td>
                      <td>{item.magenta_mm}</td>
                      <td>{item.verdict}</td>
                      <td>{item.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
    </main>
  )
}
