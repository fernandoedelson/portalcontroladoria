def sub(s, old, new, count=1):
    assert old in s, old[:80]
    return s.replace(old, new, count)

p = 'templates/agendar.html'
s = open(p, encoding='utf-8').read()
s = sub(s, '''  <p class="sub">{% if link.company %}{{ link.company.name }} · {% endif %}Escolha um horário livre ({{ duracao }} minutos). Horário de Brasília.</p>''',
        '''  <p class="sub">{% if link.company %}{{ link.company.name }} · {% endif %}{% if responsavel %}com {{ responsavel }} · {% endif %}{{ duracao }} minutos · horário de Brasília.
    {% if dia %}<br>Dia da reunião: <strong>{{ dias[dia.weekday()] }}, {{ dia.strftime('%d/%m/%Y') }}</strong> (dia útil seguinte ao prazo de entrega).{% endif %}</p>''')
s = sub(s, '''  <div class="card"><p><strong>Não há horários livres no momento.</strong></p>
    <p class="muted">Tente novamente mais tarde ou responda ao e-mail da Controladoria.</p></div>''',
        '''  <div class="card"><p><strong>Não há horários livres{% if dia %} em {{ dia.strftime('%d/%m') }}{% endif %}.</strong></p>
    <p class="muted">{% if dia %}As reuniões desta empresa acontecem só nesse dia. {% endif %}Se os horários acabaram ou o dia já passou, responda ao e-mail da Controladoria.</p></div>''')
open(p, 'w', encoding='utf-8').write(s)

p = 'templates/base.html'
s = open(p, encoding='utf-8').read()
s = sub(s, '''      {% if current_user.is_controladoria %}
      <a href="/agenda-reunioes"''', '''      {% if current_user.is_team %}
      <a href="/agenda-reunioes"''')
open(p, 'w', encoding='utf-8').write(s)

p = 'templates/ajuda.html'
s = open(p, encoding='utf-8').read()
a = s.index('<h2>Agenda de reuniões</h2>')
b = s.index('</div>', a)
s = s[:a] + '''<h2>Agenda de reuniões</h2>
    <p>Em <em>Sistema › Agenda de Reuniões</em> <strong>cada responsável tem a sua agenda</strong>: dias da semana e horários em que
      atende, e os períodos bloqueados. Reuniões de pessoas diferentes podem acontecer ao mesmo tempo.</p>
    <p class="mt-1">Cada empresa tem um link (marcador <code>{agendar}</code> nos e-mails de aviso). Ela só vê os horários livres do
      <strong>responsável dela na Carteira</strong>, e só no <strong>dia útil seguinte ao prazo de entrega</strong> (prazo no 5º
      dia útil → reunião no 6º). O convite <code>.ics</code> vai para a empresa, o responsável, a Liderança e os participantes fixos — é
      preciso <strong>aceitar o convite</strong> para entrar na agenda. O portal não lê a sua agenda do Outlook.</p>
  ''' + s[b:]
open(p, 'w', encoding='utf-8').write(s)
print('ok')
