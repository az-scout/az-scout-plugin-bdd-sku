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
        async function refresh() {
            try {
                const data = await apiFetch(`/plugins/${PLUGIN}/status`);
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
            // KPIs
            setKpi("bdd-sku-kpi-regions", fmtNum(data.regions_count ?? 0));
            setKpi("bdd-sku-kpi-retail", fmtNum(data.retail_prices_count));
            setKpi("bdd-sku-kpi-spot-skus", fmtNum(data.spot_skus_count ?? 0));

            // Retail Pricing card
            const retailRun = data.last_run;
            const rs = runStatus(retailRun);
            setBadge("retail", rs.cls, rs.label);
            let retailMeta = `<dt>Rows</dt><dd>${fmtNum(data.retail_prices_count)}</dd>`;
            retailMeta += runMeta(retailRun);
            setMeta("retail", retailMeta);

            // Spot Eviction card
            const spotRun = data.last_run_spot;
            const ss = runStatus(spotRun);
            setBadge("eviction", ss.cls, ss.label);
            let evictMeta = `<dt>Rows</dt><dd>${fmtNum(data.spot_eviction_rates_count)}</dd>`;
            evictMeta += runMeta(spotRun);
            setMeta("eviction", evictMeta);

            // Spot Price History card
            setBadge("spot-price", ss.cls, ss.label);
            let priceMeta = `<dt>Rows</dt><dd>${fmtNum(data.spot_price_history_count)}</dd>`;
            priceMeta += runMeta(spotRun);
            setMeta("spot-price", priceMeta);
        }

        // Refresh button
        const refreshBtn = document.getElementById("bdd-sku-refresh");
        if (refreshBtn) {
            refreshBtn.addEventListener("click", refresh);
        }

        // Initial load
        refresh();
    }
})();
