using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media.Animation;
using RailGo.ViewModels.Pages.Shell;

namespace RailGo.Views.Pages.Shell;

public sealed partial class MainPage : Page
{
    private const double BannerWidthRatio = 1938;
    private const double BannerHeightRatio = 585;
    public MainViewModel ViewModel
    {
        get;
    }

    public MainPage()
    {
        ViewModel = App.GetService<MainViewModel>();
        InitializeComponent();

        ViewModel.PropertyChanged += ViewModel_PropertyChanged;
    }

    private void ViewModel_PropertyChanged(object sender, System.ComponentModel.PropertyChangedEventArgs e)
    {
        if (e.PropertyName == nameof(ViewModel.CurrentBannerIndex))
        {
            if (BannerFlipView.SelectedIndex != ViewModel.CurrentBannerIndex)
            {
                BannerFlipView.SelectedIndex = ViewModel.CurrentBannerIndex;
            }
        }
    }

    private void Page_Loaded(object sender, RoutedEventArgs e)
    {
        UpdateBannerHeight();
        ViewModel.ResumeAutoPlay();
    }

    private void Page_Unloaded(object sender, RoutedEventArgs e)
    {
        ViewModel.PauseAutoPlay();
    }

    private void BannerFlipView_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (sender is FlipView flipView)
        {
            ViewModel.BannerSelectionChangedCommand?.Execute(flipView.SelectedIndex);
        }
    }

    private void OnContentAreaSizeChanged(object sender, SizeChangedEventArgs e)
    {
        UpdateBannerHeight();
    }

    private void UpdateBannerHeight()
    {
        double pageWidth = ContentArea.ActualWidth;
        double newHeight = (pageWidth / BannerWidthRatio) * BannerHeightRatio;
        BannerFlipView.Height = newHeight;
    }

    private async void ShowAllNoticesButton_Click(object sender, RoutedEventArgs e)
    {
        await AllNoticesDialog.ShowAsync();
    }
}