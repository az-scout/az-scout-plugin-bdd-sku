// SKU DB Cache plugin tab logic
(function () {
    const PLUGIN = "bdd-sku";
    const container = document.getElementById("plugin-tab-" + PLUGIN);
    if (!container) return;

    // ---------------------------------------------------------------
    // 1. Load HTML fragment
    // ---------------------------------------------------------------
    fetch(`/plugins/${PLUGIN}/static/html/bdd-sku-tab.html`)
        .then(r => r.text())
        .then(html => { container.innerHTML = html; init(); })
        .catch(err => {
            container.innerHTML = `<div class="alert alert-danger">Failed to load plugin UI: ${err.message}</div>`;
        });

    // ---------------------------------------------------------------
    // 2. Init
    // ---------------------------------------------------------------
    function init() {
        const urlInput     = document.getElementById("bdd-sku-api-url");
        const saveBtn      = document.getElementById("bdd-sku-save-url");
        const testBtn      = document.getElementById("bdd-sku-test-url");
        const settingsMsg  = document.getElementById("bdd-sku-settings-msg");
        const connStatus   = document.getElementById("bdd-sku-conn-status");
        const settingsBody = document.getElementById("bdd-sku-settings-body");
        const settingsHdr  = document.getElementById("bdd-sku-settings-toggle");
        const dashboard    = document.getElementById("bdd-sku-dashboard");
        const notConfigured = document.getElementById("bdd-sku-not-configured");

        let isConfigured = false;
        let settingsOpen = true;

        // -- Settings collapse toggle --
        settingsHdr.addEventListener("click", () => {
            settingsOpen = !settingsOpen;
            settingsBody.style.display = settingsOpen ? "" : "none";
        });

        // -- Load settings on init --
        async function loadSettings() {
            try {
                const s = await apiFetch(`/plugins/${PLUGIN}/settings`);
                urlInput.value = s.api_base_url || "";
                isConfigured = s.is_configured;
                updateConnStatus(isConfigured ? "connected" : "not-configured");
                showDashboard(isConfigured);
                if (isConfigured) {
                    settingsOpen = false;
                    settingsBody.style.display = "none";
                    refresh();
                }
            } catch {
                updateConnStatus("error");
                showDashboard(false);
            }
        }

        function showDashboard(show) {
            dashboard.style.display = show ? "" : "none";
            notConfigured.style.display = show ? "none" : "";
        }

        function updateConnStatus(state) {
            const map = {
                "connected":      { cls: "bdd-sku-conn--ok",    text: "Connected" },
                "not-configured": { cls: "bdd-sku-conn--warn",  text: "Not configured" },
                "error":          { cls: "bdd-sku-conn--error", text: "Error" },
            };
            const s = map[state] || map["not-configured"];
            connStatus.className = "bdd-sku-settings-status " + s.cls;
            connStatus.textContent = s.text;
        }

        function showMsg(text, type) {
            settingsMsg.textContent = text;
            settingsMsg.className = "bdd-sku-settings-hint bdd-sku-msg--" + type;
        }

        function clearMsg() {
            settingsMsg.textContent = "";
            settingsMsg.className = "bdd-sku-settings-hint";
        }

        // -- Save --
        saveBtn.addEventListener("click", async () => {
            clearMsg();
            const url = urlInput.value.trim();
            if (!url) { showMsg("URL is required.", "error"); return; }
            saveBtn.disabled = true;
            try {
                await apiPost(`/plugins/${PLUGIN}/settings/update`, { api_base_url: url });
                showMsg("Saved.", "ok");
                isConfigured = true;
                updateConnStatus("connected");
                showDashboard(true);
                refresh();
            } catch (e) {
                showMsg(e.message || "Save failed.", "error");
            } finally {
                saveBtn.disabled = false;
            }
        });

        // -- Test --
        testBtn.addEventListener("click", async () => {
            clearMsg();
            const url = urlInput.value.trim();
            if (!url) { showMsg("Enter a URL first.", "error"); return; }
            testBtn.disabled = true;
            try {
                const r = await apiPost(`/plugins/${PLUGIN}/settings/test`, { api_base_url: url });
                if (r.ok) {
                    showMsg(`Connection OK (HTTP ${r.status}).`, "ok");
                } else {
                    showMsg(r.error || "Connection failed.", "error");
                }
            } catch (e) {
                showMsg(e.message || "Test failed.", "error");
            } finally {
                testBtn.disabled = false;
            }
        });

        // -- Dashboard logic (unchanged) --
        async function refresh() {
            try {
                const data = await apiFetch(`/plugins/${PLUGIN}/status`);
                if (data.configured === false) {
                    showDashboard(false);
                    updateConnStatus("not-configured");
                    return;
                }
                updateDashboard(data);
            } catch (e) {
                setBadge("retail", "error", "Error");
                setBadge("eviction", "error", "Error");
                setBadge("spot-price", "error", "Error");
            }
        }

        function setBadge(card, cls, text) {
            const el = document.getElementById(`bdd-sku-badge-${card}`);
            if (!el) return;
            el.className = `bdd-sku-badge bdd-sku-badge--${cls}`;
            el.textContent = text;
        }

        function setMeta(card, html) {
            const el = document.getElementById(`bdd-sku-meta-${card}`);
            if (el) el.innerHTML = html;
        }

        function setKpi(id, value) {
            const el = document.getElementById(id);
            if (el) el.textContent = value;
        }

        function fmtNum(n) {
            return n >= 0 ? n.toLocaleString() : "N/A";
        }

        function fmtDate(iso) {
            if (!iso) return "N/A";
            return new Date(iso).toLocaleString();
        }

        function runMeta(lr) {
            if (!lr) return `<dt>Last update</dt><dd>No data</dd>`;
            let h = `<dt>Last update</dt><dd>${fmtDate(lr.started_at_utc)}</dd>`;
            if (lr.items_written != null) {
                h += `<dt>Written</dt><dd>${lr.items_written.toLocaleString()}</dd>`;
            }
            if (lr.error_message) {
                h += `<dt>Error</dt><dd class="text-danger">${lr.error_message}</dd>`;
            }
            return h;
        }

        function runStatus(lr) {
            if (!lr) return { cls: "idle", label: "No data" };
            const map = { ok: "Ready", running: "Loading\u2026", error: "Error", idle: "Idle" };
            const s = lr.status || "idle";
            return { cls: s, label: map[s] || s };
        }

        function updateDashboard(data) {
            setKpi("bdd-sku-kpi-regions", fmtNum(data.regions_count ?? 0));
            setKpi("bdd-sku-kpi-retail", fmtNum(data.retail_prices_count));
            setKpi("bdd-sku-kpi-spot-skus", fmtNum(data.spot_skus_count ?? 0));

            const retailRun = data.last_run;
            const rs = runStatus(retailRun);
            setBadge("retail", rs.cls, rs.label);
            let retailMeta = `<dt>Rows</dt><dd>${fmtNum(data.retail_prices_count)}</dd>`;
            retailMeta += runMeta(retailRun);
            setMeta("retail", retailMeta);

            const spotRun = data.last_run_spot;
            const ss = runStatus(spotRun);
            setBadge("eviction", ss.cls, ss.label);
            let evictMeta = `<dt>Rows</dt><dd>${fmtNum(data.spot_eviction_rates_count)}</dd>`;
            evictMeta += runMeta(spotRun);
            setMeta("eviction", evictMeta);

            setBadge("spot-price", ss.cls, ss.label);
            let priceMeta = `<dt>Rows</dt><dd>${fmtNum(data.spot_price_history_count)}</dd>`;
            priceMeta += runMeta(spotRun);
            setMeta("spot-price", priceMeta);
        }

        const refreshBtn = document.getElementById("bdd-sku-refresh");
        if (refreshBtn) refreshBtn.addEventListener("click", refresh);

        // Initial load
        loadSettings();
    }
})();
