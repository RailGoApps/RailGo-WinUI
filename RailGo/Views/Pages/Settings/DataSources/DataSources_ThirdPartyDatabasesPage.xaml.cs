using System.Diagnostics;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using RailGo.ViewModels.Pages.Settings.DataSources;

namespace RailGo.Views.Pages.Settings.DataSources;

public sealed partial class DataSources_ThirdPartyDatabasesPage : Page
{
    public DataSources_ThirdPartyDatabasesViewModel ViewModel
    {
        get;
    }

    public DataSources_ThirdPartyDatabasesPage()
    {
        ViewModel = App.GetService<DataSources_ThirdPartyDatabasesViewModel>();
        InitializeComponent();
    }
}