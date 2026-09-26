import { useCallback, useEffect, useState } from 'react'

const STATUS_TEXT = { pending: '待处理', running: '判定中', done: '已出结论' }

export default function App() {
  const [username, setUsername] = useState('printer')
  const [password, setPassword] = useState('print123456')
  const [token, setToken] = useState(localStorage.getItem('print_token') || '')
  const [role, setRole] = useState(localStorage.getItem('print_role') || '')
  const [page, setPage] = useState('jobs')
  const [error, setError] = useState('')

  const api = useCallback(
    async (path, options = {}) => {
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
    },
    [token],
  )

  async function enter() {
    setError('')
    try {
      const data = await api('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      localStorage.setItem('print_token', data.access_token)
      localStorage.setItem('print_role', data.role)
      setToken(data.access_token)
      setRole(data.role)
      setPage('jobs')
    } catch (err) {
      setError(err.message)
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
        {error && <p>{error}</p>}
        <p>printer / print123456 可送复核、改偏差、签发；checker / check123456 只可翻包查看</p>
      </main>
    )
  }

  return (
    <main>
      <h1>印刷套准复核台</h1>
      <nav style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <button disabled={page === 'jobs'} onClick={() => setPage('jobs')}>总表</button>
        <button disabled={page === 'freeze'} onClick={() => setPage('freeze')}>导出冻结</button>
        <span style={{ marginLeft: 'auto' }}>
          {role === 'writer' ? '印刷员' : '质检员'}（只读）
          <button onClick={leave} style={{ marginLeft: 8 }}>退出</button>
        </span>
      </nav>
      {page === 'jobs' ? <JobsPage api={api} role={role} /> : <FreezePage api={api} role={role} />}
    </main>
  )
}

function JobsPage({ api, role }) {
  const [rows, setRows] = useState([])
  const [sheet, setSheet] = useState('插页-02')
  const [cyan, setCyan] = useState('0.08')
  const [magenta, setMagenta] = useState('0.02')
  const [error, setError] = useState('')
  const [editing, setEditing] = useState(null)

  const load = useCallback(async () => {
    setRows(await api('/api/jobs'))
  }, [api])

  useEffect(() => {
    load()
    const timer = setInterval(load, 1000)
    return () => clearInterval(timer)
  }, [load])

  async function send() {
    setError('')
    try {
      await api('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({ sheet, cyan_mm: Number(cyan), magenta_mm: Number(magenta) }),
      })
    } catch (err) {
      setError(err.message)
    }
  }

  async function saveEdit(id, nextCyan, nextMagenta) {
    setError('')
    try {
      await api(`/api/jobs/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ cyan_mm: Number(nextCyan), magenta_mm: Number(nextMagenta) }),
      })
      setEditing(null)
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      <p>总表只显示当前行数字；已冻结行随后续改判刷新，但已签发冻结包内仍是签发当时的数字。</p>
      {role === 'writer' && (
        <p>
          <input value={sheet} onChange={(e) => setSheet(e.target.value)} />
          <input value={cyan} onChange={(e) => setCyan(e.target.value)} />
          <input value={magenta} onChange={(e) => setMagenta(e.target.value)} />
          <button onClick={send}>送复核</button>
        </p>
      )}
      {error && <p>{error}</p>}
      <table border={1} cellPadding={4}>
        <thead>
          <tr><th>印张</th><th>青(mm)</th><th>品(mm)</th><th>状态</th><th>结论</th><th>理由</th><th>冻结</th>{role === 'writer' && <th>操作</th>}</tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <JobRow
              key={row.id}
              row={row}
              canEdit={role === 'writer'}
              editing={editing === row.id}
              onStart={() => setEditing(row.id)}
              onCancel={() => setEditing(null)}
              onSave={(c, m) => saveEdit(row.id, c, m)}
            />
          ))}
        </tbody>
      </table>
    </section>
  )
}

function JobRow({ row, canEdit, editing, onStart, onCancel, onSave }) {
  const [cyan, setCyan] = useState(String(row.cyan_mm))
  const [magenta, setMagenta] = useState(String(row.magenta_mm))

  if (editing && row.status === 'done') {
    return (
      <tr>
        <td>{row.sheet}</td>
        <td><input value={cyan} onChange={(e) => setCyan(e.target.value)} /></td>
        <td><input value={magenta} onChange={(e) => setMagenta(e.target.value)} /></td>
        <td>{STATUS_TEXT[row.status]}</td>
        <td>{row.verdict}</td>
        <td>{row.reason}</td>
        <td>{row.frozen ? '已冻结' : ''}</td>
        {canEdit && (
          <td>
            <button onClick={() => onSave(cyan, magenta)}>保存并重判</button>{' '}
            <button onClick={onCancel}>取消</button>
          </td>
        )}
      </tr>
    )
  }

  return (
    <tr>
      <td>{row.sheet}</td>
      <td>{row.cyan_mm}</td>
      <td>{row.magenta_mm}</td>
      <td>{STATUS_TEXT[row.status] || row.status}</td>
      <td>{row.verdict || '等待'}</td>
      <td>{row.reason}</td>
      <td>{row.frozen ? '已冻结' : ''}</td>
      {canEdit && (
        <td>{row.status === 'done' && <button onClick={onStart}>改偏差</button>}</td>
      )}
    </tr>
  )
}

function FreezePage({ api, role }) {
  const [overview, setOverview] = useState({ signable: [], packages: [] })
  const [picked, setPicked] = useState(() => new Set())
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    const data = await api('/api/freeze')
    setOverview(data)
  }, [api])

  useEffect(() => {
    load()
    const timer = setInterval(load, 1500)
    return () => clearInterval(timer)
  }, [load])

  function toggle(id) {
    setPicked((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function sign() {
    setError('')
    try {
      const pkg = await api('/api/freeze', {
        method: 'POST',
        body: JSON.stringify({ job_ids: [...picked] }),
      })
      setPicked(new Set())
      await load()
      setDetail(pkg)
    } catch (err) {
      setError(err.message)
    }
  }

  async function openPkg(id) {
    setError('')
    try {
      setDetail(await api(`/api/freeze/${id}`))
    } catch (err) {
      setError(err.message)
    }
  }

  if (detail) {
    return (
      <section>
        <button onClick={() => setDetail(null)}>← 返回冻结页</button>
        <h2>冻结包 {detail.package_no}（只读）</h2>
        <p>
          签发人：{detail.signed_by} ｜ 签发时间：{new Date(detail.signed_at).toLocaleString('zh-CN')} ｜ 共 {detail.items.length} 条
        </p>
        <p>包内为签发当时的印张偏差结论与理由，之后总表改判不影响本包数字。</p>
        <table border={1} cellPadding={4}>
          <thead>
            <tr><th>印张</th><th>青(mm)</th><th>品(mm)</th><th>结论</th><th>理由</th><th>原送检人</th></tr>
          </thead>
          <tbody>
            {detail.items.map((item) => (
              <tr key={item.id}>
                <td>{item.sheet}</td>
                <td>{item.cyan_mm}</td>
                <td>{item.magenta_mm}</td>
                <td>{item.verdict}</td>
                <td>{item.reason}</td>
                <td>{item.created_by}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    )
  }

  return (
    <section>
      <h2>可签发列表（已出结论、未冻结）</h2>
      {error && <p>{error}</p>}
      {overview.signable.length === 0 ? (
        <p>暂无可签发的结论。</p>
      ) : (
        <>
          <table border={1} cellPadding={4}>
            <thead>
              <tr><th>勾选</th><th>印张</th><th>青(mm)</th><th>品(mm)</th><th>结论</th><th>理由</th></tr>
            </thead>
            <tbody>
              {overview.signable.map((row) => (
                <tr key={row.id}>
                  <td>
                    <input
                      type="checkbox"
                      checked={picked.has(row.id)}
                      onChange={() => toggle(row.id)}
                      disabled={role !== 'writer'}
                    />
                  </td>
                  <td>{row.sheet}</td>
                  <td>{row.cyan_mm}</td>
                  <td>{row.magenta_mm}</td>
                  <td>{row.verdict}</td>
                  <td>{row.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {role === 'writer' ? (
            <p>
              <button onClick={sign} disabled={picked.size === 0}>签发冻结包（{picked.size} 条）</button>
            </p>
          ) : (
            <p>质检可翻包查看，但不能签发；仅印刷员可签发。</p>
          )}
        </>
      )}

      <h2>已签发包</h2>
      {overview.packages.length === 0 ? (
        <p>尚无已签发的冻结包。</p>
      ) : (
        <table border={1} cellPadding={4}>
          <thead>
            <tr><th>包号</th><th>签发人</th><th>签发时间</th><th>条数</th><th>操作</th></tr>
          </thead>
          <tbody>
            {overview.packages.map((pkg) => (
              <tr key={pkg.id}>
                <td>{pkg.package_no}</td>
                <td>{pkg.signed_by}</td>
                <td>{new Date(pkg.signed_at).toLocaleString('zh-CN')}</td>
                <td>{pkg.job_count}</td>
                <td><button onClick={() => openPkg(pkg.id)}>翻包查看</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
