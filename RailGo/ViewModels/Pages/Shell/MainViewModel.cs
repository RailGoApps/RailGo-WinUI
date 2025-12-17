using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using RailGo.Core.Query.Online;
using RailGo.ViewModels.Pages.Stations;
using RailGo.ViewModels.Pages.StationToStation;
using RailGo.ViewModels.Pages.TrainEmus;
using RailGo.ViewModels.Pages.Trains;

namespace RailGo.ViewModels.Pages.Shell;

public partial class MainViewModel : ObservableObject
{
    public Contracts.Services.INavigationService navigationService = App.GetService<Contracts.Services.INavigationService>();

    [ObservableProperty]
    private ObservableCollection<string> _bannerImages = new ObservableCollection<string>();

    [ObservableProperty]
    private int _currentBannerIndex = 0;

    [ObservableProperty]
    private bool _isAutoPlayEnabled = true;

    private DispatcherTimer _autoPlayTimer;

    public MainViewModel()
    {
        _ = LoadBannerImagesAsync();
        InitializeAutoPlayTimer();
    }

    private void InitializeAutoPlayTimer()
    {
        _autoPlayTimer = new DispatcherTimer();
        _autoPlayTimer.Interval = TimeSpan.FromSeconds(10);
        _autoPlayTimer.Tick += AutoPlayTimer_Tick;

        if (_isAutoPlayEnabled)
        {
            _autoPlayTimer.Start();
        }
    }

    private void AutoPlayTimer_Tick(object sender, object e)
    {
        if (BannerImages.Count == 0) return;

        CurrentBannerIndex = (CurrentBannerIndex + 1) % BannerImages.Count;
    }

    [RelayCommand]
    private void BannerSelectionChanged(int selectedIndex)
    {
        if (selectedIndex >= 0 && selectedIndex < BannerImages.Count)
        {
            CurrentBannerIndex = selectedIndex;
            ResetAutoPlayTimer();
        }
    }

    private void ResetAutoPlayTimer()
    {
        _autoPlayTimer?.Stop();
        _autoPlayTimer?.Start();
    }

    public void PauseAutoPlay()
    {
        _autoPlayTimer?.Stop();
    }

    public void ResumeAutoPlay()
    {
        if (_isAutoPlayEnabled && _autoPlayTimer != null && !_autoPlayTimer.IsEnabled)
        {
            _autoPlayTimer.Start();
        }
    }

    private async Task LoadBannerImagesAsync()
    {
        try
        {
            BannerImages.Add("ms-appx:///Assets/AutoBanner.png");
            var images = await SettingsAPIService.GetBannerImagesAsync();

            if (images?.Count > 0)
            {
                foreach (var imageUrl in images)
                {
                    BannerImages.Add(imageUrl);
                }

                if (_isAutoPlayEnabled && _autoPlayTimer != null && !_autoPlayTimer.IsEnabled)
                {
                    _autoPlayTimer.Start();
                }
            }
        }
        catch
        {
        }
    }

    [RelayCommand]
    private async Task NavigationAsync(object parameter)
    {
        string buttonName = parameter?.ToString() ?? string.Empty;

        switch (buttonName)
        {
            case "ToTrainEmusButton":
                navigationService.NavigateTo(typeof(EMU_RoutingViewModel).FullName!);
                break;
            case "ToTrainsButton":
                navigationService.NavigateTo(typeof(Train_NumberViewModel).FullName!);
                break;
            case "ToStationsButton":
                navigationService.NavigateTo(typeof(Station_InformationViewModel).FullName!);
                break;
            case "StationToStationButton":
                navigationService.NavigateTo(typeof(StationToStationViewModel).FullName!);
                break;
            default:
                navigationService.NavigateTo(typeof(MainViewModel).FullName!);
                break;
        }
    }
}