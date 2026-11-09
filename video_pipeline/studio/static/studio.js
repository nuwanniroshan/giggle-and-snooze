// Live progress for a running stage: polls the job endpoints once a second, appends new log
// text, updates elapsed times, and reloads the page when the job ends. Pages work without it.
(function () {
  "use strict";
  var known = document.body.dataset.runningJob || "";
  if (!known) return;

  var log = document.querySelector("pre.log[data-live]");
  var offset = log ? parseInt(log.dataset.offset || "0", 10) : 0;
  var dirty = false;
  document.addEventListener("input", function () { dirty = true; });

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
    if (dirty) {
      var banner = document.getElementById("running");
      if (banner) banner.textContent = "The stage finished. Save your edits, then reload the page.";
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
