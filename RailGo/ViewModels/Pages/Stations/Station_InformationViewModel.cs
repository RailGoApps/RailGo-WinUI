using System.Collections.ObjectModel;
using System.Diagnostics;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using RailGo.Services;
using RailGo.Core.Models.QueryDatas;
using RailGo.Core.Models;
using System.Threading.Tasks;
using System.Linq;
using RailGo.ViewModels.Pages.Shell;

namespace RailGo.ViewModels.Pages.Stations;

public partial class Station_InformationViewModel : ObservableRecipient
{
    public Station_InformationViewModel()
    {
    }
    public MainWindowViewModel progressBarVM = App.GetService<MainWindowViewModel>();

    [ObservableProperty]
    private ObservableCollection<StationPreselectResult> stations = new ObservableCollection<StationPreselectResult>();

    [ObservableProperty]
    private string inputSearchStation;

    [ObservableProperty]
    private bool isLoading;

    // 使用 AsyncRelayCommand 替代原来的同步方法
    [RelayCommand]
    private async Task SearchStationsAsync()
    {
        if (string.IsNullOrWhiteSpace(InputSearchStation))
        {
            Stations.Clear();
            return;
        }

        try
        {
            IsLoading = true;
            progressBarVM.TaskIsInProgress = "Visible";

            var queryService = App.GetService<QueryService>();
            // 调用 API 进行搜索
            Stations = await queryService.QueryStationPreselectAsync(InputSearchStation);

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