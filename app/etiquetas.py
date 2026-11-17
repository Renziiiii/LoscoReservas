"""Contact labeling in Chatwoot (S10/L21/L24): confirmed reservations get a
configurable label on the Chatwoot contact.

When a reservation is (or becomes) "confirmada", the Chatwoot contact gets
the label configured in `Configuracion.etiqueta_confirmada`. The Chatwoot
labels API REPLACES the whole label list, so existing labels are read and
merged first.

Fail-soft by design (L6): without a configured Chatwoot token, or on any API
error, the reservation save must never fail — the incident is only logged.
"""

import logging

from app.chatwoot import ChatwootClient
from app.config import Settings

logger = logging.getLogger("losco.etiquetas")


def _configurado(settings: Settings) -> bool:
    return bool(
        settings.chatwoot_base_url
        and settings.chatwoot_api_token
        and settings.chatwoot_account_id
    )


def _resolver_contacto(chatwoot: ChatwootClient, telefono: str, contacto_id: int | None) -> dict | None:
    """The Chatwoot contact for this reserva: by known id first, else phone
    search. Returns None when Chatwoot has no such contact."""
    if contacto_id:
        contacto = chatwoot.obtener_contacto(contacto_id)
        if contacto is not None:
            return contacto
    return chatwoot.buscar_contacto(telefono)


def _objetivo_labels(actuales: list[str], quitar: str | None, poner: str | None) -> list[str]:
    """The label list the contact/conversation must end up with: the old
    state's label removed (D1), the new one added, everything else kept."""
    objetivo = [l for l in actuales if not (quitar and l == quitar)]
    if poner and poner not in objetivo:
        objetivo.append(poner)
    return objetivo


def sincronizar_estado_reserva(
    settings: Settings,
    telefono: str,
    mapa: dict[str, str | None],
    estado_anterior: str | None,
    estado_nuevo: str | None,
    contacto_id: int | None = None,
    transport=None,
) -> bool:
    """Reflect the reserva's state change on Chatwoot (S5/D1): the OLD
    state's mapped label is removed from the contact AND every one of its
    conversations (open, resolved, any channel), the NEW state's mapped
    label is added the same way. Other labels are preserved.

    `mapa` maps estado slug -> Chatwoot label (or None), as built by
    app.estados.mapa_etiquetas. When neither side has a label (or both
    states map to the same), nothing is called and True is returned
    (nothing to do). Returns False only on hard failure (unconfigured /
    no contact / contact write failed).

    Fail-soft by design (L6): never raises; the reserva save must succeed
    regardless. `contacto_id` skips the phone search when the caller
    already knows the Chatwoot contact (iframe). `transport` is an httpx
    transport override for tests."""
    if not _configurado(settings):
        logger.info("Chatwoot sin configurar: no se sincronizaron etiquetas de %s", telefono)
        return False
    vieja = (mapa or {}).get(estado_anterior) if estado_anterior else None
    nueva = (mapa or {}).get(estado_nuevo) if estado_nuevo else None
    if not vieja and not nueva:
        return True

    try:
        chatwoot = ChatwootClient(
            settings.chatwoot_base_url,
            settings.chatwoot_api_token,
            settings.chatwoot_account_id,
            transport=transport,
        )
    except Exception as exc:
        logger.warning("sincronizar etiquetas de %s falló: %s", telefono, exc)
        return False

    try:
        contacto = _resolver_contacto(chatwoot, telefono, contacto_id)
    except Exception as exc:
        logger.warning("buscar contacto de %s falló: %s", telefono, exc)
        return False
    if contacto is None:
        logger.info("sin contacto en Chatwoot para %s: etiquetas no sincronizadas", telefono)
        return False
    contacto_id_resuelto = contacto["id"]

    ok = False
    # --- el contacto ---
    try:
        actuales = chatwoot.listar_labels_contacto(contacto_id_resuelto)
        objetivo = _objetivo_labels(actuales, vieja, nueva)
        if objetivo != actuales:
            chatwoot.agregar_labels_contacto(contacto_id_resuelto, objetivo)
        ok = True
    except Exception as exc:
        logger.warning("etiquetas del contacto %s no se actualizaron: %s", contacto_id_resuelto, exc)

    # --- TODAS sus conversaciones existentes (S5) ---
    try:
        conversaciones = chatwoot.conversaciones(contacto_id_resuelto)
        for conv in conversaciones:
            conv_id = conv.get("id")
            if conv_id is None:
                continue
            actuales = conv.get("labels")
            if actuales is None:  # defensive: payload without labels
                try:
                    actuales = chatwoot.listar_labels_conversacion(conv_id)
                except Exception as exc:
                    logger.warning("labels de la conversación %s no leídas: %s", conv_id, exc)
                    continue
            objetivo = _objetivo_labels(actuales, vieja, nueva)
            if objetivo == actuales:
                continue
            try:
                chatwoot.agregar_labels_conversacion(conv_id, objetivo)
                ok = True
            except Exception as exc:
                logger.warning("etiquetas de la conversación %s no se actualizaron: %s", conv_id, exc)
    except Exception as exc:
        logger.warning("listar conversaciones del contacto %s falló: %s", contacto_id_resuelto, exc)

    return ok


