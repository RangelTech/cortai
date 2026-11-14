;(() => {
  const $ = (s) => document.querySelector(s)
  const modal = $('#m-entrar')
  let email = ''
  const msg = (t, tipo = 'erro') => { const m = $('#m-msg'); m.textContent = t || ''; m.className = 'msg ' + tipo }
  const abrir = async () => {
    try { const r = await fetch('/api/conta'); if (r.ok) return (location.href = '/painel') } catch {}
    modal.hidden = false; msg(''); $('#passo-email').hidden = false; $('#passo-codigo').hidden = true; setTimeout(() => $('#email').focus(), 50)
  }
  document.querySelectorAll('[data-entrar]').forEach((b) => (b.onclick = abrir))
  modal.addEventListener('click', (e) => { if (e.target === modal || e.target.closest('[data-fechar]')) modal.hidden = true })
  addEventListener('keydown', (e) => { if (e.key === 'Escape') modal.hidden = true })
  const api = async (url, corpo) => {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corpo) })
    let j = {}
    try { j = await r.json() } catch {}
    if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : 'Não deu certo. Tente de novo.')
    return j
  }
  $('#f-email').onsubmit = async (e) => {
    e.preventDefault()
    const b = e.submitter; b.disabled = true; msg('')
    try {
      email = $('#email').value.trim()
      const j = await api('/api/entrar/codigo', { email })
      $('#passo-email').hidden = true; $('#passo-codigo').hidden = false
      $('#cod-txt').textContent = `Mandamos 6 números para ${email}. Confira também o spam.`
      if (j.codigo_dev) $('#codigo').value = j.codigo_dev
      setTimeout(() => $('#codigo').focus(), 50)
    } catch (err) { msg(err.message) } finally { b.disabled = false }
  }
  $('#f-cod').onsubmit = async (e) => {
    e.preventDefault()
    const b = e.submitter; b.disabled = true; msg('')
    try { await api('/api/entrar', { email, codigo: $('#codigo').value }); msg('Tudo certo! Abrindo…', 'ok'); location.href = '/painel' }
    catch (err) { msg(err.message) } finally { b.disabled = false }
  }
  $('#voltar-email').onclick = () => { $('#passo-email').hidden = false; $('#passo-codigo').hidden = true; msg('') }
  if (new URLSearchParams(location.search).has('entrar')) abrir()
})()
