import ProjectWorkspace, { type AssetWorkspaceView } from "@/components/project-workspace";
import { notFound } from "next/navigation";

const assetViews = new Set<AssetWorkspaceView>([
  "characters",
  "outfits",
  "style",
  "references",
]);

export default async function AssetViewPage({
  params,
}: {
  params: Promise<{ view: string }>;
}) {
  const { view } = await params;
  if (!assetViews.has(view as AssetWorkspaceView)) notFound();
  return <ProjectWorkspace section="assets" assetView={view as AssetWorkspaceView} />;
}
