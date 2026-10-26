from django.db import migrations

# Punto de partida editable desde /admin/ (Materiales): se pueden renombrar, desactivar o borrar.
MATERIALES_INICIALES = ["Maniquíes", "Material distinto al habitual"]


def crear_materiales(apps, schema_editor):
    Material = apps.get_model("reservas", "Material")
    for orden, nombre in enumerate(MATERIALES_INICIALES, start=1):
        # get_or_create: si ya existe uno con ese nombre no se duplica ni se sobrescribe.
        Material.objects.get_or_create(nombre=nombre, defaults={"orden": orden})


class Migration(migrations.Migration):

    dependencies = [
        ("reservas", "0003_titulo_curso_materiales"),
    ]

    operations = [
        # Al deshacer la migración no se borra nada: los materiales pueden estar en uso.
        migrations.RunPython(crear_materiales, migrations.RunPython.noop),
    ]