from .permissions import es_admin, puede_pedir_reserva


def roles(request):
    """Expone el rol a todas las plantillas (para mostrar u ocultar el menú y las acciones).

    es_admin    -> Mantenimiento/Informática: gestionan, aceptan, exportan.
    puede_pedir -> cualquier usuario con rol (incluye a los administradores).
    """
    return {"es_admin": es_admin(request.user), "puede_pedir": puede_pedir_reserva(request.user)}