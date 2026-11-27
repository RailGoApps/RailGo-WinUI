using System.Collections.ObjectModel;
using System.Diagnostics;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using RailGo.Contracts.ViewModels;
using RailGo.Core.Models.QueryDatas;
using RailGo.Services;
using RailGo.ViewModels.Pages.Shell;

namespace RailGo.ViewModels.Pages.TrainAtlas;

public partial class TrainAtlasViewModel : ObservableObject, INavigationAware
{
    [ObservableProperty]
    private string keyword;

    [ObservableProperty]
    private ObservableCollection<TrainAtlasPhoto> photos = new();

    [ObservableProperty]
    private bool isLoading;

    [ObservableProperty]
    private int currentPage = 1;

    [ObservableProperty]
    private int totalPages = 1;

    [ObservableProperty]
    private int totalCount;

    [ObservableProperty]
    private TrainAtlasPhoto selectedPhoto;

    [ObservableProperty]
    private bool isPhotoDialogOpen;

    [ObservableProperty]
    private bool canGoPrevious;

    [ObservableProperty]
    private bool canGoNext;

    public MainWindowViewModel progressBarVM = App.GetService<MainWindowViewModel>();

    private const int PerPage = 30;

    public void OnNavigatedTo(object parameter)
    {
        if (parameter is string keyword && !string.IsNullOrWhiteSpace(keyword))
        {
            Keyword = keyword;
            _ = SearchWithFallbackAsync(keyword);
        }
    }

    public void OnNavigatedFrom()
    {
    }

    /// <summary>
    /// 图鉴按车型检索，若传入的是车组号（如 CR400BF-Z-0521）则回退到车型
    /// </summary>
    private async Task SearchWithFallbackAsync(string keyword)
    {
        await LoadPageAsync(1);

        if (Photos.Count == 0 && keyword.Contains('-'))
        {
            var model = keyword[..keyword.LastIndexOf('-')];
            if (!string.IsNullOrWhiteSpace(model))
            {
                Keyword = model;
                await LoadPageAsync(1);
            }
        }
    }

    partial void OnCurrentPageChanged(int value)
    {
        CanGoPrevious = value > 1;
        CanGoNext = value < TotalPages;
    }

    partial void OnTotalPagesChanged(int value)
    {
        CanGoPrevious = CurrentPage > 1;
        CanGoNext = CurrentPage < value;
    }

    [RelayCommand]
    private async Task SearchAsync()
    {
        if (string.IsNullOrWhiteSpace(Keyword))
        {
            Photos.Clear();
            CurrentPage = 1;
            TotalPages = 1;
            TotalCount = 0;
            return;
        }
        await LoadPageAsync(1);
    }

    [RelayCommand]
    private async Task PreviousPageAsync()
    {
        if (CurrentPage > 1)
        {
            await LoadPageAsync(CurrentPage - 1);
        }
    }

    [RelayCommand]
    private async Task NextPageAsync()
    {
        if (CurrentPage < TotalPages)
        {
            await LoadPageAsync(CurrentPage + 1);
        }
    }

    public void OpenPhoto(TrainAtlasPhoto photo)
    {
        if (photo != null)
        {
            SelectedPhoto = photo;
            IsPhotoDialogOpen = true;
        }
    }

    public void ClosePhoto()
    {
        IsPhotoDialogOpen = false;
    }

    private async Task LoadPageAsync(int page)
    {
        try
        {
            IsLoading = true;
            progressBarVM.TaskIsInProgress = "Visible";

            var queryService = App.GetService<QueryService>();
            var response = await queryService.QueryTrainAtlasAsync(Keyword, page, PerPage);

            Photos.Clear();
            if (response?.Models != null)
            {
                foreach (var model in response.Models)
                {
                    if (model.ReferencePhotos != null)
                    {
                        foreach (var photo in model.ReferencePhotos)
                        {
                            photo.ModelName = model.ModelName;
                            Photos.Add(photo);
                        }
                    }
                }
            }

            CurrentPage = response?.Page ?? page;
            TotalPages = response?.Pages ?? 1;
            TotalCount = response?.Total ?? 0;
        }
        catch (Exception ex)
        {
            Trace.WriteLine(ex);
            progressBarVM.IfShowErrorInfoBarOpen = true;
            progressBarVM.ShowErrorInfoBarContent = ex.Message;
            progressBarVM.ShowErrorInfoBarTitle = "Error";
            WaitCloseInfoBar();
        }
        finally
        {
            IsLoading = false;
            progressBarVM.TaskIsInProgress = "Collapsed";
        }
    }

    private async void WaitCloseInfoBar()
    {
        await Task.Delay(3000);
        progressBarVM.IfShowErrorInfoBarOpen = false;
    }
}
