using Microsoft.UI.Windowing;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using RailGo.Helpers;
using RailGo.Views;
using Windows.UI.ViewManagement;
using RailGo.ViewModels.Pages.Shell;
using RailGo.Views.Pages.Shell;
using RailGo.Contracts.Services;
using RailGo.AI.Services;

namespace RailGo;

public sealed partial class MainWindow : WindowEx
{
    private Microsoft.UI.Dispatching.DispatcherQueue dispatcherQueue;
    private UISettings settings;
    private UIElement? _shell = null;
    public static MainWindow Instance;
    public MainWindowViewModel ViewModel { get; }

    public MainWindow()
    {
        ViewModel = App.GetService<MainWindowViewModel>();
        _shell = App.GetService<ShellPage>();
        InitializeComponent();
        Instance = this;

        AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "Assets/WindowIcon.ico"));
        Title = "AppDisplayName".GetLocalized();
        ExtendsContentIntoTitleBar = true;
        ViewModel.TaskIsInProgress = "Collapsed";
        ViewModel.IfShowErrorInfoBarOpen = false;

        dispatcherQueue = Microsoft.UI.Dispatching.DispatcherQueue.GetForCurrentThread();
        settings = new UISettings();
        settings.ColorValuesChanged += Settings_ColorValuesChanged;
        Closed += OnMainWindowClosed;
    }

    private void Tab_TabCloseRequested(TabView sender, TabViewTabCloseRequestedEventArgs args)
    {
        MainTabView.TabItems.Remove(args.Tab);
    }

    private void Settings_ColorValuesChanged(UISettings sender, object args)
    {
        dispatcherQueue.TryEnqueue(() =>
        {
            TitleBarHelper.ApplySystemThemeToCaptionButtons();
            // 重新应用主题设置，使跟随系统模式能正确响应主题变化
            var themeSelectorService = App.GetService<IThemeSelectorService>();
            _ = themeSelectorService.SetRequestedThemeAsync();
        });
    }

    private void OnCustomCustomTabViewLoaded(object sender, RoutedEventArgs e)
    {
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(DragAreaGrid);
    }

    private void OnMainWindowClosed(object sender, WindowEventArgs args)
    {
        settings.ColorValuesChanged -= Settings_ColorValuesChanged;
        try
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(8));
            // Closed is raised on the UI thread. Run the async shutdown on a
            // worker so its continuations cannot deadlock against that thread.
            Task.Run(() => App.GetService<IRailGptRuntimeManager>().StopAsync(timeout.Token))
                .GetAwaiter()
                .GetResult();
        }
        catch
        {
            // The runtime manager performs a final owned-process cleanup in Dispose.
        }
    }
}
