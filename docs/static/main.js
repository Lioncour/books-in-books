/* Books in Books — literary UI + D3 graph */

(() => {
  "use strict";

  // —— State ——
  let svg, simulation, zoom;
  let linkGroup, nodeGroup;
  let nodes = [];
  let links = [];
  let allBooks = [];
  let allReferences = [];
  let startingBooks = [];
  let selectedNode = null;
  let hiddenNodeIds = new Set();
  let pendingFiles = [];
  let processPollTimer = null;
  let isStaticMode = false;
  let searchTimer = null;
  let focusBookId = null;

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
                    : `<button type="button" data-action="reprocess" data-book-id="${id}" title="Re-extract references">Retry</button>`
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
      notify("Reprocessing", "Extracting references again…", "info");
      showProcessBanner(data.message || "Reprocessing…", 5, "info");
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
      <p class="detail-kicker">${isOwned ? "In your library" : "Referenced book"}</p>
      <h2 class="detail-title">${escapeHtml(book.title)}</h2>
      <p class="detail-author">${escapeHtml(book.author || "Unknown author")}</p>
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
          ? `<p style="margin-top:0.75rem"><button type="button" class="btn btn-ghost" id="detail-reprocess" data-book-id="${book.id}">Re-extract references</button></p>`
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
                  ${escapeHtml(r.title)}${r.verified ? ' <span class="inline-verified">verified</span>' : ""}
                  <span class="ref-author">${escapeHtml(r.author || "")}</span>
                  ${r.context ? `<span class="context-snippet">…${escapeHtml(r.context)}…</span>` : ""}
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
                  ${escapeHtml(r.title)}${r.verified ? ' <span class="inline-verified">verified</span>' : ""}
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
    pickBtn?.addEventListener("click", () => fileInput?.click());
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

    submit?.addEventListener("click", uploadAndProcess);
    runProcess?.addEventListener("click", () => startProcess());
  }

  function openModal() {
    const modal = $("upload-modal");
    if (!modal) return;
    modal.hidden = false;
    pendingFiles = [];
    renderUploadQueue();
    setUploadStatus("", "info");
  }

  function closeModal() {
    const modal = $("upload-modal");
    if (modal) modal.hidden = true;
  }

  function addPendingFiles(fileList) {
    if (!fileList) return;
    let added = 0;
    let rejected = 0;
    Array.from(fileList).forEach((file) => {
      const name = file.name.toLowerCase();
      if (!name.endsWith(".epub") && !name.endsWith(".txt")) {
        rejected += 1;
        return;
      }
      pendingFiles.push(file);
      added += 1;
    });
    renderUploadQueue();
    if (rejected && !added) {
      setUploadStatus("Only .epub and .txt files are supported.", "error");
    } else if (rejected) {
      setUploadStatus(
        `Added ${added} file(s). Skipped ${rejected} unsupported file(s).`,
        "info"
      );
    } else if (added) {
      setUploadStatus(`${pendingFiles.length} file(s) ready to upload.`, "info");
    }
  }

  function renderUploadQueue() {
    const queue = $("upload-queue");
    const submit = $("upload-submit");
    if (!queue) return;
    queue.innerHTML = pendingFiles.map((f) => `<li>${escapeHtml(f.name)}</li>`).join("");
    if (submit) submit.disabled = pendingFiles.length === 0;
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
        `${data.saved.length} file(s) saved. Extracting references…`,
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
    showProcessBanner("Starting… This may take a few minutes.", 5, "info");
    try {
      const res = await fetch("/api/process", { method: "POST" });
      let data;
      try {
        data = await res.json();
      } catch (_) {
        throw new Error(`Could not start processing (HTTP ${res.status}).`);
      }

      if (res.status === 409) {
        notify("Already running", "Extraction is already in progress.", "info");
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
    }
  }

  function pollProcessStatus() {
    clearInterval(processPollTimer);
    let sawRunning = false;
    let ticksWithoutRunning = 0;

    processPollTimer = setInterval(async () => {
      try {
        const res = await fetch("/api/process/status");
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
          showProcessBanner(
            data.message || "Processing… This may take a few minutes.",
            Math.max(pct, 8),
            "info"
          );
          return;
        }

        if (!sawRunning && data.status === "idle") {
          ticksWithoutRunning += 1;
          // Server may have restarted; keep waiting briefly then warn
          if (ticksWithoutRunning > 8) {
            clearInterval(processPollTimer);
            processPollTimer = null;
            showProcessBanner(
              "Processing status was lost (server may have restarted). Click Extract references to retry.",
              0,
              "error"
            );
            notify(
              "Status lost",
              "The server restarted during processing. Try Extract references again.",
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
          notify("Extraction complete", doneMsg, "success");
        }
        await refreshAll();
        setTimeout(hideProcessBanner, 4000);
      } catch (err) {
        console.error(err);
        clearInterval(processPollTimer);
        processPollTimer = null;
        showProcessBanner(err.message, 0, "error");
        notify("Progress check failed", err.message, "error", 8000);
      }
    }, 1200);
  }

  function showProcessBanner(message, percent, kind = "info", errors = []) {
    const banner = $("process-banner");
    const msg = $("process-message");
    const bar = $("process-bar");
    const errList = $("process-errors");
    if (!banner) return;
    banner.hidden = false;
    banner.classList.remove("is-error", "is-success");
    if (kind === "error") banner.classList.add("is-error");
    if (kind === "success") banner.classList.add("is-success");
    if (msg) msg.textContent = message;
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
  function init2D() {
    const container = d3.select("#graph-container");
    if (container.empty()) return;

    container.select("svg").remove();
    let width = container.node().clientWidth;
    let height = container.node().clientHeight;
    if (!width || !height) {
      width = window.innerWidth - 360;
      height = window.innerHeight;
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

    zoom = d3
      .zoom()
      .scaleExtent([0.15, 4])
      .on("zoom", (event) => {
        nodeGroup.attr("transform", event.transform);
        linkGroup.attr("transform", event.transform);
      });
    svg.call(zoom);
  }

  function prepareGraphData(books, references) {
    books = books.filter(
      (book) => book.author && book.author.trim() && book.author.toLowerCase() !== "unknown"
    );

    startingBooks = books.filter((b) => b.is_starting).map((b) => String(b.id));

    const booksByTitle = new Map();
    books.forEach((book) => {
      const titleKey = book.title.toLowerCase().trim();
      const existing = booksByTitle.get(titleKey);
      if (!existing) {
        booksByTitle.set(titleKey, book);
      } else {
        const existingIsStarting = startingBooks.includes(String(existing.id));
        const currentIsStarting = startingBooks.includes(String(book.id));
        if (currentIsStarting && !existingIsStarting) {
          booksByTitle.set(titleKey, book);
        } else if (currentIsStarting === existingIsStarting && book.id < existing.id) {
          booksByTitle.set(titleKey, book);
        }
      }
    });

    const uniqueBooks = Array.from(booksByTitle.values());
    const uniqueBookIds = new Set(uniqueBooks.map((b) => String(b.id)));
    const idRemap = new Map();
    books.forEach((book) => {
      const uniqueBook = booksByTitle.get(book.title.toLowerCase().trim());
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

    const connectedBooks = uniqueBooks.filter((b) => connectedBookIds.has(String(b.id)));
    const connectedReferences = uniqueReferences.filter(
      (ref) => connectedBookIds.has(ref.source) && connectedBookIds.has(ref.target)
    );

    return { connectedBooks, connectedReferences };
  }

  function renderGraph(books, references) {
    if (typeof d3 === "undefined") return;
    if (!svg || svg.empty()) init2D();
    if (!svg) return;

    allBooks = books;
    allReferences = references;

    const { connectedBooks, connectedReferences } = prepareGraphData(books, references);
    const container = d3.select("#graph-container");
    const width = container.node().clientWidth || window.innerWidth - 360;
    const height = container.node().clientHeight || window.innerHeight;

    linkGroup.selectAll("*").remove();
    nodeGroup.selectAll("*").remove();

    nodes = connectedBooks.map((book, i) => {
      const isStarting = startingBooks.includes(String(book.id));
      const connected = new Set();
      connectedReferences.forEach((ref) => {
        if (String(ref.source) === String(book.id)) connected.add(String(ref.target));
        if (String(ref.target) === String(book.id)) connected.add(String(ref.source));
      });
      const connections = connected.size;

      let size, color, strokeColor;
      if (isStarting) {
        size = Math.max(14, Math.min(26, 14 + connections * 1.5));
        color = "#2a9d8f";
        strokeColor = "#0f5c5c";
      } else if (connections > 0) {
        size = Math.max(6, Math.min(14, 6 + connections));
        color = "#7a8f9c";
        strokeColor = "#4a5c66";
      } else {
        size = 5;
        color = "#5a6660";
        strokeColor = "#3d4843";
      }

      let x, y;
      if (isStarting) {
        const startingIndex = startingBooks.indexOf(String(book.id));
        const angle = (startingIndex / Math.max(startingBooks.length, 1)) * 2 * Math.PI;
        x = width / 2 + Math.cos(angle) * 90;
        y = height / 2 + Math.sin(angle) * 90;
      } else {
        const angle = (i / Math.max(connectedBooks.length, 1)) * 2 * Math.PI;
        const radius = 120 + Math.random() * 100;
        x = width / 2 + Math.cos(angle) * radius;
        y = height / 2 + Math.sin(angle) * radius;
      }

      return {
        id: String(book.id),
        title: book.title,
        author: book.author,
        isStarting,
        connections,
        size,
        color,
        strokeColor,
        x,
        y,
        vx: 0,
        vy: 0,
      };
    });

    links = connectedReferences
      .map((ref) => ({ source: String(ref.source), target: String(ref.target) }))
      .filter((l) => l.source !== l.target);

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
            if (s?.isStarting && t?.isStarting) return 90;
            return s?.isStarting || t?.isStarting ? 110 : 150;
          })
          .strength(0.45)
      )
      .force("charge", d3.forceManyBody().strength((d) => (d.isStarting ? -420 : -320)))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collision", d3.forceCollide().radius((d) => d.size + 4).strength(0.85))
      .alpha(1)
      .alphaDecay(0.025);

    const link = linkGroup
      .selectAll("line")
      .data(links)
      .enter()
      .append("line")
      .attr("stroke", "#6d7c75")
      .attr("stroke-opacity", 0.55)
      .attr("stroke-width", 1.6);

    const node = nodeGroup
      .selectAll("circle")
      .data(nodes)
      .enter()
      .append("circle")
      .attr("r", 0)
      .attr("fill", (d) => d.color)
      .attr("stroke", (d) => d.strokeColor)
      .attr("stroke-width", (d) => (d.isStarting ? 2.5 : 1.5))
      .style("cursor", "pointer")
      .style("filter", "url(#node-shadow)")
      .style("opacity", 0)
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
      })
      .call(drag(simulation));

    let animated = false;
    simulation.on("tick", () => {
      link
        .attr("x1", (d) => (typeof d.source === "object" ? d.source.x : 0))
        .attr("y1", (d) => (typeof d.source === "object" ? d.source.y : 0))
        .attr("x2", (d) => (typeof d.target === "object" ? d.target.x : 0))
        .attr("y2", (d) => (typeof d.target === "object" ? d.target.y : 0));
      node.attr("cx", (d) => d.x).attr("cy", (d) => d.y);

      if (!animated && nodes.length && nodes[0].x != null) {
        animated = true;
        node
          .transition()
          .duration(420)
          .delay((_, i) => Math.min(i * 6, 180))
          .ease(d3.easeCubicOut)
          .attr("r", (d) => d.size)
          .style("opacity", 1);
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

  function updateNodeCounts() {
    const el = $("node-counts");
    if (!el || !nodes.length) {
      if (el) el.innerHTML = "";
      return;
    }
    const ownedVisible = nodes.filter((n) => n.isStarting && !hiddenNodeIds.has(n.id)).length;
    const otherVisible = nodes.filter((n) => !n.isStarting).length;
    el.innerHTML = `<div class="owned">Library ${ownedVisible}</div><div>Linked ${otherVisible}</div>`;
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

  const SELECTED_FILL = "#2f9e6b";
  const SELECTED_STROKE = "#1d6b48";

  function selectNode(node) {
    if (selectedNode && selectedNode.id === node.id) {
      deselectNode();
      closeDetailPanel();
      return;
    }
    selectedNode = node;
    highlightPathToNode(node);
    openDetailPanel(node.id);
    renderLibrary();
  }

  function deselectNode() {
    selectedNode = null;
    highlightPathToNode(null);
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
        .attr("stroke-width", (d) => (d.isStarting ? 2.5 : 1.5));
      return;
    }

    let shortestPath = null;
    startingBooks.forEach((startId) => {
      const path = findShortestPath(startId, targetNode.id);
      if (path && (!shortestPath || path.length < shortestPath.length)) {
        shortestPath = path;
      }
    });

    const pathEdges = new Set();
    if (shortestPath) {
      for (let i = 0; i < shortestPath.length - 1; i++) {
        pathEdges.add(`${shortestPath[i]}-${shortestPath[i + 1]}`);
        pathEdges.add(`${shortestPath[i + 1]}-${shortestPath[i]}`);
      }
    }

    linkGroup
      .selectAll("line")
      .attr("stroke", (d) => {
        if (!shortestPath) return "#3d4843";
        const s = typeof d.source === "object" ? d.source.id : d.source;
        const t = typeof d.target === "object" ? d.target.id : d.target;
        return pathEdges.has(`${s}-${t}`) ? "#2a9d8f" : "#3d4843";
      })
      .attr("stroke-opacity", (d) => {
        if (!shortestPath) return 0.25;
        const s = typeof d.source === "object" ? d.source.id : d.source;
        const t = typeof d.target === "object" ? d.target.id : d.target;
        return pathEdges.has(`${s}-${t}`) ? 0.95 : 0.25;
      })
      .attr("stroke-width", (d) => {
        if (!shortestPath) return 1.2;
        const s = typeof d.source === "object" ? d.source.id : d.source;
        const t = typeof d.target === "object" ? d.target.id : d.target;
        return pathEdges.has(`${s}-${t}`) ? 3 : 1.2;
      });

    const selectedId = String(targetNode.id);
    nodeGroup
      .selectAll("circle")
      .attr("fill", (d) => (String(d.id) === selectedId ? SELECTED_FILL : d.color))
      .attr("stroke", (d) => (String(d.id) === selectedId ? SELECTED_STROKE : d.strokeColor))
      .attr("stroke-width", (d) => (String(d.id) === selectedId ? 3 : d.isStarting ? 2.5 : 1.5));
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

    nodeGroup.selectAll("circle").each(function (d) {
      const nodeId = String(d.id);
      const shouldHide = d.isStarting ? hiddenNodeIds.has(nodeId) : nodesToHide.has(nodeId);
      d3.select(this)
        .transition()
        .duration(280)
        .style("opacity", shouldHide ? 0 : 1)
        .attr("r", shouldHide ? 0 : d.size)
        .style("pointer-events", shouldHide ? "none" : "all");
    });

    linkGroup.selectAll("line").style("opacity", (d) => {
      const sourceId = String(typeof d.source === "object" ? d.source.id : d.source);
      const targetId = String(typeof d.target === "object" ? d.target.id : d.target);
      const sourceHidden =
        nodesToHide.has(sourceId) ||
        (nodes.find((n) => String(n.id) === sourceId)?.isStarting && hiddenNodeIds.has(sourceId));
      const targetHidden =
        nodesToHide.has(targetId) ||
        (nodes.find((n) => String(n.id) === targetId)?.isStarting && hiddenNodeIds.has(targetId));
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
        showProcessBanner(data.message || "Processing…", 15, "info");
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
    $("first-run-add")?.addEventListener("click", () => {
      $("open-upload")?.click();
    });
  }

  // —— Boot ——
  document.addEventListener("DOMContentLoaded", () => {
    isStaticMode = detectStaticMode();

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
    $("reset-view")?.addEventListener("click", () => {
      deselectNode();
      closeDetailPanel();
      clearFocusMode();
      if (svg && zoom) {
        svg.transition().duration(450).call(zoom.transform, d3.zoomIdentity);
      }
    });

    setupSearch();
    setupUpload();
    setupKeyboard();
    setupNetworkIo();

    if (!isStaticMode) {
      const runBtn = $("run-process");
      if (runBtn) runBtn.hidden = false;
    } else {
      document.body?.setAttribute("data-static", "true");
      notify(
        "View-only mode",
        "Uploading and extraction need the local Flask server.",
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
