using CommunityToolkit.Mvvm.ComponentModel;

using Microsoft.UI.Xaml.Controls;

using RailGo.Contracts.Services;
using RailGo.Views;

using RailGo.ViewModels.Pages.Shell;
using RailGo.ViewModels.Pages.Settings;
using RailGo.ViewModels.Pages.Settings.DataSources;
using RailGo.ViewModels.Pages.Trains;
using RailGo.ViewModels.Pages.TrainEmus;
using RailGo.ViewModels.Pages.Stations;
using RailGo.ViewModels.Pages.StationToStation;
using RailGo.Views.Pages.Shell;
using RailGo.Views.Pages.Settings;
using RailGo.Views.Pages.Settings.DataSources;
using RailGo.Views.Pages.Trains;
using RailGo.Views.Pages.TrainEmus;
using RailGo.Views.Pages.Stations;
using RailGo.Views.Pages.StationToStation;

namespace RailGo.Services;

public class PageService : IPageService
{
    private readonly Dictionary<string, Type> _pages = new();

    public PageService()
    {
        Configure<MainViewModel, MainPage>();
        Configure<SettingsViewModel, SettingsPage>();
        Configure<EMU_RoutingViewModel, EMU_RoutingPage>();
        Configure<Train_NumberViewModel, Train_NumberPage>();
        Configure<Station_InformationViewModel, Station_InformationPage>();
        Configure<StationToStationViewModel, StationToStationPage>();
        Configure<Ticket_GenerateViewModel, Ticket_GeneratePage>();
        Configure<StationDetailsViewModel, StationDetailsPage>();
        Configure<DataSources_ShellViewModel, DataSources_ShellPage>();
        Configure<DataSources_MainViewModel, DataSources_MainPage>();
        Configure<DataSources_CustomSourcesViewModel, DataSources_CustomSourcesPage>();
        Configure<DataSources_LocalDatabasesViewModel, DataSources_LocalDatabasesPage>();
        Configure<DataSources_OnlineDatabasesViewModel, DataSources_OnlineDatabasesPage>();
        Configure<DataSources_ThirdPartyApiServicesViewModel, DataSources_ThirdPartyApiServicesPage>();
        Configure<DataSources_ThirdPartyDatabasesViewModel, DataSources_ThirdPartyDatabasesPage>();
    }

    public Type GetPageType(string key)
    {
        Type? pageType;
        lock (_pages)
        {
            if (!_pages.TryGetValue(key, out pageType))
            {
                throw new ArgumentException($"Page not found: {key}. Did you forget to call PageService.Configure?");
            }
        }

        return pageType;
    }

    private void Configure<VM, V>()
        where VM : ObservableObject
        where V : Page
    {
        lock (_pages)
        {
            var key = typeof(VM).FullName!;
            if (_pages.ContainsKey(key))
            {
                throw new ArgumentException($"The key {key} is already configured in PageService");
            }

            var type = typeof(V);
            if (_pages.ContainsValue(type))
            {
                throw new ArgumentException($"This type is already configured with key {_pages.First(p => p.Value == type).Key}");
            }

            _pages.Add(key, type);
        }
    }
}
