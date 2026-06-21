"use client";

import { useCallback, useEffect, useState } from "react";

import {
  createAnnotation,
  deleteAnnotation,
  listAnnotations,
  updateAnnotation,
} from "@/lib/api-client";
import type {
  AnnotationResponse,
  AnnotationTarget,
  AnnotationType,
} from "@/lib/types";

interface AnnotationPanelProps {
  documentId: string;
  targetType: AnnotationTarget;
  targetReference?: string;
  /** Skip the initial fetch if the parent already loaded these. */
  initialAnnotations?: AnnotationResponse[];
}

const TYPE_LABELS: Record<AnnotationType, string> = {
  accepted_risk: "Accepted risk",
  escalate_to_legal: "Escalate to legal",
  disputed: "Disputed",
  requires_negotiation: "Requires negotiation",
  false_positive: "False positive",
  custom: "Custom",
};

const TYPES = Object.keys(TYPE_LABELS) as AnnotationType[];

/** Format an ISO timestamp as a coarse "x ago" string. */
function relativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} minute${mins === 1 ? "" : "s"} ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

/** Reusable annotation thread for a document, clause, or deviation target. */
export function AnnotationPanel({
  documentId,
  targetType,
  targetReference,
  initialAnnotations,
}: AnnotationPanelProps) {
  const [items, setItems] = useState<AnnotationResponse[]>(
    initialAnnotations ?? [],
  );
  const [loading, setLoading] = useState(!initialAnnotations);
  const [error, setError] = useState(false);

  const [note, setNote] = useState("");
  const [type, setType] = useState<AnnotationType>("custom");
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editNote, setEditNote] = useState("");
  const [editType, setEditType] = useState<AnnotationType>("custom");

  const load = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      setItems(await listAnnotations(documentId, targetType, targetReference));
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [documentId, targetType, targetReference]);

  useEffect(() => {
    if (!initialAnnotations) void load();
  }, [load, initialAnnotations]);

  async function onSubmit() {
    if (note.trim().length < 10) {
      setFormError("Note must be at least 10 characters.");
      return;
    }
    setSubmitting(true);
    setFormError(null);
    try {
      const created = await createAnnotation(documentId, {
        target_type: targetType,
        target_reference: targetReference ?? null,
        annotation_type: type,
        note: note.trim(),
      });
      setItems((prev) => [created, ...prev]);
      setNote("");
      setType("custom");
    } catch {
      setFormError("Could not save the note. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  async function onSaveEdit(id: string) {
    if (editNote.trim().length < 10) return;
    const updated = await updateAnnotation(documentId, id, editType, editNote.trim());
    setItems((prev) => prev.map((a) => (a.id === id ? updated : a)));
    setEditingId(null);
  }

  async function onDelete(id: string) {
    if (!window.confirm("Delete this note?")) return;
    await deleteAnnotation(documentId, id);
    setItems((prev) => prev.filter((a) => a.id !== id));
  }

  if (loading) {
    return (
      <div className="annotation-panel">
        <div className="skeleton-line" />
        <div className="skeleton-line" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="annotation-panel">
        <p className="muted">Could not load annotations.</p>
        <button className="link-btn" onClick={() => void load()}>
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="annotation-panel">
      {items.length === 0 ? (
        <p className="muted">
          No annotations yet. Be the first to leave a review note.
        </p>
      ) : (
        items.map((a) => (
          <div key={a.id} className={`annotation ann--${a.annotation_type}`}>
            {editingId === a.id ? (
              <div>
                <select
                  value={editType}
                  onChange={(e) => setEditType(e.target.value as AnnotationType)}
                >
                  {TYPES.map((t) => (
                    <option key={t} value={t}>
                      {TYPE_LABELS[t]}
                    </option>
                  ))}
                </select>
                <textarea
                  value={editNote}
                  onChange={(e) => setEditNote(e.target.value)}
                />
                <div className="annotation-actions">
                  <button className="link-btn" onClick={() => void onSaveEdit(a.id)}>
                    Save
                  </button>
                  <button className="link-btn" onClick={() => setEditingId(null)}>
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <>
                <span className={`pill ann-pill--${a.annotation_type}`}>
                  {TYPE_LABELS[a.annotation_type]}
                </span>
                <p>{a.note}</p>
                <div className="annotation-meta muted">
                  Recorded by {a.actor} · {relativeTime(a.created_at)}
                  <span className="annotation-actions">
                    <button
                      className="link-btn"
                      title="Edit"
                      onClick={() => {
                        setEditingId(a.id);
                        setEditNote(a.note);
                        setEditType(a.annotation_type);
                      }}
                    >
                      ✏️
                    </button>
                    <button
                      className="link-btn"
                      title="Delete"
                      onClick={() => void onDelete(a.id)}
                    >
                      🗑️
                    </button>
                  </span>
                </div>
              </>
            )}
          </div>
        ))
      )}

      <div className="annotation-form">
        <select
          value={type}
          onChange={(e) => setType(e.target.value as AnnotationType)}
        >
          {TYPES.map((t) => (
            <option key={t} value={t}>
              {TYPE_LABELS[t]}
            </option>
          ))}
        </select>
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Add your review note here..."
          maxLength={2000}
        />
        <div className="annotation-form-foot">
          {formError ? <span className="error">{formError}</span> : <span />}
          <span className="muted char-count">{note.length} / 2000</span>
        </div>
        <button className="btn" onClick={() => void onSubmit()} disabled={submitting}>
          {submitting ? "Saving…" : "Save Note"}
        </button>
      </div>
    </div>
  );
}
