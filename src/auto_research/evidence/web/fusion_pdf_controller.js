(() => {
  "use strict";

  const MIN_ZOOM = 25;
  const MAX_ZOOM = 300;
  const ZOOM_STEP = 25;
  const MODES = new Set(["fit-width", "fit-page", "custom"]);

  const finitePage = value => Number.isSafeInteger(Number(value)) && Number(value) > 0 ? Number(value) : 1;
  const finiteZoom = value => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Number(value) || 100));

  function safePDFURL(raw) {
    if (typeof raw !== "string" || !raw.startsWith("/api/") || raw.startsWith("//")) return null;
    let parsed;
    try { parsed = new URL(raw, "http://fusion.local"); } catch (_error) { return null; }
    if (parsed.origin !== "http://fusion.local" || !parsed.pathname.startsWith("/api/")) return null;
    return `${parsed.pathname}${parsed.search}`;
  }

  function sourceFragment(page, mode, zoom) {
    const fit = mode === "fit-width" ? "page-width" : mode === "fit-page" ? "page-fit" : String(finiteZoom(zoom));
    return `#page=${finitePage(page)}&zoom=${fit}`;
  }

  class FusionPdfController {
    constructor({ ResizeObserverClass = globalThis.ResizeObserver } = {}) {
      this.ResizeObserverClass = ResizeObserverClass;
      this.sessions = new Map();
    }

    open({ viewerId, frame, container = null, url, page = 1, mode = "fit-width", zoom = 100, returnTabId = "", returnFocus = null, scrollTop = 0, highlight = null } = {}) {
      const id = String(viewerId || "").trim(), safeURL = safePDFURL(url);
      if (!id || !frame || !safeURL || !MODES.has(mode)) return null;
      this.close(id, { clearFrame: false });
      const session = { viewerId: id, frame, container, url: safeURL, page: finitePage(page), mode, zoom: finiteZoom(zoom), returnTabId: String(returnTabId || ""), returnFocus, scrollTop: Math.max(0, Number(scrollTop) || 0), highlight, observer: null };
      if (typeof this.ResizeObserverClass === "function" && container) {
        session.observer = new this.ResizeObserverClass(() => {
          const current = this.sessions.get(id);
          if (current && current.mode !== "custom") this.project(current);
        });
        session.observer.observe(container);
      }
      this.sessions.set(id, session); this.project(session); return this.state(id);
    }

    project(session) {
      if (!session?.frame) return false;
      session.frame.src = `${session.url}${sourceFragment(session.page, session.mode, session.zoom)}`;
      if (session.frame.dataset) {
        session.frame.dataset.pdfViewerId = session.viewerId;
        session.frame.dataset.pdfMode = session.mode;
        session.frame.dataset.pdfPage = String(session.page);
        session.frame.dataset.pdfZoom = String(session.zoom);
      }
      return true;
    }

    setPage(viewerId, page) { const session = this.sessions.get(viewerId); if (!session) return null; session.page = finitePage(page); this.project(session); return this.state(viewerId); }
    fitWidth(viewerId) { return this.setMode(viewerId, "fit-width"); }
    fitPage(viewerId) { return this.setMode(viewerId, "fit-page"); }
    reset(viewerId) { const session = this.sessions.get(viewerId); if (!session) return null; session.mode = "custom"; session.zoom = 100; this.project(session); return this.state(viewerId); }
    zoomIn(viewerId) { return this.setZoom(viewerId, (this.sessions.get(viewerId)?.zoom || 100) + ZOOM_STEP); }
    zoomOut(viewerId) { return this.setZoom(viewerId, (this.sessions.get(viewerId)?.zoom || 100) - ZOOM_STEP); }
    setMode(viewerId, mode) { const session = this.sessions.get(viewerId); if (!session || !MODES.has(mode)) return null; session.mode = mode; this.project(session); return this.state(viewerId); }
    setZoom(viewerId, zoom) { const session = this.sessions.get(viewerId); if (!session) return null; session.mode = "custom"; session.zoom = finiteZoom(zoom); this.project(session); return this.state(viewerId); }

    state(viewerId) {
      const value = this.sessions.get(viewerId); if (!value) return null;
      return { viewerId: value.viewerId, url: value.url, page: value.page, mode: value.mode, zoom: value.zoom, returnTabId: value.returnTabId, scrollTop: value.scrollTop, highlight: value.highlight };
    }

    close(viewerId, { clearFrame = true } = {}) {
      const session = this.sessions.get(viewerId); if (!session) return null;
      session.observer?.disconnect?.();
      if (clearFrame && session.frame) session.frame.removeAttribute?.("src");
      this.sessions.delete(viewerId);
      return { returnTabId: session.returnTabId, returnFocus: session.returnFocus, scrollTop: session.scrollTop, highlight: session.highlight };
    }
  }

  globalThis.AutoResearchFusionPDF = Object.freeze({ FusionPdfController, safePDFURL, MIN_ZOOM, MAX_ZOOM, ZOOM_STEP });
})();
