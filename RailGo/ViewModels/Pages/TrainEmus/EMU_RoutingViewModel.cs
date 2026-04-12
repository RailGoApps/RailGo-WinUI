using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Security.Cryptography.X509Certificates;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using CommunityToolkit.WinUI.UI.Controls;
using Newtonsoft.Json;
using RailGo.Core.Models;
using RailGo.Core.Models.QueryDatas;
using RailGo.Services;
using Windows.System;
using RailGo.ViewModels.Pages.Shell;

namespace RailGo.ViewModels.Pages.TrainEmus;

public partial class EMU_RoutingViewModel : ObservableObject
{
    [ObservableProperty]
    public ObservableCollection<EmuOperation> trainNumberEmuInfos = new();

    [ObservableProperty]
    private bool isLoading;

    [ObservableProperty]
    public string inputEmuID;

    public MainWindowViewModel progressBarVM = App.GetService<MainWindowViewModel>();

    public EMU_RoutingViewModel()
    {
    }

    [RelayCommand]
    private async Task SearchEmuTrainsAsync()
    {
        if (string.IsNullOrWhiteSpace(InputEmuID))
        {
            TrainNumberEmuInfos.Clear();
            return;
        }

        try
        {
            IsLoading = true;
            progressBarVM.TaskIsInProgress = "Visible";

            var queryService = App.GetService<QueryService>();
            TrainNumberEmuInfos = await queryService.QueryEmuQueryAsync("emu", InputEmuID);

        }
        catch (Exception ex)
        {
            progressBarVM.IfShowErrorInfoBarOpen = true;
            progressBarVM.ShowErrorInfoBarContent = ex.Message;
            progressBarVM.ShowErrorInfoBarTitle = "Error";
            WaitCloseInfoBar();
        }
        finally
        {
            IsLoading = false;
            progressBarVM.TaskIsInProgress = "Collapsed";
        }
    }
    private async void WaitCloseInfoBar()
    {
        await Task.Delay(3000);
        progressBarVM.IfShowErrorInfoBarOpen = false;
    }
}
