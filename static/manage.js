/* Local library management — add / remove / publish */

(() => {
  "use strict";

  let pendingFiles = [];
  let pollTimer = null;

  function $(id) {
    return document.getElementById(id);
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text == null ? "" : String(text);
    return div.innerHTML;
  }

  function notify(title, text = "", kind = "info") {
    const region = $("toast-region");
    if (!region) return;
    const toast = document.createElement("div");
    toast.className = `toast is-${kind}`;
    toast.innerHTML = `
      <div class="toast-body">
        <div class="toast-title">${escapeHtml(title)}</div>
        ${text ? `<div class="toast-text">${escapeHtml(text)}</div>` : ""}
      </div>
      <button type="button" class="toast-close" aria-label="Dismiss">×</button>`;
    toast.querySelector(".toast-close")?.addEventListener("click", () => toast.remove());
    region.appendChild(toast);
    setTimeout(() => toast.remove(), 4500);
  }

  async function api(path, options = {}) {
    const opts = {
      credentials: "same-origin",
      ...options,
      headers: { ...(options.headers || {}) },
    };
    if (opts.body && !(opts.body instanceof FormData) && !opts.headers["Content-Type"]) {
      opts.headers["Content-Type"] = "application/json";
    }
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
    return data;
  }

  function setLoggedIn(ok) {
    $("login-panel").hidden = ok;
    $("manage-app").hidden = !ok;
    $("manage-stats").hidden = !ok;
  }

  async function checkSession() {
    try {
      const data = await api("/api/admin/session");
      setLoggedIn(!!data.authenticated);
      if (data.authenticated) await refreshLibrary();
    } catch {
      setLoggedIn(false);
    }
  }

  async function refreshLibrary() {
    const [books, refs] = await Promise.all([
      api("/api/books"),
      api("/api/references"),
    ]);
    const owned = (books || []).filter((b) => b.is_starting);
    $("stat-owned").textContent = String(owned.length);
    $("stat-refs").textContent = String((refs || []).length);
    $("library-count").textContent = owned.length ? `${owned.length}` : "";

    const list = $("library-list");
    const empty = $("library-empty");
    if (!owned.length) {
      list.innerHTML = "";
      empty.hidden = false;
      return;
    }
    empty.hidden = true;
    list.innerHTML = owned
      .map((book) => {
        const refsOut =
          book.outgoing_count != null
            ? book.outgoing_count
            : (refs || []).filter((r) => String(r.source) === String(book.id)).length;
        const cover = book.cover_url
          ? `<img class="library-cover" src="${escapeHtml(book.cover_url)}" alt="" loading="lazy">`
          : `<div class="library-cover placeholder">${escapeHtml((book.title || "?").slice(0, 1))}</div>`;
        return `
          <li class="manage-book-row">
            ${cover}
            <div class="manage-book-meta">
              <strong>${escapeHtml(book.title)}</strong>
              <span>${escapeHtml(book.author || "Unknown")}</span>
              <span class="admin-muted">${refsOut} reference${refsOut === 1 ? "" : "s"}${
                book.source_filename
                  ? ` · ${escapeHtml(book.source_filename)}`
                  : book.no_file
                  ? " · added by title, no file"
                  : ""
              }</span>
            </div>
            <div class="manage-book-actions">
              ${
                book.no_file
                  ? ""
                  : `<button type="button" class="btn btn-ghost" data-edit="${book.id}" data-title="${escapeHtml(
                      book.title
                    )}" data-author="${escapeHtml(book.author || "")}">Edit</button>`
              }
              <button type="button" class="btn btn-ghost danger" data-remove="${book.id}" data-title="${escapeHtml(
                book.title
              )}">Remove</button>
            </div>
          </li>`;
      })
      .join("");
  }

  function queueFiles(fileList) {
    const allowed = [".epub", ".txt"];
    for (const file of fileList) {
      const name = file.name || "";
      const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
      if (!allowed.includes(ext)) {
        notify("Skipped file", `${name} is not .epub or .txt`, "error");
        continue;
      }
      pendingFiles.push(file);
    }
    renderQueue();
  }

  function renderQueue() {
    const queue = $("upload-queue");
    const submit = $("upload-submit");
    const clearBtn = $("upload-clear");
    queue.innerHTML = pendingFiles
      .map(
        (f, i) =>
          `<li>${escapeHtml(f.name)} <button type="button" class="linkish" data-drop="${i}">remove</button></li>`
      )
      .join("");
    submit.disabled = !pendingFiles.length;
    clearBtn.hidden = !pendingFiles.length;
  }

  function setUploadStatus(text, kind = "info") {
    const el = $("upload-status");
    if (!text) {
      el.hidden = true;
      return;
    }
    el.hidden = false;
    el.className = `upload-status is-${kind}`;
    el.textContent = text;
  }

  async function uploadAndProcess() {
    if (!pendingFiles.length) return;
    const submit = $("upload-submit");
    submit.disabled = true;
    setUploadStatus("Uploading…", "info");
    try {
      const form = new FormData();
      pendingFiles.forEach((f) => form.append("files", f));
      const uploaded = await api("/api/upload", { method: "POST", body: form, headers: {} });
      const names = (uploaded.saved || []).map((s) => s.filename);
      pendingFiles = [];
      renderQueue();
      setUploadStatus(`Uploaded ${names.length} file(s). Extracting…`, "info");
      await api("/api/process", {
        method: "POST",
        body: JSON.stringify({ filenames: names, force: true }),
      });
      startPolling();
      notify("Upload started", "Borges is reading the new files.", "success");
    } catch (err) {
      setUploadStatus(err.message || "Upload failed", "error");
      submit.disabled = false;
      notify("Upload failed", err.message || "", "error");
    }
  }

  function showProcess(snap) {
    const banner = $("process-banner");
    const msg = $("process-message");
    const bar = $("process-bar");
    const errs = $("process-errors");
    if (!banner) return;
    const running = snap.status === "running";
    const done = snap.status === "done" || snap.status === "idle";
    const errored = snap.status === "error";
    banner.hidden = !(running || errored || (snap.message && !done));
    if (msg) msg.textContent = snap.message || "Processing…";
    if (bar) {
      const total = Math.max(1, snap.total || 1);
      const pct = Math.min(100, Math.round(((snap.processed || 0) / total) * 100));
      bar.style.width = `${running ? Math.max(pct, 8) : pct}%`;
    }
    if (errs) {
      const list = snap.errors || [];
      errs.hidden = !list.length;
      errs.innerHTML = list.map((e) => `<li>${escapeHtml(e)}</li>`).join("");
    }
  }

  async function pollOnce() {
    try {
      const snap = await api("/api/process/status");
      showProcess(snap);
      if (snap.status === "running") return true;
      if (snap.status === "done" || snap.status === "idle") {
        $("process-banner").hidden = true;
        setUploadStatus(snap.message || "Done.", "success");
        await refreshLibrary();
        return false;
      }
      if (snap.status === "error") {
        setUploadStatus(snap.message || "Processing error", "error");
        await refreshLibrary();
        return false;
      }
    } catch {
      /* ignore transient poll errors */
    }
    return true;
  }

  function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      const keep = await pollOnce();
      if (!keep && pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }, 1200);
    pollOnce();
  }

  function setupUpload() {
    const zone = $("drop-zone");
    const input = $("file-input");
    $("pick-files")?.addEventListener("click", () => input?.click());
    zone?.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      input?.click();
    });
    zone?.addEventListener("dragover", (e) => {
      e.preventDefault();
      zone.classList.add("is-drag");
    });
    zone?.addEventListener("dragleave", () => zone.classList.remove("is-drag"));
    zone?.addEventListener("drop", (e) => {
      e.preventDefault();
      zone.classList.remove("is-drag");
      if (e.dataTransfer?.files?.length) queueFiles(e.dataTransfer.files);
    });
    input?.addEventListener("change", () => {
      if (input.files?.length) queueFiles(input.files);
      input.value = "";
    });
    $("upload-queue")?.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-drop]");
      if (!btn) return;
      pendingFiles.splice(Number(btn.getAttribute("data-drop")), 1);
      renderQueue();
    });
    $("upload-clear")?.addEventListener("click", () => {
      pendingFiles = [];
      renderQueue();
      setUploadStatus("");
    });
    $("upload-submit")?.addEventListener("click", uploadAndProcess);
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("login-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const token = ($("admin-token")?.value || "").trim();
      $("login-error").hidden = true;
      try {
        await api("/api/admin/login", {
          method: "POST",
          body: JSON.stringify({ token }),
        });
        setLoggedIn(true);
        await refreshLibrary();
        notify("Unlocked", "You can add and remove books.", "success");
      } catch (err) {
        $("login-error").hidden = false;
        $("login-error").textContent = err.message || "Invalid token";
      }
    });

    $("logout-btn")?.addEventListener("click", async () => {
      await api("/api/admin/logout", { method: "POST", body: "{}" });
      setLoggedIn(false);
    });

    // Add a book by its title, with no file
    $("title-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const title = $("title-input").value.trim();
      const author = $("title-author").value.trim();
      if (!title) return;
      const status = $("title-status");
      const button = $("title-submit");
      button.disabled = true;
      status.hidden = false;
      status.textContent = "Looking it up…";
      try {
        const res = await api("/api/library/books", {
          method: "POST",
          body: JSON.stringify({ title, author }),
        });
        status.textContent = res.message;
        $("title-input").value = "";
        $("title-author").value = "";
        await refreshLibrary();
      } catch (err) {
        status.textContent = err.message || "Could not add the book";
      } finally {
        button.disabled = false;
      }
    });

    // Edit a library book's title and author in place
    $("library-list")?.addEventListener("click", (e) => {
      const edit = e.target.closest("[data-edit]");
      if (edit) {
        const meta = edit.closest(".manage-book-row")?.querySelector(".manage-book-meta");
        if (!meta || meta.querySelector(".manage-edit-form")) return;
        const form = document.createElement("form");
        form.className = "manage-edit-form";
        form.dataset.bookId = edit.getAttribute("data-edit");
        form.innerHTML = `
          <input name="title" aria-label="Title" placeholder="Title" required value="${escapeHtml(
            edit.getAttribute("data-title") || ""
          )}">
          <input name="author" aria-label="Author" placeholder="Author" value="${escapeHtml(
            edit.getAttribute("data-author") || ""
          )}">
          <button type="submit" class="btn btn-primary">Save</button>
          <button type="button" class="btn btn-ghost" data-cancel-edit>Cancel</button>`;
        meta.appendChild(form);
        form.title.focus();
        return;
      }
      if (e.target.closest("[data-cancel-edit]")) {
        e.target.closest(".manage-edit-form")?.remove();
      }
    });

    $("library-list")?.addEventListener("submit", async (e) => {
      const form = e.target.closest(".manage-edit-form");
      if (!form) return;
      e.preventDefault();
      try {
        const res = await api(`/api/library/books/${form.dataset.bookId}`, {
          method: "PATCH",
          body: JSON.stringify({ title: form.title.value.trim(), author: form.author.value.trim() }),
        });
        notify(
          "Saved",
          res.refiled ? `${res.title}: found its catalogue record and cover.` : res.title,
          "success"
        );
        await refreshLibrary();
      } catch (err) {
        notify("Could not save", err.message || "", "error");
      }
    });

    $("library-list")?.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-remove]");
      if (!btn) return;
      const id = btn.getAttribute("data-remove");
      const title = btn.getAttribute("data-title") || "this book";
      if (!window.confirm(`Remove “${title}” from your library?`)) return;
      try {
        const res = await api(`/api/library/books/${id}`, { method: "DELETE" });
        notify("Removed", res.message || title, "success");
        await refreshLibrary();
      } catch (err) {
        notify("Could not remove", err.message || "", "error");
      }
    });

    $("export-pages")?.addEventListener("click", async () => {
      const status = $("export-status");
      status.textContent = "Exporting…";
      try {
        const res = await api("/api/admin/export-static", {
          method: "POST",
          body: "{}",
        });
        status.textContent = res.message || "Exported docs/.";
        notify("Exported", "Commit and push docs/ to update GitHub Pages.", "success");
      } catch (err) {
        status.textContent = err.message || "Export failed";
        notify("Export failed", err.message || "", "error");
      }
    });

    setupUpload();
    checkSession();
  });
})();
