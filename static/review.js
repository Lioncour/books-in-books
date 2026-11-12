/* Review queue: confirm / reject links and rescue dropped mentions. */
(function () {
    const $ = (id) => document.getElementById(id);
    const state = { tab: "links", links: [], dropped: [], current: 0, lastReject: null };

    const KEYS = {
        links:
            "<kbd>↓</kbd>/<kbd>J</kbd> next · <kbd>↑</kbd>/<kbd>K</kbd> previous · " +
            "<kbd>A</kbd> confirm · <kbd>R</kbd> reject · <kbd>U</kbd> undo last reject",
        dropped:
            "<kbd>↓</kbd>/<kbd>J</kbd> next · <kbd>↑</kbd>/<kbd>K</kbd> previous · " +
            "<kbd>L</kbd> link to a work · <kbd>D</kbd> dismiss for good",
    };

    function escapeHtml(text) {
        return String(text == null ? "" : text).replace(
            /[&<>"']/g,
            (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
        );
    }

    /* The stored quote may still hold entities from older extractions. */
    function decodeEntities(text) {
        if (!text || text.indexOf("&") === -1) return text || "";
        const area = document.createElement("textarea");
        area.innerHTML = text;
        return area.value.replace(/\s+/g, " ").trim();
    }

    function quoteHtml(context, needle) {
        const text = decodeEntities(context);
        if (!text) return "";
        const safe = escapeHtml(text);
        const target = escapeHtml(decodeEntities(needle || ""));
        if (target) {
            const at = safe.toLowerCase().indexOf(target.toLowerCase());
            if (at !== -1) {
                return (
                    "…" +
                    safe.slice(0, at) +
                    "<mark>" +
                    safe.slice(at, at + target.length) +
                    "</mark>" +
                    safe.slice(at + target.length) +
                    "…"
                );
            }
        }
        return "…" + safe + "…";
    }

    function setStatus(message, isError) {
        const el = $("review-status");
        el.textContent = message || "";
        el.classList.toggle("is-error", Boolean(isError));
    }

    async function api(path, body) {
        const options = body
            ? {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify(body),
              }
            : {};
        const response = await fetch(path, options);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
        return data;
    }

    function items() {
        return state[state.tab];
    }

    function linkRow(item, index) {
        const cover = item.cover_url
            ? `<img class="review-cover" src="${escapeHtml(item.cover_url)}" alt="" loading="lazy">`
            : `<span class="review-cover is-blank"></span>`;
        const found =
            item.raw_title && item.raw_title.toLowerCase() !== (item.title || "").toLowerCase()
                ? ` · found as “${escapeHtml(decodeEntities(item.raw_title))}”`
                : "";
        return `
            <li class="review-row${index === state.current ? " is-current" : ""}" data-index="${index}">
                ${cover}
                <div class="review-main">
                    <div class="review-work">${escapeHtml(item.title)}</div>
                    <div class="review-meta">
                        <span class="review-kind">${escapeHtml(item.kind)}</span>${escapeHtml(item.author || "Unknown")}
                    </div>
                    <div class="review-meta">mentioned in <strong>${escapeHtml(item.source_title)}</strong>${found}</div>
                    ${item.context ? `<blockquote class="review-quote">${quoteHtml(item.context, item.raw_title || item.title)}</blockquote>` : ""}
                </div>
                <div class="review-actions">
                    <button type="button" class="review-btn is-primary" data-act="confirm">Confirm</button>
                    <button type="button" class="review-btn is-danger" data-act="reject">Reject</button>
                </div>
            </li>`;
    }

    function droppedRow(item, index) {
        const isCurrent = index === state.current;
        const form =
            isCurrent && state.linking
                ? `<form class="review-link-form" data-form="link">
                       <input name="title" aria-label="Title" placeholder="Title" value="${escapeHtml(item.suggested_title || decodeEntities(item.raw_title))}">
                       <input name="author" aria-label="Author" placeholder="Author (recommended)" value="${escapeHtml(item.suggested_author || "")}">
                       <button type="submit" class="review-btn is-primary">Link</button>
                   </form>`
                : "";
        return `
            <li class="review-row${isCurrent ? " is-current" : ""}" data-index="${index}">
                <span class="review-cover is-blank"></span>
                <div class="review-main">
                    <div class="review-work">${escapeHtml(decodeEntities(item.raw_title))}</div>
                    <div class="review-meta">${escapeHtml(item.reason || "Dropped")}</div>
                    <div class="review-meta">in <strong>${escapeHtml(item.source_title)}</strong></div>
                    ${item.context ? `<blockquote class="review-quote">${quoteHtml(item.context, item.raw_title)}</blockquote>` : ""}
                    ${form}
                </div>
                <div class="review-actions">
                    <button type="button" class="review-btn" data-act="link">Link…</button>
                    <button type="button" class="review-btn is-danger" data-act="dismiss">Dismiss</button>
                </div>
            </li>`;
    }

    function render() {
        const list = items();
        if (state.current >= list.length) state.current = Math.max(0, list.length - 1);
        $("count-links").textContent = state.links.length;
        $("count-dropped").textContent = state.dropped.length;
        $("review-keys").innerHTML = KEYS[state.tab];
        document.querySelectorAll(".review-tab").forEach((tab) => {
            tab.classList.toggle("is-active", tab.dataset.tab === state.tab);
        });

        const row = state.tab === "links" ? linkRow : droppedRow;
        $("review-list").innerHTML = list.map(row).join("");
        const empty = $("review-empty");
        empty.hidden = list.length > 0;
        empty.textContent =
            state.tab === "links"
                ? "Nothing left to check. Every link has been confirmed."
                : "No dropped mentions for this selection.";

        const current = document.querySelector(".review-row.is-current");
        if (current) current.scrollIntoView({ block: "nearest" });
        const input = document.querySelector(".review-link-form input");
        if (input) input.focus();
    }

    async function load() {
        const source = $("source-filter").value;
        try {
            const data = await api("/api/review/queue" + (source ? `?source=${source}` : ""));
            state.links = data.links || [];
            state.dropped = data.dropped || [];
            const select = $("source-filter");
            if (select.options.length <= 1) {
                (data.sources || []).forEach((s) => {
                    const option = document.createElement("option");
                    option.value = s.id;
                    option.textContent = s.title;
                    select.appendChild(option);
                });
            }
            render();
        } catch (err) {
            setStatus(err.message, true);
        }
    }

    function move(delta) {
        const list = items();
        if (!list.length) return;
        state.linking = false;
        state.current = Math.min(list.length - 1, Math.max(0, state.current + delta));
        render();
    }

    async function decideLink(action) {
        const item = state.links[state.current];
        if (!item) return;
        try {
            await api("/api/review/link", {
                source_id: item.source_id,
                target_id: item.target_id,
                action,
            });
            state.links.splice(state.current, 1);
            if (action === "reject") {
                state.lastReject = item;
                setStatus(`Rejected “${item.title}”. Press U to undo.`);
            } else {
                setStatus(`Confirmed “${item.title}”.`);
            }
            render();
        } catch (err) {
            setStatus(err.message, true);
        }
    }

    async function undoReject() {
        const item = state.lastReject;
        if (!item) return;
        try {
            await api("/api/review/link", {
                source_id: item.source_id,
                target_id: item.target_id,
                action: "restore",
                raw_title: item.raw_title,
                context: item.context,
            });
            state.lastReject = null;
            state.links.splice(state.current, 0, item);
            setStatus(`Restored “${item.title}”.`);
            render();
        } catch (err) {
            setStatus(err.message, true);
        }
    }

    async function dismissDropped() {
        const item = state.dropped[state.current];
        if (!item) return;
        try {
            await api("/api/review/dropped", { skipped_id: item.skipped_id, action: "dismiss" });
            state.dropped.splice(state.current, 1);
            state.linking = false;
            setStatus(`Dismissed “${decodeEntities(item.raw_title)}”. It will not be suggested again.`);
            render();
        } catch (err) {
            setStatus(err.message, true);
        }
    }

    async function linkDropped(title, author) {
        const item = state.dropped[state.current];
        if (!item) return;
        setStatus("Looking it up in the catalogue…");
        try {
            const data = await api("/api/review/dropped", {
                skipped_id: item.skipped_id,
                action: "link",
                title,
                author,
            });
            state.dropped.splice(state.current, 1);
            state.linking = false;
            setStatus(`Linked to “${data.title}” by ${data.author}.`);
            render();
        } catch (err) {
            setStatus(err.message, true);
        }
    }

    document.addEventListener("click", (event) => {
        const tab = event.target.closest(".review-tab");
        if (tab) {
            state.tab = tab.dataset.tab;
            state.current = 0;
            state.linking = false;
            setStatus("");
            render();
            return;
        }
        const row = event.target.closest(".review-row");
        if (!row) return;
        const index = Number(row.dataset.index);
        const act = event.target.closest("[data-act]");
        if (index !== state.current) {
            state.current = index;
            state.linking = false;
            if (!act) return render();
        }
        if (!act) return;
        const action = act.dataset.act;
        if (action === "confirm" || action === "reject") decideLink(action);
        else if (action === "dismiss") dismissDropped();
        else if (action === "link") {
            state.linking = true;
            render();
        }
    });

    document.addEventListener("submit", (event) => {
        const form = event.target.closest("[data-form='link']");
        if (!form) return;
        event.preventDefault();
        linkDropped(form.title.value.trim(), form.author.value.trim());
    });

    document.addEventListener("keydown", (event) => {
        const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(event.target.tagName);
        if (event.key === "Escape" && state.linking) {
            state.linking = false;
            return render();
        }
        if (typing || event.ctrlKey || event.metaKey || event.altKey) return;
        const key = event.key.toLowerCase();
        if (key === "arrowdown" || key === "j") {
            event.preventDefault();
            move(1);
        } else if (key === "arrowup" || key === "k") {
            event.preventDefault();
            move(-1);
        } else if (state.tab === "links") {
            if (key === "a" || key === "enter") {
                event.preventDefault();
                decideLink("confirm");
            } else if (key === "r") decideLink("reject");
            else if (key === "u") undoReject();
        } else if (state.tab === "dropped") {
            if (key === "l") {
                event.preventDefault();
                state.linking = true;
                render();
            } else if (key === "d") dismissDropped();
        }
    });

    $("source-filter").addEventListener("change", () => {
        state.current = 0;
        load();
    });

    load();
})();
