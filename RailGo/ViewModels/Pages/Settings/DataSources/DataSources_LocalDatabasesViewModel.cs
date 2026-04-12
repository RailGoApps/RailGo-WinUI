using System.Reflection;
using System.Windows.Input;

using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;

using RailGo.Contracts.Services;
using RailGo.Helpers;

using Microsoft.UI.Xaml;

using Windows.ApplicationModel;
using RailGo.Core.Models.Settings;
using System.Collections.ObjectModel;

namespace RailGo.ViewModels.Pages.Settings.DataSources;

public partial class DataSources_LocalDatabasesViewModel : ObservableRecipient
{
    private readonly IDataSourceService _dataSourceService;

    [ObservableProperty]
    private LocalDatabaseSource item;

    [ObservableProperty]
    public ObservableCollection<LocalDatabaseSource> localDatabaseSources = new();

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

    public DataSources_LocalDatabasesViewModel(IDataSourceService dataSourceService)
    {
        _dataSourceService = dataSourceService;
        LoadLocalDatabaseSources();
        AddValue = null;
        AddName = null;
        EditValue = null;
        EditName = null;
        DeleteName = null;
        DeleteValue = null;
    }

    [RelayCommand]
    public async void LoadLocalDatabaseSources()
    {
        LocalDatabaseSources = await _dataSourceService.GetLocalDatabaseSourcesAsync();
    }

    [RelayCommand]
    public async void AddLocalDatabaseSource()
    {
        await _dataSourceService.SaveLocalDatabaseSourceAsync(new LocalDatabaseSource { Name = AddName, Address = AddValue });
        LoadLocalDatabaseSources();
    }

    [RelayCommand]
    public async void ShowEditLocalDatabaseSource()
    {
        EditName = Item.Name;
        EditValue = Item.Address;
    }

    [RelayCommand]
    public async void EditLocalDatabaseSource()
    {
        await _dataSourceService.DeleteLocalDatabaseSourceAsync(Item.Name);
        await _dataSourceService.SaveLocalDatabaseSourceAsync(new LocalDatabaseSource { Name = EditName, Address = EditValue });
        LoadLocalDatabaseSources();
    }

    [RelayCommand]
    public async void DeleteLocalDatabaseSource()
    {
        await _dataSourceService.DeleteLocalDatabaseSourceAsync(Item.Name);
        LoadLocalDatabaseSources();
    }
}