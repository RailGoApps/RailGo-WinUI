using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using RailGo.Core.Models.QueryDatas;
using RailGo.ViewModels.Pages.StationToStation;
using RailGo.Views.Pages.Trains;
using VirtualKey = Windows.System.VirtualKey;

namespace RailGo.Views.Pages.StationToStation;

public sealed partial class StationToStationPage : Page
{
    public StationToStationViewModel ViewModel
    {
        get;
    }

    public TrainRunInfo _item;

    public StationToStationPage()
    {
        ViewModel = App.GetService<StationToStationViewModel>();
        InitializeComponent();
        Loaded += Page_Loaded;
    }

    private void Page_Loaded(object sender, RoutedEventArgs e)
    {
        FromSuggestionPopup.DesiredPlacement = Microsoft.UI.Xaml.Controls.Primitives.PopupPlacementMode.Bottom;
        FromSuggestionPopup.PlacementTarget = FromKeywordPanel;

        ToSuggestionPopup.DesiredPlacement = Microsoft.UI.Xaml.Controls.Primitives.PopupPlacementMode.Bottom;
        ToSuggestionPopup.PlacementTarget = ToKeywordPanel;
    }

    private async void SearchFromStationBtn_Click(object sender, RoutedEventArgs e)
    {
        await ViewModel.QueryFromStationSuggestionsAsync();
    }

    private async void SearchToStationBtn_Click(object sender, RoutedEventArgs e)
    {
        await ViewModel.QueryToStationSuggestionsAsync();
    }

    private void FromSuggestionList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        // ViewModel 的 OnSelectedFromSuggestionChanged 会负责把站写回输入框并关闭 Popup
    }

    private void ToSuggestionList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        // ViewModel 的 OnSelectedToSuggestionChanged 会负责把站写回输入框并关闭 Popup
    }

    private void FromSuggestionPopup_Opened(object sender, object e)
    {
        // Popup 浮起后让 ListBox 取第一个候选的焦点
        if (ViewModel.FromStationSuggestions.Count > 0 && FromSuggestionPopup.Child is FrameworkElement child)
        {
            child.Focus(FocusState.Programmatic);
        }
    }

    private void ToSuggestionPopup_Opened(object sender, object e)
    {
        if (ViewModel.ToStationSuggestions.Count > 0 && ToSuggestionPopup.Child is FrameworkElement child)
        {
            child.Focus(FocusState.Programmatic);
        }
    }

    private void SwitchFromAndToButton_Click(object sender, RoutedEventArgs e)
    {
        var from = ViewModel.FromStation;
        var to = ViewModel.ToStation;
        var fromKeyword = ViewModel.FromStationKeyword;
        var toKeyword = ViewModel.ToStationKeyword;
        ViewModel.FromStation = to;
        ViewModel.ToStation = from;
        ViewModel.FromStationKeyword = toKeyword;
        ViewModel.ToStationKeyword = fromKeyword;
        ViewModel.FromSuggestionOpen = false;
        ViewModel.ToSuggestionOpen = false;
    }

    private async void SearchButton_Click(object sender, RoutedEventArgs e)
    {
        ViewModel.FromSuggestionOpen = false;
        ViewModel.ToSuggestionOpen = false;
        await ViewModel.QueryTrainListAsync();
    }

    private void StationKeyword_KeyDown(object sender, KeyRoutedEventArgs e)
    {
        if (e.Key != VirtualKey.Enter)
        {
            return;
        }

        if (sender is TextBox tb)
        {
            if (tb.Name == null || tb.Name.Contains("From"))
            {
                _ = ViewModel.QueryFromStationSuggestionsAsync();
            }
            else
            {
                _ = ViewModel.QueryToStationSuggestionsAsync();
            }
            e.Handled = true;
        }
    }

    private void DetailsBtn_Click(object sender, RoutedEventArgs e)
    {
        if (sender is HyperlinkButton button)
        {
            string barHeader = null;
            string icon = null;
            Page page = null;
            List<string> stationType = new() { "SearchingST" };
            switch (button.Name)
            {
                case "TrainDetail_Btn":
                    icon = "\uE7C0";
                    barHeader = _item.Number;
                    page = new TrainNumberTripDetailsPage()
                    {
                        DataContext = new TrainPreselectResult { FullNumber = _item.Number, Date = ViewModel.StartDate.ToString("yyyyMMdd") }
                    };
                    break;
                case "FromStationDetail_Btn":
                    icon = "\uE707";
                    barHeader = _item.FromStationTelecode;
                    page = new RailGo.Views.Pages.Stations.StationDetailsPage()
                    {
                        DataContext = new StationPreselectResult { Name = _item.FromStationName, TeleCode = _item.FromStationTelecode, Type = stationType }
                    };
                    break;
                case "ToStationDetail_Btn":
                    icon = "\uE707";
                    barHeader = _item.ToStationTelecode;
                    page = new RailGo.Views.Pages.Stations.StationDetailsPage()
                    {
                        DataContext = new StationPreselectResult { Name = _item.ToStationName, TeleCode = _item.ToStationTelecode, Type = stationType }
                    };
                    break;
            }

            TabViewItem tabViewItem = new()
            {
                Header = barHeader,
                Content = page,
                CanDrag = true,
                IconSource = new FontIconSource() { Glyph = icon }
            };
            MainWindow.Instance.MainTabView.TabItems.Add(tabViewItem);
            MainWindow.Instance.MainTabView.SelectedItem = tabViewItem;
        }
    }
}
