using System.IO;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using MangaFlow.Native;
using MangaFlow.Native.Services;
using MangaFlow.Native.Views;

internal static class NativeButtonChecks
{
    public static async Task Run(string output)
    {
        foreach (var key in new[] { "Pill", "Chip", "AssetTab", "ModelChoice" })
        {
            var control = new ToggleButton { Content = "筛选选项", Style = (Style)Application.Current.FindResource(key) };
            foreach (var selected in new[] { false, true })
            {
                control.IsChecked = selected; Layout(control, 320, 80);
                Require(NativeParityChecks.Descendants(control).OfType<Border>().All(b => b.CornerRadius.TopLeft <= 3 && b.CornerRadius.BottomRight <= 3), key + " still has an oval surface");
            }
        }
        var projects = new[] { new ProjectItem("one", "雨夜来信", "", 0, 0), new ProjectItem("two", "山海之间", "", 0, 0) };
        var picker = new ComboBox { ItemsSource = projects, DisplayMemberPath = "Name", SelectedIndex = 1,
            Style = (Style)Application.Current.FindResource(typeof(ComboBox)), VerticalAlignment = VerticalAlignment.Top };
        Layout(picker, 190, 60);
        var text = string.Join(" ", NativeParityChecks.Descendants(picker).OfType<TextBlock>().Select(t => t.Text));
        Require(text.Contains("山海之间") && !text.Contains("ProjectItem") && picker.ActualHeight < 60, "project picker printed the record instead of DisplayMemberPath");

        using var api = new ApiClient("http://127.0.0.1:12345", new FixtureHandler());
        var view = new AssetsView();
        view.Activate(new WorkspaceContext { Api = api, Cache = new(), State = new(), Window = null!, Project = projects[0],
            NavigateSection = (_, _) => Task.CompletedTask, OpenDashboard = () => Task.CompletedTask });
        try
        {
            using var timeout = new CancellationTokenSource(3000);
            ModelPickerBand? band;
            do
            {
                await Task.Delay(5, timeout.Token); Layout(view, 1060, 800);
                band = NativeParityChecks.Descendants(view).OfType<ModelPickerBand>().FirstOrDefault();
            } while (band == null);
            var choices = NativeParityChecks.Descendants(band).OfType<ToggleButton>().ToList();
            Require(choices.Count == 2 && choices.All(c => c.ActualHeight >= 68), "model choices are not the web rectangular cards");
            choices[1].RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            Require(band.Selected == "model-b" && choices[1].IsChecked == true && choices[0].IsChecked == false, "model card click lost exclusive selection");
            var tabs = (Dictionary<string, ToggleButton>)typeof(AssetsView).GetField("tabs", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(view)!;
            tabs[AssetsView.Outfits].RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            Require(tabs[AssetsView.Outfits].IsChecked == true && tabs[AssetsView.Characters].IsChecked == false, "underlined tabs lost navigation selection");
            tabs[AssetsView.Characters].RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            tabs[AssetsView.Characters].IsChecked = false;
            tabs[AssetsView.Characters].RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
            Require(tabs[AssetsView.Characters].IsChecked == true, "clicking the active asset tab cleared its underline");
            foreach (var width in new[] { 650, 1060 })
            {
                Layout(view, width, 800);
                foreach (var tab in tabs.Values)
                {
                    var surface = (Border)tab.Template.FindName("Surface", tab);
                    Require(surface.BorderThickness.Bottom == 3 && tab.ActualHeight >= 44, "asset navigation lost its web underline or hit target");
                }
                var bitmap = new RenderTargetBitmap(width, 800, 96, 96, PixelFormats.Pbgra32);
                var background = new DrawingVisual();
                using (var drawing = background.RenderOpen()) drawing.DrawRectangle((Brush)Application.Current.FindResource("Paper"), null, new Rect(0, 0, width, 800));
                bitmap.Render(background); bitmap.Render(view);
                var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap));
                using var file = File.Create(Path.Combine(output, $"native-assets-rectangular-{width}.png")); encoder.Save(file);
            }
        }
        finally { view.Deactivate(); }
        Console.WriteLine("PASS: rectangular toggles, web asset underlines/model cards, exclusive selection and project display names");
    }
    private static void Layout(FrameworkElement view, int width, int height) { view.Measure(new Size(width, height)); view.Arrange(new Rect(0, 0, width, height)); view.UpdateLayout(); }
    private static void Require(bool value, string message) { if (!value) throw new Exception(message); }
    private sealed class FixtureHandler : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(request.RequestUri!.AbsolutePath.EndsWith("/models") ? """
                [{"model_type":"IMAGE","enabled":true,"operations":["image_edit"],"logical_alias":"model-a","display_name":"图像模型 A","provider":"示例供应商","model_id":"reference-image-standard"},
                 {"model_type":"IMAGE","enabled":true,"operations":["image_edit"],"logical_alias":"model-b","display_name":"图像模型 B","provider":"示例供应商","model_id":"reference-image-pro"}]
                """ : "[]")
        });
    }
}
