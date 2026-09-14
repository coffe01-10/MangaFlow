using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using MangaFlow.Native;
using MangaFlow.Native.Controls;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

internal static class NativeUsagePageChecks
{
    internal static void Run(string output)
    {
        Directory.CreateDirectory(output);
        var previous = (string)typeof(KeyValueStore).GetField("path", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        var prefs = Path.Combine(output, "test-prefs.json"); File.WriteAllText(prefs, "{}"); KeyValueStore.UseLocation(prefs);
        try
        {
            Exception? failure = null;
            var thread = new Thread(() =>
            {
                var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
                app.Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("/MangaFlow.Native;component/Theme.xaml", UriKind.Relative) });
                SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
                app.Dispatcher.BeginInvoke(new Action(async () =>
                {
                    try
                    {
                        await Checks(output);
                        foreach (var method in new[] { "UsageBudgetChecks", "UsageRenderChecks" })
                            typeof(NativeConsistencyChecks).GetMethod(method, BindingFlags.NonPublic | BindingFlags.Static)!.Invoke(null, null);
                    }
                    catch (Exception e) { failure = e; }
                    finally { app.Shutdown(); }
                }));
                app.Run();
            });
            thread.SetApartmentState(ApartmentState.STA); thread.Start(); thread.Join();
            if (failure != null) throw new Exception("Usage page checks failed", failure);
        }
        finally { KeyValueStore.UseLocation(previous); File.Delete(prefs); File.Delete(prefs + ".tmp"); }
    }

    private static async Task Checks(string output)
    {
        var fixture=new Fixture();
        using var api=new ApiClient("http://127.0.0.1:12345",fixture);
        var view=new UsageView(); string destination="";
        try
        {
            view.Activate(new WorkspaceContext { Api=api,State=new(),Window=null!,Project=null,
                NavigateSection=(section,_)=>{destination=section;return Task.CompletedTask;},OpenDashboard=()=>{destination="home";return Task.CompletedTask;} });
            await Until(()=>Field<UsageKpiPanel>(view,"kpiRow").Children.Count==4);
            var opened = new List<string>();
            view.AttemptDetailOverride = attempt => opened.Add(attempt.Text("id"));
            foreach(int width in new[]{1440,1240,760,360})
            {
                Layout(view,width,1400);
                Fits(Field<UsageKpiPanel>(view,"kpiRow"));
                foreach(var heading in Desc(view).OfType<PageHeading>())Fits(heading);
                var horizontal=Desc(view).OfType<ScrollViewer>().Where(s=>s.HorizontalScrollBarVisibility==ScrollBarVisibility.Auto).ToArray();
                Require(horizontal.Length>=3,"tables own horizontal scrollbars");
                if(width==360)Require(horizontal.Any(s=>s.ScrollableWidth>0),"narrow tables remain horizontally accessible");
                var canvas=Field<StackPanel>(view,"trendHost").Children.OfType<Canvas>().Single();
                Require(canvas.ActualWidth<=width && canvas.Children.OfType<System.Windows.Shapes.Rectangle>().Any(),"native trend fits viewport and renders bars");
                Render(view,width,width==360?1600:1200,Path.Combine(output,$"native-usage-{width}.png"));
            }
            var groups=Field<JsonElement>(view,"summary").Array("groups");
            Click(Desc(view).OfType<Button>().Single(b=>Equals(b.Content,"详情")));
            Require(opened.SequenceEqual(new[]{"a1"}),"detail button routes the selected attempt exactly once");
            var (days,series)=UsageView.BuildTrend(groups,"amount");
            Require(series.SequenceEqual(new[]{"CNY","USD"}),"currencies remain separate series");
            Require(days.Single(d=>d.Day=="2026-09-10").Calls==0 && days.Single(d=>d.Day=="2026-09-10").Values.Values.All(v=>v==0),"calendar gap is factual zero");
            Require(days.Single(d=>d.Day=="2026-09-12").Values["USD"]==null,"calls without estimates remain unknown");
            Require(days.Single(d=>d.Day=="2026-09-09").Values["CNY"]==60 && days.Single(d=>d.Day=="2026-09-09").Values["USD"]==2,"string money sums by currency without billed totals");
            var tokens=UsageView.BuildTrend(groups,"tokens");
            Require(tokens.Days.Single(d=>d.Day=="2026-09-12").Values["Codex CLI"]==null,"unmeasured token series stays unknown");
            Click(Desc(view).OfType<Button>().Single(b=>Equals(b.Content,"按 Token 数量")));
            Layout(view,1240,1000);Require(Field<string>(view,"trendMetric")=="tokens","token switch");
            Render(view,1240,1000,Path.Combine(output,"native-usage-tokens.png"));
            Click(Desc(view).OfType<Button>().Single(b=>Equals(b.Content,"按生图张数")));
            Layout(view,1240,1000);Require(Field<string>(view,"trendMetric")=="images","image switch");
            var data=Desc(view).OfType<Expander>().Single(); data.IsExpanded=true;
            Render(view,1240,1100,Path.Combine(output,"native-usage-images-data.png"));
            Click(Desc(view).OfType<Button>().Single(b=>Equals(b.Content,"设置预算")));
            Layout(view,760,1000);
            TextBox Named(string name)=>Desc(view).OfType<TextBox>().Single(b=>System.Windows.Automation.AutomationProperties.GetName(b)==name);
            Named("预算币种").Text="CNY";Named("预算金额").Text="50";
            Click(Desc(Field<StackPanel>(view,"budgetHost")).OfType<Button>().Single(b=>Equals(b.Content,"保存")));
            Require(KeyValueStore.Get("mangaflow.usage-budget")!.Contains("50"),"budget saved locally");
            Require(Field<StackPanel>(view,"budgetHost").Children.OfType<Border>().Single().Tag as string=="over","budget compares only selected currency estimate");
            Layout(view,360,1200);Fits(Desc(view).OfType<UsageBudgetRow>().Single());
            fixture.FailMore=true;
            await Invoke(view,"LoadMoreAsync");
            // A14：分页错误就地显示在明细表尾，不再只写页面顶部。
            Require(Field<UsageAttemptFeed>(view,"feed").Items.Count==1
                && Desc(Field<StackPanel>(view,"attemptsTable")).OfType<TextBlock>().Any(t=>t.Text.Contains("加载更多失败"))
                && !Field<TextBlock>(view,"summaryLine").Text.Contains("加载更多失败"),
                "pagination failure reports inline and preserves rows");
            fixture.FailMore=false; await Invoke(view,"LoadMoreAsync");
            Require(Field<UsageAttemptFeed>(view,"feed").Items.Count==2 && fixture.MoreReads==2,"load-more retry appends once");
            Layout(view,1240,1000);Field<ScrollViewer>(view,"scroller").ScrollToEnd();Layout(view,1240,1000);
            Render(view,1240,1000,Path.Combine(output,"native-usage-tables.png"));
            var channel=Field<ComboBox>(view,"channelSelector");
            channel.SelectedIndex=2;await Until(()=>fixture.LastAttemptQuery.Contains("channel=CLI"));
            // A10：汇总请求同样携带通道筛选，统计卡/趋势/明细口径一致。
            Require(fixture.LastSummaryQuery.Contains("channel=CLI"),"summary follows the selected channel (A10)");

            // A12：维度选项来自独立 facets（不随当前汇总结果增减），模型跟随供应商联动。
            string[] Options(ComboBox box)=>box.Items.OfType<ComboBoxItem>().Select(i=>(string?)i.Tag).Where(t=>t!.Length>0).ToArray()!;
            var provider=Field<ComboBox>(view,"providerSelector");
            Require(Options(provider).SequenceEqual(new[]{"Codex CLI","Google"}),"provider options come from facets (A12)");
            Require(Options(Field<ComboBox>(view,"modelSelector")).SequenceEqual(new[]{"codex-imagegen","gemini-3.1-flash-image"}),
                "model options cover every provider by default (A12)");
            provider.SelectedItem=provider.Items.OfType<ComboBoxItem>().Single(i=>(string?)i.Tag=="Google");
            await Until(()=>Options(Field<ComboBox>(view,"modelSelector")).SequenceEqual(new[]{"gemini-3.1-flash-image"}));
            Require((Field<ComboBox>(view,"modelSelector").SelectedItem as ComboBoxItem)!.Tag as string=="",
                "provider switch clears the incompatible model selection (A12)");
            provider.SelectedItem=provider.Items.OfType<ComboBoxItem>().Single(i=>(string?)i.Tag=="");
            await Until(()=>Options(Field<ComboBox>(view,"modelSelector")).Length==2);

            // A13：项目列表读取失败不中止用量数据、不伪装成“没有项目”，可独立重试。
            fixture.FailProjects=true;
            await Invoke(view,"LoadProjectsAsync");
            Require(Field<StackPanel>(view,"dimensionBar").Visibility==Visibility.Visible
                && Desc(view).OfType<TextBlock>().Any(t=>t.Text.Contains("筛选选项读取失败")&&t.Text.Contains("项目")),
                "project read failure surfaces with a retry (A13)");
            Require(Field<ComboBox>(view,"projectSelector").Items.OfType<ComboBoxItem>().Count()==2,
                "existing project options survive the failure (A13)");
            await Invoke(view,"LoadAsync");
            Require(Field<UsageKpiPanel>(view,"kpiRow").Children.Count==4,"usage data still loads while projects fail (A13)");
            fixture.FailProjects=false;
            Click(Desc(view).OfType<Button>().Single(b=>Equals(b.Content,"重试项目")));
            await Until(()=>Field<StackPanel>(view,"dimensionBar").Visibility==Visibility.Collapsed);

            var since=Field<DatePicker>(view,"sinceDate");var until=Field<DatePicker>(view,"untilDate");
            since.SelectedDate=new DateTime(2026,9,13);until.SelectedDate=new DateTime(2026,9,12);
            int reads=fixture.SummaryReads;Field<ComboBox>(view,"rangeSelector").SelectedIndex=3;
            Require(fixture.SummaryReads==reads && Field<TextBlock>(view,"summaryLine").Text.Contains("失败"),"invalid date range does not request HTTP");
            since.SelectedDate=new DateTime(2026,9,1);await Invoke(view,"LoadAsync");
            fixture.Empty=true;await Invoke(view,"LoadAsync");Layout(view,1240,900);
            Render(view,1240,900,Path.Combine(output,"native-usage-empty.png"));
            Require(Field<TextBlock>(view,"summaryLine").Text.Contains("暂无调用记录"),"empty state");
            // A14：汇总失败不阻止明细分区渲染，失败分区就地提示并提供重试。
            fixture.Fail=true;fixture.Empty=false;await Invoke(view,"LoadAsync");
            Require(Field<TextBlock>(view,"summaryLine").Text.Contains("用量汇总读取失败"),"failed summary read feedback");
            Require(Field<UsageAttemptFeed>(view,"feed").Items.Count==1,$"attempts partition rows (got {Field<UsageAttemptFeed>(view,"feed").Items.Count})");
            Require(Field<Button>(view,"summaryRetry").Visibility==Visibility.Visible,"summary retry appears (A14)");
            fixture.Fail=false;fixture.FailAttempts=true;await Invoke(view,"LoadAsync");
            Require(Field<UsageKpiPanel>(view,"kpiRow").Children.Count==4,"summary partition renders while attempts fail (A14)");
            Require(Desc(Field<StackPanel>(view,"attemptsTable")).OfType<TextBlock>().Any(t=>t.Text.Contains("调用明细读取失败")),
                "attempts failure shows an inline error (A14)");
            fixture.FailAttempts=false;
            Click(Desc(view).OfType<Button>().Single(b=>Equals(b.Content,"重试明细")));
            await Until(()=>Field<UsageAttemptFeed>(view,"feed").Items.Count==1);
            Require(Field<UsageKpiPanel>(view,"kpiRow").Children.Count==4,"reload recovers populated state");
            Click(Desc(view).OfType<Button>().Single(b=>Equals(b.Content,"设置首页")));Require(destination=="settings","settings link");
            Click(Desc(view).OfType<Button>().Single(b=>Equals(b.Content,"返回项目")));Require(destination=="home","home link");
            var csv=typeof(UsageView).GetMethod("Csv",BindingFlags.NonPublic|BindingFlags.Static)!;
            Require((string)csv.Invoke(null,new object[]{"=1+1"})! == "'=1+1","existing CSV formula guard");

            // A11：CSV 序列化契约——估算每币种一行、账单第二区块、转义与公式
            // 前缀守卫、未知金额不写成零、文化无关的小数格式。
            var buildCsv=typeof(UsageView).GetMethod("BuildUsageCsv",BindingFlags.NonPublic|BindingFlags.Static)!;
            var amountText=typeof(UsageView).GetMethod("AmountText",BindingFlags.NonPublic|BindingFlags.Static)!;
            using var csvSummary=JsonDocument.Parse(
                """{"groups":[{"day":"2026-09-09","provider":"=SUM(A1)","model_id":"model,with\"quote","channel":"HTTP_API","attempt_count":3,"succeeded_count":2,"failed_count":1,"pending_count":0,"input_tokens":100,"output_tokens":50,"estimated_costs":[{"currency":"CNY","amount":"60.00"},{"currency":"USD","amount":2.5}],"usage_status_counts":{"COMPLETE":2}}],"billed":[{"period_start":"2026-09-01","period_end":"2026-09-14","provider":"Google","model_id":"gemini","channel":"HTTP_API","billing_account_id":"acct-1","currency":"CNY","billed_amount":"100.00","entered_by":"ops","source_note":"发票对账"}]}""");
            var csvText=(string)buildCsv.Invoke(null,new object[]{csvSummary.RootElement})!;
            var lines=csvText.Split("\r\n");
            Require(lines[0].Split(',').Length==15,"csv keeps 15 estimate columns (A11)");
            Require(csvText.Contains("60.00")&&csvText.Contains("2.5"),"per-currency rows keep the original amount format");
            Require(csvText.Contains("'=SUM(A1)"),"formula prefix guarded in provider cells (A11)");
            Require(csvText.Contains("\"model,with\"\"quote\""),"commas and quotes escaped (A11)");
            var blank=Array.IndexOf(lines,"");
            Require(blank==3&&lines[blank+1].StartsWith("类型,账期开始"),"billed block follows a blank separator line (A11)");
            Require(lines[blank+2].Contains("账单对账")&&lines[blank+2].Contains("100.00"),"billed reconciliation row serialized (A11)");
            using var billedOnly=JsonDocument.Parse(
                """{"groups":[],"billed":[{"period_start":"2026-09-01","period_end":"2026-09-14","provider":"Google","model_id":"gemini","channel":"HTTP_API","billing_account_id":null,"currency":"CNY","billed_amount":null,"entered_by":"ops","source_note":null}]}""");
            var billedCsv=(string)buildCsv.Invoke(null,new object[]{billedOnly.RootElement})!;
            Require(!billedCsv.Contains("无估算数据"),"billed-only export skips estimate placeholder rows (A11)");
            Require((string)amountText.Invoke(null,new object[]{billedOnly.RootElement.GetProperty("billed")[0],"billed_amount"})=="",
                "unknown billed amount stays empty, never zero (A11)");
            typeof(UsageView).GetMethod("RenderBilled",BindingFlags.NonPublic|BindingFlags.Instance)!.Invoke(view,
                new object[]{billedOnly.RootElement.GetProperty("billed").EnumerateArray().ToList()});
            Require(Desc(Field<StackPanel>(view,"billedTable")).OfType<TextBlock>().Any(t=>t.Text=="金额未知"),
                "unknown billed amount is not rendered as zero (A11)");
            var culture=System.Globalization.CultureInfo.CurrentCulture;
            System.Threading.Thread.CurrentThread.CurrentCulture=System.Globalization.CultureInfo.GetCultureInfo("de-DE");
            try
            {
                using var numberAmount=JsonDocument.Parse("""{"amount":1234.56}""");
                Require((string)amountText.Invoke(null,new object[]{numberAmount.RootElement,"amount"})=="1234.56",
                    "csv numeric amounts are culture-invariant (A11)");
            }
            finally{System.Threading.Thread.CurrentThread.CurrentCulture=culture;}
            Console.WriteLine("PASS: usage layouts, 4 KPIs, three trend metrics, currency isolation, calendar gaps, unknown values, budget edit, paginated inline retry, channel parity for summary+attempts (A10), facets dimensions with provider linkage (A12), project retry (A13), partitioned summary/attempts errors (A14), csv contract incl. billed block/escaping/invariant amounts (A11), invalid dates, empty/reload and navigation.");
            Console.WriteLine("HTTP fixtures/offscreen WPF only. Real backend, CSV file dialog/write, detail modal, native high-DPI and frame timing NOT RUN.");
        }
        finally{view.Deactivate();}
    }
    private static Task Invoke(object view,string method)=>(Task)view.GetType().GetMethod(method,BindingFlags.Instance|BindingFlags.NonPublic)!.Invoke(view,null)!;
    private static void Fits(Panel panel)
    {
        var boxes = panel.Children.Cast<FrameworkElement>().Where(c => c.Visibility != Visibility.Collapsed)
            .Select(c => c.TransformToAncestor(panel).TransformBounds(new Rect(c.RenderSize))).ToArray();
        Require(boxes.All(b => b.Left >= -1 && b.Right <= panel.ActualWidth + 1), $"child escaped {panel.GetType().Name} width {panel.ActualWidth}");
        for (int i = 0; i < boxes.Length; i++) for (int j = i + 1; j < boxes.Length; j++)
        {
            var overlap = Rect.Intersect(boxes[i], boxes[j]);
            Require(overlap.IsEmpty || overlap.Width <= 1 || overlap.Height <= 1, "layout children overlap");
        }
    }
    private static IEnumerable<DependencyObject> Desc(DependencyObject e) => NativeParityChecks.Descendants(e);
    private static void Click(ButtonBase b) => b.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
    private static T Field<T>(object e, string name) => (T)e.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(e)!;
    private static void Require(bool ok, string message) { if (!ok) throw new Exception(message); }
    private static async Task Until(Func<bool> predicate) { using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(8)); while (!predicate()) await Task.Delay(10, timeout.Token); }
    private static void Layout(FrameworkElement e, int w, int h) { foreach (var child in Desc(e).OfType<UIElement>()) child.InvalidateMeasure(); e.InvalidateMeasure(); e.Measure(new Size(w, h)); e.Arrange(new Rect(0, 0, w, h)); e.UpdateLayout(); }
    private static void Render(FrameworkElement e, int w, int h, string path) { Layout(e, w, h); var bitmap = new RenderTargetBitmap(w, h, 96, 96, PixelFormats.Pbgra32); bitmap.Render(e); var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap)); using var f = File.Create(path); encoder.Save(f); }
    private sealed class Fixture:HttpMessageHandler
    {
        internal bool Fail,FailMore,Empty,FailProjects,FailAttempts;
        internal int SummaryReads,MoreReads;
        internal string LastAttemptQuery="",LastSummaryQuery="";
        private static object Group(string day,string provider,string model,int count,long? tokens,long? images,object[] costs)=>new {
            day,provider,model_id=model,channel=provider=="Codex CLI"?"CLI":"HTTP_API",attempt_count=count,succeeded_count=count-1,failed_count=1,pending_count=0,
            input_tokens=tokens,output_tokens=tokens/2,cached_input_tokens=tokens/4,output_images=images,estimated_costs=costs,usage_status_counts=new{COMPLETE=count-1,UNKNOWN=1} };
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken token)
        {
            Require(request.Method==HttpMethod.Get,"usage UI must not mutate backend");
            var path=request.RequestUri!.AbsolutePath;
            if(path.EndsWith("/projects"))
            {
                if(FailProjects)return Task.FromResult(Response("{\"detail\":\"项目服务暂时不可用\"}",HttpStatusCode.ServiceUnavailable));
                return Task.FromResult(Response("[{\"id\":\"project-1\",\"name\":\"雨夜来信\"}]"));
            }
            if(path.EndsWith("/summary"))
            {
                SummaryReads++;LastSummaryQuery=request.RequestUri.Query;
                if(Fail)return Task.FromResult(Response("{\"detail\":\"服务暂时不可用\"}",HttpStatusCode.ServiceUnavailable));
                object[] groups=Empty?[]:[
                    Group("2026-09-09","Google","gemini-3.1-flash-image",5,1200,2,[new{currency="CNY",amount="60.00"},new{currency="USD",amount="2.00"}]),
                    Group("2026-09-11","Google","gemini-3.1-flash-image",3,800,1,[new{currency="CNY",amount="20.00"}]),
                    Group("2026-09-12","Codex CLI","codex-imagegen",2,null,null,[]) ];
                object[] billed=Empty?[]:[new{provider="Google",model_id="gemini-3.1-flash-image",period_start="2026-09-01",period_end="2026-09-14",currency="CNY",billed_amount="100.00"}];
                return Task.FromResult(Response(JsonSerializer.Serialize(new{groups,billed})));
            }
            if(path.EndsWith("/attempts"))
            {
                LastAttemptQuery=request.RequestUri.Query;
                if(FailAttempts)return Task.FromResult(Response("{\"detail\":\"明细服务暂时不可用\"}",HttpStatusCode.ServiceUnavailable));
                bool more=request.RequestUri.Query.Contains("cursor=");
                if(more){MoreReads++;if(FailMore)return Task.FromResult(Response("{\"detail\":\"分页暂时失败\"}",HttpStatusCode.ServiceUnavailable));}
                object[] items=Empty?[]:[new{id=more?"a2":"a1",started_at="2026-09-12T12:34:00Z",channel=more?"CLI":"HTTP_API",
                    provider=more?"Codex CLI":"Google",model_id=more?"codex-imagegen":"gemini-3.1-flash-image",dispatch_no=1,
                    outcome=more?"FAILED":"SUCCEEDED",input_tokens=more?(int?)null:1200,output_tokens=more?(int?)null:600,output_images=more?(int?)null:1,route_switched=more}];
                return Task.FromResult(Response(JsonSerializer.Serialize(new{items,next_cursor=more||Empty?(string?)null:"older"})));
            }
            throw new Exception("unexpected usage request: "+path);
        }
        private static HttpResponseMessage Response(string json,HttpStatusCode status=HttpStatusCode.OK)=>new(status){Content=new StringContent(json)};
    }
}