def sincronizar_conversacion_nueva(
    settings: Settings,
    conversacion_id: int,
    contacto_id: int | None = None,
    telefono: str | None = None,
    transport=None,
) -> bool:
    """Copy ALL of the contact's current labels onto a newly created
    conversation (S6/D2): labels that arrive from any channel or agents
    reach the chats created later (by automated messaging or otherwise).
    Idempotent: only POSTs the label list when something is missing.

    Returns True when labels were added, False when nothing was needed or
    the sync failed. Fail-soft: never raises."""
    if not _configurado(settings):
        logger.info("Chatwoot sin configurar: no se sincronizó la conversación %s", conversacion_id)
        return False
    try:
        chatwoot = ChatwootClient(
            settings.chatwoot_base_url,
            settings.chatwoot_api_token,
            settings.chatwoot_account_id,
            transport=transport,
        )
        if contacto_id is None:
            contacto = chatwoot.buscar_contacto(telefono or "")
            if contacto is None:
                logger.info("sin contacto para la conversación %s: etiquetas no copiadas", conversacion_id)
                return False
            contacto_id = contacto["id"]
        etiquetas_contacto = chatwoot.listar_labels_contacto(contacto_id)
        if not etiquetas_contacto:
            return False
        actuales = chatwoot.listar_labels_conversacion(conversacion_id)
        faltantes = [l for l in etiquetas_contacto if l not in actuales]
        if not faltantes:
            return False
        chatwoot.agregar_labels_conversacion(conversacion_id, actuales + faltantes)
        logger.info("conversación %s etiquetada desde el contacto %s: %s",
                    conversacion_id, contacto_id, faltantes)
        return True
    except Exception as exc:
        logger.warning("sincronizar conversación nueva %s falló: %s", conversacion_id, exc)
        return False


def migrar_etiqueta(
    settings: Settings, vieja: str, nueva: str, transport=None
) -> dict:
    """Swap `vieja` for `nueva` on every contact AND conversation that has
    `vieja`, keeping all other labels.

    Returns {
        "total_contactos": N,
        "migrados_contactos": [contact_ids],
        "total_conversaciones": M,
        "migrados_conversaciones": [conversation_ids],
        "fallas": [{"tipo": "contacto"|"conversacion", "id": ..., "motivo": ...}]
    }.

    Never raises: per-item HTTP failures are recorded in `fallas` and the
    migration continues; global failures return an empty result set.
    """
    if not vieja or not nueva:
        return {
            "total_contactos": 0,
            "migrados_contactos": [],
            "total_conversaciones": 0,
            "migrados_conversaciones": [],
            "fallas": [],
        }
    if not _configurado(settings):
        logger.info("Chatwoot sin configurar: no se migró etiqueta %s -> %s", vieja, nueva)
        return {
            "total_contactos": 0,
            "migrados_contactos": [],
            "total_conversaciones": 0,
            "migrados_conversaciones": [],
            "fallas": [],
        }

    try:
        chatwoot = ChatwootClient(
            settings.chatwoot_base_url,
            settings.chatwoot_api_token,
            settings.chatwoot_account_id,
            transport=transport,
        )
        contactos = chatwoot.contactos_con_etiqueta(vieja)
    except Exception as exc:
        logger.warning("listar contactos con etiqueta %s falló: %s", vieja, exc)
        contactos = []

    migrados_contactos: list[int] = []
    migrados_conversaciones: list[int] = []
    fallas: list[dict] = []

    for contacto in contactos:
        contacto_id = contacto.get("id")
        try:
            actuales = chatwoot.listar_labels_contacto(contacto_id)
            nuevas = [label for label in actuales if label != vieja]
            if nueva not in nuevas:
                nuevas.append(nueva)
            chatwoot.agregar_labels_contacto(contacto_id, nuevas)
            migrados_contactos.append(contacto_id)
        except Exception as exc:
            logger.warning(
                "migrar etiqueta para contacto %s falló (%s -> %s): %s",
                contacto_id,
                vieja,
                nueva,
                exc,
            )
            fallas.append({"tipo": "contacto", "id": contacto_id, "motivo": str(exc)})

    try:
        conversaciones = chatwoot.conversaciones_con_etiqueta(vieja)
    except Exception as exc:
        logger.warning("listar conversaciones con etiqueta %s falló: %s", vieja, exc)
        conversaciones = []

    for conv in conversaciones:
        conv_id = conv.get("id")
        try:
            actuales = chatwoot.listar_labels_conversacion(conv_id)
            nuevas = [label for label in actuales if label != vieja]
            if nueva not in nuevas:
                nuevas.append(nueva)
            chatwoot.agregar_labels_conversacion(conv_id, nuevas)
            migrados_conversaciones.append(conv_id)
        except Exception as exc:
            logger.warning(
                "migrar etiqueta para conversación %s falló (%s -> %s): %s",
                conv_id,
                vieja,
                nueva,
                exc,
            )
            fallas.append({"tipo": "conversacion", "id": conv_id, "motivo": str(exc)})

    return {
        "total_contactos": len(contactos),
        "migrados_contactos": migrados_contactos,
        "total_conversaciones": len(conversaciones),
        "migrados_conversaciones": migrados_conversaciones,
        "fallas": fallas,
    }
