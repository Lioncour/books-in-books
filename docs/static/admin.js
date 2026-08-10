/* Catalog admin UI */

(() => {
  "use strict";

  let selectedWorkId = null;
  let searchTimer = null;
  let mentionTimer = null;

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
    setTimeout(() => toast.remove(), 4000);
  }

  async function api(path, options = {}) {
    const opts = {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    };
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
    return data;
  }

  async function refreshStats() {
    const stats = await api("/api/admin/catalog/stats");
    $("stat-works").textContent = String(stats.works ?? 0);
    $("stat-mentions").textContent = String(stats.mentions ?? 0);
    $("admin-stats").hidden = false;
  }

  async function loadWorks(query = "") {
    const q = encodeURIComponent(query || "");
    const works = await api(`/api/admin/catalog/works?q=${q}`);
    const list = $("works-list");
    $("works-count").textContent = `${works.length} shown`;
    if (!works.length) {
      list.innerHTML = `<li class="admin-muted" style="padding:0.6rem 0">No works yet. Add one on the left.</li>`;
      return;
    }
    list.innerHTML = works
      .map(
        (w) => `
      <li>
        <button type="button" class="work-row${String(w.id) === String(selectedWorkId) ? " active" : ""}" data-id="${w.id}">
          <span class="t">${escapeHtml(w.title)}</span>
          <span class="a">${escapeHtml(w.author || "Unknown author")}</span>
          <span class="m">${w.outgoing_count || 0} out · ${w.incoming_count || 0} in</span>
        </button>
      </li>`
      )
      .join("");
  }

  async function openWork(id) {
    selectedWorkId = id;
    const data = await api(`/api/admin/catalog/works/${id}`);
    const work = data.work;
    $("detail-panel").hidden = false;
    $("detail-title").textContent = work.title;
    $("detail-author").textContent = work.author || "Unknown author";
    $("mention-target-id").value = "";
    $("mention-title").value = "";
    $("mention-author").value = "";
    $("mention-context").value = "";
    $("mention-suggestions").hidden = true;

    const out = $("outgoing-list");
    const inn = $("incoming-list");
    out.innerHTML = data.outgoing.length
      ? data.outgoing
          .map(
            (m) => `
        <li class="mention-row">
          <div class="meta">
            <span class="t">${escapeHtml(m.title)}</span>
            <span class="a admin-muted">${escapeHtml(m.author || "")}</span>
            ${m.context ? `<span class="ctx">…${escapeHtml(m.context)}…</span>` : ""}
          </div>
          <button type="button" class="btn btn-small danger" data-del-mention="${m.mention_id}">Remove</button>
        </li>`
          )
          .join("")
      : `<li class="admin-muted" style="padding:0.5rem 0">No outgoing mentions yet.</li>`;

    inn.innerHTML = data.incoming.length
      ? data.incoming
          .map(
            (m) => `
        <li class="mention-row">
          <div class="meta">
            <span class="t">${escapeHtml(m.title)}</span>
            <span class="a admin-muted">${escapeHtml(m.author || "")}</span>
          </div>
        </li>`
          )
          .join("")
      : `<li class="admin-muted" style="padding:0.5rem 0">Nothing mentions this yet.</li>`;

    await loadWorks($("works-search").value.trim());
    $("detail-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function showApp() {
    $("login-panel").hidden = true;
    $("admin-app").hidden = false;
  }

  function showLogin(msg) {
    $("login-panel").hidden = false;
    $("admin-app").hidden = true;
    $("admin-stats").hidden = true;
    const err = $("login-error");
    if (msg) {
      err.hidden = false;
      err.textContent = msg;
    } else {
      err.hidden = true;
    }
  }

  async function bootAuthenticated() {
    showApp();
    await refreshStats();
    await loadWorks();
  }

  document.addEventListener("DOMContentLoaded", async () => {
    try {
      const session = await api("/api/admin/session");
      if (session.authenticated) await bootAuthenticated();
    } catch (_) {
      /* stay on login */
    }

    $("login-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api("/api/admin/login", {
          method: "POST",
          body: JSON.stringify({ token: $("admin-token").value.trim() }),
        });
        await bootAuthenticated();
        notify("Unlocked", "Catalog admin ready.", "success");
      } catch (err) {
        showLogin(err.message);
      }
    });

    $("logout-btn")?.addEventListener("click", async () => {
      await api("/api/admin/logout", { method: "POST", body: "{}" });
      selectedWorkId = null;
      showLogin();
    });

    $("works-search")?.addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        loadWorks($("works-search").value.trim()).catch((err) =>
          notify("Search failed", err.message, "error")
        );
      }, 220);
    });

    $("works-list")?.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-id]");
      if (!btn) return;
      openWork(btn.getAttribute("data-id")).catch((err) =>
        notify("Could not open work", err.message, "error")
      );
    });

    $("detail-close")?.addEventListener("click", () => {
      $("detail-panel").hidden = true;
      selectedWorkId = null;
      loadWorks($("works-search").value.trim());
    });

    $("detail-delete")?.addEventListener("click", async () => {
      if (!selectedWorkId) return;
      if (!confirm("Delete this work and all its mention links?")) return;
      try {
        await api(`/api/admin/catalog/works/${selectedWorkId}`, { method: "DELETE" });
        selectedWorkId = null;
        $("detail-panel").hidden = true;
        await refreshStats();
        await loadWorks($("works-search").value.trim());
        notify("Deleted", "Work removed from catalog.", "success");
      } catch (err) {
        notify("Delete failed", err.message, "error");
      }
    });

    $("lookup-ol")?.addEventListener("click", async () => {
      const title = $("work-title").value.trim();
      if (!title) {
        notify("Title needed", "Enter a title before looking up.", "error");
        return;
      }
      $("lookup-status").textContent = "Looking up…";
      try {
        const data = await api(`/api/admin/catalog/lookup?title=${encodeURIComponent(title)}`);
        if (!data.match) {
          $("lookup-status").textContent = "No strong match";
          return;
        }
        const m = data.match;
        $("work-title").value = m.title || title;
        if (m.author) $("work-author").value = m.author;
        if (m.open_library_id) $("work-ol").value = m.open_library_id;
        if (m.cover_id) $("work-cover").value = m.cover_id;
        $("lookup-status").textContent = `Matched ${Math.round((m.match_score || 0) * 100)}%`;
      } catch (err) {
        $("lookup-status").textContent = "";
        notify("Lookup failed", err.message, "error");
      }
    });

    $("add-work-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const data = await api("/api/admin/catalog/works", {
          method: "POST",
          body: JSON.stringify({
            title: $("work-title").value.trim(),
            author: $("work-author").value.trim(),
            open_library_id: $("work-ol").value.trim(),
            cover_id: $("work-cover").value.trim(),
            notes: $("work-notes").value.trim(),
          }),
        });
        $("add-work-form").reset();
        $("lookup-status").textContent = "";
        await refreshStats();
        await loadWorks($("works-search").value.trim());
        notify(
          data.created ? "Work added" : "Work updated",
          data.work.title,
          "success"
        );
        await openWork(data.work.id);
      } catch (err) {
        notify("Save failed", err.message, "error");
      }
    });

    $("mention-search")?.addEventListener("input", () => {
      clearTimeout(mentionTimer);
      mentionTimer = setTimeout(async () => {
        const q = $("mention-search").value.trim();
        const box = $("mention-suggestions");
        if (q.length < 2) {
          box.hidden = true;
          box.innerHTML = "";
          return;
        }
        try {
          const works = await api(`/api/admin/catalog/works?q=${encodeURIComponent(q)}`);
          const filtered = works.filter((w) => String(w.id) !== String(selectedWorkId));
          if (!filtered.length) {
            box.hidden = true;
            return;
          }
          box.hidden = false;
          box.innerHTML = filtered
            .slice(0, 8)
            .map(
              (w) => `
            <li>
              <button type="button" class="suggest-row" data-target-id="${w.id}" data-title="${escapeHtml(w.title)}" data-author="${escapeHtml(w.author || "")}">
                <span class="t">${escapeHtml(w.title)}</span>
                <span class="a">${escapeHtml(w.author || "")}</span>
              </button>
            </li>`
            )
            .join("");
        } catch (_) {
          box.hidden = true;
        }
      }, 200);
    });

    $("mention-suggestions")?.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-target-id]");
      if (!btn) return;
      $("mention-target-id").value = btn.getAttribute("data-target-id");
      $("mention-title").value = btn.getAttribute("data-title") || "";
      $("mention-author").value = btn.getAttribute("data-author") || "";
      $("mention-search").value = btn.getAttribute("data-title") || "";
      $("mention-suggestions").hidden = true;
    });

    $("add-mention-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!selectedWorkId) return;
      const targetId = $("mention-target-id").value.trim();
      const title = $("mention-title").value.trim();
      if (!targetId && !title) {
        notify("Need a target", "Pick a suggestion or type a new title.", "error");
        return;
      }
      try {
        const body = {
          source_work_id: Number(selectedWorkId),
          context: $("mention-context").value.trim(),
        };
        if (targetId) body.target_work_id = Number(targetId);
        else {
          body.target_title = title;
          body.target_author = $("mention-author").value.trim();
        }
        await api("/api/admin/catalog/mentions", {
          method: "POST",
          body: JSON.stringify(body),
        });
        $("mention-target-id").value = "";
        $("mention-title").value = "";
        $("mention-author").value = "";
        $("mention-context").value = "";
        $("mention-search").value = "";
        await refreshStats();
        await openWork(selectedWorkId);
        notify("Mention added", "", "success");
      } catch (err) {
        notify("Could not add mention", err.message, "error");
      }
    });

    $("outgoing-list")?.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-del-mention]");
      if (!btn) return;
      try {
        await api(`/api/admin/catalog/mentions/${btn.getAttribute("data-del-mention")}`, {
          method: "DELETE",
        });
        await refreshStats();
        await openWork(selectedWorkId);
      } catch (err) {
        notify("Remove failed", err.message, "error");
      }
    });

    $("import-local")?.addEventListener("click", async () => {
      if (
        !confirm(
          "Copy verified books and references from your local library into the shared catalog?"
        )
      ) {
        return;
      }
      try {
        const result = await api("/api/admin/catalog/import-local", {
          method: "POST",
          body: "{}",
        });
        await refreshStats();
        await loadWorks($("works-search").value.trim());
        notify(
          "Imported",
          `+${result.works_created} works, ${result.mentions_added} mentions (${result.works_updated} updated)`,
          "success"
        );
      } catch (err) {
        notify("Import failed", err.message, "error");
      }
    });
  });
})();
