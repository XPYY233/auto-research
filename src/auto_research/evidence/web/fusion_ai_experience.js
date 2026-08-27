(function (global) {
  "use strict";

  const SCHEMA_VERSION = "fusion-ai-experience-v1";
  const SCOPES = Object.freeze({
    literature_extraction: Object.freeze({
      host: "#fusion-literature-progress",
      stages: Object.freeze([
        ["prepare", 5, "检查论文与提取计划"],
        ["authorization", 12, "等待你确认发送范围与预算"],
        ["initial_focus", 24, "双分支提取四类候选"],
        ["coverage_gap", 42, "补查遗漏证据"],
        ["coverage_verification", 58, "核对覆盖率与来源页"],
        ["adversarial_branches", 73, "比较冲突候选"],
        ["third_review", 84, "处理低置信与冲突结果"],
        ["publishing", 94, "原子发布并刷新搜索索引"],
        ["completed", 100, "提取、发布与索引完成"],
      ]),
    }),
    personal_suggestion: Object.freeze({
      host: "#fusion-personal-progress",
      stages: Object.freeze([
        ["prepare", 8, "整理有界表头与样例"],
        ["authorization", 20, "等待你确认发送范围与预算"],
        ["analyzing", 64, "分析列角色、意义与单位"],
        ["applying", 90, "将建议写入待核验表单"],
        ["completed", 100, "AI 建议已就绪，等待人工核验"],
      ]),
    }),
    librarian: Object.freeze({
      host: "#fusion-librarian-progress",
      stages: Object.freeze([
        ["prepare", 8, "理解研究问题与约束"],
        ["authorization", 16, "等待你确认检索范围与预算"],
        ["planner", 36, "规划检索并召回四类证据"],
        ["plan_ready", 52, "检索计划与候选已经就绪"],
        ["synthesis", 76, "核对引用并组织研究回答"],
        ["organizing", 92, "生成回答、证据卡片与推荐"],
        ["completed", 100, "回答与引用已经就绪"],
      ]),
    }),
    selected_evidence_chat: Object.freeze({
      host: "#fusion-evidence-ai-progress",
      stages: Object.freeze([
        ["prepare", 10, "冻结当前证据与允许的上下文"],
        ["authorization", 22, "等待你确认本轮发送范围"],
        ["reasoning", 68, "围绕当前证据进行核验与解释"],
        ["organizing", 92, "整理回答与来源说明"],
        ["completed", 100, "本轮回答完成"],
      ]),
    }),
  });
  const STATES = new Set(["idle", "running", "waiting", "success", "error", "cancelled"]);

  function clean(value, limit = 1000) {
    return typeof value === "string" ? value.trim().slice(0, limit) : "";
  }

  class AIExperienceController {
    constructor(documentObject = global.document) {
      this.document = documentObject;
      this.snapshots = new Map();
      this.activities = new Map();
      this.startedAt = new Map();
      this.timers = new Map();
    }

    definition(scope) {
      return Object.prototype.hasOwnProperty.call(SCOPES, scope) ? SCOPES[scope] : null;
    }

    stage(scope, stageName) {
      const definition = this.definition(scope);
      if (!definition) return null;
      const row = definition.stages.find(([name]) => name === stageName);
      return row ? { name: row[0], progress: row[1], label: row[2] } : null;
    }

    update(scope, value = {}) {
      const definition = this.definition(scope);
      if (!definition) return false;
      const stage = this.stage(scope, value.stage) || this.stage(scope, "prepare");
      const state = STATES.has(value.state) ? value.state : "running";
      const progress = Number.isFinite(value.progress)
        ? Math.max(0, Math.min(100, Number(value.progress)))
        : stage.progress;
      const snapshot = Object.freeze({
        schema_version: SCHEMA_VERSION,
        scope,
        state,
        stage: stage.name,
        progress,
        label: clean(value.label, 500) || stage.label,
        detail: clean(value.detail, 1000),
      });
      this.snapshots.set(scope, snapshot);
      const host = this.document?.querySelector?.(definition.host);
      if (!host) return true;
      host.hidden = state === "idle";
      host.dataset.state = state;
      host.setAttribute("aria-busy", state === "running" ? "true" : "false");
      const label = host.querySelector?.("[data-ai-progress-label]");
      const detail = host.querySelector?.("[data-ai-progress-detail]");
      const bar = host.querySelector?.("[data-ai-progress-bar]");
      const valueNode = host.querySelector?.("[data-ai-progress-value]");
      if (label) label.textContent = snapshot.label;
      if (detail) {
        detail.textContent = snapshot.detail;
        detail.hidden = !snapshot.detail;
      }
      if (bar) {
        bar.style.width = `${snapshot.progress}%`;
        bar.parentElement?.setAttribute?.("aria-valuenow", String(Math.round(snapshot.progress)));
      }
      if (valueNode) valueNode.textContent = `${Math.round(snapshot.progress)}%`;
      host.querySelectorAll?.("[data-ai-stage]").forEach((node) => {
        const row = this.stage(scope, node.dataset.aiStage);
        const reached = row && row.progress <= snapshot.progress;
        node.classList.toggle("active", node.dataset.aiStage === snapshot.stage);
        node.classList.toggle("done", reached && node.dataset.aiStage !== snapshot.stage);
      });
      if (["success", "error", "cancelled"].includes(state)) this.stopTimer(scope);
      return true;
    }

    begin(scope) {
      const definition = this.definition(scope);
      if (!definition) return false;
      this.stopTimer(scope);
      this.activities.set(scope, []);
      this.startedAt.set(scope, Date.now());
      const host = this.document?.querySelector?.(definition.host);
      const list = host?.querySelector?.("[data-ai-activity]");
      if (list) list.replaceChildren();
      this.renderElapsed(scope);
      if (host && typeof global.setInterval === "function") {
        const timer = global.setInterval(() => this.renderElapsed(scope), 1000);
        timer?.unref?.();
        this.timers.set(scope, timer);
      }
      return true;
    }

    stopTimer(scope) {
      const timer = this.timers.get(scope);
      if (timer !== undefined) global.clearInterval?.(timer);
      this.timers.delete(scope);
      this.renderElapsed(scope);
    }

    renderElapsed(scope) {
      const started = this.startedAt.get(scope);
      const host = this.document?.querySelector?.(this.definition(scope)?.host || "");
      const elapsed = host?.querySelector?.("[data-ai-elapsed]");
      if (elapsed) elapsed.textContent = `${started ? Math.max(0, Math.floor((Date.now() - started) / 1000)) : 0} 秒`;
    }

    activity(scope, raw = {}) {
      const definition = this.definition(scope);
      if (!definition || raw?.schema_version !== "ai-activity-event-v1") return false;
      const code = clean(raw.code, 96);
      const stage = clean(raw.stage, 64);
      const label = clean(raw.label, 240);
      const detail = clean(raw.detail, 300);
      const jobId = clean(raw.job_id, 180);
      const sequence = Number(raw.sequence);
      if (!code || !stage || !label || !jobId || !Number.isSafeInteger(sequence) || sequence < 1) return false;
      const key = `${jobId}:${sequence}`;
      const events = this.activities.get(scope) || [];
      if (events.some((event) => event.key === key)) return true;
      const recognizedStage = this.stage(scope, stage);
      if (recognizedStage) {
        const state = code === "execution_completed" ? "success" : code === "execution_failed" ? "error" : "running";
        this.update(scope, { stage, state, label, detail });
      }
      events.push(Object.freeze({ key, code, stage, label, detail }));
      if (events.length > 20) events.splice(0, events.length - 20);
      this.activities.set(scope, events);
      const host = this.document?.querySelector?.(definition.host);
      const list = host?.querySelector?.("[data-ai-activity]");
      if (list) {
        list.replaceChildren();
        for (const event of events) {
          const item = this.document.createElement("li");
          item.dataset.aiActivityCode = event.code;
          const mark = this.document.createElement("i");
          mark.setAttribute("aria-hidden", "true");
          const body = this.document.createElement("span");
          const title = this.document.createElement("strong");
          title.textContent = event.label;
          body.append(title);
          if (event.detail) {
            const note = this.document.createElement("small");
            note.textContent = event.detail;
            body.append(note);
          }
          item.append(mark, body);
          list.append(item);
        }
        list.scrollTop = list.scrollHeight;
      }
      const labelNode = host?.querySelector?.("[data-ai-progress-label]");
      if (labelNode && !["execution_completed", "execution_failed"].includes(code)) labelNode.textContent = label;
      this.renderElapsed(scope);
      return true;
    }

    reset(scope) {
      this.stopTimer(scope);
      this.snapshots.delete(scope);
      this.activities.delete(scope);
      this.startedAt.delete(scope);
      const host = this.document?.querySelector?.(this.definition(scope)?.host || "");
      if (host) {
        host.hidden = true;
        host.dataset.state = "idle";
        host.setAttribute("aria-busy", "false");
      }
      return true;
    }

    renderConversation(hostOrSelector, messages, options = {}) {
      const host = typeof hostOrSelector === "string"
        ? this.document?.querySelector?.(hostOrSelector)
        : hostOrSelector;
      if (!host) return false;
      host.replaceChildren();
      const safeMessages = Array.isArray(messages) ? messages.slice(-40) : [];
      if (!safeMessages.length && options.busy !== true) {
        const welcome = this.document.createElement("div");
        welcome.className = "fusion-chat-welcome";
        const title = this.document.createElement("strong");
        title.textContent = clean(options.emptyTitle, 160) || "开始新的对话";
        const text = this.document.createElement("p");
        text.textContent = clean(options.emptyText, 1000);
        welcome.append(title, text);
        host.append(welcome);
      }
      for (const raw of safeMessages) {
        if (!raw || !["user", "assistant"].includes(raw.role)) continue;
        const article = this.document.createElement("article");
        article.className = "fusion-chat-message";
        article.dataset.role = raw.role;
        const avatar = this.document.createElement("span");
        avatar.className = "fusion-chat-avatar";
        avatar.textContent = raw.role === "assistant" ? clean(options.assistantMark, 4) || "AI" : "你";
        const body = this.document.createElement("div");
        const label = this.document.createElement("strong");
        label.textContent = raw.role === "assistant" ? clean(options.assistantLabel, 80) || "Auto Research AI" : "你";
        const content = this.document.createElement("p");
        content.textContent = clean(raw.content, 50000);
        body.append(label, content);
        article.append(avatar, body);
        host.append(article);
      }
      if (options.busy === true) {
        const busy = this.document.createElement("article");
        busy.className = "fusion-chat-message fusion-chat-thinking";
        busy.dataset.role = "assistant";
        const avatar = this.document.createElement("span");
        avatar.className = "fusion-chat-avatar";
        avatar.textContent = clean(options.assistantMark, 4) || "AI";
        const body = this.document.createElement("div");
        const label = this.document.createElement("strong");
        label.textContent = clean(options.busyLabel, 120) || "正在处理当前问题";
        const dots = this.document.createElement("span");
        dots.className = "fusion-thinking-dots";
        dots.setAttribute("aria-label", "处理中");
        dots.textContent = "•••";
        body.append(label, dots);
        busy.append(avatar, body);
        host.append(busy);
      }
      global.requestAnimationFrame?.(() => {
        host.scrollTop = host.scrollHeight;
      });
      return true;
    }
  }

  global.AutoResearchAIExperience = Object.freeze({
    schemaVersion: SCHEMA_VERSION,
    scopes: SCOPES,
    AIExperienceController,
  });
})(globalThis);
