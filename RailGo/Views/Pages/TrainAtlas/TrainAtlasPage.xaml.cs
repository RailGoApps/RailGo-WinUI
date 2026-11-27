using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using RailGo.Core.Models.QueryDatas;
using RailGo.ViewModels.Pages.TrainAtlas;

namespace RailGo.Views.Pages.TrainAtlas;

public sealed partial class TrainAtlasPage : Page
{
    public TrainAtlasViewModel ViewModel
    {
        get;
    }

    public TrainAtlasPage()
    {
        ViewModel = App.GetService<TrainAtlasViewModel>();
        InitializeComponent();
    }

    private void PhotoGridView_ItemClick(object sender, ItemClickEventArgs e)
    {
        if (e.ClickedItem is TrainAtlasPhoto photo)
        {
            ViewModel.OpenPhoto(photo);
            _ = PhotoDialog.ShowAsync();
        }
    }

    private string _loadedDialogImageUrl;

    private void PhotoDialog_Opened(ContentDialog sender, ContentDialogOpenedEventArgs args)
    {
        var url = ViewModel.SelectedPhoto?.ImageUrl;

        // 同一张图片已经加载过时直接显示，避免重新打开时一直转圈
        if (!string.IsNullOrEmpty(url) && url == _loadedDialogImageUrl)
        {
            ShowDialogImage();
            return;
        }

        _loadedDialogImageUrl = null;
        DialogImage.Opacity = 0;
        DialogImageFailedText.Visibility = Visibility.Collapsed;

        if (string.IsNullOrEmpty(url))
        {
            DialogLoadingRing.IsActive = false;
            DialogLoadingRing.Visibility = Visibility.Collapsed;
            DialogImageFailedText.Visibility = Visibility.Visible;
        }
        else
        {
            DialogLoadingRing.IsActive = true;
            DialogLoadingRing.Visibility = Visibility.Visible;
        }
    }

    private void PhotoDialog_Closed(ContentDialog sender, ContentDialogClosedEventArgs args)
    {
        ViewModel.ClosePhoto();
    }

    private void DialogImage_ImageOpened(object sender, RoutedEventArgs e)
    {
        _loadedDialogImageUrl = ViewModel.SelectedPhoto?.ImageUrl;
        ShowDialogImage();
    }

    private void DialogImage_ImageFailed(object sender, RoutedEventArgs e)
    {
        _loadedDialogImageUrl = null;
        DialogImage.Opacity = 0;
        DialogLoadingRing.IsActive = false;
        DialogLoadingRing.Visibility = Visibility.Collapsed;
        DialogImageFailedText.Visibility = Visibility.Visible;
    }

    private void ShowDialogImage()
    {
        DialogImage.Opacity = 1;
        DialogLoadingRing.IsActive = false;
        DialogLoadingRing.Visibility = Visibility.Collapsed;
        DialogImageFailedText.Visibility = Visibility.Collapsed;
    }

    private void Thumbnail_ImageOpened(object sender, RoutedEventArgs e)
    {
        if (sender is Image image && image.Parent is Grid container)
        {
            image.Opacity = 1;
            SetLoadingState(container, false);
        }
    }

    private void Thumbnail_ImageFailed(object sender, RoutedEventArgs e)
    {
        if (sender is Image image && image.Parent is Grid container)
        {
            image.Opacity = 0;
            SetLoadingState(container, true);
        }
    }

    private static void SetLoadingState(Grid container, bool failed)
    {
        foreach (var child in container.Children)
        {
            if (child is ProgressRing ring)
            {
                ring.IsActive = false;
                ring.Visibility = Visibility.Collapsed;
            }
            else if (child is TextBlock text && text.Text == "图片加载失败")
            {
                text.Visibility = failed ? Visibility.Visible : Visibility.Collapsed;
            }
        }
    }

    private void KeywordTextBox_KeyDown(object sender, Microsoft.UI.Xaml.Input.KeyRoutedEventArgs e)
    {
        if (e.Key == global::Windows.System.VirtualKey.Enter)
        {
            if (ViewModel.SearchCommand.CanExecute(null))
            {
                ViewModel.SearchCommand.Execute(null);
            }
        }
    }
}
