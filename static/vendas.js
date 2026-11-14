;(() => {
  const $ = (s) => document.querySelector(s)
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
  const brl = (v) => 'R$ ' + Number(v || 0).toLocaleString('pt-BR', { maximumFractionDigits: 0 })
  const brl2 = (v) => 'R$ ' + Number(v || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  function conta() {
    const dia = +$('#c-dia').value, mil = +$('#c-views').value, cli = +$('#c-cli').value
    $('#c-dia-v').textContent = dia; $('#c-views-v').textContent = mil + ' mil'; $('#c-cli-v').textContent = cli
    const mon = dia * 30 * mil * 1.5, vend = cli * 300
    $('#c-mon').textContent = brl(mon); $('#c-vend').textContent = brl(vend); $('#c-mes').textContent = brl(mon + vend)
  }
  ;['#c-dia', '#c-views', '#c-cli'].forEach((s) => $(s).addEventListener('input', conta))
  conta()
  fetch('/api/planos').then((r) => r.json()).then((j) => {
    const d = j.planos[0]?.lancamento_ate
    if (d) { const [, m, di] = d.split('-'); $('#lanc-data').textContent = `${di}/${m}` }
    $('#planos').innerHTML = j.planos.map((p, i) => `<div class="preco ${i === 1 ? 'dest' : ''}">${i === 1 ? '<span class="selo-top">Mais escolhido</span>' : ''}
      <div class="display" style="font-size:20px">${esc(p.nome)}</div><div class="valor">${brl2(p.preco)}<small>/mês</small></div>
      <div style="color:var(--mut);font-size:14px">Depois do lançamento: ${brl2(p.depois)}/mês</div>
      <ul class="lista-ok">${p.itens.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
      ${p.checkout ? `<a class="btn ${i === 1 ? 'btn-pri' : 'btn-sec'}" style="width:100%" href="${esc(p.checkout)}" target="_blank" rel="noopener">Assinar o ${esc(p.nome)}</a>` : `<button class="btn btn-sec" style="width:100%" data-entrar>Quero o ${esc(p.nome)}</button>`}</div>`).join('')
  }).catch(() => {})
})()
