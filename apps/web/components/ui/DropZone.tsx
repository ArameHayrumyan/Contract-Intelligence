"use client";

import { useRef, useState, type DragEvent } from "react";

interface DropZoneProps {
  onFile: (file: File) => void;
  accept?: string;
  maxSizeMB?: number;
  height?: number;
}

function formatSize(bytes: number): string {
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`;
}

/**
 * Drag-and-drop upload zone. The native <input> is visually hidden and clicked
 * programmatically, so no localized browser file-picker text ever shows.
 */
export function DropZone({
  onFile,
  accept = "application/pdf",
  maxSizeMB = 50,
  height = 180,
}: DropZoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [selected, setSelected] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);

  function validate(file: File): boolean {
    const isPdf =
      file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
    if (!isPdf) {
      setError("Only PDF files are accepted.");
      return false;
    }
    if (file.size > maxSizeMB * 1024 * 1024) {
      setError(`File exceeds the ${maxSizeMB} MB limit.`);
      return false;
    }
    return true;
  }

  function accept_(file: File) {
    setError(null);
    if (!validate(file)) {
      setSelected(null);
      return;
    }
    setSelected(file);
    onFile(file);
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) accept_(file);
  }

  return (
    <div>
      <div
        className={`dropzone${dragOver ? " is-dragover" : ""}${
          error ? " is-error" : ""
        }`}
        style={{ minHeight: height }}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          className="dropzone__hidden-input"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) accept_(file);
          }}
        />
        {dragOver ? (
          <strong>Drop to upload</strong>
        ) : selected ? (
          <>
            <FileIcon />
            <strong>{selected.name}</strong>
            <span className="dropzone__sub">{formatSize(selected.size)}</span>
            <span className="link-btn">Change file</span>
          </>
        ) : (
          <>
            <UploadIcon />
            <strong>Drag a PDF here or click to browse</strong>
            <span className="dropzone__sub">Max {maxSizeMB} MB · PDF only</span>
          </>
        )}
      </div>
      {error ? (
        <p className="error" style={{ marginTop: "var(--space-2)" }}>
          {error}
        </p>
      ) : null}
    </div>
  );
}

function UploadIcon() {
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M12 16V4m0 0L7 9m5-5l5 5M4 20h16"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
function FileIcon() {
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M14 3H6v18h12V8z M14 3v5h4"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  );
}
