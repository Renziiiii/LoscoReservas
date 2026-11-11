# Taggea los CONTACTOS de las conversaciones que llevan un label dado.
#
# Fix de audiencia para campanias one-off de WhatsApp en Chatwoot: la audiencia
# de campania se resuelve sobre labels de CONTACTOS
# (campaign.account.contacts.tagged_with en Whatsapp::OneoffCampaignService),
# no sobre labels de conversaciones.
#
# Uso (dentro del container):
#   bundle exec rails runner /tmp/chatwoot_etiquetar_contactos.rb label=campania-wsp [account_id=2] [dry_run=false]
#
# Propiedades: nivel app (Labelable#add_labels: merge, no pisa labels previas),
# idempotente, dry-run por defecto. Salida greppable: MATCHED, WOULD_TAG, TAGGED, FAILED.

args = ARGV.map { |a| a.split('=', 2) }.to_h
label_title = args.fetch('label')
account_id = args['account_id']
dry_run = args.fetch('dry_run', 'true') != 'false'

account = account_id ? Account.find(account_id.to_i) : Account.first
raise "Label '#{label_title}' no existe en account #{account.id}" unless account.labels.exists?(title: label_title)

labeled_conversations = account.conversations.tagged_with([label_title], any: true)
conversation_count = labeled_conversations.distinct.count
contact_ids = labeled_conversations.distinct.pluck(:contact_id).compact
contacts = account.contacts.where(id: contact_ids)
no_phone = contacts.where(phone_number: [nil, '']).count

puts "ACCOUNT=#{account.id}"
puts "LABEL=#{label_title}"
puts "SOURCE_CONVERSATIONS=#{conversation_count}"
puts "MATCHED_CONTACTS=#{contacts.count}"
puts "CONTACTS_WITHOUT_PHONE=#{no_phone}"

if dry_run
  puts "WOULD_TAG=#{contacts.count}"
  puts "DRY_RUN=true (no se escribio nada; agregar dry_run=false para aplicar)"
  exit 0
end

tagged = 0
failed = 0
Contact.where(id: contact_ids).find_each do |contact|
  contact.add_labels([label_title])
  tagged += 1
rescue StandardError => e
  failed += 1
  puts "FAILED contact_id=#{contact.id} #{e.class}: #{e.message}"
end
puts "TAGGED=#{tagged}"
puts "FAILED=#{failed}"
