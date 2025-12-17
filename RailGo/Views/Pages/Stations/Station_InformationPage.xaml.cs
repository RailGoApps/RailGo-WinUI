using System.Diagnostics;
using CommunityToolkit.Mvvm.Messaging;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Newtonsoft.Json.Linq;
using RailGo.Core.Models;
using RailGo.Core.Models.Messages;
using RailGo.Core.Models.QueryDatas;
using RailGo.ViewModels.Pages.Stations;

namespace RailGo.Views.Pages.Stations;

public sealed partial class Station_InformationPage : Page
{
    public Station_InformationViewModel ViewModel
    {
        get;
    }

    // 保持原有的 _item 字段，类型改为 StationSearch
    public StationPreselectResult _item;

    public string OpenMode = "NavigationView";

    public Station_InformationPage()
    {
        ViewModel = App.GetService<Station_InformationViewModel>();
        InitializeComponent();
    }

    public void SetMode(string mode)
    {
        OpenMode = mode;
    }

    private void StationDetailsBtn_Click(object sender, RoutedEventArgs e)
    {
        if (_item == null)
            return;

        StationDetailsPage page = new()
        {
            DataContext = _item
        };

        TabViewItem tabViewItem = new()
        {
            Header = _item.Name,
            Content = page,
            CanDrag = true,
            IconSource = new FontIconSource() { Glyph = "\uF161" }
        };

        MainWindow.Instance.MainTabView.TabItems.Add(tabViewItem);
        MainWindow.Instance.MainTabView.SelectedItem = tabViewItem;
    }

    private void SelectClick(object sender, RoutedEventArgs e)
    {
        if (_item == null)
            return;

        if (OpenMode == "NavigationView")
        {
            StationDetailsPage page = new()
            {
                DataContext = _item
            };

            TabViewItem tabViewItem = new()
            {
                Header = _item.Name,
                Content = page,
                CanDrag = true,
                IconSource = new FontIconSource() { Glyph = "\uF161" }
            };

            MainWindow.Instance.MainTabView.TabItems.Add(tabViewItem);
            MainWindow.Instance.MainTabView.SelectedItem = tabViewItem;
        }
        else if (OpenMode == "StationToStation_SearchFromStation")
        {
            WeakReferenceMessenger.Default.Send(new StationSelectedInStationToStationMessagerModel
            {
                MessagerName = "StationToStation_SearchFromStation",
                Data = _item
            });
        }
        else if (OpenMode == "StationToStation_SearchToStation")
        {
            WeakReferenceMessenger.Default.Send(new StationSelectedInStationToStationMessagerModel
            {
                MessagerName = "StationToStation_SearchToStation",
                Data = _item
            });
        }
    }
}