using System.Diagnostics;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using RailGo.ViewModels.Pages.Settings.DataSources;

namespace RailGo.Views.Pages.Settings.DataSources;

public sealed partial class DataSources_MainPage : Page
{
    public DataSources_MainViewModel ViewModel
    {
        get;
    }

    private bool AllowCustomSourceToggle_DoNotShow = true;
    public DataSources_MainPage()
    {
        ViewModel = App.GetService<DataSources_MainViewModel>();
        InitializeComponent();
        Loaded += (s, e) => { AllowCustomSourceToggle_DoNotShow = false; };
    }

    private async void AllowCustomSourceToggle_Toggled(object sender, RoutedEventArgs e)
    {
        var toggleSwitch = sender as ToggleSwitch;
        if (toggleSwitch != null && toggleSwitch.IsOn == true && !AllowCustomSourceToggle_DoNotShow)
        {
            var result = await OpenCustomSourcesDialog.ShowAsync();

            if (result != ContentDialogResult.Primary)
            {
                toggleSwitch.IsOn = false;
                ViewModel.AllowCustomSource = false;
            }
        }
    }
}