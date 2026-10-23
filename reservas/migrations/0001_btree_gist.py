from django.contrib.postgres.operations import BtreeGistExtension
from django.db import migrations


class Migration(migrations.Migration):
    """Activa btree_gist, necesaria para combinar "misma aula" (=) con "rangos que se
    solapan" (&&) en una única restricción de exclusión.

    En PostgreSQL 13+ es una extensión "de confianza": la puede crear el propietario de
    la base de datos. En versiones anteriores debe crearla antes un superusuario:
        CREATE EXTENSION btree_gist;
    """

    dependencies = []

    operations = [BtreeGistExtension()]