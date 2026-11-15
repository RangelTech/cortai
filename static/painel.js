;(() => {
  const $ = (s) => document.querySelector(s)
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
  const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`
  let conta = null
  let timer = null
  const msg = (t, tipo = 'erro') => { const m = $('#msg'); m.textContent = t || ''; m.className = 'msg ' + tipo }

  async function api(url, opt = {}) {
    const r = await fetch(url, { ...opt, headers: { 'Content-Type': 'application/json', ...(opt.headers || {}) } })
    if (r.status === 401) { location.href = '/?entrar=1'; throw new Error('entre de novo') }
    let j = {}
    try { j = await r.json() } catch {}
    if (!r.ok) { const e = new Error(typeof j.detail === 'string' ? j.detail : 'Não deu certo. Tente de novo.'); e.status = r.status; throw e }
    return j
  }

  function abrirPerfil(obrigatorio) {
    const m = $('#m-perfil'), p = conta?.perfil || {}
    $('#pf-ig').value = p.arroba ? '@' + p.arroba : ''
    $('#pf-tt').value = p.arroba_tiktok ? '@' + p.arroba_tiktok : ''
    $('#pf-cor').value = p.cor || '#ff3d6e'
    if (p.redes?.length) document.querySelectorAll('.redes-op input').forEach((c) => (c.checked = p.redes.includes(c.value)))
    m.dataset.obrigatorio = obrigatorio ? '1' : ''
    m.hidden = false
  }
  async function carregarConta() {
    conta = await api('/api/conta')
    if (conta.assinante && !conta.perfil_ok && $('#m-perfil').hidden) abrirPerfil(true)
    $('#cota').innerHTML = conta.assinante ? `<b>${conta.restam}</b> de ${conta.limite} vídeos neste mês` : "Sem plano ativo"
    const f = $('#aviso-assinar')
    const brl2 = (v) => 'R$ ' + Number(v || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    if (!conta.assinante) {
      f.hidden = false
      f.className = 'faixa planos-faixa'
      f.innerHTML = `<div><b class="display" style="font-size:20px">Escolha seu plano para começar a criar</b><div style="color:var(--mut);font-size:14px;margin-top:4px">Preço de lançamento por tempo limitado. Pague com este mesmo e-mail (${esc(conta.email)}): o plano libera sozinho em segundos.</div></div>
        <div class="mini-planos">${conta.planos.map((p, i) => `<a class="mp ${i === 1 ? 'dest' : ''}" href="${esc(p.checkout || '#')}" target="_blank" rel="noopener"><b>${esc(p.nome)}</b><span>${brl2(p.preco)}/mês</span><small>${p.videos_mes} vídeos</small></a>`).join('')}</div>`
    } else if (conta.restam <= 0) {
      f.hidden = false
      f.className = 'faixa'
      f.innerHTML = '<span>Você usou todos os vídeos do seu plano neste mês. Suba de plano para continuar.</span>' + conta.planos.filter((p) => p.videos_mes > conta.limite).map((p) => `<a class="btn btn-pri" href="${esc(p.checkout)}" target="_blank" rel="noopener">${esc(p.nome)}: ${p.videos_mes} vídeos</a>`).join('')
    } else {
      f.hidden = false
      f.className = 'faixa potencial'
      const mon = conta.limite * 3 * 10 * 1.5
      f.innerHTML = `<span>Seu plano dá <b>${conta.limite} vídeos</b> por mês, até ${conta.limite * 3} cortes. Com 10 mil visualizações por corte, isso pode render até <b>R$ ${mon.toLocaleString('pt-BR', { maximumFractionDigits: 0 })}</b> por mês nos programas de criadores.</span>`
    }
  }

  function cartaoCorte(t, c, k) {
    const nota = c.nota >= 80 ? '' : 'media'
    const tags = (c.hashtags || []).join(' ')
    const NOME = { tiktok: 'TikTok', instagram: 'Instagram', youtube: 'Shorts' }
    const redes = Object.keys(c.urls || {})
    return `<div class="corte" data-trab="${t.id}" data-n="${k}">
      ${c.url ? `<video src="${c.url}" ${c.capa_url ? `poster="${c.capa_url}"` : ''} controls playsinline preload="none"></video>` : '<div class="vazio" style="margin:14px">Arquivo apagado (guardamos por 7 dias)</div>'}
      ${redes.length > 1 ? `<div class="redes-tab">${redes.map((r, i) => `<button class="${i === 0 ? 'on' : ''}" data-rede-url="${c.urls[r]}">${NOME[r] || r}</button>`).join('')}</div>` : ''}
      <div class="info">
        <div class="top"><span class="nota ${nota}" title="Potencial viral">${c.nota}</span><div><div class="tt">${esc(c.titulo)}</div><div class="mut">Corte ${k} · ${mmss(c.duracao || c.fim - c.inicio)} · do minuto ${mmss(c.inicio)}</div></div></div>
        ${c.motivo ? `<div class="bloco"><strong>Por que pode viralizar</strong>${esc(c.motivo)}</div>` : ''}
        ${c.validacao ? `<div class="bloco conferido"><strong>Conferido pela IA${c.validacao.refeito ? ' (ajustado)' : ''}: nota ${c.validacao.nota}</strong>${c.validacao.problemas?.length ? esc(c.validacao.problemas[0]) : 'Enquadramento, legenda e início e fim conferidos.'}</div>` : ''}
        ${c.legenda ? `<div class="bloco"><strong>Legenda</strong><span>${esc(c.legenda)}</span><button class="copiar" data-copiar="${esc(c.legenda + (tags ? '\n\n' + tags : ''))}">Copiar</button></div>` : ''}
        ${tags ? `<div class="bloco"><strong>Hashtags</strong>${esc(tags)}</div>` : ''}
      </div>
      ${c.url ? `<div class="acoes"><a class="btn btn-pri baixar" href="${c.url}?baixar=1">Baixar${redes.length > 1 ? ' ' + (NOME[redes[0]] || '') : ''}</a>${c.editavel ? `<button class="btn btn-sec" data-editar="${esc(c.gancho || '')}" data-leg="${c.legenda_on === false ? 0 : 1}">Editar</button>` : ''}</div>` : ''}
    </div>`
  }

  function cartao(t) {
    const r = t.resultado
    const titulo = r?.video?.titulo || t.entrada.url || t.entrada.texto
    const rotulo = { fila: t.posicao ? `Na fila (${t.posicao} na frente)` : 'Na fila', rodando: 'Trabalhando', pronto: 'Pronto', erro: 'Não deu certo' }[t.estado]
    let corpo = ''
    if (t.estado === 'fila' || t.estado === 'rodando') {
      corpo = `<div class="barra"><i style="width:${Math.max(3, Math.round(t.progresso * 100))}%"></i></div><div class="etapa">${esc(t.etapa || 'Esperando a vez')}… você pode fechar a página: mandamos um e-mail quando ficar pronto.</div>`
    } else if (t.estado === 'erro') {
      corpo = `<p class="resumo" style="color:#ff9aa9">${esc(t.erro || 'Algo deu errado.')}</p>`
    } else if (r) {
      corpo = `${r.resumo ? `<p class="resumo">${esc(r.resumo)}</p>` : ''}
        <div class="cortes">${(r.cortes || []).map((c, i) => cartaoCorte(t, c, i + 1)).join('')}</div>
        ${r.dicas?.length ? `<div class="dicas"><b>Dicas para postar</b><ul>${r.dicas.map((d) => `<li>${esc(d)}</li>`).join('')}</ul></div>` : ''}`
    }
    return `<article class="trab" data-id="${t.id}">
      <div class="trab-cab"><div><div class="t">${esc(titulo)}</div><div class="s">${new Date(t.criado * 1000).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })}${r?.video?.canal ? ' · ' + esc(r.video.canal) : ''}</div></div><span class="estado ${t.estado}">${rotulo}</span></div>
      ${corpo}
    </article>`
  }

  async function listar() {
    try {
      const { trabalhos } = await api('/api/trabalhos')
      const el = $('#trabalhos')
      if (!trabalhos.length) el.innerHTML = '<div class="vazio">Nenhum pedido ainda. Cole um link acima para criar os seus primeiros cortes.</div>'
      else {
        // não redesenha cartões prontos (o vídeo tocando não pode reiniciar)
        const atuais = new Map([...el.querySelectorAll('.trab')].map((a) => [a.dataset.id, a]))
        const html = trabalhos.map((t) => {
          const a = atuais.get(t.id)
          if (a && a.dataset.estado === 'pronto' && t.estado === 'pronto') return a.outerHTML
          return cartao(t).replace('<article class="trab"', `<article class="trab" data-estado="${t.estado}"`)
        }).join('')
        const tocando = [...el.querySelectorAll('video')].some((v) => !v.paused)
        if (!tocando) el.innerHTML = html
      }
      const ativos = trabalhos.some((t) => t.estado === 'fila' || t.estado === 'rodando')
      clearTimeout(timer)
      timer = setTimeout(() => { listar(); if (!ativos) carregarConta() }, ativos ? 3000 : 30000)
    } catch (e) {
      clearTimeout(timer)
      timer = setTimeout(listar, 8000)
    }
  }

  let editando = null
  document.addEventListener('click', async (e) => {
    const rb = e.target.closest('[data-rede-url]')
    if (rb) {
      const card = rb.closest('.corte'), v = card.querySelector('video')
      card.querySelectorAll('.redes-tab button').forEach((b) => b.classList.toggle('on', b === rb))
      v.src = rb.dataset.redeUrl
      const bx = card.querySelector('.baixar'); bx.href = rb.dataset.redeUrl + '?baixar=1'; bx.textContent = 'Baixar ' + rb.textContent
      return
    }
    const ed = e.target.closest('[data-editar]')
    if (ed) {
      const card = ed.closest('.corte')
      editando = { tid: card.dataset.trab, n: +card.dataset.n }
      $('#me-gancho').value = ed.dataset.editar; $('#me-ini').value = 0; $('#me-fim').value = 0; $('#me-leg').checked = ed.dataset.leg === '1'
      $('#me-msg').textContent = ''; $('#m-editar').hidden = false
      return
    }
    if (e.target.closest('[data-fechar-m]') || e.target.id === 'm-editar') { $('#m-editar').hidden = true; return }
    if (e.target.id === 'm-perfil' && !$('#m-perfil').dataset.obrigatorio) { $('#m-perfil').hidden = true; return }
    const c = e.target.closest('[data-copiar]')
    if (c) {
      try { await navigator.clipboard.writeText(c.dataset.copiar); c.textContent = 'Copiado!'; setTimeout(() => (c.textContent = 'Copiar'), 1500) } catch {}
    }
    const a = e.target.closest('.aba')
    if (a) {
      document.querySelectorAll('.aba').forEach((x) => { x.classList.toggle('on', x === a); x.setAttribute('aria-selected', x === a) })
      const qual = a.dataset.aba
      $('#f-link').hidden = qual !== 'link'
      $('#f-texto').hidden = qual !== 'texto'
      $('#f-clonar').hidden = qual !== 'reels'
      $('#f-falado').hidden = qual !== 'falado'
      $('#em-breve').hidden = true
    }
  })
  $('#f-link').onsubmit = async (e) => {
    e.preventDefault()
    const b = e.submitter; b.disabled = true; msg('')
    try {
      await api('/api/trabalhos', { method: 'POST', body: JSON.stringify({ tipo: 'cortes', url: $('#url').value.trim(), quantos: +$('#quantos').value }) })
      $('#url').value = ''
      msg('Pedido recebido! Acompanhe abaixo.', 'ok')
      await carregarConta()
      listar()
    } catch (err) {
      msg(err.message)
      if (err.status === 402) carregarConta()
      if (err.status === 428) abrirPerfil(true)
    } finally { b.disabled = false }
  }
  $('#f-texto').onsubmit = async (e) => {
    e.preventDefault()
    const b = e.submitter; b.disabled = true; msg('')
    try {
      await api('/api/trabalhos', { method: 'POST', body: JSON.stringify({ tipo: 'animacao', texto: $('#texto').value.trim(), segundos: +$('#segundos').value, voz: $('#voz').value, estilo: $('#estilo').value }) })
      $('#texto').value = ''
      msg('Pedido recebido! A animação fica pronta em poucos minutos.', 'ok')
      await carregarConta()
      listar()
    } catch (err) { msg(err.message); if (err.status === 402) carregarConta() } finally { b.disabled = false }
  }
  $('#f-clonar').onsubmit = async (e) => {
    e.preventDefault()
    const b = e.submitter; b.disabled = true; msg('')
    try {
      await api('/api/trabalhos', { method: 'POST', body: JSON.stringify({ tipo: 'clonar', url: $('#url-ref').value.trim(), texto: $('#tema').value.trim(), voz: $('#voz-c').value }) })
      $('#url-ref').value = ''; $('#tema').value = ''
      msg('Pedido recebido! Vamos estudar o vídeo e recriar o formato com o seu tema.', 'ok')
      await carregarConta()
      listar()
    } catch (err) { msg(err.message); if (err.status === 402) carregarConta() } finally { b.disabled = false }
  }
  $('#arq-falado').onchange = () => { const f = $('#arq-falado').files[0]; $('#arq-nome').textContent = f ? `${f.name} (${(f.size / 1048576).toFixed(0)} MB)` : 'Escolha o seu vídeo falando para a câmera' }
  $('#f-falado').onsubmit = (e) => {
    e.preventDefault()
    const f = $('#arq-falado').files[0]
    if (!f) { msg('Escolha o vídeo primeiro.'); return }
    if (f.size > 400 * 1048576) { msg('O vídeo passa de 400 MB; exporte em 1080p ou menos.'); return }
    const b = e.submitter; b.disabled = true; const barra = $('#barra-up'); barra.hidden = false
    const x = new XMLHttpRequest()
    x.open('POST', '/api/falado'); x.setRequestHeader('x-arquivo', f.name.replace(/[^\w.\-]/g, '_'))
    x.upload.onprogress = (ev) => { if (ev.lengthComputable) barra.firstElementChild.style.width = (100 * ev.loaded / ev.total).toFixed(0) + '%' }
    x.onload = () => {
      b.disabled = false; barra.hidden = true
      if (x.status >= 200 && x.status < 300) { $('#arq-falado').value = ''; $('#arq-nome').textContent = 'Escolha o seu vídeo falando para a câmera'; msg('Recebido! Está na fila.', 'ok'); listar(); carregarConta(); return }
      let m = 'Não consegui enviar agora.'; try { m = JSON.parse(x.responseText).detail || m } catch {}
      msg(m); if (x.status === 428) abrirPerfil(true)
    }
    x.onerror = () => { b.disabled = false; barra.hidden = true; msg('A conexão caiu durante o envio. Tente de novo.') }
    x.send(f)
  }
  $('#ver-perfil').onclick = () => abrirPerfil(false)
  $('#f-perfil').onsubmit = async (e) => {
    e.preventDefault(); const b = e.submitter; b.disabled = true; $('#mp-msg').textContent = ''
    try {
      await api('/api/perfil', { method: 'POST', body: JSON.stringify({ arroba: $('#pf-ig').value, arroba_tiktok: $('#pf-tt').value, cor: $('#pf-cor').value,
        redes: [...document.querySelectorAll('.redes-op input:checked')].map((c) => c.value) }) })
      $('#m-perfil').hidden = true; await carregarConta()
    } catch (err) { $('#mp-msg').textContent = err.message; $('#mp-msg').className = 'msg erro' } finally { b.disabled = false }
  }
  $('#f-editar').onsubmit = async (e) => {
    e.preventDefault(); if (!editando) return
    const b = e.submitter; b.disabled = true; b.textContent = 'Refazendo as versões…'; $('#me-msg').textContent = ''
    try {
      await api(`/api/trabalhos/${editando.tid}/cortes/${editando.n}`, { method: 'POST', body: JSON.stringify({ gancho: $('#me-gancho').value, aparar_inicio: +$('#me-ini').value || 0, aparar_fim: +$('#me-fim').value || 0, legenda: $('#me-leg').checked }) })
      $('#m-editar').hidden = true
      document.querySelector(`.trab[data-id="${editando.tid}"]`)?.setAttribute('data-estado', 'editado')
      listar()
    } catch (err) { $('#me-msg').textContent = err.message; $('#me-msg').className = 'msg erro' } finally { b.disabled = false; b.textContent = 'Salvar e refazer as versões' }
  }
  $('#sair').onclick = async () => { await fetch('/api/sair', { method: 'POST' }); location.href = '/' }
  async function carregarBiblioteca() {
    try {
      const j = await api('/api/biblioteca')
      if (!j.itens.length) return
      $('#biblioteca-sec').hidden = false
      $('#biblioteca').innerHTML = j.itens.map((i) => `<div class="corte"><video src="${i.url}" poster="${i.capa_url}" controls playsinline preload="none"></video>
        <div class="info"><div class="top"><span class="tag-nicho">${esc(i.nicho)}</span><div class="tt">${esc(i.titulo)}</div></div>
        ${i.legenda ? `<div class="bloco"><strong>Legenda</strong><span>${esc(i.legenda)}</span><button class="copiar" data-copiar="${esc(i.legenda + (i.hashtags?.length ? '\n\n' + i.hashtags.join(' ') : ''))}">Copiar</button></div>` : ''}</div>
        <div class="acoes"><a class="btn btn-pri" href="${i.url}?baixar=1">Baixar</a></div></div>`).join('')
    } catch {}
  }
  carregarConta().then(() => { listar(); carregarBiblioteca() }).catch(() => {})
})()
