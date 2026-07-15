import WorkflowStudio from "@/components/workflow-studio";

export default async function ProjectWorkflowPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <WorkflowStudio projectId={id} />;
}
