from .permissions import es_admin


def roles(request):
    """Expone `es_admin` a todas las plantillas (para mostrar u ocultar el menú)."""
    return {"es_admin": es_admin(request.user)}