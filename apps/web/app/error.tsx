"use client";

// Route-segment error boundary: an uncaught render error used to unmount the
// segment and leave a blank screen, taking every unsaved editor state with
// it. Keep the recovery path explicit and loss-free where possible.
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main className="error-boundary" role="alert">
      <h2>界面出现异常</h2>
      <p>
        当前视图渲染失败{error.digest ? `（追踪码 ${error.digest}）` : ""}。工作区其他数据仍保存在服务端；
        你可以重试当前视图，或刷新页面后继续。
      </p>
      <div className="error-boundary-actions">
        <button type="button" className="button ink" onClick={() => reset()}>
          重试当前视图
        </button>
        <button type="button" className="button outline" onClick={() => window.location.reload()}>
          刷新页面
        </button>
      </div>
    </main>
  );
}
