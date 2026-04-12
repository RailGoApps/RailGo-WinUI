using System.Reflection;
using System.Windows.Input;
using System.Collections.ObjectModel;
using System.Diagnostics;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using RailGo.Core.Models.Settings;
using RailGo.Contracts.Services;
using RailGo.Helpers;

using Microsoft.UI.Xaml;

using Windows.ApplicationModel;

namespace RailGo.ViewModels.Pages.Settings.DataSources;

public partial class DataSources_ThirdPartyApiServicesViewModel : ObservableRecipient
{
    private readonly IDataSourceService _dataSourceService;

    [ObservableProperty]
    private OnlineApiSource item;

    [ObservableProperty]
    public ObservableCollection<OnlineApiSource> onlineApiSources = new();

    [ObservableProperty]
    private string addName;

    [ObservableProperty]
    private string addValue;

    [ObservableProperty]
    private string editName;

    [ObservableProperty]
    private string editValue;

    [ObservableProperty]
    private string deleteName;

    [ObservableProperty]
    private string deleteValue;

    public DataSources_ThirdPartyApiServicesViewModel(IDataSourceService dataSourceService)
    {
        _dataSourceService = dataSourceService;
        LoadOnlineApiSources();
    }

    [RelayCommand]
    public async void LoadOnlineApiSources()
    {
        OnlineApiSources = await _dataSourceService.GetOnlineApiSourcesAsync();
    }

    [RelayCommand]
    public async void AddOnlineApiSource()
    {
        await _dataSourceService.SaveOnlineApiSourceAsync(new OnlineApiSource { Name = AddName, Address=AddValue });
        LoadOnlineApiSources();
    }

    [RelayCommand]
    public async void ShowEditOnlineApiSource()
    {
        EditName = Item.Name;
        EditValue = Item.Address;
    }

    [RelayCommand]
    public async void EditOnlineApiSource()
    {
        await _dataSourceService.DeleteOnlineApiSourceAsync(Item.Name);
        await _dataSourceService.SaveOnlineApiSourceAsync(new OnlineApiSource { Name = EditName, Address = EditValue });
        LoadOnlineApiSources();
    }

    [RelayCommand]
    public async void DeleteOnlineApiSource()
    {
        await _dataSourceService.DeleteOnlineApiSourceAsync(Item.Name);
        LoadOnlineApiSources();
    }
}