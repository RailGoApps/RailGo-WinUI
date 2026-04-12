using System.Collections.ObjectModel;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

using RailGo.Core.Models;
using RailGo.Core.Models.QueryDatas;
using RailGo.ViewModels.Pages.TrainEmus;
using RailGo.Views.Pages.Trains;

namespace RailGo.Views.Pages.TrainEmus;

public sealed partial class EMU_RoutingDetailsPage : Page
{
    public EmuOperation _item;
    public EmuOperation DataFromLast => DataContext as EmuOperation;
    public EMU_RoutingDetailsViewModel ViewModel
    {
        get;
    }

    public EMU_RoutingDetailsPage()
    {
        ViewModel = App.GetService<EMU_RoutingDetailsViewModel>();
        InitializeComponent();
        this.Loaded += OnLoad;
    }
    public void OnLoad(object sender, RoutedEventArgs e)
    {
        if (DataFromLast != null)
        {
            // 使用电报码获取详细信息
            ViewModel.SearchEmuDetailsCommand.Execute((DataFromLast));
        }
        this.Loaded -= OnLoad;
    }

    private void TrainNumberDetailsBtn_Click(object sender, RoutedEventArgs e)
    {
        // 显示车次Details

        TrainNumberTripDetailsPage page = new()
        {
            DataContext = new TrainPreselectResult { FullNumber = _item.TrainNo }
        };

        TabViewItem tabViewItem = new()
        {
            Header = _item.TrainNo,
            Content = page,
            CanDrag = true,
            IconSource = new FontIconSource() { Glyph = "\uE7C0" }
        };
        MainWindow.Instance.MainTabView.TabItems.Add(tabViewItem);
        MainWindow.Instance.MainTabView.SelectedItem = tabViewItem;
    }
    private void TrainEmuDetailsBtn_Click(object sender, RoutedEventArgs e)
    {
        // 显示车组Details

        EMU_RoutingDetailsPage page = new()
        {
            DataContext = _item
        };

        TabViewItem tabViewItem = new()
        {
            Header = _item.EmuNo,
            Content = page,
            CanDrag = true,
            IconSource = new FontIconSource() { Glyph = "\uEB4D" }
        };
        MainWindow.Instance.MainTabView.TabItems.Add(tabViewItem);
        MainWindow.Instance.MainTabView.SelectedItem = tabViewItem;
    }
}
