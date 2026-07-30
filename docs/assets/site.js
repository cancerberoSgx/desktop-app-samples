// Shared behavior for all app homepages: screenshot lightbox + quickstart tabs.
// Vanilla JS, no dependencies, safe to no-op if the relevant markup is absent.

(function () {
  function initLightbox() {
    var lightbox = document.querySelector(".lightbox");
    if (!lightbox) return;
    var content = lightbox.querySelector(".lightbox-content");

    document.querySelectorAll(".gallery img, .gallery video").forEach(function (el) {
      el.addEventListener("click", function () {
        content.innerHTML = "";
        var clone = el.cloneNode(true);
        clone.removeAttribute("class");
        if (clone.tagName === "VIDEO") {
          clone.setAttribute("controls", "");
          clone.play();
        }
        content.appendChild(clone);
        lightbox.classList.add("open");
      });
    });

    lightbox.addEventListener("click", function () {
      lightbox.classList.remove("open");
      content.innerHTML = "";
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        lightbox.classList.remove("open");
        content.innerHTML = "";
      }
    });
  }

  function initTabs() {
    document.querySelectorAll(".tabs").forEach(function (tabs) {
      var buttons = tabs.querySelectorAll(".tab-btn");
      buttons.forEach(function (btn) {
        btn.addEventListener("click", function () {
          var target = btn.getAttribute("data-tab");
          var panelGroup = tabs.parentElement.querySelectorAll(".tab-panel");
          buttons.forEach(function (b) { b.classList.remove("active"); });
          panelGroup.forEach(function (p) { p.classList.remove("active"); });
          btn.classList.add("active");
          var panel = tabs.parentElement.querySelector('.tab-panel[data-tab="' + target + '"]');
          if (panel) panel.classList.add("active");
        });
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initLightbox();
    initTabs();
  });
})();
