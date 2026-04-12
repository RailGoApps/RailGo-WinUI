using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using RailGo.Core.Models.QueryDatas;
using RailGo.ViewModels.Pages.StationToStation;
using RailGo.Views.Pages.Stations;
using RailGo.Views.Pages.Trains;
using Windows.Foundation.Metadata;

namespace RailGo.Views.Pages.StationToStation;

public sealed partial class StationToStationPage : Page
{
    public StationToStationViewModel ViewModel
    {
        get;
    }

    public Station_InformationPage FromStationSelector;
    public Station_InformationPage ToStationSelector;
    public TrainRunInfo _item;

    public StationToStationPage()
    {
        ViewModel = App.GetService<StationToStationViewModel>();
        InitializeComponent();
        FromStationSelector = App.GetService<Station_InformationPage>();
        ToStationSelector = App.GetService<Station_InformationPage>();
        FromStationSelector.SetMode("StationToStation_SearchFromStation");
        ToStationSelector.SetMode("StationToStation_SearchToStation");
    }

    private void OpenStationSelector_FromStation_Click(object sender, Microsoft.UI.Xaml.RoutedEventArgs e)
    {
        PageFrame.Content = FromStationSelector;
        ViewModel.ContentText = "选择始发车站";
        ViewModel.SelectTeachingTipIsOpen = true;
    }

    private void OpenStationSelector_ToStation_Click(object sender, Microsoft.UI.Xaml.RoutedEventArgs e)
    {
        PageFrame.Content = ToStationSelector;
        ViewModel.ContentText = "选择终到车站";
        ViewModel.SelectTeachingTipIsOpen = true;
    }

    private void SwitchFromAndToButton_Click(object sender, Microsoft.UI.Xaml.RoutedEventArgs e)
    {
        var From = ViewModel.FromStation;
        var To = ViewModel.ToStation;
        var fromKeyword = ViewModel.FromStationKeyword;
        var toKeyword = ViewModel.ToStationKeyword;
        ViewModel.FromStation = To;
        ViewModel.ToStation = From;
        ViewModel.FromStationKeyword = toKeyword;
        ViewModel.ToStationKeyword = fromKeyword;
    }

    private void CloseStationChooseButton_Click(object sender, RoutedEventArgs e)
    {
        ViewModel.SelectTeachingTipIsOpen = false;
    }
    private async void SearchButton_Click(object sender, RoutedEventArgs e)
    {
        await ViewModel.QueryTrainListAsync();
    }

    private void DetailsBtn_Click(object sender, RoutedEventArgs e)
    {
        if (sender is HyperlinkButton button)
        {
            string BarHeader = null;
            string icon = null;
            Page page = null;
            List<string> StationType = new() { "SearchingST" };
            switch (button.Name.ToString())
            {
                case "TrainDetail_Btn":
                    icon = "\uE7C0";
                    BarHeader = _item.Number;
                    page = new TrainNumberTripDetailsPage()
                    {
                        DataContext = new TrainPreselectResult { FullNumber = _item.Number }
                    };
                    break;
                case "FromStationDetail_Btn":
                    icon = "\uF161";
                    BarHeader = _item.FromStationTelecode;
                    page = new StationDetailsPage()
                    {
                        DataContext = new StationPreselectResult { Name = _item.FromStationName, TeleCode = _item.FromStationTelecode, Type = StationType }
                    };
                    break;
                case "ToStationDetail_Btn":
                    icon = "\uF161";
                    BarHeader = _item.ToStationTelecode;
                    page = new StationDetailsPage()
                    {
                        DataContext = new StationPreselectResult { Name = _item.ToStationName, TeleCode = _item.ToStationTelecode, Type = StationType }
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
        }
    }
}
