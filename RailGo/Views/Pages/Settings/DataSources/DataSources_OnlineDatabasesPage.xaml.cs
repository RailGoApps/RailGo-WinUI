using System.Diagnostics;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using RailGo.ViewModels.Pages.Settings.DataSources;

namespace RailGo.Views.Pages.Settings.DataSources;

public sealed partial class DataSources_OnlineDatabasesPage : Page
{
    public DataSources_OnlineDatabasesViewModel ViewModel
    {
        get;
    }

    public DataSources_OnlineDatabasesPage()
    {
        ViewModel = App.GetService<DataSources_OnlineDatabasesViewModel>();
        InitializeComponent();
    }

    private void DeleteDBSettingsCardTeachingTip_CloseButtonClick(TeachingTip sender, object args)
    {
        //CloseButtonCommand="{x:Bind ViewModel.CloseDeleteDBSettingsCardTeachingTipCommand, Mode=TwoWay}"
        ViewModel.CloseDeleteDBSettingsCardTeachingTipAsync();
    }

    private void ExtractDBSettingsCardTeachingTip_CloseButtonClick(TeachingTip sender, object args)
    {
        //CloseButtonCommand="{x:Bind ViewModel.CloseDeleteDBSettingsCardTeachingTipCommand, Mode=TwoWay}"
        ViewModel.CloseExtractDBSettingsCardTeachingTipAsync();
    }
}