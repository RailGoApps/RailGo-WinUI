using Microsoft.UI.Xaml.Controls;
using System.Diagnostics;

using RailGo.Core.Models;
using RailGo.Core.Models.QueryDatas;
using System.Collections.ObjectModel;
using CommunityToolkit.WinUI.UI.Controls;
using Microsoft.UI.Xaml;
using RailGo.ViewModels.Pages.Trains;
using static System.Runtime.InteropServices.JavaScript.JSType;

using RailGo.Views.Pages.Stations;
using RailGo.Views.Pages.TrainEmus;

namespace RailGo.Views.Pages.Trains;

public sealed partial class TrainNumberTripDetailsPage : Page
{
    public TrainPreselectResult DataFromLast => DataContext as TrainPreselectResult;
    public TimetableItem _item_ViaStaion;
    public TrainDiagram _item_Routings;
    public EmuOperation _item_EmuRouting;
    public TrainNumberTripDetailsViewModel ViewModel
    {
        get;
    }
    public string train_no;
    public string date;
    public TrainNumberTripDetailsPage()
    {
        this.Loaded += OnLoad;
        ViewModel = App.GetService<TrainNumberTripDetailsViewModel>();
        InitializeComponent();
    }

    public void OnLoad(object sender, RoutedEventArgs e)
    {
        train_no = DataFromLast.Number;
        date = System.DateTime.Now.ToString("yyyyMMdd");
        ViewModel.GetInformationCommand.Execute((train_no, date));
        this.Loaded -= OnLoad;
    }
    private async void DetailBtnClick(object sender, RoutedEventArgs e)
    {
        if (sender is HyperlinkButton button)
        {
            string BarHeader = null;
            string icon = null;
            Page page = null;
            TimetableItem Details = null;
            var DataContexttt = new StationPreselectResult();
            List<string> StationType = new() { "SearchingST" };
            bool Await = false;
            // 根据选择的导航按钮切换右侧内容
            switch (button.Name.ToString())
            {
                case "ViaStationsStationInformation":
                    icon = "\uF161";
                    BarHeader = _item_ViaStaion.Station;
                    page = new StationDetailsPage()
                    {
                        DataContext = new StationPreselectResult { Name = _item_ViaStaion.Station, TeleCode = _item_ViaStaion.StationTelecode, Type = StationType }
                    };
                    break;
                case "RoutingsTrainNumberDetailsInformation":
                    icon = "\uE7C0";
                    BarHeader = _item_EmuRouting.TrainNo;
                    page = new TrainNumberTripDetailsPage()
                    {
                        DataContext = new TrainPreselectResult { FullNumber = _item_EmuRouting.TrainNo }
                    };
                    break;
                case "RoutingsTrainEmuDetailsInformation":
                    icon = "\uEB4D";
                    BarHeader = _item_EmuRouting.EmuNo;
                    page = new EMU_RoutingDetailsPage()
                    {
                        DataContext = _item_EmuRouting
                    };
                    break;
                case "RoutingsTrainInformation":
                    icon = "\uE7C0";
                    BarHeader = _item_Routings.TrainNum;
                    page = new TrainNumberTripDetailsPage()
                    {
                        DataContext = new TrainPreselectResult { FullNumber = _item_Routings.TrainNum }
                    };
                    break;
                case "RoutingsFromStationInformation":
                    icon = "\uF161";
                    BarHeader = _item_Routings.FromStation;
                    Details = ViewModel.FindstationTrainsByStationName(BarHeader);
                    if (Details == null)
                    {
                        var DetailsFromOline = await ViewModel.SearchStationDetails(BarHeader);
                        DataContexttt = new StationPreselectResult { Name = DetailsFromOline[0].Name, TeleCode = DetailsFromOline[0].TeleCode, Type = DetailsFromOline[0].Type };
                    }
                    else
                    {
                        DataContexttt = new StationPreselectResult { Name = Details.Station, TeleCode = Details.StationTelecode, Type = StationType };
                    }
                    page = new StationDetailsPage()
                    {
                        DataContext = DataContexttt
                    };
                    break;
                case "RoutingsToStationInformation":
                    icon = "\uF161";
                    BarHeader = _item_Routings.ToStation;
                    Details = ViewModel.FindstationTrainsByStationName(BarHeader);
                    if (Details == null)
                    {
                        var DetailsFromOline = await ViewModel.SearchStationDetails(BarHeader);
                        DataContexttt = new StationPreselectResult { Name = DetailsFromOline[0].Name, TeleCode = DetailsFromOline[0].TeleCode, Type = DetailsFromOline[0].Type };
                    }
                    else
                    {
                        DataContexttt = new StationPreselectResult { Name = Details.Station, TeleCode = Details.StationTelecode, Type = StationType };
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
