using System.Diagnostics;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using RailGo.ViewModels.Pages.Settings.DataSources;

namespace RailGo.Views.Pages.Settings.DataSources;

public sealed partial class DataSources_LocalDatabasesPage : Page
{
    public DataSources_LocalDatabasesViewModel ViewModel
    {
        get;
    }

    public DataSources_LocalDatabasesPage()
    {
        ViewModel = App.GetService<DataSources_LocalDatabasesViewModel>();
        InitializeComponent();
    }

    private async void AddButton_Click(object sender, RoutedEventArgs e)
    {
        var result = await AddDialog.ShowAsync();
        if (result == ContentDialogResult.Primary)
        {
            ViewModel.AddLocalDatabaseSourceCommand.Execute(null);
        }
    }

    private async void EditButton_Click(object sender, RoutedEventArgs e)
    {
        if (ViewModel.Item == null)
        {
            return;
        }

        ViewModel.ShowEditLocalDatabaseSourceCommand.Execute(null);
        var result = await EditDialog.ShowAsync();
        if (result == ContentDialogResult.Primary)
        {
            ViewModel.EditLocalDatabaseSourceCommand.Execute(null);
        }
    }

    private async void DeleteButton_Click(object sender, RoutedEventArgs e)
    {
        if (ViewModel.Item == null)
        {
            return;
        }

        var result = await DeleteDialog.ShowAsync();
        if (result == ContentDialogResult.Primary)
        {
            ViewModel.DeleteLocalDatabaseSourceCommand.Execute(null);
        }
    }
    private async void ViewButton_Click(object sender, RoutedEventArgs e)
    {
        if (ViewModel.Item == null)
        {
            return;
        }
        var result = await ViewDialog.ShowAsync();
    }
}