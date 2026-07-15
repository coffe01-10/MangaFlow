"use client";

import { AppShell } from "@/components/shell";
import { WorkflowEditor } from "@/components/workflow-editor";

export default function WorkflowPage() {
  return (
    <AppShell>
      <WorkflowEditor />
    </AppShell>
  );
}
