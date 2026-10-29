(() => {
  "use strict";

  const sidebar = document.getElementById("guide-sidebar");
  const menuToggle = document.getElementById("menu-toggle");
  const backdrop = document.getElementById("sidebar-backdrop");
  const search = document.getElementById("guide-search");
  const results = document.getElementById("search-results");
  const resultsList = document.getElementById("search-list");
  const searchStatus = document.getElementById("search-status");
  const contents = document.getElementById("contents");
  const viewer = document.getElementById("image-viewer");
  const viewerTitle = document.getElementById("image-viewer-title");
  const viewerImage = document.getElementById("viewer-image");
  const annotations = document.getElementById("show-annotations");
  const chapters = [...document.querySelectorAll(".chapter")];
  const navLinks = [...contents.querySelectorAll('a[href^="#"]')];
  const normalize = (value) => value.normalize("NFKC").toLocaleLowerCase("ko-KR").replace(/\s+/g, "");

  function setMenu(open, restoreFocus = false) {
    sidebar.classList.toggle("is-open", open);
    backdrop.hidden = !open;
    menuToggle.setAttribute("aria-expanded", String(open));
    document.body.classList.toggle("no-scroll", open || viewer.open);
    if (restoreFocus) menuToggle.focus({ preventScroll: true });
  }

  menuToggle.addEventListener("click", () => {
    const open = menuToggle.getAttribute("aria-expanded") !== "true";
    setMenu(open);
    if (open) search.focus({ preventScroll: true });
  });
  backdrop.addEventListener("click", () => setMenu(false, true));
  window.matchMedia("(min-width: 981px)").addEventListener("change", (event) => {
    if (event.matches) setMenu(false);
  });
  contents.addEventListener("click", (event) => {
    if (event.target.closest("a")) setMenu(false);
  });

  const entries = chapters.map((chapter) => ({
    chapter,
    heading: chapter.querySelector("h2").textContent,
    text: chapter.textContent.replace(/\s+/g, " ").trim(),
    keywords: chapter.dataset.keywords,
  }));

  function searchGuide() {
    const query = search.value.trim();
    const words = query.split(/\s+/).filter(Boolean).map(normalize);
    resultsList.replaceChildren();
    results.hidden = !query;
    contents.hidden = !!query;
    if (!query) return;

    const matches = entries.filter((entry) => {
      const haystack = normalize(entry.heading + entry.text + entry.keywords);
      return words.every((word) => haystack.includes(word));
    }).sort((a, b) => {
      const score = (entry) => words.reduce((total, word) => (
        total + (normalize(entry.heading).includes(word) ? 5 : 0)
        + (normalize(entry.keywords).includes(word) ? 2 : 0)
      ), 0);
      return score(b) - score(a);
    });

    searchStatus.textContent = matches.length
      ? `${matches.length}개 항목을 찾았습니다.`
      : "검색 결과가 없습니다. 더 짧은 단어로 찾아보세요.";

    matches.forEach((entry) => {
      const item = document.createElement("li");
      const link = document.createElement("a");
      const title = document.createElement("strong");
      const snippet = document.createElement("p");
      const blocks = [...entry.chapter.querySelectorAll("h3, p, dt, th, td, li")];
      const matchingBlock = blocks.find((block) => words.every((word) => normalize(block.textContent).includes(word)));
      title.textContent = entry.heading;
      snippet.textContent = (matchingBlock?.textContent || entry.text).replace(/\s+/g, " ").trim().slice(0, 82) + "…";
      link.href = `#${entry.chapter.id}`;
      link.append(title, snippet);
      link.addEventListener("click", (event) => {
        event.preventDefault();
        entry.chapter.querySelectorAll("details").forEach((detail) => {
          if (words.some((word) => normalize(detail.textContent).includes(word))) detail.open = true;
        });
        const target = matchingBlock || entry.chapter;
        setMenu(false);
        target.classList.remove("search-hit");
        requestAnimationFrame(() => {
          target.scrollIntoView({ behavior: "smooth", block: "center" });
          target.classList.add("search-hit");
          target.setAttribute("tabindex", "-1");
          target.focus({ preventScroll: true });
        });
      });
      item.append(link);
      resultsList.append(item);
    });
  }

  search.addEventListener("input", searchGuide);
  search.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      const first = resultsList.querySelector("a");
      if (first) {
        event.preventDefault();
        first.click();
      }
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Tab" && sidebar.classList.contains("is-open")) {
      const focusable = [menuToggle, ...sidebar.querySelectorAll(
        'input, button, a[href], [tabindex="0"]'
      )].filter((element) => element.getClientRects().length > 0);
      const index = focusable.indexOf(document.activeElement);
      if ((event.shiftKey && index <= 0) || (!event.shiftKey && index === focusable.length - 1)) {
        event.preventDefault();
        focusable[event.shiftKey ? focusable.length - 1 : 0]?.focus();
      }
    }
    const editing = event.target instanceof Element
      && event.target.closest('input, textarea, select, [contenteditable="true"]');
    if (event.key === "/" && !editing && !event.ctrlKey && !event.metaKey && !event.altKey && !viewer.open) {
      event.preventDefault();
      if (window.matchMedia("(max-width: 980px)").matches) setMenu(true);
      search.focus();
    }
    if (event.key === "Escape" && !viewer.open) {
      if (search.value) {
        search.value = "";
        searchGuide();
      } else if (sidebar.classList.contains("is-open")) {
        setMenu(false, true);
      }
    }
  });

  let scrollScheduled = false;
  function updateCurrentChapter() {
    const threshold = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--topbar")) + 100;
    let active = null;
    for (const chapter of chapters) {
      if (chapter.getBoundingClientRect().top <= threshold) active = chapter.id;
      else break;
    }
    navLinks.forEach((link) => {
      if (link.getAttribute("href") === `#${active}`) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    });
    scrollScheduled = false;
  }
  window.addEventListener("scroll", () => {
    if (!scrollScheduled) {
      scrollScheduled = true;
      requestAnimationFrame(updateCurrentChapter);
    }
  }, { passive: true });
  updateCurrentChapter();

  let imageTrigger = null;
  document.querySelectorAll(".screen-open").forEach((button) => {
    button.addEventListener("click", () => {
      imageTrigger = button;
      const figure = button.closest("figure");
      const original = button.querySelector("img");
      const canvas = document.createElement("div");
      canvas.className = "screen-canvas";
      const image = original.cloneNode();
      image.loading = "eager";
      image.removeAttribute("width");
      image.removeAttribute("height");
      // Capture resolution is 1.5× CSS pixels. Keep desktop screens scrollable
      // on mobile instead of shrinking their button labels again in the viewer.
      image.style.width = `${Math.round(original.naturalWidth / 1.5)}px`;
      canvas.append(image);
      figure.querySelectorAll(".annotation").forEach((pin) => canvas.append(pin.cloneNode(true)));
      viewerTitle.textContent = figure.querySelector("figcaption").textContent;
      viewerImage.replaceChildren(canvas);
      viewer.showModal();
      document.body.classList.add("no-scroll");
      viewer.querySelector(".viewer-scroll").scrollTo(0, 0);
      document.getElementById("close-viewer").focus({ preventScroll: true });
    });
  });
  document.getElementById("close-viewer").addEventListener("click", () => viewer.close());
  viewer.addEventListener("click", (event) => {
    if (event.target !== viewer) return;
    const box = viewer.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right
      || event.clientY < box.top || event.clientY > box.bottom) viewer.close();
  });
  viewer.addEventListener("close", () => {
    document.body.classList.toggle("no-scroll", sidebar.classList.contains("is-open"));
    viewerImage.replaceChildren();
    imageTrigger?.focus({ preventScroll: true });
  });

  annotations.addEventListener("change", () => {
    document.body.classList.toggle("annotations-hidden", !annotations.checked);
  });

  document.getElementById("copy-template").addEventListener("click", async () => {
    const text = document.getElementById("template-text").textContent;
    const status = document.getElementById("copy-status");
    try {
      try {
        await navigator.clipboard.writeText(text);
      } catch {
        // file:// and some browser policies do not expose the Clipboard API.
        const field = document.createElement("textarea");
        field.value = text;
        field.style.cssText = "position:fixed;top:0;left:-9999px";
        document.body.append(field);
        field.select();
        const copied = document.execCommand("copy");
        field.remove();
        if (!copied) throw new Error("Clipboard unavailable");
      }
      status.textContent = "복사했습니다. 설정 → 요약 양식 → 양식 내용에 붙여넣으세요.";
    } catch {
      status.textContent = "자동 복사를 할 수 없습니다. 위 예시를 직접 선택해 복사해 주세요.";
    }
  });

  const figures = [...document.querySelectorAll(".screenshot img")];
  // All images are embedded: eager decoding also makes native Ctrl/Cmd+P safe.
  figures.forEach((image) => { image.loading = "eager"; });
  let printState = null;
  function preparePrint() {
    if (printState) return;
    printState = [...document.querySelectorAll("details")].map((detail) => ({
      detail, open: detail.open,
    }));
    printState.forEach(({ detail }) => { detail.open = true; });
    if (viewer.open) viewer.close();
    setMenu(false);
  }
  function restoreAfterPrint() {
    printState?.forEach(({ detail, open }) => { detail.open = open; });
    printState = null;
  }
  window.addEventListener("beforeprint", preparePrint);
  window.addEventListener("afterprint", restoreAfterPrint);
  document.getElementById("print-guide").addEventListener("click", async () => {
    preparePrint();
    await Promise.allSettled(figures.map((image) => image.decode()));
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    window.print();
  });
})();
