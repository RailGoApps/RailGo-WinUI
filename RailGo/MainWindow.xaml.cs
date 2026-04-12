using Microsoft.UI.Windowing;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using RailGo.Helpers;
using RailGo.Views;

using Windows.UI.ViewManagement;
using RailGo.ViewModels.Pages.Shell;
using RailGo.Views.Pages.Shell;

namespace RailGo;

public sealed partial class MainWindow : WindowEx
{
    private Microsoft.UI.Dispatching.DispatcherQueue dispatcherQueue;

    private UISettings settings;
    private UIElement? _shell = null;
    public static MainWindow Instance;
    public MainWindowViewModel ViewModel
    {
        get;
    }

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

        // Theme change code picked from https://github.com/microsoft/WinUI-Gallery/pull/1239
        dispatcherQueue = Microsoft.UI.Dispatching.DispatcherQueue.GetForCurrentThread();
        settings = new UISettings();
        settings.ColorValuesChanged += Settings_ColorValuesChanged; // cannot use FrameworkElement.ActualThemeChanged event
    }
    private void Tab_TabCloseRequested(TabView sender, TabViewTabCloseRequestedEventArgs args)
    {
        MainTabView.TabItems.Remove(args.Tab);
    }
    // this handles updating the caption button colors correctly when indows system theme is changed
    // while the app is open
    private void Settings_ColorValuesChanged(UISettings sender, object args)
    {
        // This calls comes off-thread, hence we will need to dispatch it to current app's thread
        dispatcherQueue.TryEnqueue(() =>
        {
            TitleBarHelper.ApplySystemThemeToCaptionButtons();
        });
    }
    private void OnCustomCustomTabViewLoaded(object sender, RoutedEventArgs e)
    {
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(DragAreaGrid);
    }

}
