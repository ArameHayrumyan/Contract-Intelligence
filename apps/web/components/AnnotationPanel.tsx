"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
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

const TYPE_LABELS: Record<AnnotationType, string> = {
  accepted_risk: "Accepted risk",
  escalate_to_legal: "Escalate to legal",
  disputed: "Disputed",
  requires_negotiation: "Requires negotiation",
  false_positive: "False positive",
  custom: "Custom",
};
const TYPES = Object.keys(TYPE_LABELS) as AnnotationType[];

function relativeTime(iso: string): string {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

interface AnnotationPanelProps {
  documentId: string;
  targetType: AnnotationTarget;
  targetReference?: string;
  initialAnnotations?: AnnotationResponse[];
  compact?: boolean;
}

/** Self-contained annotation thread for a document, clause, or deviation. */
export function AnnotationPanel({
  documentId,
  targetType,
  targetReference,
  initialAnnotations,
  compact = false,
}: AnnotationPanelProps) {
  const { toast } = useToast();
  const [items, setItems] = useState<AnnotationResponse[]>(initialAnnotations ?? []);
  const [loading, setLoading] = useState(!initialAnnotations);
  const [failed, setFailed] = useState(false);
  const [showAll, setShowAll] = useState(false);

  const [note, setNote] = useState("");
  const [type, setType] = useState<AnnotationType>("custom");
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editNote, setEditNote] = useState("");
  const [editType, setEditType] = useState<AnnotationType>("custom");

  const load = useCallback(async () => {
    setLoading(true);
    setFailed(false);
    try {
      setItems(await listAnnotations(documentId, targetType, targetReference));
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, [documentId, targetType, targetReference]);

  useEffect(() => {
    if (!initialAnnotations) void load();
  }, [load, initialAnnotations]);

  async function submit() {
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
      toast("Note saved.", "success");
    } catch {
      setFormError("Could not save the note.");
    } finally {
      setSubmitting(false);
    }
  }

  async function saveEdit(id: string) {
    if (editNote.trim().length < 10) return;
    const updated = await updateAnnotation(documentId, id, editType, editNote.trim());
    setItems((prev) => prev.map((a) => (a.id === id ? updated : a)));
    setEditingId(null);
  }

  async function remove(id: string) {
    if (!window.confirm("Delete this note?")) return;
    await deleteAnnotation(documentId, id);
    setItems((prev) => prev.filter((a) => a.id !== id));
    toast("Note deleted.", "info");
  }

  if (loading) {
    return (
      <div>
        <Skeleton height={14} />
        <Skeleton height={14} width="70%" />
      </div>
    );
  }
  if (failed) {
    return (
      <div className="muted">
        Could not load annotations.{" "}
        <button className="link-btn" onClick={() => void load()}>
          Retry
        </button>
      </div>
    );
  }

  const visible = compact && !showAll ? items.slice(0, 2) : items;

  return (
    <div>
      {items.length === 0 ? (
        <p className="muted">No annotations yet. Be the first to leave a note.</p>
      ) : (
        visible.map((a) => (
          <div
            key={a.id}
            className="annotation"
            style={{ borderLeftColor: `var(--color-annotation-${a.annotation_type})` }}
          >
            {editingId === a.id ? (
              <div className="stack" style={{ gap: "var(--space-2)" }}>
                <select
                  className="select"
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
                  className="textarea"
                  value={editNote}
                  onChange={(e) => setEditNote(e.target.value)}
                />
                <div className="row">
                  <button className="link-btn" onClick={() => void saveEdit(a.id)}>
                    Save
                  </button>
                  <button className="link-btn" onClick={() => setEditingId(null)}>
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <>
                <span
                  className="badge badge--xs"
                  style={{
                    color: `var(--color-annotation-${a.annotation_type})`,
                    borderColor: `var(--color-annotation-${a.annotation_type})`,
                  }}
                >
                  {TYPE_LABELS[a.annotation_type]}
                </span>
                <p style={{ margin: "var(--space-2) 0 0" }}>{a.note}</p>
                <div className="annotation__meta">
                  <span>
                    {a.actor} · {relativeTime(a.created_at)}
                  </span>
                  <span className="row" style={{ gap: "var(--space-2)" }}>
                    <button
                      className="link-btn"
                      title="Edit"
                      onClick={() => {
                        setEditingId(a.id);
                        setEditNote(a.note);
                        setEditType(a.annotation_type);
                      }}
                    >
                      Edit
                    </button>
                    <button
                      className="link-btn"
                      title="Delete"
                      onClick={() => void remove(a.id)}
                    >
                      Delete
                    </button>
                  </span>
                </div>
              </>
            )}
          </div>
        ))
      )}

      {compact && items.length > 2 ? (
        <button className="link-btn" onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Show fewer" : `View all ${items.length}`}
        </button>
      ) : null}

      <div className="annotation-form">
        <select
          className="select"
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
          className="textarea"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Add your review note here..."
          maxLength={2000}
        />
        <div className="annotation-form__foot">
          {formError ? <span className="error">{formError}</span> : <span />}
          <span className="muted">{note.length} / 2000</span>
        </div>
        <Button size="sm" onClick={submit} loading={submitting}>
          Save Note
        </Button>
      </div>
    </div>
  );
}
