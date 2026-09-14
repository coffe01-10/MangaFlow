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
            Require(Field<UsageAttemptFeed>(view,"feed").Items.Count==1 && Field<TextBlock>(view,"summaryLine").Text.Contains("加载更多失败"),"pagination failure preserves rows");
            fixture.FailMore=false; await Invoke(view,"LoadMoreAsync");
            Require(Field<UsageAttemptFeed>(view,"feed").Items.Count==2 && fixture.MoreReads==2,"load-more retry appends once");
            Layout(view,1240,1000);Field<ScrollViewer>(view,"scroller").ScrollToEnd();Layout(view,1240,1000);
            Render(view,1240,1000,Path.Combine(output,"native-usage-tables.png"));
            var channel=Field<ComboBox>(view,"channelSelector");
            channel.SelectedIndex=2;await Until(()=>fixture.LastAttemptQuery.Contains("channel=CLI"));
            var since=Field<DatePicker>(view,"sinceDate");var until=Field<DatePicker>(view,"untilDate");
            since.SelectedDate=new DateTime(2026,9,13);until.SelectedDate=new DateTime(2026,9,12);
            int reads=fixture.SummaryReads;Field<ComboBox>(view,"rangeSelector").SelectedIndex=3;
            Require(fixture.SummaryReads==reads && Field<TextBlock>(view,"summaryLine").Text.Contains("失败"),"invalid date range does not request HTTP");
            since.SelectedDate=new DateTime(2026,9,1);await Invoke(view,"LoadAsync");
            fixture.Empty=true;await Invoke(view,"LoadAsync");Layout(view,1240,900);
            Render(view,1240,900,Path.Combine(output,"native-usage-empty.png"));
            Require(Field<TextBlock>(view,"summaryLine").Text.Contains("暂无调用记录"),"empty state");
            fixture.Fail=true;await Invoke(view,"LoadAsync");
            Require(Field<TextBlock>(view,"summaryLine").Text.Contains("加载失败"),"failed read feedback");
            fixture.Fail=false;fixture.Empty=false;await Invoke(view,"LoadAsync");
            Require(Field<UsageKpiPanel>(view,"kpiRow").Children.Count==4,"reload recovers populated state");
            Click(Desc(view).OfType<Button>().Single(b=>Equals(b.Content,"设置首页")));Require(destination=="settings","settings link");
            Click(Desc(view).OfType<Button>().Single(b=>Equals(b.Content,"返回项目")));Require(destination=="home","home link");
            var csv=typeof(UsageView).GetMethod("Csv",BindingFlags.NonPublic|BindingFlags.Static)!;
            Require((string)csv.Invoke(null,new object[]{"=1+1"})! == "'=1+1","existing CSV formula guard");
            Console.WriteLine("PASS: usage 1440/1240/760/360 layouts, 4 KPIs, three trend metrics, currency isolation, calendar gaps, unknown values, budget edit, paginated retry, invalid dates, empty/error/reload and navigation.");
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
        internal bool Fail,FailMore,Empty;
        internal int SummaryReads,MoreReads;
        internal string LastAttemptQuery="";
        private static object Group(string day,string provider,string model,int count,long? tokens,long? images,object[] costs)=>new {
            day,provider,model_id=model,channel=provider=="Codex CLI"?"CLI":"HTTP_API",attempt_count=count,succeeded_count=count-1,failed_count=1,pending_count=0,
            input_tokens=tokens,output_tokens=tokens/2,cached_input_tokens=tokens/4,output_images=images,estimated_costs=costs,usage_status_counts=new{COMPLETE=count-1,UNKNOWN=1} };
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken token)
        {
            Require(request.Method==HttpMethod.Get,"usage UI must not mutate backend");
            var path=request.RequestUri!.AbsolutePath;
            if(path.EndsWith("/projects"))return Task.FromResult(Response("[{\"id\":\"project-1\",\"name\":\"雨夜来信\"}]"));
            if(path.EndsWith("/summary"))
            {
                SummaryReads++;
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
