/* Pure review-queue-v1 projection. No DOM, navigation, storage or requests. */
(() => {
  "use strict";
  const visualFields = [
    "display_name", "label", "caption", "context_explanation", "conditions_text",
    "methods_text", "source_page", "page_start", "page_end", "source_locator",
    "source_excerpt", "physical_quantities", "materials", "variables", "tags",
  ];
  const fields = {
    item: new Set(["value_text", "meaning", "unit", "context_explanation", "source_page",
      "source_locator", "source_excerpt", "evidence_type", "source_precision"]),
    finding: new Set(["finding_text", "value_text", "meaning", "context_explanation",
      "source_page", "source_locator", "source_excerpt"]),
    table: new Set(visualFields),
    figure: new Set(visualFields),
  };
  const scoreKeys = ["agreement", "factuality", "completeness", "evidence", "overall"];

  function create({cleanText, reviewQueueRoute, safeError, fieldLabel}) {
    function cleanValue(value, depth = 0) {
      if (depth > 3) return null;
      if (value === null || typeof value === "boolean" ||
          typeof value === "number" && Number.isFinite(value)) return value;
      if (typeof value === "string") return cleanText(value, 2000);
      if (Array.isArray(value)) return value.slice(0, 32).map(item => cleanValue(item, depth + 1));
      if (value && typeof value === "object") {
        return Object.fromEntries(Object.entries(value).slice(0, 32)
          .map(([key, item]) => [cleanText(key, 120), cleanValue(item, depth + 1)])
          .filter(([key]) => key));
      }
      return null;
    }

    function candidate(raw, type) {
      if (!raw || typeof raw !== "object" || Array.isArray(raw) || !Object.hasOwn(fields, type)) return null;
      const result = {};
      for (const key of fields[type]) {
        if (!Object.hasOwn(raw, key)) continue;
        const value = cleanValue(raw[key]);
        if (value !== null) result[key] = value;
      }
      return result;
    }

    function projectItem(item, index) {
      const type = cleanText(item?.entity_type, 20);
      const paperUid = cleanText(item?.paper_uid, 80);
      const token = cleanText(item?.review_token, 140);
      const scores = item?.scores, operations = item?.allowed_operations;
      if (!Object.hasOwn(fields, type) || !/^paper_[0-9a-f]{32}$/.test(paperUid) ||
          !/^rq_[A-Za-z0-9_-]{32,96}$/.test(token) || !Number.isFinite(item?.expires_at) ||
          item.source_scope !== "workspace" || item.source_id !== "workspace" ||
          !item.paper || typeof item.paper !== "object" || !scores || typeof scores !== "object" ||
          !operations || typeof operations !== "object") return null;
      const safeScores = {};
      for (const key of scoreKeys) {
        const value = scores[key];
        if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 100) return null;
        safeScores[key] = value / 100;
      }
      const primary = candidate(item.candidate, type), alternate = candidate(item.alternate, type);
      if (!primary) return null;
      const previewURL = item.preview?.image_url;
      if (previewURL !== undefined && (!["figure", "table"].includes(type) ||
          previewURL !== `${reviewQueueRoute}/${token}/image`)) return null;
      return {
        previewURL: previewURL || "", reviewToken: token, expiresAt: Number(item.expires_at),
        sourceScope: "workspace", sourceId: "workspace", paperUid,
        paper: {title: cleanText(item.paper.title, 1200), doi: cleanText(item.paper.doi, 500)},
        entityType: type, candidate: primary, alternate,
        conflictReason: cleanText(item.conflict_reason, 2000), scores: safeScores,
        allowedOperations: {
          approve: operations.approve?.available === true,
          reject: operations.reject?.available === true,
          correct: operations.correct?.available === true,
        },
        reviewOrdinal: index,
      };
    }

    function project(raw) {
      if (raw?.schema_version !== "review-queue-v1" || raw.source_scope !== "workspace" ||
          raw.source_id !== "workspace" || !Number.isInteger(raw.total) || raw.total < 0 ||
          raw.total > 500 || !Array.isArray(raw.items) || raw.items.length > 500) return null;
      const items = raw.items.map(projectItem);
      if (items.some(item => item === null) || raw.total !== items.length) return null;
      return {schemaVersion: "review-queue-v1", total: raw.total, items};
    }

    function editableFields(item) {
      if (!Object.hasOwn(fields, item.entityType)) return [];
      return Object.keys(item.candidate).filter(key => fields[item.entityType].has(key));
    }

    function correctionPayload(item, entries) {
      const allowed = new Set(editableFields(item)), result = {};
      for (const {field, value, complex} of entries) {
        if (!allowed.has(field)) throw safeError("review_queue_invalid", "纠正字段无效。");
        const raw = String(value ?? "");
        if (complex) {
          try { result[field] = JSON.parse(raw); }
          catch { throw safeError("review_queue_invalid", `${fieldLabel(field)} 的格式无效。`); }
        } else result[field] = raw;
      }
      if (!Object.keys(result).length) throw safeError("review_queue_invalid", "没有可纠正的公开字段。");
      return result;
    }

    return Object.freeze({project, editableFields, correctionPayload});
  }
  globalThis.AutoResearchReviewQueueContract = Object.freeze({create});
})();
