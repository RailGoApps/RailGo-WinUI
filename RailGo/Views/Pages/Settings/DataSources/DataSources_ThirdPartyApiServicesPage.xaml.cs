using System.Diagnostics;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using RailGo.Core.Models.QueryDatas;
using RailGo.Core.Models.Settings;
using RailGo.ViewModels.Pages.Settings.DataSources;
using RailGo.Services;

namespace RailGo.Views.Pages.Settings.DataSources;

public sealed partial class DataSources_ThirdPartyApiServicesPage : Page
{
    public DataSources_ThirdPartyApiServicesViewModel ViewModel
    {
        get;
    }

    public DataSources_ThirdPartyApiServicesPage()
    {
        ViewModel = App.GetService<DataSources_ThirdPartyApiServicesViewModel>();
        InitializeComponent();
    }

    private async void AddButton_Click(object sender, RoutedEventArgs e)
    {
        var result = await AddDialog.ShowAsync();
        if (result == ContentDialogResult.Primary)
        {
            ViewModel.AddOnlineApiSourceCommand.Execute(null);
        }
    }

    private async void EditButton_Click(object sender, RoutedEventArgs e)
    {
        if (ViewModel.Item == null)
        {
            return;
        }

        ViewModel.ShowEditOnlineApiSourceCommand.Execute(null);
        var result = await EditDialog.ShowAsync();
        if (result == ContentDialogResult.Primary)
        {
            ViewModel.EditOnlineApiSourceCommand.Execute(null);
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
            ViewModel.DeleteOnlineApiSourceCommand.Execute(null);
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