// Pequeños comportamientos de la interfaz. HTMX hace el resto (ver plantillas).
(function () {
  "use strict";

  // Selector de aula con búsqueda: convierte <select data-buscable> en un cuadro
  // donde se escribe para filtrar. El <select> original sigue siendo el que se envía.
  function iniciarBuscadores(raiz) {
    (raiz || document).querySelectorAll("select[data-buscable]").forEach(function (el) {
      if (el.tomselect) return;
      var ts = new TomSelect(el, {
        create: false,
        maxOptions: 500,
        closeAfterSelect: true,
        // Sin allowEmptyOption: la opción vacía del <select> no debe listarse como si fuera un aula.
        placeholder: "Escribe para buscar un aula…",
        render: {
          no_results: function () {
            return '<div class="no-results">Ningún aula coincide con lo escrito</div>';
          },
        },
      });
      // Con teclado, Tom Select vuelve a abrir la lista al soltar Enter justo después de
      // elegir. Si se reabre en ese instante, la cerramos.
      var recienElegida = false;
      ts.on("item_add", function () {
        recienElegida = true;
        setTimeout(function () { recienElegida = false; }, 300);
      });
      ts.on("dropdown_open", function () {
        if (recienElegida) setTimeout(function () { ts.close(); }, 0);
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    iniciarBuscadores();
  });

  document.addEventListener("htmx:afterSwap", function (evento) {
    iniciarBuscadores(evento.target);

    // En pantallas estrechas el detalle queda debajo del calendario: lo acercamos.
    if (evento.target.id === "detalle" && window.matchMedia("(max-width: 1000px)").matches) {
      var reducido = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      evento.target.scrollIntoView({ behavior: reducido ? "auto" : "smooth", block: "nearest" });
    }
  });

  // Marca la reserva del calendario cuyo detalle se está viendo.
  document.addEventListener("click", function (evento) {
    var bloque = evento.target.closest(".bloque");
    if (!bloque) return;
    document.querySelectorAll(".bloque.seleccionado").forEach(function (b) {
      b.classList.remove("seleccionado");
    });
    bloque.classList.add("seleccionado");
  });
})();