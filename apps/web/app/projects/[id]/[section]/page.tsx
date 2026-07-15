import ProjectWorkspace, { type WorkspaceSection } from "@/components/project-workspace";
import { notFound } from "next/navigation";

const workspaceSections = new Set<WorkspaceSection>([
  "source",
  "assets",
  "script",
  "storyboard",
  "generate",
  "library",
  "jobs",
]);

export default async function ProjectSectionPage({ params }: { params: Promise<{ section: string }> }) {
  const { section } = await params;
  if (!workspaceSections.has(section as WorkspaceSection)) notFound();
  return <ProjectWorkspace section={section as WorkspaceSection} />;
}
