using System.Collections.ObjectModel;
using System.Diagnostics;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using RailGo.Contracts.Services;
using RailGo.Core.Query.Online;
using RailGo.Services;
using RailGo.ViewModels.Pages.Stations;
using RailGo.ViewModels.Pages.StationToStation;
using RailGo.ViewModels.Pages.TrainAtlas;
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
    private string _noticeTitle = "公告";

    [ObservableProperty]
    private InfoBarSeverity _noticeSeverity = InfoBarSeverity.Informational;

    [ObservableProperty]
    private bool _hasNotices;

    private DispatcherTimer _autoPlayTimer;
    private DispatcherTimer _noticeTimer;
    private int _currentNoticeIndex = 0;
    private readonly List<string> _bannerJumpUrls = new();
    private readonly List<string> _rawNotices = new();

    public MainViewModel()
    {
        _ = LoadBannerImagesAsync();
        _ = LoadNoticesAsync();
        InitializeAutoPlayTimer();
        InitializeNoticeTimer();
    }

    /// <summary>
    /// 初始化本地默认公告（远端拉取失败或离线时使用）
    /// </summary>
    private void InitializeDefaultNotices()
    {
        Notices.Clear();
        _rawNotices.Clear();
        var defaultNotices = new[]
        {
            "欢迎使用RailGo！",
            "WinUI新版本0.1.4正式上线，欢迎使用，如有问题请在设置-关于中进入交流群中反馈，感谢支持。",
            "本软件为免费使用，如果是付费的那就是被骗了，盗版举报请发送邮件至report@railgo.dev"
        };
        foreach (var notice in defaultNotices)
        {
            Notices.Add(notice);
            _rawNotices.Add(notice);
        }
        HasNotices = true;
        _currentNoticeIndex = 0;
        ApplyNotice(_currentNoticeIndex);
    }

    /// <summary>
    /// 从远端拉取公告，失败或离线时保留本地公告轮播
    /// </summary>
    private async Task LoadNoticesAsync()
    {
        InitializeDefaultNotices();

        try
        {
            if (await IsOfflineModeAsync())
            {
                return;
            }

            var remoteNotices = await SettingsAPIService.GetNoticesAsync();
            if (remoteNotices is { Count: > 0 })
            {
                var validNotices = remoteNotices
                    .Where(notice => !string.IsNullOrWhiteSpace(notice))
                    .ToList();
                if (validNotices.Count == 0)
                {
                    return;
                }

                Notices.Clear();
                _rawNotices.Clear();
                foreach (var notice in validNotices)
                {
                    Notices.Add(StripNoticePrefix(notice));
                    _rawNotices.Add(notice);
                }

                HasNotices = true;
                _currentNoticeIndex = 0;
                ApplyNotice(_currentNoticeIndex);
                ResetNoticeTimer();
            }
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"加载在线公告失败，使用本地公告: {ex.Message}");
        }
    }

    /// <summary>
    /// 解析公告前缀（[AD] / [PSAD] / [WAR]）并更新展示的标题、颜色与正文
    /// </summary>
    private void ApplyNotice(int index)
    {
        if (Notices.Count == 0)
        {
            CurrentNotice = "暂无公告";
            NoticeTitle = "公告";
            NoticeSeverity = InfoBarSeverity.Informational;
            return;
        }

        var raw = index < _rawNotices.Count ? (_rawNotices[index] ?? string.Empty) : (Notices[index] ?? string.Empty);
        NoticeSeverity = raw.StartsWith("[WAR]", StringComparison.Ordinal)
            ? InfoBarSeverity.Error
            : InfoBarSeverity.Informational;
        NoticeTitle = raw.StartsWith("[AD]", StringComparison.Ordinal)
            ? "广告"
            : raw.StartsWith("[PSAD]", StringComparison.Ordinal)
                ? "公益广告"
                : "公告";
        CurrentNotice = StripNoticePrefix(raw);
    }

    private static string StripNoticePrefix(string notice)
    {
        return System.Text.RegularExpressions.Regex.Replace(notice ?? string.Empty, @"^\[(AD|PSAD|WAR)\]", string.Empty);
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
        _currentNoticeIndex = (_currentNoticeIndex + 1) % Notices.Count;
        ApplyNotice(_currentNoticeIndex);
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
        // 先显示本地默认背景图
        const string localBanner = "ms-appx:///Assets/HomeBanner.jpg";
        BannerImages.Add(localBanner);
        _bannerJumpUrls.Add(string.Empty);

        try
        {
            if (await IsOfflineModeAsync())
            {
                return;
            }

            // 从远端拉取图片广告，失败或为空时保持本地图片
            var ads = await SettingsAPIService.GetPicAdsAsync();
            var validAds = ads?.Where(ad => !string.IsNullOrWhiteSpace(ad?.Img)).ToList();
            if (validAds is { Count: > 0 })
            {
                BannerImages.Clear();
                _bannerJumpUrls.Clear();
                foreach (var ad in validAds)
                {
                    BannerImages.Add(ad.Img);
                    _bannerJumpUrls.Add(ad.Jump ?? string.Empty);
                }

                CurrentBannerIndex = 0;
            }
        }
        catch (Exception ex)
        {
            // 在线获取失败，保持本地默认图
            Debug.WriteLine($"加载在线轮播图失败，使用本地默认图: {ex.Message}");
        }
    }

    /// <summary>
    /// 获取指定轮播图对应的跳转地址，没有则返回空字符串
    /// </summary>
    public string GetBannerJumpUrl(int index)
    {
        if (index < 0 || index >= _bannerJumpUrls.Count)
        {
            return string.Empty;
        }

        return _bannerJumpUrls[index];
    }

    /// <summary>
    /// 当前是否处于离线模式
    /// </summary>
    private static async Task<bool> IsOfflineModeAsync()
    {
        try
        {
            var dataSourceService = App.GetService<IDataSourceService>();
            return await dataSourceService.GetQueryModeAsync() == "Offline";
        }
        catch
        {
            return false;
        }
    }

    [RelayCommand]
    private void NextNotice()
    {
        if (Notices.Count == 0) return;
        _currentNoticeIndex = (_currentNoticeIndex + 1) % Notices.Count;
        ApplyNotice(_currentNoticeIndex);
        ResetNoticeTimer();
    }

    [RelayCommand]
    private void PreviousNotice()
    {
        if (Notices.Count == 0) return;
        _currentNoticeIndex = (_currentNoticeIndex - 1 + Notices.Count) % Notices.Count;
        ApplyNotice(_currentNoticeIndex);
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
            case "ToTrainAtlasButton":
                navigationService.NavigateTo(typeof(TrainAtlasViewModel).FullName!);
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
