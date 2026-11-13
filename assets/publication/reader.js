// Giggle & Snooze web reader (spec 023): one scene at a time, arrows / swipe / keys, optional read-to-me.
(() => {
  const scenes = [...document.querySelectorAll(".scene")];
  if (!scenes.length) return;
  document.body.classList.add("is-paged");
  const count = document.querySelector(".chrome__count");
  const prev = document.querySelector(".turn--prev");
  const next = document.querySelector(".turn--next");
  const fill = document.querySelector(".progress__fill");
  const listen = document.querySelector(".chrome__listen");
  const audio = new Audio();
  const story = scenes.filter((s) => s.dataset.page);
  let i = Math.max(0, Math.min(scenes.length - 1, Number(location.hash.slice(2)) || 0));
  let reading = false;

  function show(n) {
    i = Math.max(0, Math.min(scenes.length - 1, n));
    scenes.forEach((s, k) => {
      s.classList.toggle("is-current", k === i);
      s.inert = k !== i;
    });
    const s = scenes[i];
    const page = s.dataset.page;
    count.textContent = page ? `${page} of ${story.length}` : i === 0 ? "Cover" : "The end";
    prev.disabled = i === 0;
    next.disabled = i === scenes.length - 1;
    fill.style.setProperty("--p", i / (scenes.length - 1));
    history.replaceState(null, "", `#p${i}`);
    if (reading) play();
  }
  function play() {
    const src = scenes[i].dataset.audio;
    audio.pause();
    if (!src) return;
    audio.src = src;
    audio.play().catch(() => setReading(false));
  }
  function setReading(on) {
    reading = on;
    if (listen) {
      listen.setAttribute("aria-pressed", String(on));
      listen.textContent = on ? "Reading to you" : "Read to me";
    }
    on ? play() : audio.pause();
  }
  audio.addEventListener("ended", () => { if (reading && i < scenes.length - 1) show(i + 1); });
  audio.addEventListener("error", () => setReading(false));

  prev.addEventListener("click", () => show(i - 1));
  next.addEventListener("click", () => show(i + 1));
  if (listen) listen.addEventListener("click", () => setReading(!reading));
  document.querySelectorAll("[data-start]").forEach((b) => b.addEventListener("click", () => show(1)));
  document.querySelectorAll("[data-again]").forEach((b) => b.addEventListener("click", () => show(0)));
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowRight" || ev.key === "PageDown") show(i + 1);
    if (ev.key === "ArrowLeft" || ev.key === "PageUp") show(i - 1);
  });
  let x0 = null;
  document.addEventListener("pointerdown", (ev) => { x0 = ev.clientX; });
  document.addEventListener("pointerup", (ev) => {
    if (x0 === null) return;
    const dx = ev.clientX - x0;
    x0 = null;
    if (Math.abs(dx) > 60) show(dx < 0 ? i + 1 : i - 1);
  });
  window.addEventListener("hashchange", () => {
    const n = Number(location.hash.slice(2));
    if (Number.isFinite(n) && n !== i) show(n);
  });
  show(i);
})();
