using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Shapes;
using MangaFlow.Native.Controls;

namespace MangaFlow.Native.Views;

public sealed partial class UsageView
{
    private static readonly string[] TrendColors = ["#d34a2f","#3f6d4e","#3f5e8c","#a8842c","#6d4a7e"];
    private FrameworkElement BuildTrendActions()
    {
        var actions = new WrapPanel();
        foreach (var (key,label) in new[] { ("amount","按估算金额"),("tokens","按 Token 数量"),("images","按生图张数") })
        {
            var button = Kit.Act(label, (_,_) => { trendMetric=key; RenderTrend(summary.Array("groups")); }, "Compact");
            button.Tag=key; button.Margin=new Thickness(6,0,0,4); trendButtons.Add(button); actions.Children.Add(button);
        }
        return actions;
    }
    internal sealed class TrendDay(string day)
    {
        internal string Day { get; } = day;
        internal int Calls;
        internal Dictionary<string,double?> Values { get; } = new();
    }
    internal static (List<TrendDay> Days,string[] Series) BuildTrend(List<JsonElement> groups,string metric)
    {
        var byDay = new SortedDictionary<string,TrendDay>(StringComparer.Ordinal);
        foreach(var group in groups)
        {
            string day=group.Text("day"); if(!byDay.TryGetValue(day,out var row))byDay[day]=row=new TrendDay(day);
            row.Calls+=group.Number("attempt_count");
            if(metric=="amount")
                foreach(var cost in group.Array("estimated_costs")) {var key=cost.Text("currency");row.Values[key]=(row.Values.GetValueOrDefault(key)??0)+Money(cost);}
            else
            {
                var key=metric=="tokens"?group.Text("provider"):"images";
                bool known=metric=="tokens"?group.Element("input_tokens").ValueKind==JsonValueKind.Number||group.Element("output_tokens").ValueKind==JsonValueKind.Number
                    :group.Element("output_images").ValueKind==JsonValueKind.Number;
                double measured=metric=="tokens"?(SumKnown([group],"input_tokens")??0)+(SumKnown([group],"output_tokens")??0):(SumKnown([group],"output_images")??0);
                if(known)row.Values[key]=(row.Values.GetValueOrDefault(key)??0)+measured;
                else if(!row.Values.ContainsKey(key))row.Values[key]=null;
            }
        }
        if(byDay.Count>1 && DateOnly.TryParse(byDay.Keys.First(),out var first)&&DateOnly.TryParse(byDay.Keys.Last(),out var last))
            for(var date=first;date<last;date=date.AddDays(1)){var key=date.ToString("yyyy-MM-dd");if(!byDay.ContainsKey(key))byDay[key]=new TrendDay(key);}
        var series=metric=="amount"?groups.SelectMany(g=>g.Array("estimated_costs")).Select(c=>c.Text("currency")).Distinct().Order().ToArray()
            :metric=="tokens"?groups.Select(g=>g.Text("provider")).Distinct().Order().ToArray():groups.Count>0?new[]{"images"}:[];
        foreach(var row in byDay.Values)foreach(var key in series)
            if(!row.Values.ContainsKey(key))row.Values[key]=row.Calls==0||metric!="amount"?0:null;
        return (byDay.Values.ToList(),series);
    }
    private void RenderTrend(List<JsonElement> groups)
    {
        trendHost.Children.Clear();
        foreach(var button in trendButtons)
        {
            bool active=Equals(button.Tag,trendMetric);
            button.Background=AssetPageUi.Brush(active?"Ink":"Surface");button.Foreground=AssetPageUi.Brush(active?"Surface":"Muted");
            System.Windows.Automation.AutomationProperties.SetHelpText(button,active?"当前趋势度量":"切换趋势度量");
        }
        var (days,series)=BuildTrend(groups,trendMetric);
        if(series.Length==0){trendHost.Children.Add(Kit.Caption(trendMetric=="amount"?"所选范围内无估算金额记录":"所选范围内无用量记录。"));return;}
        string Label(string key)=>trendMetric=="amount"?$"{Symbol(key)} {key}":key=="images"?"生成图片":key;
        string Value(string key,double? value)=>value is not {} n?"未知":trendMetric=="amount"?$"{key} {n:0.00}":$"{n:N0}"+(trendMetric=="images"?" 张":" Tokens");
        var legend=new WrapPanel { Margin=new Thickness(0,0,0,12) };
        for(int i=0;i<series.Length;i++)
        {
            var item=new StackPanel { Orientation=Orientation.Horizontal,Margin=new Thickness(0,0,16,6) };
            item.Children.Add(new Rectangle { Width=10,Height=10,Fill=(Brush)new BrushConverter().ConvertFromString(TrendColors[i%TrendColors.Length])!,Margin=new Thickness(0,0,6,0) });
            item.Children.Add(new TextBlock { Text=Label(series[i]),FontSize=12 });legend.Children.Add(item);
        }
        trendHost.Children.Add(legend);
        var canvas=new Canvas { Height=154,ClipToBounds=true };
        void Draw()
        {
            canvas.Children.Clear(); double w=Math.Max(1,canvas.ActualWidth),slot=w/days.Count,segment=slot/series.Length;
            var baseline=new Line { X1=0,X2=w,Y1=128,Y2=128,Stroke=AssetPageUi.Brush("Line"),StrokeThickness=1 };canvas.Children.Add(baseline);
            for(int d=0;d<days.Count;d++)
            {
                var row=days[d];
                for(int k=0;k<series.Length;k++)
                {
                    string key=series[k]; var value=row.Values.GetValueOrDefault(key); if(value is not >0)continue;
                    double max=Math.Max(1,days.Max(r=>r.Values.GetValueOrDefault(key)??0)),height=Math.Max(2,value.Value/max*120);
                    var bar=new Rectangle { Width=segment*.7,Height=height,Fill=(Brush)new BrushConverter().ConvertFromString(TrendColors[k%TrendColors.Length])!,
                        ToolTip=$"{row.Day} · {Label(key)} · {Value(key,value)}" };
                    Canvas.SetLeft(bar,d*slot+k*segment+segment*.15);Canvas.SetTop(bar,128-height);canvas.Children.Add(bar);
                }
                if(row.Calls>0&&row.Values.Values.All(v=>v==null))
                {
                    var unknown=new Rectangle {Width=slot*.24,Height=8,Fill=AssetPageUi.Brush("LineDark"),ToolTip=$"{row.Day} · 有 {row.Calls} 次调用，但用量未知"};
                    Canvas.SetLeft(unknown,d*slot+slot*.38);Canvas.SetTop(unknown,120);canvas.Children.Add(unknown);
                }
                int step=Math.Max(1,(int)Math.Ceiling(58/slot));
                if(d%step==0){var text=new TextBlock {Text=row.Day.Length>=10?row.Day[5..10]:row.Day,FontSize=11,Foreground=AssetPageUi.Brush("Muted")};
                    Canvas.SetLeft(text,d*slot);Canvas.SetTop(text,136);canvas.Children.Add(text);}
            }
        }
        canvas.SizeChanged+=(_,_)=>Draw();trendHost.Children.Add(canvas);
        var data=new StackPanel();
        var widths=Enumerable.Repeat(150d,series.Length+1).ToArray();
        data.Children.Add(TableRow(new[]{"日期"}.Concat(series.Select(Label)).ToArray(),widths,true));
        foreach(var day in days)data.Children.Add(TableRow(new[]{day.Day}.Concat(series.Select(key=>day.Calls==0?"0（无调用）":Value(key,day.Values.GetValueOrDefault(key)))).ToArray(),widths));
        var expander=new Expander {Header="图表数据表（无障碍）",Style=(Style)FindResource("UsageDataDisclosure"),Margin=new Thickness(0,8,0,0),
            Content=new ScrollViewer {Content=data,HorizontalScrollBarVisibility=ScrollBarVisibility.Auto,VerticalScrollBarVisibility=ScrollBarVisibility.Disabled}};
        trendHost.Children.Add(expander);
        trendHost.Children.Add(new TextBlock {Text=trendMetric=="amount"?"金额按币种独立刻度展示，柱高仅用于比较各币种自身趋势；未做汇率换算，不同币种不相加。":"未知表示未返回可用计量，未知不等于 0。",
            FontSize=12,Foreground=AssetPageUi.Brush("Muted"),TextWrapping=TextWrapping.Wrap,Margin=new Thickness(0,10,0,0)});
    }
}
