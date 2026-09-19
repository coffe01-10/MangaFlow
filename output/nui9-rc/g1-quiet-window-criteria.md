# P5-2 G1「安静测量窗口」口径草案（AUTH-REQ：请 lead 确认后执行 N=20）

G1 当前状态：仪器化完成（首帧挂真实呈现回调，PR #987 落地），**可测但无达标线**。本草案定义「安静窗口」与样本规则，lead 确认后即可跑严格首帧 N=20。

## 安静窗口定义（全部满足才开测）

1. 进程面：MangaFlow.Native / native-host / node / python(除本测量脚本) 全部不在进程表；sidecar 已 `stop` 且 `issues: none`。
2. 负载面：开测前 5 分钟内本机无 dotnet build / npm install / npm run check / 浏览器重负载；测前 30 秒 CPU 总占用 < 10%（Get-Counter 采样 6 次 × 5s）。
3. 温度面：无要求（台式机无 ARM 降频顾虑），但同轮 20 样本须连续完成，中途不得插入其他任务。
4. 磁场面：测量脚本与被测 exe 同盘（D:），测量期间不做任何文件写操作（evidence 落盘移到轮次结束后）。

## 首帧定义（收窄）

- **只计「壳窗口首帧可见」**：`App.xaml.cs` 呈现回调首次触发（PR #987 的仪器点）。
- **不含** native-host / web API 的 provisioning 完成时间——那是「数据就绪」，不是首帧；NUI-8 轮冷热口径的教训（首帧被子进程树 provisioning 主导，热比冷还慢）正是没有做这个切分。

## 采样与剔除规则

- 冷启 N=20：每样本前 Kill 进程树（IsProcessInJob 验证归属），间隔 5s。
- 样本全保留、不剔除（沿用 NUI-8 口径「不挑好的报」）；但每样本附注两组宿主负载读数（采样窗内 CPU % 与 WorkingSet 增量）。
- 受扰标注：单样本期间若系统 CPU > 30% 持续 > 1s，标 `DISTURBED`，统计时分列报告（P50/P95 各算一遍 全样本 / 非 DISTURBED）。
- 达标线：**本轮不设**。等两轮安静窗口数据落地后由 lead 定（建议方向：P95 ≤ 某绝对值 + DISTURBED 占比上限）。

## 执行前置

- AUTH-REQ 确认本口径 → 直接可跑（脚本 `measure-native-startup.ps1` 已具备回调等待与落盘）。
