"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { UploadFlow } from "@/components/UploadFlow";

interface UploadModalProps {
  isOpen: boolean;
  onClose: () => void;
  onUploaded?: () => void;
}

/** Upload modal wrapping the shared UploadFlow. */
export function UploadModal({ isOpen, onClose, onUploaded }: UploadModalProps) {
  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Upload a Contract" size="lg">
      <UploadFlow onUploaded={onUploaded} onClose={onClose} />
    </Modal>
  );
}

/** Standalone trigger button + modal, for quick reuse in headers/nav. */
export function UploadButton({
  label = "Upload Contract",
  variant = "primary",
  onUploaded,
}: {
  label?: string;
  variant?: "primary" | "secondary" | "ghost";
  onUploaded?: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant={variant} onClick={() => setOpen(true)}>
        {label}
      </Button>
      <UploadModal
        isOpen={open}
        onClose={() => setOpen(false)}
        onUploaded={onUploaded}
      />
    </>
  );
}
