using System.Collections.ObjectModel;
using System.Diagnostics;
using CommunityToolkit.Mvvm.Input;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using RailGo.Contracts.Services;
using RailGo.Core.Models.Settings;
using RailGo.ViewModels.Pages.Settings.DataSources;
using RailGo.Views.ContentDialogs;

namespace RailGo.Views.Pages.Settings.DataSources;

public sealed partial class DataSources_CustomSourcesPage : Page
{
    public DataSources_CustomSourcesViewModel ViewModel
    {
        get;
    }

    public DataSources_CustomSourcesPage()
    {
        ViewModel = App.GetService<DataSources_CustomSourcesViewModel>();
        InitializeComponent();
    }
    private async void OnSelectSourceClick(object sender, RoutedEventArgs e)
    {
        if (ViewModel.SelectedMethodItem == null) return;

        ViewModel.MethodModeSelectIsOnline = ViewModel.SelectedMethodItem.IsOnlineMode;
        ViewModel.MethodModeSelectIsOffline = ViewModel.SelectedMethodItem.IsOfflineMode;
        await SourceSelectionDialog.ShowAsync();
    }
    private async void OnStartCreateNewClick(object sender, RoutedEventArgs e)
    {
        await CreateNewDialog.ShowAsync();
    }

    private void ListView_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        ViewModel.SelectionItemCommand.Execute(null);
    }

    private void SelectOnlineButton_Checked(object sender, RoutedEventArgs e)
    {
        ViewModel.SourcesLoadingCommand.Execute("online");
    }

    private void SelectOfflineButton_Checked(object sender, RoutedEventArgs e)
    {
        ViewModel.SourcesLoadingCommand.Execute("offline");
    }
}