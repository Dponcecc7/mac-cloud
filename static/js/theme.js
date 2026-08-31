// Proyecto MAC -- comportamiento compartido (Davor, 2026-08-29): toggle de
// tema, barra de carga en navegaciones/submits, boton "guardando..." y
// auto-cierre de los mensajes flash. Un solo archivo referenciado desde
// las 19 paginas -- ver static/css/theme.css para los estilos.
(function () {
  "use strict";
  var CLAVE_TEMA = "mac_theme";

  function temaActual() {
    return document.documentElement.getAttribute("data-theme") || "auto";
  }

  function aplicarTema(valor) {
    if (valor === "auto") {
      document.documentElement.removeAttribute("data-theme");
    } else {
      document.documentElement.setAttribute("data-theme", valor);
    }
    try { localStorage.setItem(CLAVE_TEMA, valor); } catch (e) { /* localStorage bloqueado -- ignorar */ }
    actualizarBotones();
  }

  function prefiereOscuroDelSistema() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function esOscuroEfectivo() {
    var t = temaActual();
    if (t === "dark") return true;
    if (t === "light") return false;
    return prefiereOscuroDelSistema();
  }

  function actualizarBotones() {
    var oscuro = esOscuroEfectivo();
    document.querySelectorAll(".theme-toggle").forEach(function (btn) {
      btn.textContent = oscuro ? "☀️" : "🌙";
      btn.title = oscuro ? "Cambiar a modo claro" : "Cambiar a modo oscuro";
      btn.setAttribute("aria-label", btn.title);
    });
  }

  function alternarTema() {
    aplicarTema(esOscuroEfectivo() ? "light" : "dark");
  }

  document.addEventListener("DOMContentLoaded", function () {
    actualizarBotones();
    document.querySelectorAll(".theme-toggle").forEach(function (btn) {
      btn.addEventListener("click", alternarTema);
    });
  });

  // ---- Barra de carga arriba de la pagina --------------------------------
  var barra = document.createElement("div");
  barra.id = "mac-progress";
  document.addEventListener("DOMContentLoaded", function () {
    document.body.appendChild(barra);
  });

  function mostrarBarra() {
    requestAnimationFrame(function () { barra.classList.add("activa"); });
  }

  // Red de seguridad: si en `ms` no hubo una navegacion real (ej. un
  // <a>/<form> que dispara una DESCARGA de archivo -- Content-Disposition:
  // attachment, sin atributo download en el HTML, asi que no se puede
  // detectar antes de hacer clic -- o un submit que fallo su validacion),
  // la pagina sigue viva y hay que destrabar la barra/spinner a mano; si SI
  // hubo navegacion, el documento ya se descarto y este timeout nunca corre.
  function resetearLuegoDe(ms, elementos) {
    setTimeout(function () {
      barra.classList.remove("activa");
      elementos.forEach(function (el) {
        el.classList.remove("is-guardando");
        el.disabled = false;
      });
    }, ms);
  }

  document.addEventListener("click", function (ev) {
    var a = ev.target.closest("a[href]");
    if (!a) return;
    var href = a.getAttribute("href");
    if (!href || href.startsWith("#") || href.startsWith("mailto:") || href.startsWith("javascript:")) return;
    if (a.target === "_blank" || a.hasAttribute("download") || a.dataset.noLoading !== undefined) return;
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey) return; // abrir en pestaña nueva -- no es una navegacion de esta pestaña
    mostrarBarra();
    // Los <a> con pinta de boton (ej. "Descargar Excel") tambien muestran el
    // spinner -- el resto de los <a> son texto de nav, no necesitan esto.
    if (a.classList.contains("btn-descargar")) {
      a.classList.add("is-guardando");
      resetearLuegoDe(4000, [a]);
    }
  });

  // ---- Feedback en submits: boton deshabilitado + spinner + barra -------
  document.addEventListener("submit", function (ev) {
    // ev.defaultPrevented ya en true acá significa que un onsubmit="return
    // confirm(...)" (ej. el "Borrar" de historial.html) devolvió false --
    // sin este chequeo el boton quedaba con el spinner pegado aunque el
    // usuario cancelara el dialogo, porque preventDefault() no corta la
    // propagacion del evento hacia este listener delegado en document.
    if (ev.defaultPrevented) return;
    var form = ev.target;
    if (form.dataset.noLoading !== undefined) return;
    mostrarBarra();
    var botones = Array.prototype.slice.call(
      form.querySelectorAll('button[type="submit"], input[type="submit"]')
    );
    // setTimeout(0), NO deshabilitar sincronico aca -- un formulario con
    // VARIOS botones submit del mismo name (ej. accion=Asistio/Vacante/
    // Falta en Marcar asistencia) manda el valor del boton que se clickeo
    // como parte del mismo evento submit; si se lo deshabilita ANTES de que
    // el navegador termine de armar esos datos, un control disabled queda
    // afuera del envio y el campo llega vacio al servidor (bug real,
    // Davor 2026-08-31: "Vacante" llegaba con accion='' y nunca guardaba).
    // Con el timeout, el navegador ya capturo el envio antes de bloquear.
    setTimeout(function () {
      botones.forEach(function (btn) {
        btn.disabled = true;
        btn.classList.add("is-guardando");
      });
    }, 0);
    resetearLuegoDe(8000, botones);
  });

  // ---- Auto-cierre de mensajes flash de exito (los de error quedan) -----
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".flash.ok").forEach(function (el) {
      setTimeout(function () {
        el.classList.add("flash-saliendo");
        setTimeout(function () { el.remove(); }, 320);
      }, 4000);
    });
  });
})();
