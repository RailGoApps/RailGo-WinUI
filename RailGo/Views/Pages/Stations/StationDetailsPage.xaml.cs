using System.Diagnostics;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using RailGo.Core.Models;
using RailGo.Core.Models.QueryDatas;
using RailGo.ViewModels.Pages.Stations;
using RailGo.Services;

using RailGo.Views.Pages.Trains;

namespace RailGo.Views.Pages.Stations;

public sealed partial class StationDetailsPage : Page
{
    public StationPreselectResult DataFromLast => DataContext as StationPreselectResult;
    public StationScreenItem _item_bigscreen;
    public StationTrain _item_trains;

    public StationDetailsViewModel ViewModel
    {
        get;
    }

    public StationDetailsPage()
    {
        InitializeComponent();
        this.Loaded += OnLoad;
        ViewModel = App.GetService<StationDetailsViewModel>();
        TrainsDataGrid.Visibility = Visibility.Visible;
        BigScreenDataGrid.Visibility = Visibility.Collapsed;
    }

    public void OnLoad(object sender, RoutedEventArgs e)
    {
        if (DataFromLast != null)
        {
            // 使用电报码获取详细信息
            ViewModel.GetInformationCommand.Execute(DataFromLast);
        }
        this.Loaded -= OnLoad;
    }

    private void OnNavButtonChecked(object sender, RoutedEventArgs e)
    {
        if (sender is RadioButton radioButton && TrainsDataGrid != null && BigScreenDataGrid != null)
        {
            // 根据选择的导航按钮切换右侧内容
            switch (radioButton.Content.ToString())
            {
                case "途经车次":
                    TrainsDataGrid.Visibility = Visibility.Visible;
                    BigScreenDataGrid.Visibility = Visibility.Collapsed;
                    // 其他页面隐藏
                    break;
                case "车站大屏":
                    TrainsDataGrid.Visibility = Visibility.Collapsed;
                    BigScreenDataGrid.Visibility = Visibility.Visible;
                    // 显示页面二
                    break;
                case "路线":
                    TrainsDataGrid.Visibility = Visibility.Collapsed;
                    BigScreenDataGrid.Visibility = Visibility.Collapsed;
                    // 显示页面三
                    break;
            }
        }
    }

    private async void DetailBtnClick(object sender, RoutedEventArgs e)
    {
        if (sender is HyperlinkButton button)
        {
            string BarHeader = null;
            string icon = null;
            Page page = null;
            StationTrain Details = null;
            List<string> StationType = new() { "SearchingST" };
            bool Await = false;
            var DataContexttt = new StationPreselectResult();
            // 根据选择的导航按钮切换右侧内容
            switch (button.Name.ToString())
            {
                case "StationTrains_TrainInformation":
                    icon = "\uE7C0";
                    BarHeader = _item_trains.Number;
                    page = new TrainNumberTripDetailsPage()
                    {
                        DataContext = new TrainPreselectResult { FullNumber = _item_trains.Number }
                    };
                    break;
                case "StationTrains_FromStation":
                    icon = "\uF161";
                    BarHeader = _item_trains.FromStation.Station;
                    page = new StationDetailsPage()
                    {
                        DataContext = new StationPreselectResult { Name = _item_trains.FromStation.Station, TeleCode = _item_trains.FromStation.StationTelecode, Type = StationType }
                    };
                    break;
                case "StationTrains_ToStation":
                    icon = "\uF161";
                    BarHeader = _item_trains.ToStation.Station;
                    page = new StationDetailsPage()
                    {
                        DataContext = new StationPreselectResult { Name = _item_trains.ToStation.Station, TeleCode = _item_trains.ToStation.StationTelecode, Type = StationType }
                    };
                    break;
                case "BigScreen_TrainInformation":
                    icon = "\uE7C0";
                    BarHeader = _item_bigscreen.TrainNumber;
                    page = new TrainNumberTripDetailsPage()
                    {
                        DataContext = new TrainPreselectResult { FullNumber = _item_bigscreen.TrainNumber }
                    };
                    break;
                case "BigScreen_FromStation":
                    icon = "\uF161";
                    BarHeader = _item_bigscreen.FromStation;                   
                    Details = ViewModel.FindstationTrainsByTrainNumber(_item_bigscreen.TrainNumber);
                    if (Details == null)
                    {
                        var DetailsFromOline = await ViewModel.SearchStationDetails(BarHeader);
                        DataContexttt = new StationPreselectResult { Name = DetailsFromOline[0].Name, TeleCode = DetailsFromOline[0].TeleCode, Type = DetailsFromOline[0].Type };
                    }
                    else
                    {
                        DataContexttt = new StationPreselectResult { Name = Details.FromStation.Station, TeleCode = Details.FromStation.StationTelecode, Type = StationType };
                    }
                    page = new StationDetailsPage()
                        {
                            DataContext = DataContexttt
                        };
                    break;
                case "BigScreen_ToStation":
                    icon = "\uF161";
                    BarHeader = _item_bigscreen.ToStation;
                    Details = ViewModel.FindstationTrainsByTrainNumber(_item_bigscreen.TrainNumber);
                    if (Details == null)
                    {
                        var DetailsFromOline = await ViewModel.SearchStationDetails(BarHeader);
                        DataContexttt = new StationPreselectResult { Name = DetailsFromOline[0].Name, TeleCode = DetailsFromOline[0].TeleCode, Type = DetailsFromOline[0].Type };
                    }
                    else
                    {
                        DataContexttt = new StationPreselectResult { Name = Details.ToStation.Station, TeleCode = Details.ToStation.StationTelecode, Type = StationType };
                    }
                    page = new StationDetailsPage()
                    {
                        DataContext = DataContexttt
                    };
                    break;
            }

            TabViewItem tabViewItem = new()
            {
                Header = BarHeader,
                Content = page,
                CanDrag = true,
                IconSource = new FontIconSource() { Glyph = icon }
            };
            MainWindow.Instance.MainTabView.TabItems.Add(tabViewItem);
            MainWindow.Instance.MainTabView.SelectedItem = tabViewItem;

            if (Await)
            {

                //var StationDetails = await ApiService.StationPreselectAsync(BarHeader);
                //page = new StationDetailsPage()
               // {
                   // DataContext = new StationPreselectResult { Name = Details.ToStation.Station, TeleCode = Details.ToStation.StationTelecode, Type = StationDetails }
               // };
            }
        }

    }
}