// BDD SKU offcanvas — API connection settings (navbar action)
(function () {
    "use strict";

    const PLUGIN = "bdd-sku";
    const offcanvasEl = document.getElementById("bdd-sku-offcanvas");
    if (!offcanvasEl) return;

    const body = document.getElementById("bdd-sku-offcanvas-body");
    let loaded = false;

    function setStatus(state) {
        const el = document.getElementById("bdd-sku-oc-conn-status");
        if (!el) return;
        const map = {
            "connected":      { cls: "bdd-sku-conn--ok",    text: "Connected" },
            "not-configured": { cls: "bdd-sku-conn--warn",  text: "Not configured" },
            "error":          { cls: "bdd-sku-conn--error", text: "Error" },
        };
        const s = map[state] || map["not-configured"];
        el.className = "bdd-sku-settings-status " + s.cls;
        el.textContent = s.text;
    }

    function showMsg(text, type) {
        const el = document.getElementById("bdd-sku-oc-settings-msg");
        if (!el) return;
        el.textContent = text;
        el.className = "bdd-sku-settings-hint bdd-sku-msg--" + type;
    }

    function clearMsg() {
        const el = document.getElementById("bdd-sku-oc-settings-msg");
        if (!el) return;
        el.textContent = "";
        el.className = "bdd-sku-settings-hint";
    }

    function bindEvents() {
        const urlInput = document.getElementById("bdd-sku-oc-api-url");
        const saveBtn  = document.getElementById("bdd-sku-oc-save-url");
        const testBtn  = document.getElementById("bdd-sku-oc-test-url");

        // Load current settings
        apiFetch("/plugins/" + PLUGIN + "/settings")
            .then(function (s) {
                urlInput.value = s.api_base_url || "";
                setStatus(s.is_configured ? "connected" : "not-configured");
            })
            .catch(function () { setStatus("error"); });

        saveBtn.addEventListener("click", async function () {
            clearMsg();
            const url = urlInput.value.trim();
            if (!url) { showMsg("URL is required.", "error"); return; }
            saveBtn.disabled = true;
            try {
                await apiPost("/plugins/" + PLUGIN + "/settings/update", { api_base_url: url });
                showMsg("Saved.", "ok");
                setStatus("connected");
            } catch (e) {
                showMsg(e.message || "Save failed.", "error");
            } finally { saveBtn.disabled = false; }
        });

        testBtn.addEventListener("click", async function () {
            clearMsg();
            const url = urlInput.value.trim();
            if (!url) { showMsg("Enter a URL first.", "error"); return; }
            testBtn.disabled = true;
            try {
                const r = await apiPost("/plugins/" + PLUGIN + "/settings/test", { api_base_url: url });
                if (r.ok) showMsg("Connection OK (HTTP " + r.status + ").", "ok");
                else showMsg(r.error || "Connection failed.", "error");
            } catch (e) {
                showMsg(e.message || "Test failed.", "error");
            } finally { testBtn.disabled = false; }
        });
    }

    offcanvasEl.addEventListener("show.bs.offcanvas", function () {
        if (loaded) return;
        loaded = true;
        fetch("/plugins/" + PLUGIN + "/static/html/bdd-sku-offcanvas.html")
            .then(function (r) { return r.text(); })
            .then(function (html) {
                body.innerHTML = html;
                bindEvents();
            });
    });
})();
