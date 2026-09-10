/* Notificacoes push: ativar/desativar neste aparelho e mostrar o estado.
   Elementos da pagina que reagem ao estado:
     [data-push-estado]   texto do estado
     [data-push-ativar]   botao "Ativar"      (some quando ativo/bloqueado)
     [data-push-desativar] botao "Desativar"  (so quando ativo)
     #menu-push           item do menu do usuario                          */
(function () {
  'use strict';
  var TEXTO = {
    'ativo': 'Ativas neste aparelho',
    'inativo': 'Desligadas neste aparelho',
    'bloqueado': 'Bloqueadas pelo navegador — libere nas configurações do site',
    'ios-instalar': 'No iPhone/iPad, instale o app na Tela de Início para receber notificações',
    'nao-suportado': 'Este navegador não recebe notificações push'
  };

  function b64ParaBytes(s) {
    s = s.replace(/-/g, '+').replace(/_/g, '/');
    s += '='.repeat((4 - s.length % 4) % 4);
    var bin = atob(s), out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  function ehIOS() {
    return /iphone|ipad|ipod/i.test(navigator.userAgent) ||
      (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  }
  function instalado() {
    return (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) ||
      navigator.standalone === true;
  }
  function suportado() {
    return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  }

  // serviceWorker.ready nunca resolve se o registro falhar: limita a espera
  function swPronto(ms) {
    return Promise.race([navigator.serviceWorker.ready, new Promise(function (_, rej) {
      setTimeout(function () { rej(new Error('o serviço de notificações não iniciou neste navegador')); }, ms || 4000);
    })]);
  }
  async function assinatura() {
    var reg = await swPronto();
    return reg.pushManager.getSubscription();
  }
  async function estado() {
    if (!suportado()) return (ehIOS() && !instalado()) ? 'ios-instalar' : 'nao-suportado';
    if (Notification.permission === 'denied') return 'bloqueado';
    try { return (await assinatura()) ? 'ativo' : 'inativo'; } catch (e) { return 'inativo'; }
  }
  async function enviaAoServidor(sub) {
    var r = await fetch('/push/assinar', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(sub), credentials: 'same-origin'
    });
    if (!r.ok) throw new Error('o portal recusou o aparelho (' + r.status + ')');
  }

  async function ativar() {
    if (!suportado()) { alert(TEXTO[(ehIOS() && !instalado()) ? 'ios-instalar' : 'nao-suportado']); return false; }
    if (Notification.permission === 'denied') { alert(TEXTO.bloqueado); return false; }
    // o pedido de permissao vem ANTES de qualquer outra espera: o navegador (sobretudo o
    // Safari do iPhone) so aceita se estiver colado no toque da pessoa
    var perm = await Notification.requestPermission();
    if (perm !== 'granted') {
      alert('Sem a permissão do navegador não dá para enviar notificações.');
      await atualiza(); return false;
    }
    try {
      var reg = await swPronto(8000);
      var rc = await fetch('/push/chave', {credentials: 'same-origin'});
      var dc = null;
      try { dc = await rc.json(); } catch (e) { dc = null; }       // pagina de erro/login em HTML
      if (!rc.ok || !dc || !dc.publicKey) {
        throw new Error((dc && dc.erro) || (rc.status === 401 || rc.redirected
          ? 'sua sessão expirou — entre de novo no portal'
          : 'o servidor não respondeu como esperado (' + rc.status + ')'));
      }
      var chave = dc.publicKey;
      var sub = await reg.pushManager.getSubscription() ||
        await reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: b64ParaBytes(chave)});
      await enviaAoServidor(sub);
      try { sessionStorage.setItem('push-sincronizado', '1'); } catch (e) {}
    } catch (e) {
      alert('Não foi possível ativar as notificações: ' + (e && e.message ? e.message : e));
    }
    await atualiza();
    return true;
  }
  async function desativar() {
    try {
      var sub = await assinatura();
      if (sub) {
        await fetch('/push/cancelar', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({endpoint: sub.endpoint}), credentials: 'same-origin'
        });
        await sub.unsubscribe();
      }
    } catch (e) {}
    await atualiza();
  }

  async function atualiza() {
    var st = await estado();
    document.querySelectorAll('[data-push-estado]').forEach(function (el) {
      el.textContent = TEXTO[st]; el.dataset.st = st;
    });
    document.querySelectorAll('[data-push-ativar]').forEach(function (el) {
      el.hidden = !(st === 'inativo' || st === 'ios-instalar');
    });
    document.querySelectorAll('[data-push-desativar]').forEach(function (el) { el.hidden = st !== 'ativo'; });
    var m = document.getElementById('menu-push');
    if (m) {
      m.hidden = (st === 'nao-suportado');
      m.textContent = st === 'ativo' ? '🔕 Desativar notificações neste aparelho'
        : st === 'bloqueado' ? '🔔 Notificações bloqueadas no navegador' : '🔔 Ativar notificações';
      m.dataset.st = st;
    }
    return st;
  }

  // ---- convite no primeiro uso (o pedido oficial so pode sair de um toque) ----
  var ADIA_DIAS = 7;
  function conviteAdiado() {
    try { return Date.now() < (+localStorage.getItem('push-convite-adiado') || 0); } catch (e) { return false; }
  }
  function mostraConvite(st) {
    if (st !== 'inativo' && st !== 'ios-instalar') return;           // ativo/bloqueado/sem suporte: nada
    if (st === 'inativo' && Notification.permission !== 'default') return;
    if (conviteAdiado() || document.getElementById('push-convite')) return;
    var ios = st === 'ios-instalar';
    var d = document.createElement('div');
    d.id = 'push-convite'; d.className = 'push-convite';
    d.setAttribute('role', 'dialog'); d.setAttribute('aria-label', 'Receber avisos no aparelho');
    d.innerHTML =
      '<img src="/static/icons/icon-192.png?v=4" alt="" width="44" height="44">' +
      '<div class="pc-txt"><strong>Receber os avisos no aparelho?</strong><span>' +
      (ios ? 'No iPhone/iPad: Compartilhar → Adicionar à Tela de Início, e abra o portal por esse ícone para ativar.'
           : 'Atividades que vencem, atrasos e aprovações chegam como notificação, mesmo com o portal fechado.') +
      '</span></div><div class="pc-acoes">' +
      (ios ? '' : '<button type="button" class="btn btn-primary btn-sm" data-pc="sim">Ativar</button>') +
      '<button type="button" class="btn btn-outline btn-sm" data-pc="nao">' + (ios ? 'Entendi' : 'Agora não') +
      '</button></div>';
    document.body.appendChild(d);
    d.addEventListener('click', function (ev) {
      var b = ev.target.closest('[data-pc]'); if (!b) return;
      d.remove();
      if (b.dataset.pc === 'sim') { ativar(); return; }               // chamada direta: ainda no toque
      try { localStorage.setItem('push-convite-adiado', String(Date.now() + ADIA_DIAS * 864e5)); } catch (e) {}
    });
  }

  // menu do usuario: alterna
  window.menuPush = function (ev) {
    ev.preventDefault();
    var m = document.getElementById('menu-push');
    if (m && m.dataset.st === 'ativo') {
      if (confirm('Parar de receber notificações neste aparelho?')) desativar();
    } else if (m && m.dataset.st === 'bloqueado') {
      alert(TEXTO.bloqueado);
    } else {
      ativar();
    }
  };
  window.Push = {estado: estado, ativar: ativar, desativar: desativar, atualiza: atualiza};

  document.addEventListener('DOMContentLoaded', async function () {
    var st = await atualiza();
    setTimeout(function () { mostraConvite(st); }, 1200);             // deixa a pagina carregar antes
    // re-sincroniza uma vez por sessao (se o banco do portal foi recriado, o aparelho volta)
    if (st === 'ativo') {
      try {
        if (!sessionStorage.getItem('push-sincronizado')) {
          await enviaAoServidor(await assinatura());
          sessionStorage.setItem('push-sincronizado', '1');
        }
      } catch (e) {}
    }
  });
})();
