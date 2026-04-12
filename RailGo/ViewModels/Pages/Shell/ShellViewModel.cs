using CommunityToolkit.Mvvm.ComponentModel;

using RailGo.Contracts.Services;
using RailGo.Views.Pages.Settings;

using Microsoft.UI.Xaml.Navigation;

namespace RailGo.ViewModels.Pages.Shell;

public partial class ShellViewModel : ObservableRecipient
{
    [ObservableProperty]
    private object? selected;

    public INavigationService NavigationService
    {
        get;
    }

    public INavigationViewService NavigationViewService
    {
        get;
    }

    public MainWindowViewModel progressBarVM = App.GetService<MainWindowViewModel>();

    public ShellViewModel(INavigationService navigationService, INavigationViewService navigationViewService)
    {
        NavigationService = navigationService;
        NavigationService.Navigated += OnNavigated;
        NavigationViewService = navigationViewService;
    }

    private void OnNavigated(object sender, NavigationEventArgs e)
    {
        progressBarVM.TaskIsInProgress = "Collapsed";
        progressBarVM.IfShowErrorInfoBarOpen = false;
        if (e.SourcePageType == typeof(SettingsPage))
        {
            Selected = NavigationViewService.SettingsItem;
            return;
        }

        var selectedItem = NavigationViewService.GetSelectedItem(e.SourcePageType);
        if (selectedItem != null)
        {
            Selected = selectedItem;
        }
    }
}
