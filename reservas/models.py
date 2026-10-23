from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeBoundary, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from .expresiones import FechaMasHora, RangoFechaHora


class Aula(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    ubicacion = models.CharField("ubicación", max_length=150, blank=True)
    capacidad = models.PositiveSmallIntegerField(null=True, blank=True)
    activa = models.BooleanField(
        default=True,
        help_text="Desmárcala para dejar de ofrecerla en el formulario sin perder su historial.",
    )

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Reserva(models.Model):
    """Una petición de reserva. Solo ocupa el aula cuando está ACEPTADA."""

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        ACEPTADA = "aceptada", "Aceptada"
        RECHAZADA = "rechazada", "Rechazada"

    aula = models.ForeignKey(Aula, on_delete=models.PROTECT, related_name="reservas")
    fecha = models.DateField()
    hora_inicio = models.TimeField()
    hora_fin = models.TimeField()
    # Texto libre: la reserva puede hacerse a nombre de otra persona.
    profesor = models.CharField("profesor/a", max_length=150)
    notas = models.TextField(blank=True)
    estado = models.CharField(
        max_length=10, choices=Estado.choices, default=Estado.PENDIENTE, db_index=True
    )

    # Auditoría: quién la envió y quién/cuándo tomó la decisión vigente.
    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    creada_en = models.DateTimeField(auto_now_add=True)
    gestionada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    gestionada_en = models.DateTimeField(null=True, blank=True)
    motivo_rechazo = models.TextField(blank=True)

    class Meta:
        ordering = ["fecha", "hora_inicio", "id"]
        indexes = [models.Index(fields=["fecha", "estado"], name="reserva_fecha_estado_idx")]
        constraints = [
            models.CheckConstraint(
                condition=Q(hora_fin__gt=F("hora_inicio")),
                name="reserva_fin_posterior_a_inicio",
                violation_error_message="La hora de fin debe ser posterior a la de inicio.",
            ),
            # Garantía a nivel de base de datos: dos reservas ACEPTADAS no pueden
            # solaparse en la misma aula. Las pendientes y rechazadas no cuentan.
            ExclusionConstraint(
                name="reserva_sin_solape_aceptadas",
                expressions=[
                    ("aula", RangeOperators.EQUAL),
                    (
                        RangoFechaHora(
                            FechaMasHora("fecha", "hora_inicio"),
                            FechaMasHora("fecha", "hora_fin"),
                            RangeBoundary(),
                        ),
                        RangeOperators.OVERLAPS,
                    ),
                ],
                condition=Q(estado="aceptada"),
                violation_error_message=(
                    "Esa aula ya tiene otra reserva aceptada que se solapa con esta franja."
                ),
            ),
        ]

    def __str__(self):
        return f"{self.aula} · {self.fecha:%d/%m/%Y} {self.hora_inicio:%H:%M}-{self.hora_fin:%H:%M}"

    # ------------------------------------------------------------------ utilidades
    @property
    def duracion(self) -> timedelta:
        inicio = datetime.combine(self.fecha, self.hora_inicio)
        fin = datetime.combine(self.fecha, self.hora_fin)
        return fin - inicio

    @property
    def duracion_texto(self) -> str:
        horas, minutos = divmod(int(self.duracion.total_seconds() // 60), 60)
        if horas and minutos:
            return f"{horas} h {minutos} min"
        return f"{horas} h" if horas else f"{minutos} min"

    @classmethod
    def aceptadas_solapadas(cls, aula, fecha, hora_inicio, hora_fin, excluir_pk=None):
        """Reservas ACEPTADAS del aula que se solapan con la franja dada.

        Dos franjas se solapan si cada una empieza antes de que termine la otra
        (mismos criterios que '[)' en la restricción de PostgreSQL).
        """
        qs = cls.objects.filter(
            aula=aula,
            fecha=fecha,
            estado=cls.Estado.ACEPTADA,
            hora_inicio__lt=hora_fin,
            hora_fin__gt=hora_inicio,
        )
        if excluir_pk is not None:
            qs = qs.exclude(pk=excluir_pk)
        return qs.select_related("aula")

    def validate_constraints(self, exclude=None):
        """Valida las restricciones del modelo (incluida la anti-solape).

        La condición de la restricción de exclusión usa `estado`. Django protege los
        campos de las *expresiones* cuando quedan fuera de un formulario, pero no los de
        la *condición*: si `estado` no está en el formulario (formulario público, o campo
        de solo lectura en el admin) falla con FieldError. `estado` es siempre un
        atributo válido de la instancia (valor por defecto o el guardado), así que lo
        quitamos de las exclusiones.
        """
        if exclude and "estado" in exclude:
            exclude = set(exclude) - {"estado"}
        super().validate_constraints(exclude=exclude)

    def clean(self):
        """Reglas estructurales válidas siempre (formulario público y panel de admin)."""
        if self.hora_inicio is None or self.hora_fin is None:
            return
        if self.hora_fin <= self.hora_inicio:
            raise ValidationError("La hora de fin debe ser posterior a la de inicio.")
        if self.hora_inicio < settings.HORA_APERTURA or self.hora_fin > settings.HORA_CIERRE:
            raise ValidationError(
                f"Las reservas deben estar entre las {settings.HORA_APERTURA:%H:%M} "
                f"y las {settings.HORA_CIERRE:%H:%M}."
            )


class EventoReserva(models.Model):
    """Historial inmutable de lo que le ha pasado a cada reserva (auditoría)."""

    class Tipo(models.TextChoices):
        CREADA = "creada", "Creada"
        ACEPTADA = "aceptada", "Aceptada"
        RECHAZADA = "rechazada", "Rechazada"
        REABIERTA = "reabierta", "Devuelta a pendiente"
        EDITADA = "editada", "Editada"

    reserva = models.ForeignKey(Reserva, on_delete=models.CASCADE, related_name="eventos")
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    fecha = models.DateTimeField(auto_now_add=True)
    detalle = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["fecha", "id"]

    def __str__(self):
        return f"{self.get_tipo_display()} · {self.reserva_id} · {self.usuario}"