# Gestão Controladoria J&F S.A. — Portal de Gestão do Time

Aplicação web interna (Flask) para a área de Controladoria gerir o trabalho do
time: **atividades** (motor único), **projetos**, **indicadores & metas**,
**capacidade**, **férias/ausências**, **carteira de entidades**, **alertas**,
**atas de reunião**, **tarefas** e **notas** pessoais.

> Esta é a versão **enxuta**, sem o módulo de Consolidação (envio/validação de
> Excel, conta corrente intercompany, forecast, restatement). O foco é a Gestão
> da Área.

## Stack
- Python 3.13 · Flask 3 · Flask-SQLAlchemy · Flask-Login · Flask-WTF (CSRF)
- SQLite (`instance/portal.db`) · waitress (produção) · holidays (calendário BR)
- Front: Jinja2 SSR + JavaScript vanilla · design system "Aurora" (tema claro/escuro)

## Como rodar
```bash
pip install -r requirements.txt
python seed.py            # cria o banco + dados de exemplo (admin, time, projetos)
python run.py             # sobe o servidor (waitress) em http://127.0.0.1:5050
```
No Windows há o atalho `iniciar_portal.bat` (sobe o servidor e abre o navegador).

### Credenciais do seed
- **admin**: `admin@jfsa.com.br` / `JF@Gestao2026!`
- **controladoria**: `controladoria@jfsa.com.br` / `JF@Controla2026!`
- **time** (perfil controladoria, senha inicial `jfsa@2026T`): `fernando.edelson@jfsa.com.br`, etc.

## Perfis
- `admin` — tudo, incluindo Administração.
- `controladoria` — gestão da área inteira.
- `profissional` — vê só o próprio painel (atividades/projetos/indicadores dele).

## Estrutura
```
app.py                 factory Flask, auth, home, busca, API de notificações
models.py              User, Company, Competency, Setting, Notification, AuditLog
team/                  módulo de Gestão do Time
  models.py            TeamMember, Project, Milestone, Activity, Indicator, ...
  models_workflow.py   notas, anexos, ausências, tarefas, painéis, atas, ...
  routes.py            telas do time (painel, atividades, projetos, capacidade...)
  engine.py            motor: calendário útil, geração de atividades, farol
  alerts.py            matriz de alertas (e-mail/WhatsApp plugáveis)
  seed_team.py         povoamento do time
workflow_routes.py     ausências, resumo, tarefas, notas, atas
admin_center.py        Administração (membros, entidades, painéis, segmentos, sistema)
engine/calendar_br.py  feriados nacionais e dias úteis (5º DU)
scheduler.py           agendador diário (alertas, avisos de tarefa, resumo)
templates/  static/    Jinja2 + CSS/JS (tema Aurora)
```

## Notas técnicas
- Migrações não-destrutivas: `db.create_all()` cria tabelas novas; nunca rode
  `drop_all` num banco com dados reais (o `seed.py` dropa — use só para recriar).
- Algumas tabelas herdadas (ex.: `submissions`) permanecem como definição porque
  o motor do time usa a chegada de "insumos-âncora" para calcular prazos; elas
  ficam vazias nesta versão e não têm telas.
- Canais de e-mail/WhatsApp são plugáveis (guardados por flag/credencial); sem
  credencial, o envio é registrado como "simulado" e nada quebra.
