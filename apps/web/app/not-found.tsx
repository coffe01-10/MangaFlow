import Link from "next/link";

// 无自定义 not-found 时，非法 section/view（notFound()）与未匹配路由都会
// 落到 Next 内置 404：白底英文、无全局导航，与全应用 zh-CN 风格断裂。
// 保持与 error.tsx 相同的恢复路径形态。
export default function NotFound() {
  return (
    <main className="error-boundary" role="alert">
      <h2>页面不存在</h2>
      <p>
        你访问的地址不存在或已被移动。工作区数据仍保存在服务端，可以返回首页或项目列表继续。
      </p>
      <div className="error-boundary-actions">
        <Link className="button ink" href="/">返回首页</Link>
        <Link className="button outline" href="/workflow">打开工作流</Link>
      </div>
    </main>
  );
}
