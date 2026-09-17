/* Flipbook viewer -- no dependencies, works from file:// as well as from a host.
 *
 * The book is a stack of leaves. Leaf k carries two faces; flipping it rotates
 * it -180deg around the spine, revealing the leaf underneath. On a phone the
 * same stack is rebuilt one page per leaf, so a turn is a full-width swipe.
 *
 * Reads window.CATALOGO = { titulo, proporcao, pdf, paginas: [{src, alt, link}] }.
 */
(function () {
  "use strict";

  var dados = window.CATALOGO || { paginas: [] };
  var paginas = dados.paginas || [];
  var proporcao = dados.proporcao || (1080 / 1920);   // width / height of one page
  var LIMITE_SOLO = 760;
  var JANELA = 3;                                      // how many leaves to keep loaded

  var palco = document.getElementById("palco");
  var livro = document.getElementById("livro");
  var miniaturas = document.getElementById("miniaturas");
  var campoPagina = document.getElementById("campo-pagina");
  var totalTexto = document.getElementById("total-paginas");
  var aviso = document.getElementById("aviso");

  var folhas = [];          // DOM nodes, in order
  var modo = "";            // "duplo" | "solo"
  var viradas = 0;          // number of leaves currently flipped
  var zoom = 1;
  var timerAviso = null;

  if (!paginas.length) { return; }

  // -- helpers -------------------------------------------------------------

  function elemento(tag, classe, pai) {
    var no = document.createElement(tag);
    if (classe) { no.className = classe; }
    if (pai) { pai.appendChild(no); }
    return no;
  }

  function mostrarAviso(texto) {
    if (!aviso) { return; }
    aviso.textContent = texto;
    aviso.classList.add("visivel");
    clearTimeout(timerAviso);
    timerAviso = setTimeout(function () { aviso.classList.remove("visivel"); }, 2200);
  }

  function modoDesejado() {
    if (window.innerWidth <= LIMITE_SOLO) { return "solo"; }
    var caixa = palco.getBoundingClientRect();
    // A spread only earns its place when the stage is genuinely landscape.
    return (caixa.width / Math.max(1, caixa.height)) < proporcao * 1.7 ? "solo" : "duplo";
  }

  // -- building the stack --------------------------------------------------

  function criarFace(classe, pagina) {
    var face = elemento("div", "face " + classe);
    if (!pagina) {
      face.classList.add("vazia");
      return face;
    }
    var img = elemento("img", null, face);
    img.alt = pagina.alt || "";
    img.dataset.src = pagina.src;
    img.decoding = "async";
    if (pagina.link) {
      var acao = elemento("a", "acao", face);
      acao.href = pagina.link;
      acao.target = "_blank";
      acao.rel = "noopener";
      acao.textContent = pagina.rotulo_link || dados.rotulo_link || "Comprar";
    }
    return face;
  }

  function montar(novoModo) {
    modo = novoModo;
    livro.classList.toggle("solo", modo === "solo");
    livro.innerHTML = "";
    folhas = [];

    var total = modo === "solo" ? paginas.length : Math.ceil(paginas.length / 2);
    for (var k = 0; k < total; k++) {
      var folha = elemento("div", "folha", livro);
      if (modo === "solo") {
        folha.appendChild(criarFace("frente", paginas[k]));
        folha.appendChild(criarFace("verso", null));
      } else {
        folha.appendChild(criarFace("frente", paginas[2 * k]));
        folha.appendChild(criarFace("verso", paginas[2 * k + 1]));
      }
      folhas.push(folha);
    }
  }

  // -- page numbering ------------------------------------------------------

  function paginaAtual() {                       // 0-based index of the left-most visible page
    if (modo === "solo") { return Math.min(viradas, paginas.length - 1); }
    return viradas === 0 ? 0 : Math.min(2 * viradas - 1, paginas.length - 1);
  }

  function viradasPara(indice) {
    if (modo === "solo") { return Math.max(0, Math.min(indice, folhas.length)); }
    if (indice <= 0) { return 0; }
    return Math.max(0, Math.min(Math.floor((indice + 1) / 2), folhas.length));
  }

  // -- rendering -----------------------------------------------------------

  function carregarProximas() {
    var centro = viradas;
    folhas.forEach(function (folha, k) {
      if (Math.abs(k - centro) > JANELA) { return; }
      folha.querySelectorAll("img[data-src]").forEach(function (img) {
        img.src = img.dataset.src;
        delete img.dataset.src;
      });
    });
  }

  function aplicar(animar) {
    var total = folhas.length;
    folhas.forEach(function (folha, k) {
      var virada = k < viradas;
      folha.classList.toggle("virada", virada);
      folha.style.zIndex = virada ? String(k + 1) : String(total - k + 1);
    });

    livro.classList.remove("fechado", "aberto", "final");
    if (viradas === 0) { livro.classList.add("fechado"); }
    else if (viradas >= total) { livro.classList.add("final"); }
    else { livro.classList.add("aberto"); }

    if (!animar) {
      livro.classList.add("sem-animacao");
      void livro.offsetWidth;                  // flush, so the class actually applies
      requestAnimationFrame(function () { livro.classList.remove("sem-animacao"); });
    }

    carregarProximas();
    atualizarControles();
  }

  function atualizarControles() {
    var indice = paginaAtual();
    if (campoPagina && document.activeElement !== campoPagina) {
      campoPagina.value = String(indice + 1);
    }
    if (totalTexto) { totalTexto.textContent = String(paginas.length); }

    document.querySelectorAll("[data-acao='anterior'], [data-acao='primeira']")
      .forEach(function (b) { b.disabled = viradas <= 0; });
    document.querySelectorAll("[data-acao='proxima'], [data-acao='ultima']")
      .forEach(function (b) { b.disabled = viradas >= folhas.length; });

    if (miniaturas) {
      var visiveis = modo === "solo" ? [indice] : [indice, indice + 1];
      miniaturas.querySelectorAll("button").forEach(function (b, i) {
        b.classList.toggle("atual", visiveis.indexOf(i) !== -1);
      });
    }

    var hash = "#p=" + (indice + 1);
    if (location.hash !== hash) {
      try { history.replaceState(null, "", hash); } catch (e) { /* file:// */ }
    }
  }

  // -- navigation ----------------------------------------------------------

  var timerZ = null;

  function virar(destino, animar) {
    destino = Math.max(0, Math.min(destino, folhas.length));
    if (destino === viradas) { return; }
    var indiceMovendo = destino > viradas ? viradas : destino;
    viradas = destino;
    aplicar(animar !== false);

    // The leaf in motion has to ride above both stacks while it turns, so the
    // bump goes on after aplicar() has laid down the resting order, and comes
    // off once the transition is done.
    var movendo = folhas[indiceMovendo];
    if (movendo && animar !== false) {
      movendo.style.zIndex = String(folhas.length + 20);
      clearTimeout(timerZ);
      timerZ = setTimeout(function () { aplicar(true); }, 720);
    }
  }

  function proxima() { virar(viradas + 1); }
  function anterior() { virar(viradas - 1); }

  function irPara(numero, animar) {             // 1-based, as shown to the reader
    var indice = Math.max(0, Math.min((numero | 0) - 1, paginas.length - 1));
    virar(viradasPara(indice), animar);
  }

  // -- layout --------------------------------------------------------------

  function dimensionar() {
    var caixa = palco.getBoundingClientRect();
    var margem = window.innerWidth <= LIMITE_SOLO ? 20 : 40;
    var dispL = Math.max(120, caixa.width - margem);
    var dispA = Math.max(120, caixa.height - margem);
    var proporcaoLivro = modo === "solo" ? proporcao : proporcao * 2;

    var largura = Math.min(dispL, dispA * proporcaoLivro);
    var altura = largura / proporcaoLivro;
    livro.style.width = Math.round(largura) + "px";
    livro.style.height = Math.round(altura) + "px";
  }

  function reavaliar() {
    var desejado = modoDesejado();
    if (desejado !== modo) {
      var indice = folhas.length ? paginaAtual() : 0;
      montar(desejado);
      viradas = viradasPara(indice);
      dimensionar();
      aplicar(false);
    } else {
      dimensionar();
    }
  }

  // -- thumbnails ----------------------------------------------------------

  function montarMiniaturas() {
    if (!miniaturas) { return; }
    paginas.forEach(function (pagina, i) {
      var botao = elemento("button", null, miniaturas);
      botao.type = "button";
      botao.title = "Página " + (i + 1);
      var img = elemento("img", null, botao);
      img.src = pagina.thumb || pagina.src;
      img.alt = "";
      img.loading = "lazy";
      elemento("span", null, botao).textContent = String(i + 1);
      botao.addEventListener("click", function () { irPara(i + 1); });
    });
  }

  // -- zoom, fullscreen, sharing ------------------------------------------

  function aplicarZoom(valor) {
    zoom = Math.max(1, Math.min(valor, 3));
    livro.style.setProperty("--zoom", String(zoom));
    var botao = document.querySelector("[data-acao='zoom']");
    if (botao) { botao.classList.toggle("ativo", zoom > 1); }
    palco.style.cursor = zoom > 1 ? "grab" : "";
  }

  function alternarTelaCheia() {
    var alvo = document.documentElement;
    if (document.fullscreenElement) { document.exitFullscreen(); }
    else if (alvo.requestFullscreen) { alvo.requestFullscreen().catch(function () {}); }
    else { mostrarAviso("Tela cheia não disponível neste navegador"); }
  }

  function compartilhar() {
    var url = location.href;
    if (navigator.share) {
      navigator.share({ title: dados.titulo || document.title, url: url })
        .catch(function () {});
      return;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(
        function () { mostrarAviso("Link copiado"); },
        function () { mostrarAviso(url); }
      );
    } else {
      mostrarAviso(url);
    }
  }

  // -- input ---------------------------------------------------------------

  function ligarControles() {
    document.querySelectorAll("[data-acao]").forEach(function (botao) {
      botao.addEventListener("click", function () {
        switch (botao.dataset.acao) {
          case "primeira": virar(0); break;
          case "anterior": anterior(); break;
          case "proxima": proxima(); break;
          case "ultima": virar(folhas.length); break;
          case "miniaturas":
            miniaturas.classList.toggle("visivel");
            botao.classList.toggle("ativo", miniaturas.classList.contains("visivel"));
            reavaliar();
            break;
          case "zoom": aplicarZoom(zoom >= 2.2 ? 1 : (zoom >= 1.6 ? 2.2 : 1.6)); break;
          case "tela-cheia": alternarTelaCheia(); break;
          case "compartilhar": compartilhar(); break;
        }
      });
    });

    if (campoPagina) {
      campoPagina.addEventListener("change", function () { irPara(campoPagina.value); });
      campoPagina.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { irPara(campoPagina.value); campoPagina.blur(); }
      });
    }

    document.addEventListener("keydown", function (e) {
      if (e.target === campoPagina) { return; }
      switch (e.key) {
        case "ArrowRight": case "PageDown": case " ": proxima(); e.preventDefault(); break;
        case "ArrowLeft": case "PageUp": anterior(); e.preventDefault(); break;
        case "Home": virar(0); break;
        case "End": virar(folhas.length); break;
        case "+": case "=": aplicarZoom(zoom + .4); break;
        case "-": aplicarZoom(zoom - .4); break;
        case "f": case "F": alternarTelaCheia(); break;
        case "Escape": if (zoom > 1) { aplicarZoom(1); } break;
      }
    });

    // Click one half of the book to turn that way -- unless a link was hit.
    livro.addEventListener("click", function (e) {
      if (e.target.closest("a")) { return; }
      if (arrastou) { return; }
      var caixa = livro.getBoundingClientRect();
      if (e.clientX - caixa.left > caixa.width / 2) { proxima(); } else { anterior(); }
    });

    var inicioX = 0, inicioY = 0, scrollX = 0, scrollY = 0, pressionado = false;
    var arrastou = false;

    palco.addEventListener("pointerdown", function (e) {
      if (e.button !== 0) { return; }
      pressionado = true;
      arrastou = false;
      inicioX = e.clientX; inicioY = e.clientY;
      scrollX = palco.scrollLeft; scrollY = palco.scrollTop;
      if (zoom > 1) { palco.classList.add("arrastando"); }
    });

    palco.addEventListener("pointermove", function (e) {
      if (!pressionado) { return; }
      var dx = e.clientX - inicioX, dy = e.clientY - inicioY;
      if (Math.abs(dx) > 8 || Math.abs(dy) > 8) { arrastou = true; }
      if (zoom > 1) {                            // panning a zoomed page
        palco.scrollLeft = scrollX - dx;
        palco.scrollTop = scrollY - dy;
      }
    });

    function soltar(e) {
      if (!pressionado) { return; }
      pressionado = false;
      palco.classList.remove("arrastando");
      if (zoom > 1) { return; }
      var dx = e.clientX - inicioX;
      if (Math.abs(dx) > 55 && Math.abs(e.clientY - inicioY) < 90) {
        if (dx < 0) { proxima(); } else { anterior(); }
        setTimeout(function () { arrastou = false; }, 0);
      }
    }
    palco.addEventListener("pointerup", soltar);
    palco.addEventListener("pointercancel", function () { pressionado = false; });
    palco.addEventListener("dblclick", function () { aplicarZoom(zoom > 1 ? 1 : 2.2); });

    window.addEventListener("resize", debounce(reavaliar, 140));
    window.addEventListener("orientationchange", function () { setTimeout(reavaliar, 250); });
    window.addEventListener("hashchange", function () {
      var n = parseInt((location.hash.match(/p=(\d+)/) || [])[1], 10);
      if (n && n - 1 !== paginaAtual()) { irPara(n); }
    });
  }

  function debounce(fn, ms) {
    var t = null;
    return function () { clearTimeout(t); t = setTimeout(fn, ms); };
  }

  // -- start ---------------------------------------------------------------

  montarMiniaturas();
  montar(modoDesejado());
  dimensionar();

  var inicial = parseInt((location.hash.match(/p=(\d+)/) || [])[1], 10) || 1;
  viradas = viradasPara(inicial - 1);
  aplicar(false);
  ligarControles();

  // Once the first pages are up, quietly warm the rest of the catalogue.
  window.addEventListener("load", function () {
    setTimeout(function () {
      paginas.forEach(function (p) { var i = new Image(); i.src = p.src; });
    }, 900);
  });
})();
