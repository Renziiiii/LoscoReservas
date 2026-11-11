#!/usr/bin/env ruby
# Etiquetado masivo de conversaciones de Chatwoot POR FILTRO (ops del server).
#
# Corre DENTRO del container chatwoot-rails-1 como `rails runner`, a nivel
# aplicación: usa add_labels/update_labels, mantiene taggings +
# cached_label_list consistentes, no genera mensajes de actividad en los
# timelines (sin usuario) ni modifica last_activity_at.
#
# Filtros (todos opcionales salvo label):
#   account_id=2            cuenta de Chatwoot (default: la primera)
#   channel=Channel::Whatsapp   inbox por tipo de canal (default WhatsApp)
#   inactive_days=21        solo chats con last_activity_at hace MÁS de N días
#   statuses=open,resolved  solo esos estados (default: todos)
#   exclude_label=a,b       descarta chats cuyas labels (o las de su CONTACTO)
#                           incluyan alguna de las dadas (coma-separado); sirve
#                           para no repetir tandas previas ni tocar opt-outs
#
# Acciones:
#   action=tag        agrega la label a los que matchean (merge: no pisa otras)
#   action=untag      quita la label a los que matchean y la tienen
#   action=keep_oldest + limit=N   de los que matchean y la tienen, deja la
#                    label solo en los N con last_activity_at más antigua
#                    (orden (last_activity_at, id): determinístico ante empates)
#
# Seguridad: dry_run=true por DEFAULT (solo imprime qué haría, no escribe).
# Pasar dry_run=false para escribir. Idempotente: re-ejecutar tag no duplica.
#
# Uso (desde el VPS; script host: /root/chatwoot/scripts/, ver docs/losco-server.md):
#   docker cp /root/chatwoot/scripts/chatwoot_etiquetar_chats.rb chatwoot-rails-1:/tmp/
#   docker exec chatwoot-rails-1 bundle exec rails runner /tmp/chatwoot_etiquetar_chats.rb \
#     action=tag label=campania-wsp channel=Channel::Whatsapp inactive_days=21 dry_run=false
#
# 2da tanda de campaña (2026-11-11): etiqueta solo los chats que NO llevaron la
# 1ra tanda y sin opt-outs (exclude aplica a labels de conversación y contacto):
#   docker exec chatwoot-rails-1 bundle exec rails runner /tmp/chatwoot_etiquetar_chats.rb \
#     action=tag label=campania-wsp-2 channel=Channel::Whatsapp inactive_days=28 \
#     exclude_label=campania-wsp,no-publicidad dry_run=false
#
# Verificar SIEMPRE antes de escribir (backup en /root/chatwoot/backups):
#   docker exec chatwoot-postgres-1 pg_dump -U postgres -Fc -d chatwoot_production \
#     > /root/chatwoot/backups/chatwoot_production_$(date +%Y%m%d_%H%M%S).dump
#
# Salida (greppable): PARAMS / MATCHED / TAGGED|UNTAGGED|KEPT / FAILED / DONE.

STATUS_MAP = { 'open' => 0, 'resolved' => 1, 'pending' => 2, 'snoozed' => 3 }.freeze

params = {}
ARGV.each do |arg|
  key, value = arg.split('=', 2)
  params[key] = value if key && value
end

label_title = params['label']
action = params.fetch('action', 'tag')
channel = params.fetch('channel', 'Channel::Whatsapp')
inactive_days = params['inactive_days']&.to_f
limit = params['limit']&.to_i
dry_run = params.fetch('dry_run', 'true') != 'false'
statuses = params['statuses']&.split(',')&.map(&:strip)&.reject(&:empty?)
exclude_labels = params['exclude_label']&.split(',')&.map(&:strip)&.reject(&:empty?)

if label_title.nil? || label_title.empty?
  abort('FALTA label=<titulo> (ej: label=campania-wsp)')
end
unless %w[tag untag keep_oldest].include?(action)
  abort("accion desconocida: #{action} (tag | untag | keep_oldest)")
