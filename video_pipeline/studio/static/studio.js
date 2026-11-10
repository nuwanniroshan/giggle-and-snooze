// Studio page helpers. Pages work without them.
// 1. Fields marked data-keep keep their typed text across reloads (sessionStorage), and a
//    [data-count-for] element shows the field's character count.
// 2. Live progress for a running stage: polls the job endpoints once a second, appends new log
//    text, updates elapsed times, and reloads the page when the job ends.
(function () {
  "use strict";

  function store(action, key, value) {
    try {
      if (action === "get") return window.sessionStorage.getItem(key);
      if (action === "set") window.sessionStorage.setItem(key, value);
      if (action === "remove") window.sessionStorage.removeItem(key);
    } catch (e) { /* storage blocked: the field simply is not kept */ }
    return null;
  }

  document.querySelectorAll("textarea[data-keep], input[data-keep]").forEach(function (field) {
    var key = "keep:" + window.location.pathname + ":" + field.name;
    var saved = store("get", key);
    if (saved !== null) field.value = saved;
    field.addEventListener("input", function () { store("set", key, field.value); });
    if (field.form) field.form.addEventListener("submit", function () { store("remove", key); });
  });

  document.querySelectorAll("[data-count-for]").forEach(function (counter) {
    var field = document.getElementById(counter.dataset.countFor);
    if (!field) return;
    var min = field.getAttribute("minlength"), max = field.getAttribute("maxlength");
    function update() {
      var n = field.value.trim().length;
      counter.textContent = n + " / " + max + " characters" + (min && n < +min ? " (at least " + min + ")" : "");
    }
    field.addEventListener("input", update);
    update();
  });

  var known = document.body.dataset.runningJob || "";
  if (!known) return;

  var log = document.querySelector("pre.log[data-live]");
  var offset = log ? parseInt(log.dataset.offset || "0", 10) : 0;
  var dirty = false;  // an edit that a reload would lose
  document.addEventListener("input", function (event) {
    if (!(event.target.closest && event.target.closest("[data-keep]"))) dirty = true;
  });

  function fmt(seconds) {
    var total = Math.round(seconds);
    var h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
    var pad = function (n) { return (n < 10 ? "0" : "") + n; };
    return h ? h + ":" + pad(m) + ":" + pad(s) : m + ":" + pad(s);
  }

  function getJSON(url) {
    return fetch(url, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(url + ": " + r.status);
      return r.json();
    });
  }

  function pollLog() {
    if (!log || log.dataset.jobId !== known) return Promise.resolve();
    return getJSON("/api/jobs/" + encodeURIComponent(known) + "/log?offset=" + offset)
      .then(function (data) {
        if (data.text) {
          var atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
          log.appendChild(document.createTextNode(data.text));
          if (atEnd) log.scrollTop = log.scrollHeight;
        }
        offset = data.offset;
      });
  }

  function ended() {
    var banner = document.getElementById("running");
    if (dirty) {
      if (banner) banner.textContent = "The stage finished. Save your edits, then reload the page.";
      return;
    }
    var active = document.activeElement;
    if (banner && active && active.closest && active.closest("[data-keep]")) {
      // Don't pull the cursor out of a field the user is typing in: offer the reload instead.
      banner.textContent = "";
      var note = document.createElement("span");
      note.textContent = "The stage finished. Your typing is kept.";
      var button = document.createElement("button");
      button.type = "button";
      button.className = "secondary";
      button.textContent = "Show the result";
      button.addEventListener("click", function () { window.location.reload(); });
      banner.appendChild(note);
      banner.appendChild(button);
      return;
    }
    window.location.reload();
  }

  function tick() {
    getJSON("/api/jobs/current")
      .then(function (data) {
        var id = data.job ? data.job.id : "";
        if (id !== known) { ended(); return; }
        document.querySelectorAll("[data-elapsed]").forEach(function (el) {
          el.textContent = fmt(data.elapsed_seconds);
        });
        return pollLog().then(function () { setTimeout(tick, 1000); });
      })
      .catch(function () { setTimeout(tick, 2000); });
  }

  setTimeout(tick, 1000);
})();
