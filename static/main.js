/* Books in Books — literary UI + D3 graph */

(() => {
  "use strict";

  // —— State ——
  let svg, simulation, zoom;
  let linkGroup, nodeGroup, labelGroup;
  let nodes = [];
  let links = [];
  let allBooks = [];
  let allReferences = [];
  let startingBooks = [];
  let selectedNode = null;
  let hiddenNodeIds = new Set();
  let pendingFiles = [];
  let processPollTimer = null;
  let processStartInFlight = false;
  let processPollInFlight = false;
  let isStaticMode = false;
  let searchTimer = null;
  let focusBookId = null;
  /** Books always on; opt-in cultural layers */
  let enabledKinds = loadEnabledKinds();
  let showAuthorLinks = loadAuthorLinksPref();
  let sharedNodeIds = new Set();
  let zoomK = 1;
  const LABEL_ZOOM = 1.6; // zoomed in this far, every node gets its title
  const AUTHOR_LINK_COLOR = "#c9a227";
  const REF_LINK_COLOR = "#6d7c75";

  function loadAuthorLinksPref() {
    try {
      return localStorage.getItem("bib.authorLinks") !== "off";
    } catch (e) {
      return true;
    }
  }

  /* Stored quotes from older extractions still carry HTML entities (&#13;). */
  function cleanQuote(text) {
    if (!text) return "";
    let out = String(text);
    if (out.indexOf("&") !== -1) {
      const area = document.createElement("textarea");
      area.innerHTML = out;
      out = area.value;
    }
    return out.replace(/\s+/g, " ").trim();
  }

  /* "Dickens, Charles" and "Charles Dickens" are the same author. */
  function authorKey(name) {
    const text = String(name || "")
      .toLowerCase()
      .replace(/\([^)]*\)/g, " ")
      .replace(/[^\p{L}\s]/gu, " ");
    const tokens = text.split(/\s+/).filter((t) => t.length > 1);
    if (!tokens.length) return "";
    const key = tokens.sort().join(" ");
    const generic = ["unknown", "anonymous", "various", "cocktail", "movie", "music"];
    return generic.includes(key) ? "" : key;
  }

  function linkStroke(d) {
    return d && d.type === "author" ? AUTHOR_LINK_COLOR : REF_LINK_COLOR;
  }

  function linkDash(d) {
    return d && d.type === "author" ? "2 5" : null;
  }

  function linkStrength(d) {
    // Author ties only nudge clusters together; they must not reshape the graph
    return d && d.type === "author" ? 0.06 : 0.55;
  }

  const KIND_COLORS = {
    book: { fill: "#7a8f9c", stroke: "#4a5c66" },
    movie: { fill: "#d4a84b", stroke: "#8a6a24" },
    music: { fill: "#4f7ec8", stroke: "#2f528a" },
    cocktail: { fill: "#c45f6a", stroke: "#8a3540" },
  };

  const SELECTED_FILL = "#2f9e6b";
  const SELECTED_STROKE = "#1d6b48";

  function loadEnabledKinds() {
    const base = new Set(["book"]);
    try {
      const raw = localStorage.getItem("bib-enabled-kinds");
      if (!raw) return base;
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        parsed.forEach((k) => {
          if (k && k !== "book") base.add(k);
        });
      }
    } catch (_) {
      /* ignore */
    }
    return base;
  }

  function saveEnabledKinds() {
    try {
      localStorage.setItem(
        "bib-enabled-kinds",
        JSON.stringify([...enabledKinds].filter((k) => k !== "book"))
      );
    } catch (_) {
      /* ignore */
    }
  }

  function kindOf(bookOrNode) {
    const raw = (bookOrNode && bookOrNode.kind) || "book";
    const kind = String(raw).toLowerCase().trim();
    if (kind === "film" || kind === "films" || kind === "movies") return "movie";
    if (kind === "song" || kind === "songs" || kind === "album") return "music";
    if (kind === "drink" || kind === "drinks" || kind === "cocktail") return "cocktail";
    if (KIND_COLORS[kind]) return kind;
    return "book";
  }

  function isKindVisible(kind) {
    return enabledKinds.has(kindOf({ kind }));
  }

  function nodeShouldStay(bookOrNode) {
    if (!bookOrNode) return false;
    if (bookOrNode.isStarting || bookOrNode.is_starting) return true;
    return isKindVisible(kindOf(bookOrNode));
  }

  // —— Helpers ——
  function $(id) {
    return document.getElementById(id);
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text == null ? "" : String(text);
    return div.innerHTML;
  }

  function notify(title, text = "", kind = "info", duration = 4500) {
    const region = $("toast-region");
    if (!region) {
      console[kind === "error" ? "error" : "log"](title, text);
      return;
    }
    const toast = document.createElement("div");
    toast.className = `toast is-${kind}`;
    toast.innerHTML = `
      <div class="toast-body">
        <div class="toast-title">${escapeHtml(title)}</div>
        ${text ? `<div class="toast-text">${escapeHtml(text)}</div>` : ""}
      </div>
      <button type="button" class="toast-close" aria-label="Dismiss">×</button>`;
    const remove = () => toast.remove();
    toast.querySelector(".toast-close")?.addEventListener("click", remove);
    region.appendChild(toast);
    if (duration > 0) setTimeout(remove, duration);
  }

  function setUploadStatus(message, kind = "info") {
    const status = $("upload-status");
    if (!status) return;
    status.hidden = !message;
    status.textContent = message || "";
    status.classList.remove("error", "is-error", "is-success", "is-info");
    if (kind === "error") status.classList.add("is-error", "error");
    else if (kind === "success") status.classList.add("is-success");
    else status.classList.add("is-info");
  }

  function showGraphMessage(title, text) {
    const container = $("graph-container");
    if (!container) return;
    let el = container.querySelector(".graph-message");
    if (!el) {
      el = document.createElement("div");
      el.className = "graph-message";
      container.appendChild(el);
    }
    el.innerHTML = `<strong>${escapeHtml(title)}</strong><p>${escapeHtml(text)}</p>`;
  }

  function clearGraphMessage() {
    $("graph-container")?.querySelector(".graph-message")?.remove();
  }

  function detectStaticMode() {
    return (
      window.location.pathname.includes("/docs/") ||
      window.location.hostname.includes("github.io") ||
      window.location.protocol === "file:"
    );
  }

  function staticBasePath() {
    const pathname = window.location.pathname;
    if (pathname.includes("/docs/")) return ".";
    if (window.location.hostname.includes("github.io")) {
      const pathParts = pathname.split("/").filter((p) => p && p !== "index.html");
      if (pathParts.length > 0) return "/" + pathParts[0];
    }
    return ".";
  }

  // —— Data ——
  async function fetchBooksAndReferences() {
    let booksPromise;
    let referencesPromise;

    if (isStaticMode) {
      const base = staticBasePath();
      booksPromise = fetch(`${base}/books.json`).then((r) => {
        if (!r.ok) throw new Error(`Failed to load books.json: ${r.status}`);
        return r.json();
      });
      referencesPromise = fetch(`${base}/references.json`).then((r) => {
        if (!r.ok) throw new Error(`Failed to load references.json: ${r.status}`);
        return r.json();
      });
    } else {
      booksPromise = fetch("/api/books").then((r) => r.json());
      referencesPromise = fetch("/api/references").then((r) => r.json());
    }

    const [books, references] = await Promise.all([booksPromise, referencesPromise]);
    if (books.error) throw new Error(books.error);
    if (references.error) throw new Error(references.error);
    allBooks = books;
    allReferences = references;
    return { books, references };
  }

  async function refreshAll() {
    try {
      clearGraphMessage();
      const { books, references } = await fetchBooksAndReferences();
      renderLibrary();
      renderGraph(books, references);
      updateFirstRun(!ownedBooks().length);
      if (!ownedBooks().length && isStaticMode) {
        showGraphMessage(
          "No library books in this export",
          "This static site only shows books marked as owned in the exported network."
        );
      }
    } catch (err) {
      console.error(err);
      updateFirstRun(false);
      showGraphMessage(
        "Could not load your network",
        err.message || "Check that the server is running, then refresh."
      );
      notify("Could not load data", err.message || "Unknown error", "error", 7000);
    }
  }

  function updateFirstRun(show) {
    const panel = $("first-run");
    if (!panel) return;
    panel.hidden = !show || isStaticMode;
  }

  // —— Library ——
  function ownedBooks() {
    return allBooks.filter((b) => b.is_starting);
  }

  function referenceCountFor(bookId) {
    const id = String(bookId);
    let count = 0;
    allReferences.forEach((ref) => {
      if (String(ref.source) === id) count += 1;
    });
    return count;
  }

  function renderLibrary() {
    const list = $("library-list");
    const empty = $("library-empty");
    const countEl = $("library-count");
    if (!list) return;

    const owned = ownedBooks();
    if (countEl) countEl.textContent = owned.length ? `${owned.length}` : "";

    if (!owned.length) {
      list.innerHTML = "";
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;

    list.innerHTML = owned
      .map((book) => {
        const id = String(book.id);
        const hidden = hiddenNodeIds.has(id);
        const refs = book.outgoing_count != null ? book.outgoing_count : referenceCountFor(book.id);
        const active = selectedNode && String(selectedNode.id) === id;
        const focused = focusBookId && String(focusBookId) === id;
        const cover = book.cover_url
          ? `<img class="library-cover" src="${escapeHtml(book.cover_url)}" alt="" loading="lazy">`
          : `<div class="library-cover placeholder">${escapeHtml((book.title || "?").slice(0, 1))}</div>`;
        return `
          <li>
            <div class="library-item${hidden ? " hidden-book" : ""}${active || focused ? " active" : ""}" data-book-id="${id}">
              ${cover}
              <button type="button" class="library-item-body" data-action="focus" data-book-id="${id}">
                <span class="book-title">${escapeHtml(book.title)}</span>
                <span class="book-author">${escapeHtml(book.author || "Unknown")}</span>
                <span class="book-meta">${refs} reference${refs === 1 ? "" : "s"}</span>
              </button>
              <div class="library-actions">
                <button type="button" data-action="toggle" data-book-id="${id}" title="Show or hide on graph">${hidden ? "Show" : "Hide"}</button>
                ${
                  isStaticMode
                    ? ""
                    : `<button type="button" data-action="reprocess" data-book-id="${id}" title="Ask Borges to re-read">Retry</button>`
                }
              </div>
            </div>
          </li>`;
      })
      .join("");
  }

  function onLibraryClick(e) {
    const actionBtn = e.target.closest("[data-action]");
    if (!actionBtn) return;
    const bookId = actionBtn.getAttribute("data-book-id");
    if (!bookId) return;
    const action = actionBtn.getAttribute("data-action");

    if (action === "toggle") {
      e.preventDefault();
      toggleBookVisibility(bookId);
      return;
    }
    if (action === "reprocess") {
      e.preventDefault();
      reprocessBook(bookId);
      return;
    }
    if (action === "focus") {
      focusBookId = bookId;
      const focusBtn = $("focus-mode");
      if (focusBtn) focusBtn.hidden = false;
      const node = nodes.find((n) => String(n.id) === String(bookId));
      if (node) {
        selectNode(node);
        centerOnNode(node);
        applyFocusMode();
      } else {
        openDetailPanel(bookId);
      }
      renderLibrary();
    }
  }

  async function reprocessBook(bookId) {
    if (isStaticMode) {
      notify("Unavailable", "Reprocess needs the local Flask server.", "error");
      return;
    }
    try {
      const res = await fetch(`/api/book/${bookId}/reprocess`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Could not reprocess");
      notify("Re-reading", "Borges is opening the book again…", "info");
      showProcessBanner(data.message || "Borges: Re-reading…", 5, "info");
      pollProcessStatus();
    } catch (err) {
      notify("Reprocess failed", err.message, "error", 8000);
    }
  }

  // —— Detail panel ——
  async function openDetailPanel(bookId) {
    const panel = $("detail-panel");
    const content = $("detail-content");
    const app = document.querySelector(".app");
    if (!panel || !content) return;

    panel.hidden = false;
    if (app) app.classList.add("detail-open");
    content.innerHTML = `<p class="detail-empty">Loading…</p>`;

    let data;
    if (isStaticMode) {
      const book = allBooks.find((b) => String(b.id) === String(bookId));
      if (!book) {
        content.innerHTML = `<p class="detail-empty">Book not found.</p>`;
        return;
      }
      const outgoing = allReferences
        .filter((r) => String(r.source) === String(bookId))
        .map((r) => allBooks.find((b) => String(b.id) === String(r.target)))
        .filter(Boolean);
      const incoming = allReferences
        .filter((r) => String(r.target) === String(bookId))
        .map((r) => allBooks.find((b) => String(b.id) === String(r.source)))
        .filter(Boolean);
      data = {
        source_book: book,
        outgoing,
        incoming,
        outgoing_count: outgoing.length,
        incoming_count: incoming.length,
      };
    } else {
      try {
        const res = await fetch(`/api/book/${bookId}/references`);
        data = await res.json();
        if (!res.ok) throw new Error(data.error || "Failed to load");
        data.outgoing = data.outgoing || data.references || [];
        data.incoming = data.incoming || [];
      } catch (err) {
        content.innerHTML = `<p class="detail-empty">${escapeHtml(err.message)}</p>`;
        notify("Could not open book", err.message, "error");
        return;
      }
    }

    const book = data.source_book;
    const isOwned = allBooks.find((b) => String(b.id) === String(book.id))?.is_starting;
    data.skipped = data.skipped || [];

    content.innerHTML = `
      <p class="detail-kicker">${isOwned ? "In your library" : kindLabel(book.kind)}</p>
      <h2 class="detail-title">${escapeHtml(book.title)}</h2>
      <p class="detail-author">${escapeHtml(book.author || "Unknown author")}</p>
      ${
        book.kind && book.kind !== "book"
          ? `<p class="kind-badge kind-${escapeHtml(book.kind)}">${escapeHtml(kindLabel(book.kind))}</p>`
          : ""
      }
      ${
        book.cover_url
          ? `<img class="detail-cover" src="${escapeHtml(book.cover_url)}" alt="">`
          : ""
      }
      ${
        book.verified
          ? `<p class="verify-badge">Verified via Open Library${
              book.match_score != null ? ` · ${Math.round(book.match_score * 100)}% match` : ""
            }</p>`
          : `<p class="verify-badge unverified">Not verified in Open Library</p>`
      }
      ${
        isOwned && !isStaticMode
          ? `<p style="margin-top:0.75rem"><button type="button" class="btn btn-ghost" id="detail-reprocess" data-book-id="${book.id}">Ask Borges to re-read</button></p>`
          : ""
      }

      <section class="detail-section">
        <h3>Mentions (${data.outgoing_count ?? data.outgoing.length})</h3>
        ${
          data.outgoing.length
            ? `<ul class="ref-list">${data.outgoing
                .map(
                  (r) => `
              <li>
                <button type="button" data-jump-id="${r.id}">
                  ${escapeHtml(r.title)}${r.verified ? ' <span class="inline-verified">verified</span>' : ""}${
                    r.kind && r.kind !== "book"
                      ? ` <span class="inline-kind">${escapeHtml(kindLabel(r.kind))}</span>`
                      : ""
                  }
                  <span class="ref-author">${escapeHtml(r.author || "")}</span>
                  ${r.context ? `<span class="context-snippet">…${escapeHtml(cleanQuote(r.context))}…</span>` : ""}
                </button>
              </li>`
                )
                .join("")}</ul>`
            : `<p class="detail-empty">No outgoing references found.</p>`
        }
      </section>

      <section class="detail-section">
        <h3>Mentioned by (${data.incoming_count ?? data.incoming.length})</h3>
        ${
          data.incoming.length
            ? `<ul class="ref-list">${data.incoming
                .map(
                  (r) => `
              <li>
                <button type="button" data-jump-id="${r.id}">
                  ${escapeHtml(r.title)}${r.verified ? ' <span class="inline-verified">verified</span>' : ""}${
                    r.kind && r.kind !== "book"
                      ? ` <span class="inline-kind">${escapeHtml(kindLabel(r.kind))}</span>`
                      : ""
                  }
                  <span class="ref-author">${escapeHtml(r.author || "")}</span>
                </button>
              </li>`
                )
                .join("")}</ul>`
            : `<p class="detail-empty">Nothing in your network cites this book yet.</p>`
        }
      </section>

      ${
        data.skipped.length
          ? `<section class="detail-section">
              <h3>Skipped mentions (${data.skipped.length})</h3>
              <ul class="skipped-list">
                ${data.skipped
                  .map(
                    (s) => `<li>${escapeHtml(s.title)}<span class="reason">${escapeHtml(s.reason || "")}</span></li>`
                  )
                  .join("")}
              </ul>
            </section>`
          : ""
      }
    `;

    content.querySelector("#detail-reprocess")?.addEventListener("click", () => {
      reprocessBook(book.id);
    });
  }

  function closeDetailPanel() {
    const panel = $("detail-panel");
    const app = document.querySelector(".app");
    if (panel) panel.hidden = true;
    if (app) app.classList.remove("detail-open");
    selectedNode = null;
    highlightPathToNode(null);
    renderLibrary();
    // Resize graph after layout change
    setTimeout(() => {
      if (svg) {
        const container = $("graph-container");
        if (container) {
          svg.attr("width", container.clientWidth).attr("height", container.clientHeight);
        }
      }
    }, 50);
  }

  function onDetailJump(e) {
    const btn = e.target.closest("[data-jump-id]");
    if (!btn) return;
    const id = btn.getAttribute("data-jump-id");
    const node = nodes.find((n) => String(n.id) === String(id));
    if (node) {
      selectNode(node);
      centerOnNode(node);
    } else {
      openDetailPanel(id);
    }
  }

  // —— Search ——
  function setupSearch() {
    const input = $("search-input");
    const results = $("search-results");
    if (!input || !results) return;

    input.addEventListener("input", () => {
      clearTimeout(searchTimer);
      const q = input.value.trim();
      if (!q) {
        results.hidden = true;
        results.innerHTML = "";
        return;
      }
      searchTimer = setTimeout(() => runSearch(q), 200);
    });

    input.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        results.hidden = true;
        input.blur();
      }
    });

    document.addEventListener("click", (e) => {
      if (!e.target.closest(".search-wrap")) {
        results.hidden = true;
      }
    });
  }

  async function runSearch(query) {
    const results = $("search-results");
    if (!results) return;

    let books;
    if (isStaticMode) {
      const q = query.toLowerCase();
      books = allBooks
        .filter(
          (b) =>
            (b.title || "").toLowerCase().includes(q) ||
            (b.author || "").toLowerCase().includes(q)
        )
        .slice(0, 50);
    } else {
      try {
        const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
        books = await res.json();
        if (!res.ok) throw new Error(books.error || "Search failed");
      } catch (err) {
        console.error(err);
        notify("Search failed", err.message, "error");
        return;
      }
    }

    if (!books.length) {
      results.innerHTML = `<li><button type="button" disabled>No matches</button></li>`;
      results.hidden = false;
      return;
    }

    results.innerHTML = books
      .map(
        (b) => `
      <li>
        <button type="button" data-search-id="${b.id}">
          <span class="result-title">${escapeHtml(b.title)}</span>
          <span class="result-author">${escapeHtml(b.author || "")}</span>
        </button>
      </li>`
      )
      .join("");
    results.hidden = false;
  }

  function onSearchPick(e) {
    const btn = e.target.closest("[data-search-id]");
    if (!btn) return;
    const id = btn.getAttribute("data-search-id");
    $("search-results").hidden = true;
    $("search-input").value = "";
    const node = nodes.find((n) => String(n.id) === String(id));
    if (node) {
      selectNode(node);
      centerOnNode(node);
    } else {
      openDetailPanel(id);
    }
  }

  // —— Upload / process ——
  function setupUpload() {
    const modal = $("upload-modal");
    const openBtn = $("open-upload");
    const pickBtn = $("pick-files");
    const fileInput = $("file-input");
    const dropZone = $("drop-zone");
    const submit = $("upload-submit");
    const clearBtn = $("upload-clear");
    const runProcess = $("run-process");

    if (isStaticMode) {
      if (openBtn) {
        openBtn.disabled = true;
        openBtn.title = "Uploading requires the local Flask server";
      }
      openBtn?.addEventListener("click", () => {
        notify(
          "Upload unavailable",
          "This static page can’t upload books. Run the Flask app locally to add files.",
          "error",
          7000
        );
      });
      return;
    }

    openBtn?.addEventListener("click", () => openModal());
    modal?.querySelectorAll("[data-close-modal]").forEach((el) => {
      el.addEventListener("click", closeModal);
    });
    const openPicker = (e) => {
      e?.preventDefault?.();
      e?.stopPropagation?.();
      fileInput?.click();
    };
    pickBtn?.addEventListener("click", openPicker);
    dropZone?.addEventListener("click", (e) => {
      if (e.target === pickBtn || pickBtn?.contains(e.target)) return;
      openPicker(e);
    });
    dropZone?.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") openPicker(e);
    });
    fileInput?.addEventListener("change", () => {
      addPendingFiles(fileInput.files);
      fileInput.value = "";
    });

    dropZone?.addEventListener("dragover", (e) => {
      e.preventDefault();
      dropZone.classList.add("dragover");
    });
    dropZone?.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
    dropZone?.addEventListener("drop", (e) => {
      e.preventDefault();
      dropZone.classList.remove("dragover");
      addPendingFiles(e.dataTransfer.files);
    });

    clearBtn?.addEventListener("click", () => {
      pendingFiles = [];
      renderUploadQueue();
      setUploadStatus("List cleared.", "info");
    });
    submit?.addEventListener("click", uploadAndProcess);
    runProcess?.addEventListener("click", () => startProcess());
  }

  function openModal() {
    const modal = $("upload-modal");
    if (!modal) return;
    modal.hidden = false;
    // Keep any files already queued so users can add more in another browse pass
    renderUploadQueue();
    if (!pendingFiles.length) setUploadStatus("", "info");
    else setUploadStatus(`${pendingFiles.length} file(s) ready to upload.`, "info");
  }

  function closeModal() {
    const modal = $("upload-modal");
    if (modal) modal.hidden = true;
  }

  function addPendingFiles(fileList) {
    if (!fileList) return;
    let added = 0;
    let rejected = 0;
    let dupes = 0;
    const existing = new Set(pendingFiles.map((f) => f.name.toLowerCase()));
    Array.from(fileList).forEach((file) => {
      const name = file.name.toLowerCase();
      if (!name.endsWith(".epub") && !name.endsWith(".txt")) {
        rejected += 1;
        return;
      }
      if (existing.has(name)) {
        dupes += 1;
        return;
      }
      existing.add(name);
      pendingFiles.push(file);
      added += 1;
    });
    renderUploadQueue();
    const bits = [];
    if (added) bits.push(`Added ${added}`);
    if (dupes) bits.push(`skipped ${dupes} duplicate(s)`);
    if (rejected) bits.push(`skipped ${rejected} unsupported`);
    if (bits.length) {
      setUploadStatus(`${bits.join(", ")}. ${pendingFiles.length} file(s) ready.`, "info");
    }
  }

  function renderUploadQueue() {
    const queue = $("upload-queue");
    const submit = $("upload-submit");
    const clearBtn = $("upload-clear");
    if (!queue) return;
    queue.innerHTML = pendingFiles
      .map(
        (f, idx) =>
          `<li><span>${escapeHtml(f.name)}</span><button type="button" class="queue-remove" data-idx="${idx}" aria-label="Remove">×</button></li>`
      )
      .join("");
    queue.querySelectorAll(".queue-remove").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.getAttribute("data-idx"));
        if (!Number.isNaN(idx)) {
          pendingFiles.splice(idx, 1);
          renderUploadQueue();
          setUploadStatus(
            pendingFiles.length ? `${pendingFiles.length} file(s) ready to upload.` : "",
            "info"
          );
        }
      });
    });
    if (submit) submit.disabled = pendingFiles.length === 0;
    if (clearBtn) clearBtn.hidden = pendingFiles.length === 0;
  }

  async function uploadAndProcess() {
    const submit = $("upload-submit");
    if (!pendingFiles.length) {
      setUploadStatus("Choose at least one .epub or .txt file first.", "error");
      return;
    }

    setUploadStatus("Uploading…", "info");
    if (submit) submit.disabled = true;

    const form = new FormData();
    pendingFiles.forEach((f) => form.append("files", f));

    try {
      const res = await fetch("/api/upload", { method: "POST", body: form });
      let data;
      try {
        data = await res.json();
      } catch (_) {
        throw new Error(`Upload failed (HTTP ${res.status}). Is the server running?`);
      }

      if (!res.ok) {
        const rejected = (data.rejected || [])
          .map((r) => `${r.filename}: ${r.reason}`)
          .join("; ");
        throw new Error(data.error || rejected || "Upload failed");
      }

      const rejectedCount = (data.rejected || []).length;
      let msg = `Uploaded ${data.saved.length} file(s).`;
      if (rejectedCount) msg += ` ${rejectedCount} rejected.`;
      msg += " Starting extraction…";
      setUploadStatus(msg, "success");
      notify(
        "Upload complete",
        `${data.saved.length} file(s) saved. Borges & Lucien are on it…`,
        "success"
      );

      pendingFiles = [];
      renderUploadQueue();
      closeModal();
      await startProcess();
    } catch (err) {
      setUploadStatus(err.message, "error");
      notify("Upload failed", err.message, "error", 8000);
      if (submit) submit.disabled = false;
    }
  }

  async function startProcess() {
    if (processStartInFlight) return;
    processStartInFlight = true;
    showProcessBanner("Borges: Opening the stack. This may take a few minutes.", 5, "info");
    try {
      // Attach to an in-progress job instead of POSTing again (avoids lock fights)
      try {
        const stRes = await fetch("/api/process/status", { signal: AbortSignal.timeout(8000) });
        if (stRes.ok) {
          const st = await stRes.json();
          if (st.status === "running") {
            notify("Already reading", "Borges & Lucien are already at work.", "info");
            pollProcessStatus();
            return;
          }
        }
      } catch (_) {
        /* fall through and try to start */
      }

      const res = await fetch("/api/process", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
        signal: AbortSignal.timeout(15000),
      });
      let data;
      try {
        data = await res.json();
      } catch (_) {
        throw new Error(`Could not start processing (HTTP ${res.status}).`);
      }

      if (res.status === 409) {
        notify("Already reading", "Borges & Lucien are already at work.", "info");
        pollProcessStatus();
        return;
      }
      if (!res.ok) throw new Error(data.error || "Could not start processing");

      if (data.status === "done" && !(data.total > 0) && !(data.books_processed || []).length) {
        showProcessBanner(
          data.message || "All books are already processed.",
          100,
          "success"
        );
        notify("Nothing new to process", data.message || "Your library is up to date.", "info");
        await refreshAll();
        setTimeout(hideProcessBanner, 3500);
        return;
      }

      pollProcessStatus();
    } catch (err) {
      showProcessBanner(err.message, 0, "error");
      notify("Processing failed", err.message, "error", 8000);
      setTimeout(hideProcessBanner, 8000);
    } finally {
      processStartInFlight = false;
    }
  }

  function pollProcessStatus() {
    clearInterval(processPollTimer);
    let sawRunning = false;
    let ticksWithoutRunning = 0;
    let staleTicks = 0;
    let lastMessage = "";

    processPollTimer = setInterval(async () => {
      if (processPollInFlight) return;
      processPollInFlight = true;
      try {
        const res = await fetch("/api/process/status", {
          signal: AbortSignal.timeout(10000),
        });
        let data;
        try {
          data = await res.json();
        } catch (_) {
          throw new Error("Lost connection while checking progress.");
        }

        const total = data.total || 0;
        const processed = data.processed || 0;
        const pct = total
          ? Math.round((processed / total) * 100)
          : data.status === "running"
            ? Math.min(90, 10 + ticksWithoutRunning * 2)
            : 0;

        if (data.status === "running") {
          sawRunning = true;
          ticksWithoutRunning = 0;
          if (data.message === lastMessage) staleTicks += 1;
          else {
            staleTicks = 0;
            lastMessage = data.message || "";
          }
          // Worker died but status was not healed yet — ask server to reset
          if (data.worker_alive === false || staleTicks > 90) {
            try {
              await fetch("/api/process/reset", { method: "POST" });
            } catch (_) {
              /* ignore */
            }
            clearInterval(processPollTimer);
            processPollTimer = null;
            showProcessBanner(
              "Borges lost his place. Click Ask Borges & Lucien to continue.",
              0,
              "error"
            );
            notify(
              "Processing stalled",
              "Cleared a stuck job. Try Ask Borges & Lucien again.",
              "error",
              9000
            );
            return;
          }
          showProcessBanner(
            data.message || "Borges: Reading… This may take a few minutes.",
            Math.max(pct, 8),
            "info"
          );
          return;
        }

        if (!sawRunning && data.status === "idle") {
          ticksWithoutRunning += 1;
          if (ticksWithoutRunning > 8) {
            clearInterval(processPollTimer);
            processPollTimer = null;
            showProcessBanner(
              "Borges lost his place (server may have restarted). Click Ask Borges & Lucien to retry.",
              0,
              "error"
            );
            notify(
              "Status lost",
              "The server restarted while Borges was reading. Try Ask Borges & Lucien again.",
              "error",
              9000
            );
          }
          return;
        }

        clearInterval(processPollTimer);
        processPollTimer = null;

        if (data.status === "error") {
          const errText =
            data.message || (data.errors && data.errors[0]) || "Processing failed";
          showProcessBanner(errText, pct, "error", data.errors || []);
          notify("Processing failed", errText, "error", 10000);
          return;
        }

        const doneMsg = data.message || "Done";
        showProcessBanner(doneMsg, 100, "success", data.errors || []);
        if (data.errors && data.errors.length) {
          notify(
            "Finished with warnings",
            `${data.errors.length} issue(s). Check the banner for details.`,
            "error",
            8000
          );
        } else {
          notify("Shelved", doneMsg, "success");
        }
        await refreshAll();
        setTimeout(hideProcessBanner, 4000);
      } catch (err) {
        console.error(err);
        // Don't kill the poll on a single timeout — keep trying
        if (/timeout|abort|Failed to fetch|NetworkError/i.test(String(err.message || err))) {
          ticksWithoutRunning += 1;
          if (ticksWithoutRunning < 20) return;
        }
        clearInterval(processPollTimer);
        processPollTimer = null;
        showProcessBanner(err.message, 0, "error");
        notify("Progress check failed", err.message, "error", 8000);
      } finally {
        processPollInFlight = false;
      }
    }, 1200);
  }

  function formatAgentMessage(message) {
    const text = message == null ? "" : String(message);
    return text.replace(
      /^(Borges|Lucien):\s*/i,
      '<span class="agent-name">$1</span> '
    );
  }

  function showProcessBanner(message, percent, kind = "info", errors = []) {
    const banner = $("process-banner");
    const msg = $("process-message");
    const bar = $("process-bar");
    const errList = $("process-errors");
    if (!banner) return;
    banner.hidden = false;
    banner.classList.remove("is-error", "is-success", "is-borges", "is-lucien");
    if (kind === "error") banner.classList.add("is-error");
    if (kind === "success") banner.classList.add("is-success");
    const raw = message == null ? "" : String(message);
    if (/^Borges:/i.test(raw)) banner.classList.add("is-borges");
    if (/^Lucien:/i.test(raw)) banner.classList.add("is-lucien");
    if (msg) msg.innerHTML = formatAgentMessage(raw);
    if (bar) {
      bar.style.width = `${Math.min(100, Math.max(0, percent))}%`;
      if (bar.parentElement) bar.parentElement.hidden = false;
    }
    if (errList) {
      if (errors && errors.length) {
        errList.hidden = false;
        errList.innerHTML = errors
          .slice(0, 6)
          .map((e) => `<li>${escapeHtml(e)}</li>`)
          .join("");
      } else {
        errList.hidden = true;
        errList.innerHTML = "";
      }
    }
  }

  function hideProcessBanner() {
    const banner = $("process-banner");
    if (banner) banner.hidden = true;
  }

  // —— Graph ——
  function truncateLabel(text, max = 26) {
    const t = text == null ? "" : String(text).trim();
    if (!t) return "Untitled";
    if (t.length <= max) return t;
    return `${t.slice(0, max - 1)}…`;
  }

  function shouldShowLabel(d) {
    if (!d) return false;
    if (d.isStarting) return true;
    if (kindOf(d) !== "book") return true;
    if (selectedNode && String(selectedNode.id) === String(d.id)) return true;
    if (sharedNodeIds.has(String(d.id))) return true;
    return zoomK >= LABEL_ZOOM;
  }

  function init2D() {
    const container = d3.select("#graph-container");
    if (container.empty()) return;

    container.select("svg").remove();
    let width = container.node().clientWidth;
    let height = container.node().clientHeight;
    if (!width || !height) {
      width = Math.max(320, window.innerWidth - 360);
      height = Math.max(320, window.innerHeight);
    }

    svg = container
      .append("svg")
      .attr("width", width)
      .attr("height", height)
      .style("background", "transparent")
      .style("display", "block");

    const defs = svg.append("defs");
    const shadow = defs
      .append("filter")
      .attr("id", "node-shadow")
      .attr("x", "-50%")
      .attr("y", "-50%")
      .attr("width", "200%")
      .attr("height", "200%");
    shadow.append("feGaussianBlur").attr("in", "SourceAlpha").attr("stdDeviation", "1.5").attr("result", "blur");
    shadow.append("feOffset").attr("in", "blur").attr("dx", "0").attr("dy", "1").attr("result", "offsetBlur");
    const merge = shadow.append("feMerge");
    merge.append("feMergeNode").attr("in", "offsetBlur");
    merge.append("feMergeNode").attr("in", "SourceGraphic");

    linkGroup = svg.append("g").attr("class", "links");
    nodeGroup = svg.append("g").attr("class", "nodes");
    labelGroup = svg.append("g").attr("class", "labels");

    zoom = d3
      .zoom()
      .scaleExtent([0.15, 4])
      .on("zoom", (event) => {
        nodeGroup.attr("transform", event.transform);
        linkGroup.attr("transform", event.transform);
        labelGroup.attr("transform", event.transform);
        const wasClose = zoomK >= LABEL_ZOOM;
        zoomK = event.transform.k;
        if (zoomK >= LABEL_ZOOM !== wasClose) syncNodeLabels();
      });
    svg.call(zoom);
  }

  function prepareGraphData(books, references) {
    books = books.filter(
      (book) => book.author && book.author.trim() && book.author.toLowerCase() !== "unknown"
    );

    startingBooks = books.filter((b) => b.is_starting).map((b) => String(b.id));

    // Dedupe within kind only — a film and a novel with the same title stay separate
    const booksByTitleKind = new Map();
    books.forEach((book) => {
      const titleKey = `${kindOf(book)}::${book.title.toLowerCase().trim()}`;
      const existing = booksByTitleKind.get(titleKey);
      if (!existing) {
        booksByTitleKind.set(titleKey, book);
      } else {
        const existingIsStarting = startingBooks.includes(String(existing.id));
        const currentIsStarting = startingBooks.includes(String(book.id));
        if (currentIsStarting && !existingIsStarting) {
          booksByTitleKind.set(titleKey, book);
        } else if (currentIsStarting === existingIsStarting && book.id < existing.id) {
          booksByTitleKind.set(titleKey, book);
        }
      }
    });

    const uniqueBooks = Array.from(booksByTitleKind.values());
    const uniqueBookIds = new Set(uniqueBooks.map((b) => String(b.id)));
    const idRemap = new Map();
    books.forEach((book) => {
      const uniqueBook = booksByTitleKind.get(
        `${kindOf(book)}::${book.title.toLowerCase().trim()}`
      );
      if (uniqueBook && String(book.id) !== String(uniqueBook.id)) {
        idRemap.set(String(book.id), String(uniqueBook.id));
      }
    });

    let uniqueReferences = references
      .map((ref) => ({
        source: idRemap.get(String(ref.source)) || String(ref.source),
        target: idRemap.get(String(ref.target)) || String(ref.target),
      }))
      .filter(
        (ref) =>
          uniqueBookIds.has(ref.source) &&
          uniqueBookIds.has(ref.target) &&
          ref.source !== ref.target
      );

    startingBooks = startingBooks.map((id) => idRemap.get(id) || id).filter((id) => uniqueBookIds.has(id));

    const connectedBookIds = new Set(startingBooks);
    uniqueReferences.forEach((ref) => {
      if (startingBooks.includes(ref.source)) {
        connectedBookIds.add(ref.target);
      }
    });

    const connectedBooks = uniqueBooks.filter((b) => {
      if (!connectedBookIds.has(String(b.id))) return false;
      // Owned library books always show; other nodes respect layer toggles
      return nodeShouldStay({ ...b, isStarting: b.is_starting });
    });
    const visibleIds = new Set(connectedBooks.map((b) => String(b.id)));
    startingBooks.forEach((id) => visibleIds.add(String(id)));

    const connectedReferences = uniqueReferences.filter(
      (ref) => visibleIds.has(ref.source) && visibleIds.has(ref.target)
    );

    // Works that two or more library books mention: the real connections
    const sourcesByTarget = new Map();
    connectedReferences.forEach((ref) => {
      if (!sourcesByTarget.has(ref.target)) sourcesByTarget.set(ref.target, new Set());
      sourcesByTarget.get(ref.target).add(ref.source);
    });
    const sharedIds = new Set();
    sourcesByTarget.forEach((sources, target) => {
      if (sources.size >= 2) sharedIds.add(target);
    });

    // Tie together different works by the same author (Dickens in one book,
    // Dickens in another), as a chain so big groups don't become a hairball.
    const direct = new Set();
    connectedReferences.forEach((ref) => {
      direct.add(`${ref.source}|${ref.target}`);
      direct.add(`${ref.target}|${ref.source}`);
    });
    const byAuthor = new Map();
    connectedBooks.forEach((book) => {
      if (kindOf(book) !== "book") return;
      const key = authorKey(book.author);
      if (!key) return;
      if (!byAuthor.has(key)) byAuthor.set(key, []);
      byAuthor.get(key).push(String(book.id));
    });
    const authorLinks = [];
    byAuthor.forEach((ids) => {
      if (ids.length < 2) return;
      ids.sort((a, b) => Number(a) - Number(b));
      for (let i = 0; i < ids.length - 1; i += 1) {
        if (!direct.has(`${ids[i]}|${ids[i + 1]}`)) {
          authorLinks.push({ source: ids[i], target: ids[i + 1] });
        }
      }
    });

    return { connectedBooks, connectedReferences, authorLinks, sharedIds };
  }

  /* Everything the Connections panel shows, from the same data as the graph. */
  function computeInsights() {
    // Count films, music and drinks too, whatever the layer toggles show
    const savedKinds = enabledKinds;
    enabledKinds = new Set(Object.keys(KIND_COLORS).concat(["book"]));
    let prepared;
    try {
      prepared = prepareGraphData(allBooks, allReferences);
    } finally {
      enabledKinds = savedKinds;
    }
    const { connectedBooks, connectedReferences } = prepared;
    const byId = new Map(connectedBooks.map((b) => [String(b.id), b]));
    const owned = new Set(startingBooks.map(String));

    const sourcesByTarget = new Map();
    connectedReferences.forEach((ref) => {
      if (!owned.has(ref.source) || !byId.has(ref.target)) return;
      if (!sourcesByTarget.has(ref.target)) sourcesByTarget.set(ref.target, new Set());
      sourcesByTarget.get(ref.target).add(ref.source);
    });
    const withSources = (id) => ({
      book: byId.get(id),
      sources: [...(sourcesByTarget.get(id) || [])].map((s) => byId.get(s)).filter(Boolean),
    });

    const shared = [...sourcesByTarget.keys()]
      .filter((id) => sourcesByTarget.get(id).size >= 2)
      .map(withSources)
      .sort((a, b) => b.sources.length - a.sources.length || a.book.title.localeCompare(b.book.title));

    const ownedMentioned = [...sourcesByTarget.keys()]
      .filter((id) => owned.has(id))
      .map(withSources)
      .sort((a, b) => a.book.title.localeCompare(b.book.title));

    // Authors who turn up in more than one of your books, or with several works
    const authors = new Map();
    sourcesByTarget.forEach((sources, id) => {
      const book = byId.get(id);
      if (!book || kindOf(book) !== "book") return;
      const key = authorKey(book.author);
      if (!key) return;
      if (!authors.has(key)) authors.set(key, { name: book.author, works: [], sources: new Set() });
      const entry = authors.get(key);
      entry.works.push(book);
      sources.forEach((s) => entry.sources.add(s));
    });
    const recurringAuthors = [...authors.values()]
      .filter((a) => a.sources.size >= 2 || a.works.length >= 2)
      .map((a) => ({
        name: a.name,
        works: a.works.sort((x, y) => x.title.localeCompare(y.title)),
        sources: [...a.sources].map((s) => byId.get(s)).filter(Boolean),
      }))
      .sort(
        (a, b) =>
          b.sources.length - a.sources.length ||
          b.works.length - a.works.length ||
          a.name.localeCompare(b.name)
      );

    const authorWeight = new Map();
    recurringAuthors.forEach((a) => authorWeight.set(authorKey(a.name), a.sources.length + a.works.length));
    const readingList = [...sourcesByTarget.keys()]
      .filter((id) => !owned.has(id) && kindOf(byId.get(id)) === "book")
      .map(withSources)
      .map((entry) => ({
        ...entry,
        score: entry.sources.length * 10 + (authorWeight.get(authorKey(entry.book.author)) || 0),
      }))
      .sort((a, b) => b.score - a.score || a.book.title.localeCompare(b.book.title))
      .slice(0, 20);

    return { shared, ownedMentioned, recurringAuthors, readingList };
  }

  function openInsightsPanel() {
    const panel = $("detail-panel");
    const content = $("detail-content");
    const app = document.querySelector(".app");
    if (!panel || !content) return;
    panel.hidden = false;
    if (app) app.classList.add("detail-open");

    const { shared, ownedMentioned, recurringAuthors, readingList } = computeInsights();
    const names = (books) => books.map((b) => escapeHtml(b.title)).join(" · ");
    const workItem = (entry) => `
      <li>
        <button type="button" data-jump-id="${entry.book.id}">
          ${escapeHtml(entry.book.title)}${
            kindOf(entry.book) !== "book"
              ? ` <span class="inline-kind">${escapeHtml(kindLabel(kindOf(entry.book)))}</span>`
              : ""
          }
          <span class="ref-author">${escapeHtml(entry.book.author || "")}</span>
          <span class="context-snippet">mentioned in ${names(entry.sources)}</span>
        </button>
      </li>`;
    const section = (title, hint, body, emptyText) => `
      <section class="detail-section">
        <h3>${title}</h3>
        <p class="insight-hint">${hint}</p>
        ${body || `<p class="detail-empty">${emptyText}</p>`}
      </section>`;

    content.innerHTML = `
      <p class="detail-kicker">Connections</p>
      <h2 class="detail-title">How your shelves meet</h2>
      ${section(
        `Shared mentions (${shared.length})`,
        "Works that two or more of your books refer to.",
        shared.length ? `<ul class="ref-list">${shared.map(workItem).join("")}</ul>` : "",
        "No work is mentioned by more than one of your books yet."
      )}
      ${section(
        `Recurring authors (${recurringAuthors.length})`,
        "Authors who appear in several of your books, or with several works.",
        recurringAuthors.length
          ? `<ul class="ref-list">${recurringAuthors
              .map(
                (a) => `
            <li>
              <button type="button" data-jump-id="${a.works[0].id}">
                ${escapeHtml(a.name)}
                <span class="ref-author">${names(a.works)}</span>
                <span class="context-snippet">mentioned in ${names(a.sources)}</span>
              </button>
            </li>`
              )
              .join("")}</ul>`
          : "",
        "No author recurs yet."
      )}
      ${section(
        `Your books that cite each other (${ownedMentioned.length})`,
        "Library books mentioned inside other library books.",
        ownedMentioned.length ? `<ul class="ref-list">${ownedMentioned.map(workItem).join("")}</ul>` : "",
        "None of your books mention another one you own."
      )}
      ${section(
        "Reading list",
        "Books you don't own, ranked by how often your library points to them.",
        readingList.length ? `<ol class="ref-list reading-list">${readingList.map(workItem).join("")}</ol>` : "",
        "Add and read a few books to build a list."
      )}
    `;
  }

  function styleForBook(book, connectedReferences) {
    const isStarting = startingBooks.includes(String(book.id));
    const connected = new Set();
    connectedReferences.forEach((ref) => {
      if (String(ref.source) === String(book.id)) connected.add(String(ref.target));
      if (String(ref.target) === String(book.id)) connected.add(String(ref.source));
    });
    const connections = connected.size;
    const kind = kindOf(book);
    let size;
    let color;
    let strokeColor;
    if (isStarting) {
      size = Math.max(14, Math.min(26, 14 + connections * 1.5));
      color = "#2a9d8f";
      strokeColor = "#0f5c5c";
    } else if (kind !== "book") {
      const palette = KIND_COLORS[kind] || KIND_COLORS.book;
      size = Math.max(7, Math.min(15, 7 + connections));
      color = palette.fill;
      strokeColor = palette.stroke;
    } else if (sharedNodeIds.has(String(book.id))) {
      // Mentioned by two or more library books
      size = Math.max(9, Math.min(16, 7 + connections * 1.5));
      color = "#d9b23c";
      strokeColor = "#8f7216";
    } else if (connections > 0) {
      size = Math.max(6, Math.min(14, 6 + connections));
      color = "#7a8f9c";
      strokeColor = "#4a5c66";
    } else {
      size = 5;
      color = "#5a6660";
      strokeColor = "#3d4843";
    }
    return { isStarting, connections, kind, size, color, strokeColor };
  }

  function linkKey(link) {
    const s = typeof link.source === "object" ? link.source.id : link.source;
    const t = typeof link.target === "object" ? link.target.id : link.target;
    return link.type === "author" ? `${s}|${t}|author` : `${s}|${t}`;
  }

  function findAnchorNode(bookId, refs, nodeById) {
    const id = String(bookId);
    // Prefer an already-visible neighbor (owned books first)
    const candidates = [];
    refs.forEach((ref) => {
      if (String(ref.source) === id && nodeById.has(String(ref.target))) {
        candidates.push(nodeById.get(String(ref.target)));
      }
      if (String(ref.target) === id && nodeById.has(String(ref.source))) {
        candidates.push(nodeById.get(String(ref.source)));
      }
    });
    if (!candidates.length) return null;
    candidates.sort((a, b) => Number(b.isStarting) - Number(a.isStarting));
    return candidates[0];
  }

  /** Center of the live graph — used so new books spawn near the pack, not far off-canvas. */
  function getClusterCenter(nodeById) {
    const live = [...nodeById.values()].filter(
      (n) => n && n.x != null && n.y != null && !n._exiting
    );
    if (!live.length) return null;
    const starters = live.filter((n) => n.isStarting);
    const pool = starters.length ? starters : live;
    const x = d3.mean(pool, (n) => n.x);
    const y = d3.mean(pool, (n) => n.y);
    let nearest = pool[0];
    let best = Infinity;
    pool.forEach((n) => {
      const d = (n.x - x) ** 2 + (n.y - y) ** 2;
      if (d < best) {
        best = d;
        nearest = n;
      }
    });
    return { x, y, nearest };
  }

  function wireNodeInteractions(selection) {
    selection
      .on("mouseover", function (event, d) {
        showTooltip(event, d);
        d3.select(this).transition().duration(120).attr("r", d.size * 1.12);
      })
      .on("mousemove", function (event, d) {
        showTooltip(event, d);
      })
      .on("mouseout", function (event, d) {
        hideTooltip();
        d3.select(this).transition().duration(120).attr("r", d.size);
      })
      .on("click", function (event, d) {
        event.stopPropagation();
        selectNode(d);
      });
    if (simulation) selection.call(drag(simulation));
  }

  function bindGraphTick() {
    if (!simulation) return;
    simulation.on("tick", () => {
      linkGroup.selectAll("line").each(function (d) {
        if (!d || d._blooming) return;
        const s = typeof d.source === "object" ? d.source : null;
        const t = typeof d.target === "object" ? d.target : null;
        if (!s || !t) return;
        d3.select(this)
          .attr("x1", s.x)
          .attr("y1", s.y)
          .attr("x2", t.x)
          .attr("y2", t.y);
      });
      nodeGroup.selectAll("circle").each(function (d) {
        if (!d || d._exiting || d._growing) return;
        d3.select(this).attr("cx", d.x).attr("cy", d.y);
      });
      if (labelGroup) {
        labelGroup.selectAll("text").each(function (d) {
          if (!d || d._exiting || d._growing) return;
          d3.select(this)
            .attr("x", d.x)
            .attr("y", d.y + (d.size || 8) + 10);
        });
      }
    });
  }

  /**
   * Sync graph to current data/layers.
   * reset=false keeps existing node positions and grows new nodes from neighbors.
   */
  function syncGraph({ reset = false } = {}) {
    if (!svg || !linkGroup || !nodeGroup) return;
    const container = d3.select("#graph-container");
    const width = container.node().clientWidth || Math.max(320, window.innerWidth - 360);
    const height = container.node().clientHeight || Math.max(320, window.innerHeight);

    const { connectedBooks, connectedReferences, authorLinks, sharedIds } = prepareGraphData(
      allBooks,
      allReferences
    );
    sharedNodeIds = sharedIds;
    const oldById = new Map((nodes || []).map((n) => [String(n.id), n]));
    const desiredIds = new Set(connectedBooks.map((b) => String(b.id)));

    const nextNodes = connectedBooks.map((book, i) => {
      const id = String(book.id);
      const style = styleForBook(book, connectedReferences);
      const existing = !reset ? oldById.get(id) : null;
      if (existing) {
        return {
          ...existing,
          title: book.title,
          author: book.author,
          kind: style.kind,
          isStarting: style.isStarting,
          connections: style.connections,
          size: style.size,
          color: style.color,
          strokeColor: style.strokeColor,
          _isNew: false,
          _growing: false,
          _exiting: false,
          _blooming: false,
        };
      }

      let x;
      let y;
      let bloomAngle = 0;
      let bloomParentId = null;
      let bloomDist = 36 + Math.random() * 28;
      const anchor = findAnchorNode(id, connectedReferences, oldById);
      const cluster = !reset ? getClusterCenter(oldById) : null;

      if (anchor && anchor.x != null && anchor.y != null) {
        // Seed at the mother node; bloom tween pushes petals outward
        bloomAngle = (Math.PI * 2 * (i % 12)) / 12 + Math.random() * 0.35;
        bloomParentId = anchor.id;
        x = anchor.x;
        y = anchor.y;
        bloomDist = 32 + Math.random() * 24;
      } else if (cluster && !reset) {
        // New library book (or orphan): grow beside the existing pack
        bloomAngle = Math.random() * Math.PI * 2;
        bloomParentId = cluster.nearest?.id || null;
        x = cluster.x;
        y = cluster.y;
        bloomDist = style.isStarting ? 42 + Math.random() * 26 : 28 + Math.random() * 22;
      } else if (style.isStarting) {
        const startingIndex = startingBooks.indexOf(id);
        const angle = (startingIndex / Math.max(startingBooks.length, 1)) * 2 * Math.PI;
        x = width / 2 + Math.cos(angle) * 70;
        y = height / 2 + Math.sin(angle) * 70;
      } else {
        const angle = (i / Math.max(connectedBooks.length, 1)) * 2 * Math.PI;
        const radius = 70 + Math.random() * 50;
        x = width / 2 + Math.cos(angle) * radius;
        y = height / 2 + Math.sin(angle) * radius;
      }

      return {
        id,
        title: book.title,
        author: book.author,
        kind: style.kind,
        isStarting: style.isStarting,
        connections: style.connections,
        size: style.size,
        color: style.color,
        strokeColor: style.strokeColor,
        x,
        y,
        vx: 0,
        vy: 0,
        _isNew: !reset,
        _growing: !reset,
        _bloomAngle: bloomAngle,
        _bloomParentId: bloomParentId,
        _bloomDelay: 0,
        _bloomFromX: x,
        _bloomFromY: y,
        _bloomTargetDist: bloomDist,
      };
    });

    // Petal order: siblings around the same parent bloom in angle order
    if (!reset) {
      const byParent = new Map();
      nextNodes.forEach((n) => {
        if (!n._growing || !n._bloomParentId) return;
        if (!byParent.has(n._bloomParentId)) byParent.set(n._bloomParentId, []);
        byParent.get(n._bloomParentId).push(n);
      });
      byParent.forEach((siblings) => {
        siblings.sort((a, b) => a._bloomAngle - b._bloomAngle);
        siblings.forEach((n, idx) => {
          n._bloomDelay = 120 + idx * 140;
          // Even spacing around the parent for a clearer flower
          n._bloomAngle = (idx / Math.max(siblings.length, 1)) * Math.PI * 2 - Math.PI / 2;
          if (n._bloomParentId && oldById.has(n._bloomParentId)) {
            const parent = oldById.get(n._bloomParentId);
            n._bloomFromX = parent.x;
            n._bloomFromY = parent.y;
            n.x = parent.x;
            n.y = parent.y;
          }
        });
      });
      let orphanIdx = 0;
      nextNodes.forEach((n) => {
        if (!n._growing) return;
        if (n._bloomParentId) return;
        n._bloomDelay = 200 + orphanIdx * 120;
        orphanIdx += 1;
      });
    }

    const nextLinks = connectedReferences
      .map((ref) => ({
        source: String(ref.source),
        target: String(ref.target),
        type: "ref",
      }))
      .concat(
        showAuthorLinks
          ? authorLinks.map((l) => ({ source: l.source, target: l.target, type: "author" }))
          : []
      )
      .filter((l) => l.source !== l.target && desiredIds.has(l.source) && desiredIds.has(l.target));

    // Layer toggle with nothing to add/remove: refresh styles only — don't jolt the layout
    if (!reset && simulation && nodes.length) {
      const prevIds = new Set(nodes.map((n) => String(n.id)));
      const nextIds = new Set(nextNodes.map((n) => String(n.id)));
      const sameNodes =
        prevIds.size === nextIds.size && [...nextIds].every((id) => prevIds.has(id));
      const prevLinks = new Set((links || []).map((l) => linkKey(l)));
      const nextLinkSet = new Set(nextLinks.map((l) => linkKey(l)));
      const sameLinks =
        prevLinks.size === nextLinkSet.size &&
        [...nextLinkSet].every((k) => prevLinks.has(k));

      if (sameNodes && sameLinks) {
        const byId = new Map(nextNodes.map((n) => [String(n.id), n]));
        nodes.forEach((n) => {
          const fresh = byId.get(String(n.id));
          if (!fresh) return;
          n.title = fresh.title;
          n.author = fresh.author;
          n.kind = fresh.kind;
          n.isStarting = fresh.isStarting;
          n.connections = fresh.connections;
          n.size = fresh.size;
          n.color = fresh.color;
          n.strokeColor = fresh.strokeColor;
        });
        nodeGroup
          .selectAll("circle")
          .attr("fill", (d) =>
            selectedNode && String(selectedNode.id) === String(d.id) ? SELECTED_FILL : d.color
          )
          .attr("stroke", (d) =>
            selectedNode && String(selectedNode.id) === String(d.id)
              ? SELECTED_STROKE
              : d.strokeColor
          )
          .attr("r", (d) => d.size);
        syncNodeLabels();
        renderLibrary();
        updateNodeVisibility();
        updateNodeCounts();
        return;
      }
    }

    nodes = nextNodes;
    links = nextLinks;

    if (!simulation || reset) {
      if (simulation) simulation.stop();
      simulation = d3
        .forceSimulation(nodes)
        .force(
          "link",
          d3
            .forceLink(links)
            .id((d) => d.id)
            .distance((d) => {
              const s = typeof d.source === "object" ? d.source : nodes.find((n) => n.id === d.source);
              const t = typeof d.target === "object" ? d.target : nodes.find((n) => n.id === d.target);
              if (s?.isStarting && t?.isStarting) return 70;
              return s?.isStarting || t?.isStarting ? 85 : 110;
            })
            .strength(linkStrength)
        )
        .force("charge", d3.forceManyBody().strength((d) => (d.isStarting ? -240 : -160)))
        .force("center", d3.forceCenter(width / 2, height / 2))
        .force("collision", d3.forceCollide().radius((d) => d.size + 3).strength(0.9))
        .alpha(reset ? 1 : 0.22)
        .alphaDecay(reset ? 0.028 : 0.022);
    } else {
      simulation.nodes(nodes);
      const linkForce = simulation.force("link");
      if (linkForce) {
        linkForce.links(links);
        linkForce.distance((d) => {
          const s = typeof d.source === "object" ? d.source : nodes.find((n) => n.id === d.source);
          const t = typeof d.target === "object" ? d.target : nodes.find((n) => n.id === d.target);
          if (s?.isStarting && t?.isStarting) return 70;
          return s?.isStarting || t?.isStarting ? 85 : 110;
        });
        linkForce.strength(linkStrength);
      }
      simulation.force(
        "charge",
        d3.forceManyBody().strength((d) => (d.isStarting ? -240 : -160))
      );
      simulation.force(
        "collision",
        d3.forceCollide().radius((d) => d.size + 3).strength(0.9)
      );
      // Gentle bloom — don't explode the existing layout
      simulation.alpha(0.22).alphaDecay(0.02).alphaTarget(0).restart();
    }

    const nodeById = new Map(nodes.map((n) => [String(n.id), n]));

    const link = linkGroup
      .selectAll("line")
      .data(links, linkKey)
      .join(
        (enter) =>
          enter
            .append("line")
            .attr("stroke", linkStroke)
            .attr("stroke-dasharray", linkDash)
            .attr("stroke-width", 0)
            .attr("stroke-opacity", 0)
            .each(function (d) {
              d._blooming = !reset;
            }),
        (update) =>
          update
            .attr("stroke", linkStroke)
            .attr("stroke-dasharray", linkDash)
            .attr("stroke-width", 1.6)
            .attr("stroke-opacity", 0.55)
            .each((d) => {
              d._blooming = false;
            }),
        (exit) =>
          exit
            .interrupt()
            .each((d) => {
              if (d) d._blooming = false;
            })
            .transition()
            .duration(380)
            .ease(d3.easeCubicIn)
            .attr("stroke-opacity", 0)
            .attr("stroke-width", 0)
            .remove()
      );

    // Grow stems from the mother node toward the new petal
    if (reset) {
      link
        .interrupt()
        .transition()
        .duration(500)
        .delay(40)
        .ease(d3.easeCubicOut)
        .attr("stroke-width", 1.6)
        .attr("stroke-opacity", 0.55);
    } else {
      link
        .filter((d) => d._blooming)
        .interrupt()
        .attr("stroke-width", 1.35)
        .attr("stroke-opacity", 0)
        .transition()
        .delay((d) => {
          const s = nodeById.get(String(typeof d.source === "object" ? d.source.id : d.source));
          const t = nodeById.get(String(typeof d.target === "object" ? d.target.id : d.target));
          return Math.max(s?._bloomDelay || 0, t?._bloomDelay || 0) + 80;
        })
        .duration(1100)
        .ease(d3.easeCubicOut)
        .tween("growStem", function (d) {
          return function (t) {
            const s = typeof d.source === "object" ? d.source : nodeById.get(String(d.source));
            const tgt = typeof d.target === "object" ? d.target : nodeById.get(String(d.target));
            if (!s || !tgt) return;
            // Prefer growing out of the already-visible mother
            let mother = s;
            let child = tgt;
            if (s._growing && !tgt._growing) {
              mother = tgt;
              child = s;
            } else if (tgt._growing && !s._growing) {
              mother = s;
              child = tgt;
            } else if ((s._bloomParentId && String(s._bloomParentId) === String(tgt.id)) || tgt.isStarting) {
              mother = tgt;
              child = s;
            }
            const eased = d3.easeCubicOut(t);
            const x2 = mother.x + (child.x - mother.x) * eased;
            const y2 = mother.y + (child.y - mother.y) * eased;
            d3.select(this)
              .attr("x1", mother.x)
              .attr("y1", mother.y)
              .attr("x2", x2)
              .attr("y2", y2)
              .attr("stroke-opacity", 0.55 * eased)
              .attr("stroke-width", 1.6);
          };
        })
        .on("end", function (d) {
          if (d) d._blooming = false;
        });
    }

    const node = nodeGroup
      .selectAll("circle")
      .data(nodes, (d) => String(d.id))
      .join(
        (enter) => {
          const sel = enter
            .append("circle")
            .attr("r", 0)
            .attr("fill", (d) => d.color)
            .attr("stroke", (d) => d.strokeColor)
            .attr("stroke-width", (d) => (d.isStarting ? 2.5 : 1.5))
            .style("cursor", "pointer")
            .style("filter", "url(#node-shadow)")
            .style("opacity", 0)
            .attr("cx", (d) => d.x)
            .attr("cy", (d) => d.y);
          wireNodeInteractions(sel);
          return sel;
        },
        (update) =>
          update
            .attr("fill", (d) => d.color)
            .attr("stroke", (d) => d.strokeColor)
            .attr("stroke-width", (d) => (d.isStarting ? 2.5 : 1.5)),
        (exit) =>
          exit
            .interrupt()
            .each((d) => {
              if (!d) return;
              d.fx = null;
              d.fy = null;
              d._exiting = true;
              d._growing = false;
            })
            .transition()
            .duration(420)
            .ease(d3.easeCubicIn)
            .attr("r", 0)
            .style("opacity", 0)
            .remove()
      );

    if (reset) {
      node
        .interrupt()
        .attr("r", 0)
        .style("opacity", 0)
        .transition()
        .duration(420)
        .delay((_, i) => Math.min(i * 6, 180))
        .ease(d3.easeCubicOut)
        .attr("r", (d) => d.size)
        .style("opacity", 1);
    } else {
      // Soft pulse on mother nodes that are sprouting petals
      const motherIds = new Set(
        nodes.filter((n) => n._growing && n._bloomParentId).map((n) => String(n._bloomParentId))
      );

      node
        .filter((d) => d._growing)
        .interrupt()
        .each((d) => {
          d.fx = d._bloomFromX;
          d.fy = d._bloomFromY;
          d.x = d._bloomFromX;
          d.y = d._bloomFromY;
          d.vx = 0;
          d.vy = 0;
        })
        .attr("cx", (d) => d._bloomFromX)
        .attr("cy", (d) => d._bloomFromY)
        .attr("r", 0)
        .style("opacity", 1)
        .transition()
        .delay((d) => d._bloomDelay || 120)
        .duration(1450)
        .ease(d3.easeCubicOut)
        .tween("bloom", function (d) {
          const peak = d.size * 1.22;
          const fromX = d._bloomFromX;
          const fromY = d._bloomFromY;
          const angle = d._bloomAngle;
          const dist = d._bloomTargetDist || 40;
          return function (t) {
            // Ease out for travel; slight overshoot on radius for a living feel
            const eased = d3.easeCubicOut(t);
            const r =
              t < 0.72
                ? d3.easeCubicOut(t / 0.72) * peak
                : peak + (d.size - peak) * d3.easeCubicInOut((t - 0.72) / 0.28);
            d.fx = fromX + Math.cos(angle) * dist * eased;
            d.fy = fromY + Math.sin(angle) * dist * eased;
            d.x = d.fx;
            d.y = d.fy;
            d3.select(this).attr("cx", d.x).attr("cy", d.y).attr("r", Math.max(0.2, r));
          };
        })
        .on("end", function (d) {
          if (!d) return;
          d.fx = null;
          d.fy = null;
          d._growing = false;
          d._isNew = false;
          d.vx = Math.cos(d._bloomAngle || 0) * 0.55;
          d.vy = Math.sin(d._bloomAngle || 0) * 0.55;
          d3.select(this).attr("r", d.size);
        });

      node
        .filter((d) => !d._growing && !motherIds.has(String(d.id)))
        .attr("r", (d) => d.size)
        .style("opacity", 1);

      node
        .filter((d) => motherIds.has(String(d.id)))
        .style("opacity", 1)
        .transition()
        .duration(280)
        .attr("r", (d) => d.size * 1.16)
        .transition()
        .duration(420)
        .ease(d3.easeCubicOut)
        .attr("r", (d) => d.size);
    }

    wireNodeInteractions(node);
    bindGraphTick();
    syncNodeLabels({ reset });

    // Hard prune: remove any circle/line that should not exist for current layers
    const keepIds = new Set(nodes.map((n) => String(n.id)));
    nodeGroup.selectAll("circle").each(function (d) {
      if (!d) {
        d3.select(this).interrupt().remove();
        return;
      }
      const id = String(d.id);
      const allowed = keepIds.has(id) && nodeShouldStay(d);
      if (allowed) return;
      d._exiting = true;
      d3.select(this).interrupt().remove();
    });
    if (labelGroup) {
      labelGroup.selectAll("text").each(function (d) {
        if (!d || !keepIds.has(String(d.id)) || !nodeShouldStay(d)) {
          d3.select(this).interrupt().remove();
        }
      });
    }
    const keepLinks = new Set(links.map((l) => linkKey(l)));
    linkGroup.selectAll("line").each(function (d) {
      if (!d || !keepLinks.has(linkKey(d))) {
        d3.select(this).interrupt().remove();
      }
    });

    svg.on("click", function (event) {
      if (event.target === this) {
        closeDetailPanel();
        deselectNode();
        clearFocusMode();
      }
    });

    renderLibrary();
    updateNodeVisibility();
    updateNodeCounts();
    if (focusBookId) applyFocusMode();
  }

  function syncNodeLabels({ reset = false } = {}) {
    if (!labelGroup) return;
    const labeled = nodes.filter((d) => shouldShowLabel(d));
    const label = labelGroup
      .selectAll("text")
      .data(labeled, (d) => String(d.id))
      .join(
        (enter) =>
          enter
            .append("text")
            .attr("class", "node-label")
            .attr("text-anchor", "middle")
            .attr("dy", "0.35em")
            .style("pointer-events", "none")
            .style("opacity", 0)
            .text((d) => truncateLabel(d.title))
            .attr("x", (d) => d.x)
            .attr("y", (d) => d.y + (d.size || 8) + 10)
            .call((sel) =>
              sel
                .transition()
                .delay(reset ? 80 : 400)
                .duration(420)
                .style("opacity", 1)
            ),
        (update) =>
          update
            .text((d) => truncateLabel(d.title))
            .style("opacity", 1),
        (exit) => exit.transition().duration(200).style("opacity", 0).remove()
      );

    label
      .classed("is-starting", (d) => !!d.isStarting)
      .classed("is-movie", (d) => kindOf(d) === "movie")
      .classed("is-music", (d) => kindOf(d) === "music")
      .classed("is-cocktail", (d) => kindOf(d) === "cocktail");
  }

  function tidyGraphLayout() {
    if (!nodes.length || !svg || !nodeGroup) return;

    const container = d3.select("#graph-container");
    const width = container.node().clientWidth || window.innerWidth - 360;
    const height = container.node().clientHeight || window.innerHeight;
    const cx = width / 2;
    const cy = height / 2;

    const starters = nodes
      .filter((n) => n.isStarting && !hiddenNodeIds.has(String(n.id)))
      .sort((a, b) => String(a.title).localeCompare(String(b.title)));
    const others = nodes.filter((n) => !n.isStarting);

    const starterR = Math.max(70, Math.min(width, height) * 0.16);
    starters.forEach((n, i) => {
      const angle = (i / Math.max(starters.length, 1)) * Math.PI * 2 - Math.PI / 2;
      n.fx = null;
      n.fy = null;
      n.vx = 0;
      n.vy = 0;
      n.x = cx + Math.cos(angle) * starterR;
      n.y = cy + Math.sin(angle) * starterR;
      n._growing = false;
      n._exiting = false;
    });

    const starterById = new Map(starters.map((n) => [String(n.id), n]));
    const childrenByParent = new Map();

    others.forEach((n) => {
      let parentId = null;
      links.forEach((link) => {
        const s = String(typeof link.source === "object" ? link.source.id : link.source);
        const t = String(typeof link.target === "object" ? link.target.id : link.target);
        if (t === String(n.id) && starterById.has(s)) parentId = parentId || s;
        else if (s === String(n.id) && starterById.has(t)) parentId = parentId || t;
      });
      const key = parentId || "__orphan__";
      if (!childrenByParent.has(key)) childrenByParent.set(key, []);
      childrenByParent.get(key).push(n);
    });

    childrenByParent.forEach((kids, parentId) => {
      kids.sort((a, b) => {
        const ka = kindOf(a);
        const kb = kindOf(b);
        if (ka !== kb) return ka.localeCompare(kb);
        return String(a.title).localeCompare(String(b.title));
      });
      const parent = starterById.get(parentId);
      const baseX = parent ? parent.x : cx;
      const baseY = parent ? parent.y : cy;
      const ring = parent ? 88 : Math.max(130, starterR + 70);
      kids.forEach((n, i) => {
        const angle = (i / Math.max(kids.length, 1)) * Math.PI * 2 - Math.PI / 2;
        // Slight spiral so dense rings stay readable
        const r = ring + Math.floor(i / 10) * 28;
        n.fx = null;
        n.fy = null;
        n.vx = 0;
        n.vy = 0;
        n.x = baseX + Math.cos(angle) * r;
        n.y = baseY + Math.sin(angle) * r;
        n._growing = false;
        n._exiting = false;
      });
    });

    nodeGroup
      .selectAll("circle")
      .interrupt()
      .transition()
      .duration(650)
      .ease(d3.easeCubicOut)
      .attr("cx", (d) => d.x)
      .attr("cy", (d) => d.y)
      .attr("r", (d) => d.size)
      .style("opacity", 1);

    if (labelGroup) {
      syncNodeLabels();
      labelGroup
        .selectAll("text")
        .interrupt()
        .transition()
        .duration(650)
        .ease(d3.easeCubicOut)
        .attr("x", (d) => d.x)
        .attr("y", (d) => d.y + (d.size || 8) + 10);
    }

    if (linkGroup) {
      linkGroup
        .selectAll("line")
        .interrupt()
        .transition()
        .duration(650)
        .ease(d3.easeCubicOut)
        .attr("x1", (d) => (typeof d.source === "object" ? d.source.x : 0))
        .attr("y1", (d) => (typeof d.source === "object" ? d.source.y : 0))
        .attr("x2", (d) => (typeof d.target === "object" ? d.target.x : 0))
        .attr("y2", (d) => (typeof d.target === "object" ? d.target.y : 0));
    }

    if (simulation) {
      simulation.nodes(nodes);
      simulation.alpha(0.55).alphaDecay(0.022).alphaTarget(0).restart();
    }

    if (zoom) {
      svg.transition().duration(500).ease(d3.easeCubicOut).call(zoom.transform, d3.zoomIdentity);
    }

    updateNodeVisibility();
    if (focusBookId) applyFocusMode();
  }

  function renderGraph(books, references) {
    if (typeof d3 === "undefined") return;
    if (!svg || svg.empty()) init2D();
    if (!svg) return;

    allBooks = books;
    allReferences = references;
    syncGraph({ reset: true });
  }

  function updateNodeCounts() {
    const el = $("node-counts");
    if (!el || !nodes.length) {
      if (el) el.innerHTML = "";
      return;
    }
    const ownedVisible = nodes.filter((n) => n.isStarting && !hiddenNodeIds.has(n.id)).length;
    const bookLinked = nodes.filter((n) => !n.isStarting && kindOf(n) === "book").length;
    const extra = nodes.filter((n) => !n.isStarting && kindOf(n) !== "book").length;
    el.innerHTML = `<div class="owned">Library ${ownedVisible}</div><div>Books ${bookLinked}</div>${
      extra ? `<div>Other ${extra}</div>` : ""
    }`;
  }

  function drag(sim) {
    return d3
      .drag()
      .on("start", (event, d) => {
        if (!d.isStarting) return;
        if (!event.active) sim.alphaTarget(0.25).restart();
        d.fx = d.x;
        d.fy = d.y;
      })
      .on("drag", (event, d) => {
        if (!d.isStarting) return;
        d.fx = event.x;
        d.fy = event.y;
      })
      .on("end", (event, d) => {
        if (!d.isStarting) return;
        if (!event.active) sim.alphaTarget(0);
        d.fx = null;
        d.fy = null;
      });
  }

  function selectNode(node) {
    if (selectedNode && selectedNode.id === node.id) {
      deselectNode();
      closeDetailPanel();
      return;
    }
    selectedNode = node;
    highlightPathToNode(node);
    openDetailPanel(node.id);
    syncNodeLabels();
    renderLibrary();
  }

  function deselectNode() {
    selectedNode = null;
    highlightPathToNode(null);
    syncNodeLabels();
  }

  function highlightPathToNode(targetNode) {
    if (!linkGroup || !nodeGroup) return;
    if (!targetNode) {
      linkGroup
        .selectAll("line")
        .attr("stroke", "#6d7c75")
        .attr("stroke-opacity", 0.55)
        .attr("stroke-width", 1.6);
      nodeGroup
        .selectAll("circle")
        .attr("fill", (d) => d.color)
        .attr("stroke", (d) => d.strokeColor)
        .attr("stroke-width", (d) => (d.isStarting ? 2.5 : 1.5))
        .attr("opacity", 1);
      return;
    }

    const selectedId = String(targetNode.id);
    const neighborIds = new Set([selectedId]);
    const connectedEdges = new Set();

    links.forEach((link) => {
      const s = String(typeof link.source === "object" ? link.source.id : link.source);
      const t = String(typeof link.target === "object" ? link.target.id : link.target);
      if (s === selectedId || t === selectedId) {
        connectedEdges.add(`${s}|${t}`);
        connectedEdges.add(`${t}|${s}`);
        neighborIds.add(s);
        neighborIds.add(t);
      }
    });

    linkGroup
      .selectAll("line")
      .attr("stroke", (d) => {
        const s = String(typeof d.source === "object" ? d.source.id : d.source);
        const t = String(typeof d.target === "object" ? d.target.id : d.target);
        return connectedEdges.has(`${s}|${t}`) ? "#2a9d8f" : "#3d4843";
      })
      .attr("stroke-opacity", (d) => {
        const s = String(typeof d.source === "object" ? d.source.id : d.source);
        const t = String(typeof d.target === "object" ? d.target.id : d.target);
        return connectedEdges.has(`${s}|${t}`) ? 0.95 : 0.22;
      })
      .attr("stroke-width", (d) => {
        const s = String(typeof d.source === "object" ? d.source.id : d.source);
        const t = String(typeof d.target === "object" ? d.target.id : d.target);
        return connectedEdges.has(`${s}|${t}`) ? 3 : 1.15;
      });

    nodeGroup
      .selectAll("circle")
      .attr("fill", (d) => (String(d.id) === selectedId ? SELECTED_FILL : d.color))
      .attr("stroke", (d) => {
        const id = String(d.id);
        if (id === selectedId) return SELECTED_STROKE;
        if (neighborIds.has(id)) return "#2a9d8f";
        return d.strokeColor;
      })
      .attr("stroke-width", (d) => {
        const id = String(d.id);
        if (id === selectedId) return 3;
        if (neighborIds.has(id)) return 2.4;
        return d.isStarting ? 2.5 : 1.5;
      })
      .attr("opacity", (d) => (neighborIds.has(String(d.id)) ? 1 : 0.35));
  }

  function findShortestPath(startId, targetId) {
    const queue = [[startId]];
    const visited = new Set([String(startId)]);

    while (queue.length) {
      const path = queue.shift();
      const currentId = path[path.length - 1];
      if (String(currentId) === String(targetId)) return path;

      links.forEach((link) => {
        const sourceId = typeof link.source === "object" ? link.source.id : link.source;
        const target = typeof link.target === "object" ? link.target.id : link.target;
        let nextId = null;
        if (String(sourceId) === String(currentId)) nextId = target;
        else if (String(target) === String(currentId)) nextId = sourceId;
        if (nextId && !visited.has(String(nextId))) {
          visited.add(String(nextId));
          queue.push([...path, String(nextId)]);
        }
      });
    }
    return null;
  }

  function centerOnNode(node) {
    if (!svg || !zoom || node.x == null) return;
    const container = $("graph-container");
    const width = container.clientWidth;
    const height = container.clientHeight;
    const scale = 1.35;
    const transform = d3.zoomIdentity
      .translate(width / 2, height / 2)
      .scale(scale)
      .translate(-node.x, -node.y);
    svg.transition().duration(550).ease(d3.easeCubicOut).call(zoom.transform, transform);
  }

  function toggleBookVisibility(bookId) {
    const id = String(bookId);
    if (hiddenNodeIds.has(id)) hiddenNodeIds.delete(id);
    else hiddenNodeIds.add(id);
    updateNodeVisibility();
    renderLibrary();
    updateNodeCounts();
  }

  function updateNodeVisibility() {
    if (!nodeGroup || !linkGroup) return;

    const nodesToHide = new Set();
    hiddenNodeIds.forEach((id) => {
      if (startingBooks.includes(String(id))) nodesToHide.add(String(id));
    });

    hiddenNodeIds.forEach((hiddenId) => {
      const hiddenIdStr = String(hiddenId);
      if (!startingBooks.includes(hiddenIdStr)) return;

      const directlyReferenced = new Set();
      links.forEach((link) => {
        const sourceId = String(typeof link.source === "object" ? link.source.id : link.source);
        const targetId = String(typeof link.target === "object" ? link.target.id : link.target);
        if (sourceId === hiddenIdStr) directlyReferenced.add(targetId);
      });

      directlyReferenced.forEach((refNodeId) => {
        if (startingBooks.includes(refNodeId)) return;
        let hasDirectLinkToOtherVisible = false;
        startingBooks.forEach((startId) => {
          const startIdStr = String(startId);
          if (startIdStr !== hiddenIdStr && !hiddenNodeIds.has(startIdStr)) {
            links.forEach((link) => {
              const sourceId = String(typeof link.source === "object" ? link.source.id : link.source);
              const targetId = String(typeof link.target === "object" ? link.target.id : link.target);
              if (
                (sourceId === startIdStr && targetId === refNodeId) ||
                (targetId === startIdStr && sourceId === refNodeId)
              ) {
                hasDirectLinkToOtherVisible = true;
              }
            });
          }
        });
        if (!hasDirectLinkToOtherVisible) nodesToHide.add(refNodeId);
      });
    });

    const liveIds = new Set((nodes || []).map((n) => String(n.id)));

    nodeGroup.selectAll("circle").each(function (d) {
      if (!d || d._exiting || d._growing) return;
      const nodeId = String(d.id);
      if (!liveIds.has(nodeId) || !nodeShouldStay(d)) {
        d3.select(this).interrupt().style("opacity", 0).attr("r", 0).style("pointer-events", "none");
        return;
      }
      const shouldHide = d.isStarting ? hiddenNodeIds.has(nodeId) : nodesToHide.has(nodeId);
      d3.select(this)
        .transition()
        .duration(280)
        .style("opacity", shouldHide ? 0 : 1)
        .attr("r", shouldHide ? 0 : d.size)
        .style("pointer-events", shouldHide ? "none" : "all");
    });

    linkGroup.selectAll("line").style("opacity", (d) => {
      if (!d) return 0;
      const sourceId = String(typeof d.source === "object" ? d.source.id : d.source);
      const targetId = String(typeof d.target === "object" ? d.target.id : d.target);
      const sourceNode = nodes.find((n) => String(n.id) === sourceId);
      const targetNode = nodes.find((n) => String(n.id) === targetId);
      if (!sourceNode || !targetNode || !nodeShouldStay(sourceNode) || !nodeShouldStay(targetNode)) {
        return 0;
      }
      const sourceHidden =
        nodesToHide.has(sourceId) ||
        (sourceNode.isStarting && hiddenNodeIds.has(sourceId));
      const targetHidden =
        nodesToHide.has(targetId) ||
        (targetNode.isStarting && hiddenNodeIds.has(targetId));
      return sourceHidden || targetHidden ? 0 : 0.55;
    });
  }

  function applyFocusMode() {
    if (!focusBookId || !nodeGroup || !linkGroup) return;
    const focusId = String(focusBookId);
    const related = new Set([focusId]);
    links.forEach((link) => {
      const s = String(typeof link.source === "object" ? link.source.id : link.source);
      const t = String(typeof link.target === "object" ? link.target.id : link.target);
      if (s === focusId) related.add(t);
      if (t === focusId) related.add(s);
    });

    nodeGroup.selectAll("circle").style("opacity", (d) => {
      if (hiddenNodeIds.has(String(d.id)) && d.isStarting) return 0;
      return related.has(String(d.id)) ? 1 : 0.12;
    });
    linkGroup.selectAll("line").style("opacity", (d) => {
      const s = String(typeof d.source === "object" ? d.source.id : d.source);
      const t = String(typeof d.target === "object" ? d.target.id : d.target);
      return related.has(s) && related.has(t) ? 0.7 : 0.05;
    });
  }

  function clearFocusMode() {
    focusBookId = null;
    const focusBtn = $("focus-mode");
    if (focusBtn) focusBtn.hidden = true;
    updateNodeVisibility();
    renderLibrary();
  }

  function setupKeyboard() {
    document.addEventListener("keydown", (e) => {
      const tag = (e.target && e.target.tagName) || "";
      if (e.key === "/" && tag !== "INPUT" && tag !== "TEXTAREA") {
        e.preventDefault();
        $("search-input")?.focus();
      }
      if (e.key === "Escape") {
        closeModal();
        closeDetailPanel();
        deselectNode();
        clearFocusMode();
        $("search-results").hidden = true;
        $("search-input")?.blur();
      }
    });
  }

  async function resumeProcessIfNeeded() {
    if (isStaticMode) return;
    try {
      const res = await fetch("/api/process/status");
      const data = await res.json();
      if (data.status === "running") {
        showProcessBanner(data.message || "Borges: Still reading…", 15, "info");
        pollProcessStatus();
      } else if (data.status === "error" && data.message && /interrupted|restart/i.test(data.message)) {
        showProcessBanner(data.message, 0, "error", data.errors || []);
        notify("Processing interrupted", data.message, "error", 9000);
      }
    } catch (_) {
      /* ignore */
    }
  }

  function showTooltip(event, d) {
    const tip = $("node-tooltip");
    if (!tip) return;
    tip.hidden = false;
    tip.innerHTML = `
      <div class="tooltip-title">${escapeHtml(d.title)}</div>
      <div class="tooltip-author">${escapeHtml(d.author || "Unknown")}</div>`;
    tip.style.left = `${event.pageX + 12}px`;
    tip.style.top = `${event.pageY - 10}px`;
  }

  function hideTooltip() {
    const tip = $("node-tooltip");
    if (tip) tip.hidden = true;
  }

  // —— Network import / export / PNG ——
  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  function downloadJson(data, filename) {
    const blob = new Blob([JSON.stringify(data, null, 2)], {
      type: "application/json",
    });
    downloadBlob(blob, filename);
  }

  async function exportNetworkJson() {
    try {
      if (isStaticMode) {
        downloadJson(
          { books: allBooks, references: allReferences },
          "books-in-books-network.json"
        );
        notify("Exported", "Downloaded the current network JSON.", "success");
        return;
      }
      const res = await fetch("/api/network/export");
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Export failed");
      downloadJson(data, "books-in-books-network.json");
      notify("Exported", "Downloaded books + references JSON.", "success");
    } catch (err) {
      notify("Export failed", err.message, "error");
    }
  }

  async function importNetworkFromFile(file) {
    if (!file) return;
    try {
      const raw = JSON.parse(await file.text());
      let books = raw.books;
      let references = raw.references;
      // Allow importing docs/books.json + separately? Prefer combined shape.
      if (!Array.isArray(books) && Array.isArray(raw)) {
        throw new Error("Use an export with { books, references }, or Export network first.");
      }
      if (!Array.isArray(books) || !Array.isArray(references)) {
        throw new Error("JSON must include books and references arrays.");
      }

      if (isStaticMode) {
        allBooks = books;
        allReferences = references;
        renderLibrary();
        renderGraph(books, references);
        updateFirstRun(!ownedBooks().length);
        notify("Loaded network", `${books.length} books (view only).`, "success");
        return;
      }

      const res = await fetch("/api/network/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ books, references }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Import failed");
      notify("Imported", data.message || "Network merged.", "success");
      await refreshAll();
    } catch (err) {
      notify("Import failed", err.message, "error", 8000);
    }
  }

  function exportGraphPng() {
    const svgEl = document.querySelector("#graph-container svg");
    if (!svgEl) {
      notify("Nothing to save", "Load a network first.", "error");
      return;
    }
    const container = $("graph-container");
    const width = container?.clientWidth || 1200;
    const height = container?.clientHeight || 800;

    const clone = svgEl.cloneNode(true);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    clone.setAttribute("width", String(width));
    clone.setAttribute("height", String(height));

    // Solid background for the export
    const bg = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    bg.setAttribute("width", "100%");
    bg.setAttribute("height", "100%");
    bg.setAttribute("fill", "#1a2220");
    clone.insertBefore(bg, clone.firstChild);

    const serializer = new XMLSerializer();
    const source = serializer.serializeToString(clone);
    const svgBlob = new Blob([source], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(svgBlob);
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = width * 2;
      canvas.height = height * 2;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#1a2220";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      canvas.toBlob((blob) => {
        if (!blob) {
          notify("Save failed", "Could not create PNG.", "error");
          return;
        }
        downloadBlob(blob, "books-in-books-graph.png");
        notify("Saved image", "Downloaded graph PNG.", "success");
      }, "image/png");
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      notify("Save failed", "Could not render the graph image.", "error");
    };
    img.src = url;
  }

  function kindLabel(kind) {
    return (
      {
        book: "Referenced book",
        movie: "Movie",
        music: "Music",
        cocktail: "Drink",
      }[kind] || "Referenced"
    );
  }

  function setupLayerToggles() {
    const root = $("layer-toggles");
    if (!root) return;
    root.querySelectorAll("input[data-kind]").forEach((input) => {
      const kind = input.getAttribute("data-kind");
      input.checked = enabledKinds.has(kind);
      input.addEventListener("change", () => {
        if (input.checked) enabledKinds.add(kind);
        else enabledKinds.delete(kind);
        enabledKinds.add("book");
        saveEnabledKinds();
        if (allBooks.length) {
          if (svg && nodes.length) syncGraph({ reset: false });
          else renderGraph(allBooks, allReferences);
        }
        if (selectedNode && !nodeShouldStay(selectedNode)) {
          closeDetailPanel();
          deselectNode();
        }
      });
    });
  }

  function setupNetworkIo() {
    $("export-network")?.addEventListener("click", exportNetworkJson);
    $("export-png")?.addEventListener("click", exportGraphPng);
    $("import-network")?.addEventListener("click", () => {
      $("import-file")?.click();
    });
    $("import-file")?.addEventListener("change", (e) => {
      const file = e.target.files && e.target.files[0];
      importNetworkFromFile(file);
      e.target.value = "";
    });
    $("first-run-add")?.addEventListener("click", (e) => {
      if (isStaticMode) {
        e.preventDefault();
        return;
      }
      // Anchor already goes to /manage; keep as navigation.
    });
  }

  function applyPublicChrome() {
    if (!isStaticMode) return;
    document.body?.setAttribute("data-static", "true");
    const lede = $("site-lede");
    if (lede) {
      lede.textContent =
        "A public map of books that mention other books. Browse the graph — adding titles happens elsewhere.";
    }
    // Hide every local-only control
    document.querySelectorAll(".local-only, #manage-actions, #network-actions, #first-run").forEach((el) => {
      el.hidden = true;
    });
    const manageLink = $("manage-link");
    if (manageLink) manageLink.hidden = true;
  }

  // —— Boot ——
  document.addEventListener("DOMContentLoaded", () => {
    isStaticMode = detectStaticMode();
    if (document.body?.getAttribute("data-static") === "true") {
      isStaticMode = true;
    }

    if (typeof d3 === "undefined") {
      showGraphMessage(
        "Graph library failed to load",
        "Check your network connection and refresh the page."
      );
      notify("D3.js failed to load", "The graph cannot render until you refresh.", "error", 0);
      return;
    }

    $("library-list")?.addEventListener("click", onLibraryClick);
    $("detail-content")?.addEventListener("click", onDetailJump);
    $("detail-close")?.addEventListener("click", () => {
      closeDetailPanel();
      deselectNode();
    });
    $("search-results")?.addEventListener("click", onSearchPick);
    $("process-dismiss")?.addEventListener("click", hideProcessBanner);
    $("focus-mode")?.addEventListener("click", clearFocusMode);
    $("open-insights")?.addEventListener("click", openInsightsPanel);
    const authorToggle = $("layer-author");
    if (authorToggle) {
      authorToggle.checked = showAuthorLinks;
      authorToggle.addEventListener("change", () => {
        showAuthorLinks = authorToggle.checked;
        try {
          localStorage.setItem("bib.authorLinks", showAuthorLinks ? "on" : "off");
        } catch (e) {
          /* private mode */
        }
        syncGraph();
      });
    }
    $("reset-view")?.addEventListener("click", () => {
      deselectNode();
      closeDetailPanel();
      clearFocusMode();
      if (svg && zoom) {
        svg.transition().duration(450).call(zoom.transform, d3.zoomIdentity);
      }
    });
    $("tidy-layout")?.addEventListener("click", () => {
      deselectNode();
      closeDetailPanel();
      clearFocusMode();
      tidyGraphLayout();
    });

    setupSearch();
    setupUpload();
    setupKeyboard();
    setupNetworkIo();
    setupLayerToggles();
    applyPublicChrome();

    if (!isStaticMode) {
      const runBtn = $("run-process");
      if (runBtn) runBtn.hidden = false;
      const quickAdd = $("open-upload");
      if (quickAdd) quickAdd.hidden = false;
      // Say plainly when extraction will run without Claude's judgement
      fetch("/api/health")
        .then((r) => r.json())
        .then((health) => {
          const hint = $("judge-hint");
          if (!hint || !health.judge) return;
          hint.hidden = false;
          hint.textContent = health.judge.available
            ? `The Judge is on (${health.judge.model}): mentions are checked in context.`
            : "The Judge is off: no Claude credentials, so matching is by title only. Set ANTHROPIC_API_KEY and restart for accurate links.";
          hint.classList.toggle("is-off", !health.judge.available);
        })
        .catch(() => {});
    } else {
      notify(
        "Explore-only site",
        "This GitHub Pages copy is view-only. Library changes are made locally on /manage.",
        "info",
        5000
      );
    }

    window.addEventListener("resize", () => {
      if (!svg) return;
      const container = $("graph-container");
      if (!container) return;
      svg.attr("width", container.clientWidth).attr("height", container.clientHeight);
    });

    refreshAll().then(() => resumeProcessIfNeeded());
  });
})();
