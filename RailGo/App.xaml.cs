using System.Collections.ObjectModel;
using System.Diagnostics;
using CommunityToolkit.WinUI;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.UI.Xaml;
using Newtonsoft.Json;
using RailGo.Activation;
using RailGo.Contracts.Services;
using RailGo.Core.Contracts.Services;
using RailGo.Core.Models;
using RailGo.Core.Models.QueryDatas;
using RailGo.Core.Models.Settings;
using RailGo.Core.Query.Online;
using RailGo.Core.Services;
using RailGo.Helpers;
using RailGo.Notifications;
using RailGo.Services;
using RailGo.ViewModels;
using RailGo.ViewModels.Pages.Settings;
using RailGo.ViewModels.Pages.Settings.DataSources;
using RailGo.ViewModels.Pages.Shell;
using RailGo.ViewModels.Pages.Stations;
using RailGo.ViewModels.Pages.StationToStation;
using RailGo.ViewModels.Pages.TrainEmus;
using RailGo.ViewModels.Pages.Trains;
using RailGo.ViewModels.Windows;
using RailGo.Views;
using RailGo.Views.Windows;
using RailGo.Views.ContentDialogs;
using RailGo.Views.Pages.Settings;
using RailGo.Views.Pages.Settings.DataSources;
using RailGo.Views.Pages.Shell;
using RailGo.Views.Pages.Stations;
using RailGo.Views.Pages.StationToStation;
using RailGo.Views.Pages.TrainEmus;
using RailGo.Views.Pages.Trains;
using Windows.Storage;
using static System.Collections.Specialized.BitVector32;


namespace RailGo;

// To learn more about WinUI 3, see https://docs.microsoft.com/windows/apps/winui/winui3/.
public partial class App : Application
{
    // The .NET Generic Host provides dependency injection, configuration, logging, and other services.
    // https://docs.microsoft.com/dotnet/core/extensions/generic-host
    // https://docs.microsoft.com/dotnet/core/extensions/dependency-injection
    // https://docs.microsoft.com/dotnet/core/extensions/configuration
    // https://docs.microsoft.com/dotnet/core/extensions/logging
    public IHost Host
    {
        get;
    }

    public static T GetService<T>()
        where T : class
    {
        if ((App.Current as App)!.Host.Services.GetService(typeof(T)) is not T service)
        {
            throw new ArgumentException($"{typeof(T)} needs to be registered in ConfigureServices within App.xaml.cs.");
        }

        return service;
    }

    public static WindowEx MainWindow { get; } = new MainWindow();

    public static UIElement? AppTitlebar { get; set; }

    public App()
    {
        InitializeComponent();

        Host = Microsoft.Extensions.Hosting.Host.
        CreateDefaultBuilder().
        UseContentRoot(AppContext.BaseDirectory).
        ConfigureServices((context, services) =>
        {
            // Default Activation Handler
            services.AddTransient<ActivationHandler<LaunchActivatedEventArgs>, DefaultActivationHandler>();

            // Other Activation Handlers
            services.AddTransient<IActivationHandler, AppNotificationActivationHandler>();

            // Services
            services.AddSingleton<IAppNotificationService, AppNotificationService>();
            services.AddSingleton<ILocalSettingsService, LocalSettingsService>();
            services.AddSingleton<IBackgroundImageService, BackgroundImageService>();
            services.AddSingleton<IDataSourceService, DataSourceService>();
            services.AddSingleton<IThemeSelectorService, ThemeSelectorService>();
            services.AddTransient<INavigationViewService, NavigationViewService>();

            services.AddSingleton<QueryService>();

            services.AddSingleton<IActivationService, ActivationService>();
            services.AddSingleton<IPageService, PageService>();
            services.AddSingleton<INavigationService, NavigationService>();

            // Core Services
            services.AddSingleton<IFileService, FileService>();

            // Views and ViewModels
            services.AddTransient<EMU_RoutingDetailsPage>();
            services.AddTransient<EMU_RoutingDetailsViewModel>();
            services.AddTransient<StationDetailsViewModel>();
            services.AddTransient<StationDetailsPage>();
            services.AddTransient<TrainNumberTripDetailsPage>();
            services.AddTransient<TrainNumberTripDetailsViewModel>();
            services.AddTransient<Ticket_GenerateViewModel>();
            services.AddTransient<Ticket_GeneratePage>();
            services.AddTransient<StationToStationViewModel>();
            services.AddTransient<StationToStationPage>();
            services.AddTransient<Station_InformationViewModel>();
            services.AddTransient<Station_InformationPage>();
            services.AddTransient<Train_NumberViewModel>();
            services.AddTransient<Train_NumberPage>();
            services.AddTransient<EMU_RoutingViewModel>();
            services.AddTransient<EMU_RoutingPage>();
            services.AddTransient<SettingsViewModel>();
            services.AddTransient<SettingsPage>();
            services.AddTransient<MainViewModel>();
            services.AddSingleton<MainWindowViewModel>();
            services.AddSingleton<GetOfflineDatabaseWindowViewModel>();
            services.AddTransient<GetOfflineDatabaseWindow>();
            services.AddTransient<MainPage>();
            services.AddTransient<ShellPage>();
            services.AddTransient<DataSources_ShellPage>();
            services.AddTransient<ShellViewModel>();
            services.AddTransient<DataSources_ShellViewModel>();
            services.AddSingleton<DataSources_MainPage>();
            services.AddSingleton<DataSources_MainViewModel>();
            services.AddTransient<DataSources_CustomSourcesViewModel>();
            services.AddTransient<DataSources_LocalDatabasesViewModel>();
            services.AddSingleton<DataSources_OnlineDatabasesViewModel>();
            services.AddTransient<DataSources_ThirdPartyApiServicesViewModel>();
            services.AddTransient<DataSources_ThirdPartyDatabasesViewModel>();
            services.AddTransient<DataSources_CustomSourcesPage>();
            services.AddTransient<DataSources_LocalDatabasesPage>();
            services.AddSingleton<DataSources_OnlineDatabasesPage>();
            services.AddTransient<DataSources_ThirdPartyApiServicesPage>();
            services.AddTransient<DataSources_ThirdPartyDatabasesPage>();

            // Configuration
            services.Configure<LocalSettingsOptions>(context.Configuration.GetSection(nameof(LocalSettingsOptions)));
        }).
        Build();

        App.GetService<IAppNotificationService>().Initialize();

        UnhandledException += App_UnhandledException;
    }

    private async void App_UnhandledException(object sender, Microsoft.UI.Xaml.UnhandledExceptionEventArgs e)
    {
        e.Handled = true; 

        System.Diagnostics.Debug.WriteLine($"未处理异常: {e.Exception}");

        await MainWindow.DispatcherQueue.EnqueueAsync(async () =>
        {
            var exceptionDialog = new ExceptionDialog(e.Exception)
            {
                XamlRoot = MainWindow.Content.XamlRoot
            };

            await exceptionDialog.ShowAsync();
        });
    }

    public static class Global
    {
        public static ObservableCollection<StationPreselectResult> StationsJson;
    }

    protected async override void OnLaunched(LaunchActivatedEventArgs args)
    {
        // 检查可以使用的查询模式（DataSourcesSettings）
        var DataSourcesShell = App.GetService<DataSources_ShellViewModel>();
        DataSourcesShell.CheckAvailableModes();

        base.OnLaunched(args);
        // App.GetService<IAppNotificationService>().Show(string.Format("AppNotificationSamplePayload".GetLocalized(), AppContext.BaseDirectory));
        await App.GetService<IActivationService>().ActivateAsync(args);
    }
}
