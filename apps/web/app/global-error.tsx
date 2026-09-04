"use client";

// Last-resort boundary for errors that escape the root layout itself.
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="zh-CN">
      <body style={{ margin: 0, fontFamily: "system-ui, sans-serif", padding: "48px 24px", textAlign: "center" }}>
        <h2>应用出现异常</h2>
        <p>
          页面发生未处理的错误{error.digest ? `（追踪码 ${error.digest}）` : ""}。刷新后未保存的界面状态会丢失，
          服务端数据不受影响。
        </p>
        <button type="button" onClick={() => reset()} style={{ padding: "8px 20px", cursor: "pointer" }}>
          重试
        </button>
      </body>
    </html>
  );
}
