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

  async function assinatura() {
    var reg = await navigator.serviceWorker.ready;
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
    var st = await estado();
    if (st === 'ios-instalar' || st === 'nao-suportado') { alert(TEXTO[st]); return false; }
    var perm = await Notification.requestPermission();
    if (perm !== 'granted') {
      alert('Sem a permissão do navegador não dá para enviar notificações.');
      await atualiza(); return false;
    }
    try {
      var reg = await navigator.serviceWorker.ready;
      var chave = (await (await fetch('/push/chave', {credentials: 'same-origin'})).json()).publicKey;
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
