import { AppShell, GlobalNav } from "@/components/shell";
import { ArrowRight, BookOpenText, Boxes, CircleHelp, CloudCog, ListChecks, Workflow } from "lucide-react";
import Link from "next/link";

const stages = [
  ["01", "导入原作", "粘贴文本或上传 TXT / Markdown，系统保留每次修订。", BookOpenText],
  ["02", "建立参考资产", "分别建立角色、服装和漫画风格档案，并绑定参考图。", Boxes],
  ["03", "编排与生产", "生成剧本和动态分页，逐页抽卡、收藏并采用满意版本。", Workflow],
  ["04", "检查与导出", "在任务中心查看进度，检查连续性后导出 PNG、PDF 或 JSON。", ListChecks],
] as const;

export default function HelpPage() {
  return (
    <AppShell>
      <header className="topbar site-topbar">
        <Link href="/" className="site-brand"><span>漫</span><div><small>MANGAFLOW / GUIDE</small><strong>使用帮助</strong></div></Link>
        <GlobalNav />
      </header>
      <main className="help-page">
        <section className="help-hero"><span className="section-index">HELP / 01</span><CircleHelp size={32} /><h1>把复杂的漫画生产，<br /><em>拆成可确认的每一步。</em></h1><p>项目侧栏就是完整生产路径。每个入口都拥有独立网址，可以刷新、收藏和通过浏览器前进后退。</p></section>
        <section className="help-stage-grid">{stages.map(([index, title, detail, Icon]) => <article key={index}><span>{index}</span><Icon size={22} /><h2>{title}</h2><p>{detail}</p></article>)}</section>
        <section className="help-troubleshoot"><div><CloudCog size={22} /><span>VERTEX AI / 故障排查</span><h2>凭据显示断开时怎么办？</h2></div><ol><li>打开“设置”，确认凭据文件仍存在。</li><li>先运行无付费的连接诊断，再按需验证文本模型。</li><li>网络波动会显示为连接降级，不会清除服务端配置。</li></ol><Link className="button ink" href="/settings">打开系统设置 <ArrowRight size={15} /></Link></section>
      </main>
    </AppShell>
  );
}
