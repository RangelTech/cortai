;(() => {
  // Exemplos: quadros reais de cortes feitos pelo Cortaí (já com gancho e legenda queimados) e cenas geradas por IA (pessoas fictícias)
  const EX = [
    { img: 'gta6', rede: 'TikTok' },
    { img: 'futebol', rede: 'Reels', g: 'O GOL QUE PAROU O ESTÁDIO', l: 'ATÉ O GOLEIRO <b>APLAUDIU</b>' },
    { img: 'carro', rede: 'Shorts', g: 'ELE COMPROU À VISTA', l: 'E O PREÇO É <b>ABSURDO</b>' },
    { img: 'anime', rede: 'TikTok' },
    { img: 'influencer', rede: 'Reels', g: 'MINHA ROTINA DE R$ 50 MIL', l: 'O SEGREDO É <b>ESSE</b>' },
    { img: 'filme', rede: 'Shorts' },
    { img: 'podcast', rede: 'TikTok', g: 'ELE NÃO SABIA QUE ESTAVA GRAVANDO', l: 'E SOLTOU <b>TUDO</b>' },
    { img: 'polvo', rede: 'Reels' },
    { img: 'treino', rede: 'Shorts', g: 'O TREINO DE 7 MINUTOS', l: 'QUE <b>SECA</b> A BARRIGA' },
    { img: 'receita', rede: 'TikTok', g: 'BOLO QUE VENDE 100 POR DIA', l: 'A RECEITA É <b>SIMPLES</b>' },
  ]
  const trilho = document.getElementById('trilho'), pontos = document.getElementById('pontos-c')
  if (!trilho) return
  trilho.innerHTML = EX.map((e, i) => `<div class="cel-c" data-i="${i}"><div class="img" style="background-image:url('/s/ex/${e.img}.webp')"></div>
    ${e.g ? `<div class="g">${e.g}</div>` : ''}${e.l ? `<div class="l">${e.l}</div>` : ''}
    <span class="rede">${e.rede}</span>
    <div class="ui"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3.2 4.5 7 4.5c2 0 3.4 1.1 5 3 1.6-1.9 3-3 5-3 3.8 0 6.1 3.9 4.6 7.3C19.5 16.4 12 21 12 21z"/></svg><svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 4h16v12H7l-3 3z"/></svg><svg viewBox="0 0 24 24" fill="currentColor"><path d="m3 11 18-8-8 18-2-8z"/></svg></div></div>`).join('')
  pontos.innerHTML = EX.map((_, i) => `<i data-i="${i}"></i>`).join('')
  const cards = [...trilho.children], dots = [...pontos.children]
  let atual = 0, timer = null
  const N = EX.length
  function pintar() {
    cards.forEach((c, i) => {
      let d = i - atual
      if (d > N / 2) d -= N
      if (d < -N / 2) d += N
      const abs = Math.abs(d)
      const vis = abs <= 2
      c.style.transform = `translateX(${d * 62}%) translateZ(${-abs * 120}px) rotateY(${d * -18}deg) scale(${1 - abs * 0.12})`
      c.style.opacity = vis ? String(1 - abs * 0.28) : '0'
      c.style.zIndex = String(10 - abs)
      c.style.pointerEvents = vis ? 'auto' : 'none'
      c.classList.toggle('centro', d === 0)
    })
    dots.forEach((d, i) => d.classList.toggle('on', i === atual))
  }
  const ir = (n) => { atual = (n + N) % N; pintar() }
  const auto = () => { clearInterval(timer); timer = setInterval(() => ir(atual + 1), 2500) }
  trilho.addEventListener('click', (e) => { const c = e.target.closest('.cel-c'); if (c) { ir(+c.dataset.i); auto() } })
  pontos.addEventListener('click', (e) => { const d = e.target.closest('i'); if (d) { ir(+d.dataset.i); auto() } })
  let x0 = null
  trilho.addEventListener('pointerdown', (e) => { x0 = e.clientX })
  addEventListener('pointerup', (e) => { if (x0 === null) return; const dx = e.clientX - x0; x0 = null; if (Math.abs(dx) > 40) { ir(atual + (dx < 0 ? 1 : -1)); auto() } })
  trilho.addEventListener('mouseenter', () => clearInterval(timer))
  trilho.addEventListener('mouseleave', auto)
  pintar(); auto()
})()
