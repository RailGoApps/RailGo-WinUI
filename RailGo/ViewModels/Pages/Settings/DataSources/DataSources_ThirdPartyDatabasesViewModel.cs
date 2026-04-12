using System.Reflection;
using System.Windows.Input;

using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;

using RailGo.Contracts.Services;
using RailGo.Helpers;

using Microsoft.UI.Xaml;

using Windows.ApplicationModel;

namespace RailGo.ViewModels.Pages.Settings.DataSources;

public partial class DataSources_ThirdPartyDatabasesViewModel : ObservableRecipient
{
    public DataSources_ThirdPartyDatabasesViewModel(IThemeSelectorService themeSelectorService)
    {

    }
}