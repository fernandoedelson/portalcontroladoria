# Deploy na Render (substituindo o Release Builder)

Objetivo: colocar o **Portal de Gestão da Controladoria** no lugar do Release
Builder, mantendo **um único Starter**. SQLite + disco persistente (os dados
sobrevivem a deploys).

> ⚠️ Substituir = aposentar o Release Builder. **Faça o backup do banco dele
> ANTES** (no chat do projeto Release Builder). Depois de excluir o serviço, o
> disco e a URL dele se perdem.

## 0. Pré-requisitos
- Repositório no GitHub com o push feito: `github.com/fernandoedelson/portalcontroladoria`.
- Backup do Release Builder já salvo.
- O seu SQLite atual com dados (`instance/portal.db`, com os 26 indicadores, carteira etc.).

## 1. Criar o novo serviço (Blueprint)
1. Render → **New +** → **Blueprint** → conecte o repo `portalcontroladoria`.
2. A Render lê o `render.yaml` e propõe: Web Service **Starter** + disco `dados` (1 GB) em `/var/data` + `SECRET_KEY` gerado.
3. **Confira a região** (`region:` no `render.yaml`) — deixe igual à do serviço antigo.
4. **Apply** → aguarde o build/deploy. No primeiro boot o banco está **vazio** (sem usuários) — isso é esperado; corrigimos no passo 3.

> Alternativa sem Blueprint: **New + → Web Service** → repo → Runtime Python →
> Build `pip install -r requirements.txt` → Start `python run.py` → plano Starter
> → adicione um **Disk** em `/var/data` (1 GB) → env vars `HOST=0.0.0.0`,
> `PORTAL_DATA_DIR=/var/data`, `SECRET_KEY=<algo aleatório>`.

## 2. Nome / subdomínio
- O subdomínio vem do **nome do serviço**. Se o nome desejado estiver preso ao
  Release Builder, **renomeie ou exclua** o serviço antigo primeiro para liberar.
- Render → serviço → **Settings → Name** para ajustar (`<nome>.onrender.com`).

## 3. Levar os seus dados (SQLite) para o disco
O disco começa vazio. Suba o seu banco atual para `/var/data/instance/portal.db`
via SSH da Render (Starter tem SSH):

1. Render → serviço → **Settings → SSH** → cadastre sua chave pública.
2. No seu PC (Git Bash), com o serviço no ar:
   ```bash
   # descubra o endereço SSH em Settings → SSH (algo como srv-xxxxx@ssh.oregon.render.com)
   scp "C:/Users/fernandoedelson-jfm/git/portal-gestao-controladoria/instance/portal.db" \
       srv-xxxxx@ssh.oregon.render.com:/var/data/instance/portal.db
   ```
3. Render → serviço → **Manual Deploy → Restart** (ou Deploy) para reler o banco.
4. Login: `controladoria@jfsa.com.br` / `Controladoria@2026`.

> Se preferir começar limpo (sem trazer dados), pule o passo 3 e rode uma vez,
> na **Shell** da Render, `python seed.py` — mas os indicadores virão vazios,
> porque a planilha de metas não está no repo. Trazer o `portal.db` é o certo.

## 4. Desativar o Release Builder
Só depois de confirmar que o Controladoria está no ar e com seus dados:
- Render → serviço do Release Builder → **Settings → Delete**.
- Se houver **Cloudflare** apontando para ele, ajuste/remova o registro/route.

## Variáveis de ambiente (resumo)
| Var | Valor | Para quê |
|-----|-------|----------|
| `HOST` | `0.0.0.0` | escutar em todas as interfaces (Render) |
| `PORT` | (a Render injeta) | porta do serviço |
| `PORTAL_DATA_DIR` | `/var/data` | pôr `instance/`+`uploads/`+`data/` no disco |
| `SECRET_KEY` | (gerado) | segurança de sessão/CSRF |
| `TEAM_PORTAL_URL` | `https://portalcontroladoria.onrender.com` | link nas mensagens |
| `SMTP_USER` | conta Gmail remetente | liga o canal de e-mail |
| `SMTP_PASSWORD` | **senha de app** do Gmail (16 letras) | autenticação SMTP |
| `SMTP_FROM` | (opcional) endereço exibido | padrão = `SMTP_USER` |
| `TWILIO_ACCOUNT_SID` | igual ao do Release Builder | liga o WhatsApp |
| `TWILIO_AUTH_TOKEN` | igual ao do Release Builder | autenticação Twilio |
| `TWILIO_WHATSAPP_FROM` | número do sandbox (`whatsapp:+1415…`) | remetente |

## E-mail (Gmail) e WhatsApp (Twilio)
1. **Gmail:** na conta Google remetente, ative a verificação em 2 etapas →
   *Senhas de app* → gere uma para "Portal" → use os 16 caracteres em `SMTP_PASSWORD`.
2. **Twilio:** copie `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` e `TWILIO_WHATSAPP_FROM`
   do serviço do Release Builder (aba Environment) — antes de excluí-lo.
3. Cadastre o WhatsApp de cada pessoa em **Administração › Time**.
4. Sandbox: cada pessoa manda uma vez `join <código>` para o número do sandbox.
5. Em **Alertas › Testar canais**, envie um teste para você mesmo.

## Segurança
App público na internet: mantenha `SECRET_KEY` forte (gerado pela Render),
troque a senha do admin após o primeiro acesso e considere manter o **Cloudflare**
na frente (como no Release Builder) para WAF/HTTPS.