end
if action == 'keep_oldest' && (limit.nil? || limit <= 0)
  abort('keep_oldest requiere limit=N (N > 0)')
end

account = params['account_id'] ? Account.find(params['account_id'].to_i) : Account.first
label = account.labels.find_by!(title: label_title)
missing_exclude = (exclude_labels || []).reject { |t| account.labels.exists?(title: t) }
unless missing_exclude.empty?
  abort("exclude_label inexistente en account #{account.id}: #{missing_exclude.join(', ')}")
end
puts "PARAMS account=#{account.id} action=#{action} label=#{label_title} " \
     "channel=#{channel} inactive_days=#{inactive_days || 'off'} " \
     "statuses=#{statuses ? statuses.join(',') : 'all'} limit=#{limit || '-'} " \
     "exclude=#{exclude_labels ? exclude_labels.join(',') : '-'} dry_run=#{dry_run}"

scope = account.conversations.joins(:inbox).where(inboxes: { channel_type: channel })
scope = scope.where('conversations.last_activity_at < ?', inactive_days.days.ago) if inactive_days
scope = scope.where(status: statuses.map { |s| STATUS_MAP.fetch(s, s.to_i) }) if statuses

if exclude_labels
  excluded_conv_ids = account.conversations.tagged_with(exclude_labels, any: true).distinct.ids
  excluded_contact_ids = account.contacts.tagged_with(exclude_labels, any: true).distinct.ids
  scope = scope.where.not(id: excluded_conv_ids)
  unless excluded_contact_ids.empty?
    scope = scope.where('contact_id IS NULL OR contact_id NOT IN (?)', excluded_contact_ids)
  end
  puts "EXCLUDED labels=#{exclude_labels.join(',')} convs=#{excluded_conv_ids.size} " \
       "contacts=#{excluded_contact_ids.size}"
end

if action == 'tag'
  candidates = scope
else
  tagged_ids = ActiveRecord::Base.connection.select_values(
    "SELECT taggable_id FROM taggings WHERE tag_id = #{label.id} AND taggable_type = 'Conversation'"
  ).map(&:to_i)
  candidates = scope.where(id: tagged_ids)
end

matched = candidates.count
puts "MATCHED=#{matched}"

if dry_run
  oldest = candidates.order(last_activity_at: :asc).first
  newest = candidates.order(last_activity_at: :desc).first
  puts "RANGE oldest=#{oldest&.last_activity_at} newest=#{newest&.last_activity_at}"
  if action == 'keep_oldest'
    keep_ids = candidates.order(last_activity_at: :asc, id: :asc).limit(limit).pluck(:id)
    puts "WOULD_KEEP=#{keep_ids.size} WOULD_UNTAG=#{matched - keep_ids.size}"
  end
  puts 'DRY RUN: nada escrito. Re-ejecutar con dry_run=false para aplicar.'
else
  changed = 0
  failed = []

  if action == 'keep_oldest'
    keep_ids = candidates.order(last_activity_at: :asc, id: :asc).limit(limit).pluck(:id)
    untag_ids = candidates.where.not(id: keep_ids).pluck(:id)
    puts "KEEP=#{keep_ids.size} UNTAG=#{untag_ids.size}"
    Conversation.where(id: untag_ids).find_each(batch_size: 200) do |conversation|
      conversation.update_labels(conversation.cached_label_list_array - [label_title])
      changed += 1
    rescue StandardError => e
      failed << [conversation.id, e.class, e.message]
    end
  else
    candidates.find_each(batch_size: 200) do |conversation|
      case action
      when 'tag'
        conversation.add_labels([label_title])
      when 'untag'
        conversation.update_labels(conversation.cached_label_list_array - [label_title])
      end
      changed += 1
    rescue StandardError => e
      failed << [conversation.id, e.class, e.message]
    end
  end

  puts "CHANGED=#{changed} FAILED=#{failed.size}"
  failed.first(10).each { |row| puts "FAIL #{row.inspect}" }
  abort('ETIQUETADO INCOMPLETO') unless failed.empty?
  puts 'DONE'
end
