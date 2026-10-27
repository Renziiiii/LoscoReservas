Hola Gentelment! estoy haciendo una mini-app para mi cliete que nesesita gestionar mejor las reservas de su local. 


# Requiermientos
- CRUD con API

## Docker
La app va a estar dockerizada para mayor control y seguridad

## Mensajeria automatizada
- Que 24hs antes se le envie un mensaje personalizado al cliente, confirmando invitados
- Que 48hs antes, un mensje confirmando la reserva
- Que en chatwoot se incerte un inframe que muestre las reseras de ese ususario (Puede ser por instagram, wsp, messenger) Chatwoot se encarga de eso

##  Admin Dashbord
Tiene que haber un dashbord donde pueda ver todas las reservas, actualmente usan un google sheets
## Chatwoot Dashbord
La idea es usar los inframe de chatwoot para alojar ahi la informacion de ese cliente! 
# Futuro
Esa api se tiene que servir para que mas adelante, cuando el cliente quiera una IA en su chat, que esta pueda usar esata api para regisrar las reservas


# Comentarios mios
- Nunca antes hice algo asi... Eh echo paginas web con Astro y se lo basico de JS y PY y un poco de docker. pero nunca hice una API. ni que una app consuma la api

# Contexto
El server lo podes enontrar en las .ssh (Es LoscoServer)

# Nota de obsidian
> Te paso lo que tengo anotado en Obsidian


## Campañas personalizadas en WSP
### Contexto
> Valen tiene 900 contactos que le hablaron, por ende tiene 900 personas interesadas en su bar.

## Tareas
- [x] Investigar cómo funcionan las campañas en WSP
	- Si el template genera reportes/bloqueos. Baja el credito de la cuenta.. Ofrecer ofertas ==🔴NO genericas==
	- SI O SI. una plantill de tipo MARKETING
	- Hay un limite por dia: [Meta for Business (formerly Facebook for Business)](https://business.facebook.com/latest/whatsapp_manager/messaging_limits?business_id=1051648438029739&asset_id=2918723418486867)
- Estrategia de Andra: promos + cupo límite + campaña de 3 o 4 días para aumentar la urgencia
- Ponerse del lado del cliente — ¿qué tipo de planes organizarlas así de rápido?

# Ventana de 24hs
# Valen coments
- 2 dias antes mejor (para confirmar la reserva)
- El mismo dia de la reserva que confirme la cantidad de invitados
	- Entre as 17 y 18

- Tenes 24hs desde el ultimo mensaje de wsp del usuario para enviarle cualquier mensaje sin nesesidad de plantilla
- [ ] Contactos con tag ==🟢Confimar reserva== → enviarles un mensaje 24hs antes para confirmar la reserva
	- [ ] Usar el Google Sheets para sacar los datos de reserva
		- [x] Pedirle a valen el google shets asi veo la data
	- Enviar la confirmacion a la maniana / horario especifico
https://docs.google.com/spreadsheets/d/1-qXc7qTZ0tAdTlh7z2MvbEAgZ1P0-JqF/edit?gid=574962065#gid=57496206
- [[CRUD]]
- Usar InForge para hacer el login y base de datos
- Si es posible, desplegar la infrastructura en el server de losco
- Usar los infram de Chatwoot
- Un mini dashbord que muestre el resumen de reservastura en el server de losco
- Usar los infram de Chatwoot
- DB con: id,nombre,telefono,fecha, hora personas, estado, detalles, enviar recordatorio,
- Un cron que cada dia, verifica a un horario especifico que corra un script para enviarle msg de wsp a la gente que tiene que confirar personas
# Extras
- [x] Cómo enviar stickers en [[Chatwoot]]
	- No se puede, oficialmente
