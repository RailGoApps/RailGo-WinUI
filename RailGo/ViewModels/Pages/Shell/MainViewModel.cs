using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using RailGo.Core.Query.Online;
using RailGo.Services;
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

    [ObservableProperty]
    private ObservableCollection<string> _notices = new ObservableCollection<string>();

    [ObservableProperty]
    private string _currentNotice = "暂无系统公告";

    [ObservableProperty]
    private bool _hasNotices;

    private DispatcherTimer _autoPlayTimer;
    private DispatcherTimer _noticeTimer;
    private int _currentNoticeIndex = 0;

    public MainViewModel()
    {
        _ = LoadBannerImagesAsync();
        InitializeDefaultNotices();
        InitializeAutoPlayTimer();
        InitializeNoticeTimer();
    }

    /// <summary>
    /// 初始化默认公告
    /// </summary>
    private void InitializeDefaultNotices()
    {
        Notices.Add("欢迎使用RailGo！");
        Notices.Add("WinUI新版本0.1.3正式上线，欢迎使用，如有问题请在设置-关于中进入交流群中反馈，感谢支持。");
        Notices.Add("本软件为免费使用，如果是付费的那就是被骗了，盗版举报请发送邮件至report@railgo.dev");
        HasNotices = true;
        CurrentNotice = Notices[0];
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

    private void InitializeNoticeTimer()
    {
        _noticeTimer = new DispatcherTimer();
        _noticeTimer.Interval = TimeSpan.FromSeconds(5);
        _noticeTimer.Tick += NoticeTimer_Tick;
    }

    private void AutoPlayTimer_Tick(object sender, object e)
    {
        if (BannerImages.Count == 0) return;
        CurrentBannerIndex = (CurrentBannerIndex + 1) % BannerImages.Count;
    }

    private void NoticeTimer_Tick(object sender, object e)
    {
        if (Notices.Count == 0) return;
        CurrentNotice = Notices[_currentNoticeIndex];
        _currentNoticeIndex = (_currentNoticeIndex + 1) % Notices.Count;
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
        _noticeTimer?.Stop(); 
    }

    public void ResumeAutoPlay()
    {
        if (_isAutoPlayEnabled && _autoPlayTimer != null && !_autoPlayTimer.IsEnabled)
        {
            _autoPlayTimer.Start();
        }
        if (HasNotices && _noticeTimer != null && !_noticeTimer.IsEnabled)
        {
            _noticeTimer.Start();
        }
    }

    private async Task LoadBannerImagesAsync()
    {
        try
        {
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
    private void NextNotice()
    {
        if (Notices.Count == 0) return;
        _currentNoticeIndex = (_currentNoticeIndex + 1) % Notices.Count;
        CurrentNotice = Notices[_currentNoticeIndex];
        ResetNoticeTimer();
    }

    [RelayCommand]
    private void PreviousNotice()
    {
        if (Notices.Count == 0) return;
        _currentNoticeIndex = (_currentNoticeIndex - 1 + Notices.Count) % Notices.Count;
        CurrentNotice = Notices[_currentNoticeIndex];
        ResetNoticeTimer();
    }

    private void ResetNoticeTimer()
    {
        if (_noticeTimer != null && _noticeTimer.IsEnabled)
        {
            _noticeTimer.Stop();
            _noticeTimer.Start();
        }
    }

    [RelayCommand]
    private void ToggleNoticeAutoPlay()
    {
        if (_noticeTimer == null) return;
        if (_noticeTimer.IsEnabled)
        {
            _noticeTimer.Stop();
        }
        else if (HasNotices && Notices.Count > 1)
        {
            _noticeTimer.Start();
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
            case "ToStationToStationButton":
                navigationService.NavigateTo(typeof(StationToStationViewModel).FullName!);
                break;
            default:
                navigationService.NavigateTo(typeof(MainViewModel).FullName!);
                break;
        }
    }

    public void Cleanup()
    {
        _autoPlayTimer?.Stop();
        _noticeTimer?.Stop();
        _autoPlayTimer.Tick -= AutoPlayTimer_Tick;
        _noticeTimer.Tick -= NoticeTimer_Tick;
    }
}
