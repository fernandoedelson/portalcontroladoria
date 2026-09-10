/* Ao tocar numa notificacao push, o service worker guarda o aviso (titulo,
   texto e, se agrupado, a lista) no Cache 'avisos-push' e abre a tela com
   ?aviso=1. Aqui a tela le o aviso e mostra no topo do conteudo — o que
   chegou no celular aparece dentro do app, nao so a tela de destino.
   Paginas que ja mostram o conteudo completo marcam [data-sem-aviso-push]. */
(function () {
  'use strict';
  var params = new URLSearchParams(location.search);
  if (!params.has('aviso')) return;
  params.delete('aviso');                       // recarregar nao repete o quadro
  var limpa = location.pathname + (params.toString() ? '?' + params : '') + location.hash;
  try { history.replaceState(history.state, '', limpa); } catch (e) {}
  if (!('caches' in window)) return;

  var CHAVE = '/__aviso-push';
  caches.open('avisos-push').then(function (c) {
    return c.match(CHAVE).then(function (r) {
      if (!r) return null;
      return r.json().then(function (d) { c.delete(CHAVE); return d; });
    });
  }).then(function (d) {
    if (!d || (d.em && Date.now() - d.em > 10 * 60 * 1000)) return;   // aviso velho: ignora
    if (document.querySelector('[data-sem-aviso-push]')) return;
    mostra(d);
  }).catch(function () {});

  function el(tag, cls, txt) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt) e.textContent = txt;              // sempre texto: nada de HTML vindo do push
    return e;
  }
  function mostra(d) {
    var alvo = document.getElementById('conteudo');
    if (!alvo) return;
    var box = el('section', 'aviso-push');
    box.setAttribute('role', 'status');
    var topo = el('div', 'aviso-push-topo');
    topo.appendChild(el('span', 'aviso-push-rotulo', 'Notificação recebida'));
    var fecha = el('button', 'aviso-push-fecha', '×');
    fecha.type = 'button';
    fecha.setAttribute('aria-label', 'Fechar aviso');
    fecha.onclick = function () { box.remove(); };
    topo.appendChild(fecha);
    box.appendChild(topo);
    box.appendChild(el('strong', 'aviso-push-titulo', d.t || 'Controladoria J&F'));
    if (d.i && d.i.length) {
      var ul = el('ul', 'aviso-push-lista');
      d.i.forEach(function (it) {
        var li = el('li');
        var a = el('a', null, it.t || 'Aviso');
        a.href = (it.u && it.u.charAt(0) === '/') ? it.u : '/';   // so links internos
        li.appendChild(a);
        if (it.b) li.appendChild(el('span', 'aviso-push-texto', it.b));
        ul.appendChild(li);
      });
      box.appendChild(ul);
    } else if (d.b) {
      box.appendChild(el('p', 'aviso-push-texto', d.b));
    }
    alvo.insertBefore(box, alvo.firstChild);
    try { box.scrollIntoView({block: 'start'}); } catch (e) {}
  }
})();
